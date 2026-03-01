from __future__ import annotations

import asyncio
import hmac
import http.client
import json
import re
import signal
import socket
import subprocess
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import httpx
import uvicorn
from rich.console import Console
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.service import check_budget_and_maybe_shutdown, maybe_auto_shutdown_idle
from app.state import load_state, save_state
from app.usage import track_usage
from app.user_config import load_config
from app.vast import VastAPI, probe_worker_ready_async

console = Console()
_REQUEST_LOG_PATH: Path | None = None
_WATCHDOG_ENABLED = False


class ConfigCache:
    _cfg = None
    _state = None
    _loaded = 0.0
    ttl_s = 5.0

    @classmethod
    def get(cls):
        now = time.time()
        if cls._cfg is None or (now - cls._loaded) > cls.ttl_s:
            cls.reload()
        return cls._cfg, cls._state

    @classmethod
    def reload(cls):
        cls._cfg = load_config()
        cls._state = load_state()
        cls._loaded = time.time()


def _err(message: str, type_: str, code: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": type_, "code": code}}, status_code=status)


def _extract_usage(payload: dict) -> tuple[int, int]:
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))


def _extract_timings(payload: dict) -> tuple[float, float]:
    timings = payload.get("timings") if isinstance(payload, dict) else None
    if not isinstance(timings, dict):
        return 0.0, 0.0
    prompt_ms = timings.get("prompt_ms", 0.0)
    predicted_ms = timings.get("predicted_ms", 0.0)
    try:
        return max(0.0, float(prompt_ms)), max(0.0, float(predicted_ms))
    except (TypeError, ValueError):
        return 0.0, 0.0


def _valid_worker_url(url: str) -> bool:
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in {"http", "https"}:
        return False
    if not p.hostname:
        return False
    return bool(re.match(r"^[A-Za-z0-9.-]+$", p.hostname))


async def health(_: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


def _log_request(method: str, path: str, status: int, latency_ms: float) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"[dim]{ts}[/dim] {method} {path} -> [bold]{status}[/bold] ({latency_ms:.1f}ms)")
    if _REQUEST_LOG_PATH is None:
        return
    try:
        _REQUEST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _REQUEST_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "method": method,
                        "path": path,
                        "status": status,
                        "latency_ms": round(latency_ms, 1),
                    },
                    separators=(",", ":"),
                )
            )
            handle.write("\n")
    except OSError:
        pass


def _origin_allowed(origin: str, allowed_origins: list[str] | None) -> bool:
    if not origin or not allowed_origins:
        return False
    return "*" in allowed_origins or origin in allowed_origins


def _apply_cors_headers(response: Response, request: Request, allowed_origins: list[str] | None) -> None:
    origin = request.headers.get("origin")
    if not origin or not _origin_allowed(origin, allowed_origins):
        return
    allow_any = "*" in (allowed_origins or [])
    response.headers["Access-Control-Allow-Origin"] = "*" if allow_any else origin
    if not allow_any:
        response.headers["Vary"] = "Origin"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = request.headers.get("access-control-request-headers", "*")


def _preflight_response(request: Request, allowed_origins: list[str] | None) -> Response:
    response = Response(status_code=204)
    _apply_cors_headers(response, request, allowed_origins)
    return response


async def _idle_monitor() -> None:
    while True:
        try:
            if maybe_auto_shutdown_idle():
                console.print("[yellow]Idle timeout reached; worker auto-shutdown triggered[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Idle monitor warning: {exc}[/yellow]")
        await asyncio.sleep(30)


async def _worker_watchdog(client: httpx.AsyncClient) -> None:
    while True:
        try:
            cfg, state = ConfigCache.get()
            if state.instance_id and state.worker_url and _valid_worker_url(state.worker_url):
                headers = {"Authorization": f"Bearer {cfg.bearer_token_plain}"} if cfg.bearer_token_plain else {}
                healthy = await probe_worker_ready_async(client, state.worker_url, headers=headers)
                if not healthy and cfg.vast_api_key_plain:
                    api = VastAPI(cfg.vast_api_key_plain, cfg.vast_base_url)
                    try:
                        fresh = await asyncio.to_thread(api.refresh_worker_url, state.instance_id)
                    finally:
                        api.client.close()
                    if fresh and fresh != state.worker_url:
                        state.worker_url = fresh
                        save_state(state)
                        ConfigCache.reload()
                        console.print(f"[yellow]Watchdog refreshed worker URL to {fresh}[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Watchdog warning: {exc}[/yellow]")
        await asyncio.sleep(30)


