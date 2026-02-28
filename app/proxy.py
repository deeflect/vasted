from __future__ import annotations

import asyncio
import hmac
import json
import re
import signal
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import cast
from urllib.parse import urlparse

import httpx
import uvicorn
from rich.console import Console
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app.service import check_budget_and_maybe_shutdown, maybe_auto_shutdown_idle
from app.state import load_state, save_state
from app.usage import track_usage
from app.user_config import load_config
from app.vast import VastAPI

console = Console()


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


async def models(_: Request) -> JSONResponse:
    state = load_state()
    return JSONResponse(
        {"object": "list", "data": [{"id": state.model_name or "unknown", "object": "model", "owned_by": "vasted"}]}
    )


def _log_request(method: str, path: str, status: int, latency_ms: float) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    console.print(f"[dim]{ts}[/dim] {method} {path} -> [bold]{status}[/bold] ({latency_ms:.1f}ms)")


async def _idle_monitor() -> None:
    while True:
        try:
            if maybe_auto_shutdown_idle():
                console.print("[yellow]Idle timeout reached; worker auto-shutdown triggered[/yellow]")
        except Exception as exc:
            console.print(f"[yellow]Idle monitor warning: {exc}[/yellow]")
        await asyncio.sleep(30)


async def forward(request: Request) -> Response:
    start = time.perf_counter()
    cfg, state = ConfigCache.get()

    auth = request.headers.get("authorization", "")
    expected = f"Bearer {cfg.bearer_token_plain}"
    if not hmac.compare_digest(auth, expected):
        return _err("unauthorized", "authentication_error", "unauthorized", 401)

    stopped, budget_msg = check_budget_and_maybe_shutdown()
    if budget_msg:
        console.print(f"[yellow]{budget_msg}[/yellow]")
    if stopped:
        return _err("budget exceeded; worker shut down", "billing_error", "budget_limit", 402)

    if not state.worker_url:
        return _err("worker not available", "service_unavailable", "worker_down", 503)
    if not _valid_worker_url(state.worker_url):
        return _err("invalid worker url", "server_error", "invalid_worker_url", 500)

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
                fresh = VastAPI(cfg.vast_api_key_plain, cfg.vast_base_url).refresh_worker_url(state.instance_id)
                if fresh:
                    state.worker_url = fresh
                    save_state(state)
                    ConfigCache.reload()
                    upstream = await _send_once(f"{fresh.rstrip('/')}/v1{suffix}")
                else:
                    raise
            except Exception:
                return _err("upstream unavailable", "service_unavailable", "upstream_connect_error", 502)
        else:
            return _err("upstream unavailable", "service_unavailable", "upstream_connect_error", 502)

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
        usage_accum = {"in": 0, "out": 0}

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
                                    usage_accum["in"] += in_tok
                                    usage_accum["out"] += out_tok
                                except Exception:
                                    pass
                        yield (line + "\n").encode("utf-8")
            finally:
                await upstream.aclose()
                if usage_accum["in"] or usage_accum["out"]:
                    track_usage(usage_accum["in"], usage_accum["out"])

        latency = (time.perf_counter() - start) * 1000
        _log_request(request.method, request.url.path, upstream.status_code, latency)
        return StreamingResponse(iter_sse(), status_code=upstream.status_code, headers=passthrough_headers)

    raw = await upstream.aread()
    await upstream.aclose()
    if upstream.status_code in {502, 503}:
        ConfigCache.reload()

    try:
        payload = json.loads(raw.decode("utf-8"))
        in_tok, out_tok = _extract_usage(payload)
        if in_tok or out_tok:
            track_usage(in_tok, out_tok)
    except Exception:
        pass

    latency = (time.perf_counter() - start) * 1000
    _log_request(request.method, request.url.path, upstream.status_code, latency)
    return Response(content=raw, status_code=upstream.status_code, media_type=content_type, headers=passthrough_headers)


@asynccontextmanager
async def _lifespan(app: Starlette):
    ConfigCache.reload()
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=30.0, pool=120.0))
    idle_task = asyncio.create_task(_idle_monitor())
    try:
        yield
    finally:
        idle_task.cancel()
        await app.state.client.aclose()


app = Starlette(
    routes=[
        Route("/healthz", health, methods=["GET"]),
        Route("/v1/models", models, methods=["GET"]),
        Route("/v1/{path:path}", forward, methods=["GET", "POST", "PUT", "PATCH", "DELETE"]),
    ],
    lifespan=_lifespan,
)

cfg_for_cors = load_config()
app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg_for_cors.cors_origins or ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def run_proxy(host: str, port: int, watchdog: bool = False, log_file: str | None = None) -> None:
    _ = watchdog
    _ = log_file
    try:
        signal.signal(signal.SIGHUP, lambda *_: ConfigCache.reload())
    except Exception:
        pass
    uvicorn.run(app, host=host, port=port, log_level="warning")