async def forward(request: Request) -> Response:
    start = time.perf_counter()
    cfg, state = ConfigCache.get()

    if request.method == "OPTIONS":
        return _preflight_response(request, cfg.cors_origins)

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {cfg.bearer_token_plain}"
    if not hmac.compare_digest(auth, expected):
        response = _err("unauthorized", "authentication_error", "unauthorized", 401)
        _apply_cors_headers(response, request, cfg.cors_origins)
        return response

    stopped, budget_msg = check_budget_and_maybe_shutdown()
    if budget_msg:
        console.print(f"[yellow]{budget_msg}[/yellow]")
    if stopped:
        response = _err("budget exceeded; worker shut down", "billing_error", "budget_limit", 402)
        _apply_cors_headers(response, request, cfg.cors_origins)
        return response

    if not state.worker_url:
        response = _err("worker not available", "service_unavailable", "worker_down", 503)
        _apply_cors_headers(response, request, cfg.cors_origins)
        return response
    if not _valid_worker_url(state.worker_url):
        response = _err("invalid worker url", "server_error", "invalid_worker_url", 500)
        _apply_cors_headers(response, request, cfg.cors_origins)
        return response

    suffix = request.url.path.replace("/v1", "", 1)
    target = f"{state.worker_url.rstrip('/')}/v1{suffix}"

    body = await request.body()
    # Replace proxy bearer with worker auth token (OPEN_BUTTON_TOKEN = bearer_token).
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length", "authorization"}}
    headers["Authorization"] = f"Bearer {cfg.bearer_token_plain}"

    client = cast(httpx.AsyncClient | None, request.app.state.client)
    assert client is not None

    async def _send_once(current_target: str) -> httpx.Response:
        req = client.build_request(
            method=request.method, url=current_target, params=request.query_params, content=body, headers=headers
        )
        return await client.send(req, stream=True)

    try:
        upstream = await _send_once(target)
    except (httpx.ConnectError, httpx.ConnectTimeout):
        if state.instance_id:
            try:
                api = VastAPI(cfg.vast_api_key_plain, cfg.vast_base_url)
                try:
                    fresh = api.refresh_worker_url(state.instance_id)
                finally:
                    api.client.close()
                if fresh:
                    state.worker_url = fresh
                    save_state(state)
                    ConfigCache.reload()
                    upstream = await _send_once(f"{fresh.rstrip('/')}/v1{suffix}")
                else:
                    raise
            except Exception:
                response = _err("upstream unavailable", "service_unavailable", "upstream_connect_error", 502)
                _apply_cors_headers(response, request, cfg.cors_origins)
                return response
        else:
            response = _err("upstream unavailable", "service_unavailable", "upstream_connect_error", 502)
            _apply_cors_headers(response, request, cfg.cors_origins)
            return response

    content_type = upstream.headers.get("content-type", "")
    is_sse = "text/event-stream" in content_type.lower()
    passthrough_headers = {
        k: v
        for k, v in upstream.headers.items()
        if k.lower() not in {"content-length", "transfer-encoding", "connection", "content-encoding"}
    }

    state.last_request_at = time.time()
    save_state(state)

    if is_sse:
        usage_accum = {"in": 0, "out": 0, "prompt_ms": 0.0, "predicted_ms": 0.0}

        async def iter_sse():
            buffer = ""
            try:
                async for chunk in upstream.aiter_text():
                    buffer += chunk
                    while "\n" in buffer:
                        line, buffer = buffer.split("\n", 1)
                        if line.startswith("data:"):
                            payload_s = line.removeprefix("data:").strip()
                            if payload_s and payload_s != "[DONE]":
                                try:
                                    payload = json.loads(payload_s)
                                    in_tok, out_tok = _extract_usage(payload)
                                    prompt_ms, predicted_ms = _extract_timings(payload)
                                    usage_accum["in"] += in_tok
                                    usage_accum["out"] += out_tok
                                    usage_accum["prompt_ms"] = max(usage_accum["prompt_ms"], prompt_ms)
                                    usage_accum["predicted_ms"] = max(usage_accum["predicted_ms"], predicted_ms)
                                except json.JSONDecodeError:
                                    pass
                        yield (line + "\n").encode("utf-8")
            finally:
                await upstream.aclose()
                if any(
                    (
                        usage_accum["in"],
                        usage_accum["out"],
                        usage_accum["prompt_ms"] > 0,
                        usage_accum["predicted_ms"] > 0,
                    )
                ):
                    track_usage(
                        usage_accum["in"],
                        usage_accum["out"],
                        prompt_ms=usage_accum["prompt_ms"],
                        predicted_ms=usage_accum["predicted_ms"],
                    )

        latency = (time.perf_counter() - start) * 1000
        _log_request(request.method, request.url.path, upstream.status_code, latency)
        streaming_response = StreamingResponse(
            iter_sse(), status_code=upstream.status_code, headers=passthrough_headers
        )
        _apply_cors_headers(streaming_response, request, cfg.cors_origins)
        return streaming_response

    raw = await upstream.aread()
    await upstream.aclose()
    if upstream.status_code in {502, 503}:
        ConfigCache.reload()

    try:
        payload = json.loads(raw.decode("utf-8"))
        in_tok, out_tok = _extract_usage(payload)
        prompt_ms, predicted_ms = _extract_timings(payload)
        if in_tok or out_tok or prompt_ms > 0 or predicted_ms > 0:
            track_usage(in_tok, out_tok, prompt_ms=prompt_ms, predicted_ms=predicted_ms)
    except (UnicodeDecodeError, json.JSONDecodeError):
        pass

    latency = (time.perf_counter() - start) * 1000
    _log_request(request.method, request.url.path, upstream.status_code, latency)
    proxy_response = Response(
        content=raw, status_code=upstream.status_code, media_type=content_type, headers=passthrough_headers
    )
    _apply_cors_headers(proxy_response, request, cfg.cors_origins)
    return proxy_response


async def models(request: Request) -> Response:
    cfg, state = ConfigCache.get()
    if request.method == "OPTIONS":
        return _preflight_response(request, cfg.cors_origins)
    response = JSONResponse(
        {"object": "list", "data": [{"id": state.model_name or "unknown", "object": "model", "owned_by": "vasted"}]}
    )
    _apply_cors_headers(response, request, cfg.cors_origins)
    return response


@asynccontextmanager
async def _lifespan(app: Starlette):
    ConfigCache.reload()
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=120.0))
    idle_task = asyncio.create_task(_idle_monitor())
    watchdog_task = asyncio.create_task(_worker_watchdog(app.state.client)) if _WATCHDOG_ENABLED else None
    try:
        yield
    finally:
        idle_task.cancel()
        if watchdog_task is not None:
            watchdog_task.cancel()
        await app.state.client.aclose()


app = Starlette(
    routes=[
        Route("/healthz", health, methods=["GET"]),
        Route("/v1/models", models, methods=["GET", "OPTIONS"]),
        Route("/v1/{path:path}", forward, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]),
    ],
    lifespan=_lifespan,
)


def run_proxy(host: str, port: int, watchdog: bool = False, log_file: str | None = None) -> None:
    global _REQUEST_LOG_PATH
    global _WATCHDOG_ENABLED

    _WATCHDOG_ENABLED = watchdog
    _REQUEST_LOG_PATH = Path(log_file).expanduser() if log_file else None
    try:
        signal.signal(signal.SIGHUP, lambda *_: ConfigCache.reload())
    except Exception:
        pass
    uvicorn.run(app, host=host, port=port, log_level="warning")


def _probe_host(host: str) -> str:
    return "127.0.0.1" if host == "0.0.0.0" else host


def is_proxy_running(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        with socket.create_connection((_probe_host(host), port), timeout=timeout_s):
            return True
    except OSError:
        return False


def is_proxy_healthy(host: str, port: int, timeout_s: float = 0.5) -> bool:
    try:
        conn = http.client.HTTPConnection(_probe_host(host), port, timeout=timeout_s)
        conn.request("GET", "/healthz")
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status == 200 and b'"ok":true' in body
    except Exception:
        return False


def ensure_proxy_running(host: str, port: int, wait_s: float = 15.0) -> bool:
    if is_proxy_healthy(host, port):
        return False
    subprocess.Popen(
        [sys.executable, "-m", "app.cli", "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
    )
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if is_proxy_healthy(host, port):
            return True
        time.sleep(0.2)
    if is_proxy_running(host, port):
        raise RuntimeError(
            f"Something is bound to {_probe_host(host)}:{port}, but the proxy health check never became ready"
        )
    raise RuntimeError(f"Proxy did not start on {_probe_host(host)}:{port}")
