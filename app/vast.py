from __future__ import annotations

import re
import shlex
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import httpx

from app.config import GPU_PRESETS, QUALITY_PROFILES
from app.defaults import (
    DEFAULT_LLAMA_CPP_IMAGE,
    DEFAULT_LLAMA_CPP_PORT,
    DEFAULT_MIN_CUDA_MAX_GOOD,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    DEFAULT_VAST_BASE_URL,
)
from app.models import ModelSpec


class VastAPIError(RuntimeError):
    pass


class VastAuthError(VastAPIError):
    pass


class VastOffer(TypedDict, total=False):
    id: int
    ask_id: int
    ask_contract_id: int
    gpu_name: str
    gpu_ram: float
    dph: float
    dph_total: float
    reliability: float
    inet_down: float
    inet_up: float
    interruptible: bool


class VastInstance(TypedDict, total=False):
    id: int
    actual_status: str
    status: str
    public_ipaddr: str
    ssh_host: str
    ports: Any
    dph_total: float
    total_cost: float


class VastUserInfo(TypedDict, total=False):
    username: str
    email: str
    balance: float


@dataclass(slots=True)
class BillingInfo:
    estimated_cost: float
    billed_cost: float | None


WORKER_READINESS_PATHS: tuple[str, ...] = ("/health", "/v1/models")
WORKER_READINESS_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HF_XET_CONCURRENT_RANGE_GETS_DEFAULT = 64
HF_DOWNLOAD_MAX_WORKERS = 16
HF_DOWNLOAD_ATTEMPTS = 4
DOWNLOAD_ATTEMPTS = 6
INSTALL_ATTEMPTS = 3
INSTALL_RETRY_SECONDS = 3
DOWNLOAD_RETRY_SECONDS = 5
ARIA2_SPLIT = 8
ARIA2_MAX_CONNECTIONS = 8
ARIA2_MIN_SPLIT_SIZE = "16M"
ARIA2_MAX_TRIES = 3
DOWNLOAD_CONNECT_TIMEOUT_SECONDS = 30
DOWNLOAD_SPEED_TIME_SECONDS = 60
DOWNLOAD_SPEED_LIMIT_BPS = 1_048_576
BILLED_COST_KEYS: tuple[str, ...] = (
    "total_cost",
    "total_charged",
    "charged_amount",
    "amount_charged",
    "billed_cost",
    "cost",
)
BILLED_COST_NESTED_KEYS: tuple[str, ...] = (
    "billing",
    "charges",
    "summary",
    "instance",
    "instances",
    "contract",
    "invoice",
    "data",
)
BALANCE_KEYS: tuple[str, ...] = ("balance", "credit", "available_credit", "available_balance")
BALANCE_NESTED_KEYS: tuple[str, ...] = ("user", "account", "wallet", "data", "result")


def _sh_quote(value: str) -> str:
    return shlex.quote(value)


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if not isinstance(value, str):
        return None
    cleaned = value.strip().replace("$", "").replace(",", "")
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_numeric_by_keys(
    payload: Any,
    keys: tuple[str, ...],
    container_keys: tuple[str, ...],
    depth: int = 0,
) -> float | None:
    if depth > 4:
        return None
    if isinstance(payload, dict):
        for key in keys:
            if key in payload:
                numeric = _coerce_float(payload.get(key))
                if numeric is not None:
                    return numeric
        for key in container_keys:
            if key in payload:
                nested = _extract_numeric_by_keys(payload.get(key), keys, container_keys, depth=depth + 1)
                if nested is not None:
                    return nested
        return None
    if isinstance(payload, list):
        for item in payload:
            nested = _extract_numeric_by_keys(item, keys, container_keys, depth=depth + 1)
            if nested is not None:
                return nested
    return None


def _last_non_empty_line(value: str) -> str:
    for line in reversed(value.splitlines()):
        cleaned = " ".join(line.strip().split())
        if cleaned:
            return cleaned
    return ""


def _compact_status_detail(status_msg: str) -> str:
    detail = _last_non_empty_line(status_msg)
    if not detail:
        return "waiting for worker"
    if detail.lower() == "download complete":
        return "downloads complete; waiting for server"
    return detail[:120]


def _readiness_phase(actual: str, worker_url: str | None) -> str:
    if actual in {"running", "loaded", "online"}:
        return "waiting for llama.cpp health" if worker_url else "waiting for port mapping"
    if actual in {"loading", "starting", "created", "provisioning"}:
        return "waiting for Vast provisioning"
    if actual:
        return f"waiting for {actual}"
    return "waiting for worker"


def _looks_like_auth_error(resp: httpx.Response) -> bool:
    if resp.status_code in {401, 403}:
        return True
    if resp.status_code != 404:
        return False
    try:
        payload = resp.json()
    except Exception:
        payload = None
    if isinstance(payload, dict):
        if str(payload.get("error", "")).lower() == "auth_error":
            return True
        msg = str(payload.get("msg", "")).lower()
        if "invalid user key" in msg:
            return True
    return "invalid user key" in resp.text.lower()


def _extract_port_value(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        if value.isdigit():
            return int(value)
        m = re.search(r"\b(\d{2,5})\b", value)
        if m:
            return int(m.group(1))
        return None
    if isinstance(value, list):
        for item in value:
            port = _extract_port_value(item)
            if port:
                return port
        return None
    if isinstance(value, dict):
        for key in (
            "HostPort",
            "host_port",
            "public_port",
            "external_port",
            "mapped_port",
            "published",
            "host",
        ):
            port = _extract_port_value(value.get(key))
            if port:
                return port
        for key in ("port", "container_port", "internal_port", "private_port"):
            port = _extract_port_value(value.get(key))
            if port:
                return port
        for nested in value.values():
            port = _extract_port_value(nested)
            if port:
                return port
    return None


def _port_matches(key: Any, service_port: int) -> bool:
    m = re.search(r"(\d+)", str(key))
    return bool(m and int(m.group(1)) == service_port)


def _parse_worker_port(ports: Any, service_port: int = DEFAULT_LLAMA_CPP_PORT) -> int | None:
    if isinstance(ports, dict):
        for key, value in ports.items():
            if _port_matches(key, service_port):
                port = _extract_port_value(value)
                if port:
                    return port
    elif isinstance(ports, list):
        for item in ports:
            if not isinstance(item, dict):
                continue
            for key in ("container_port", "internal_port", "private_port", "port", "target_port"):
                raw = item.get(key)
                if raw is None or not str(raw).isdigit() or int(str(raw)) != service_port:
                    continue
                port = _extract_port_value(item)
                if port:
                    return port
    return None


def _extract_public_host(info: dict[str, Any] | VastInstance) -> str | None:
    for key in ("public_ipaddr", "public_ip", "ssh_host", "host", "hostname"):
        value = info.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _unwrap_instances_payload(payload: Any) -> dict[str, Any] | None:
    if isinstance(payload, list):
        return cast(dict[str, Any] | None, payload[0] if payload else None)
    if not isinstance(payload, dict):
        return None
    inst = payload.get("instances")
    if isinstance(inst, dict):
        return cast(dict[str, Any], inst)
    if isinstance(inst, list):
        return cast(dict[str, Any] | None, inst[0] if inst else None)
    return cast(dict[str, Any], payload)


def probe_worker_ready_sync(
    worker_url: str,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> bool:
    probe_timeout = timeout or WORKER_READINESS_TIMEOUT
    request_headers = headers or {}
    for path in WORKER_READINESS_PATHS:
        try:
            response = httpx.get(
                f"{worker_url}{path}",
                headers=request_headers,
                timeout=probe_timeout,
            )
            if response.status_code == 200:
                return True
        except Exception:
            continue
    return False


async def probe_worker_ready_async(
    client: httpx.AsyncClient,
    worker_url: str,
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
) -> bool:
    probe_timeout = timeout or WORKER_READINESS_TIMEOUT
    request_headers = headers or {}
    for path in WORKER_READINESS_PATHS:
        try:
            response = await client.get(
                f"{worker_url}{path}",
                headers=request_headers,
                timeout=probe_timeout,
            )
            if response.status_code == 200:
                return True
        except Exception:
            continue
    return False


class VastAPI:
    def __init__(self, api_key: str, base_url: str = DEFAULT_VAST_BASE_URL) -> None:
        self.api_key = api_key
        self.client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(20.0, connect=10.0),
            follow_redirects=True,
        )

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        delay = 1
        last_err: Exception | None = None
        for _ in range(3):
            try:
                resp = self.client.request(method, path, headers=_headers(self.api_key), **kwargs)
                if _looks_like_auth_error(resp):
                    raise VastAuthError("Vast API key is invalid or unauthorized")
                resp.raise_for_status()
                return resp
            except VastAuthError:
                raise
            except Exception as exc:
                last_err = exc
                time.sleep(delay)
                delay *= 2
        raise VastAPIError(f"Vast API request failed: {method} {path}: {last_err}")

    def validate_api_key(self) -> VastUserInfo:
        data = self._request("GET", "/users/current").json()
        if not isinstance(data, dict):
            raise VastAPIError("Unexpected response from /users/current")
        return cast(VastUserInfo, data)

    def get_account_balance(self) -> float | None:
        data = self._request("GET", "/users/current").json()
        return _extract_numeric_by_keys(data, BALANCE_KEYS, BALANCE_NESTED_KEYS)

    def _offer_query(
        self,
        gpu_preset: str,
        instance_type: str = "any",
        limit: int = 50,
        relaxed: bool = False,
        min_disk_gb: int = 1,
        min_cuda_max_good: float | None = None,
    ) -> dict[str, Any]:
        preset = GPU_PRESETS[gpu_preset]
        min_disk_gb = max(1, int(min_disk_gb))
        query: dict[str, Any] = {
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "gpu_name": {"eq": preset.vast_gpu_names[0]},
            "gpu_ram": {"gte": preset.min_vram_gb * 1000},
            "num_gpus": {"eq": preset.num_gpus},
            "direct_port_count": {"gte": 1},
            "disk_space": {"gte": min_disk_gb},
            "allocated_storage": float(min_disk_gb),
            "order": [["dph_total", "asc"]],
            "limit": limit,
            "type": instance_type,
        }
        if len(preset.vast_gpu_names) > 1:
            query["gpu_name"] = {"in": list(preset.vast_gpu_names)}
        if min_cuda_max_good is not None:
            query["cuda_max_good"] = {"gte": min_cuda_max_good}
        if not relaxed:
            query["verified"] = {"eq": True}
            query["external"] = {"eq": False}
            query["reliability"] = {"gte": 0.95}
            query["inet_down"] = {"gte": 200}
            query["inet_up"] = {"gte": 200}
        if query["type"] == "spot":
            query["type"] = "bid"
        return query

    def search_offers(
        self,
        gpu_preset: str,
        instance_type: str = "any",
        limit: int = 50,
        relaxed: bool = False,
        min_disk_gb: int = 1,
        min_cuda_max_good: float | None = None,
    ) -> list[VastOffer]:
        query = self._offer_query(
            gpu_preset,
            instance_type=instance_type,
            limit=limit,
            relaxed=relaxed,
            min_disk_gb=min_disk_gb,
            min_cuda_max_good=min_cuda_max_good,
        )
        rows = self._request("POST", "/bundles/", json=query).json()
        if not isinstance(rows, dict):
            return []
        offers = rows.get("offers", [])
        if not isinstance(offers, list):
            return []
        return cast(list[VastOffer], offers)

    def estimate_disk_gb(self, model_spec: ModelSpec) -> int:
        size_gb = 20
        try:
            from app.sizing import fetch_model_file_size_gb

            size_gb = int(fetch_model_file_size_gb(model_spec)) + 1
        except Exception:
            pass
        name = model_spec.filename.lower()
        if "q8" in name:
            size_gb = max(size_gb, 20)
        elif "q6" in name:
            size_gb = max(size_gb, 16)
        elif "q5" in name:
            size_gb = max(size_gb, 12)
        else:
            size_gb = max(size_gb, 8)
        return max(40, size_gb + 20)

    def _recommended_free_disk_gb(self, model_spec: ModelSpec) -> int:
        required_free_gb = 12
        try:
            from app.sizing import fetch_model_file_size_gb

            required_free_gb = max(8, int(fetch_model_file_size_gb(model_spec)) + 4)
        except Exception:
            pass
        return required_free_gb

    def _build_launch_commands(
        self,
        model_spec: ModelSpec,
        ctx: int,
        port: int,
        cached_path: str,
        api_key_flag: str,
        enable_jinja: bool = True,
    ) -> tuple[str, str]:
        jinja_flag = " --jinja" if enable_jinja else ""
        launch_local = (
            f"exec /app/llama-server --host 0.0.0.0 --port {port}"
            f" -m {cached_path}"
            f" -c {ctx} -np 1 -cb --flash-attn on{jinja_flag} -ngl -1{api_key_flag}\n"
        )
        launch_remote = (
            f"exec /app/llama-server --host 0.0.0.0 --port {port}"
            f" --hf-repo {_sh_quote(model_spec.hf_repo)} --hf-file {_sh_quote(model_spec.filename)}"
            f" -c {ctx} -np 1 -cb --flash-attn on{jinja_flag} -ngl -1{api_key_flag}\n"
        )
        return launch_local, launch_remote

    def _build_onstart_bootstrap(
        self,
        model_spec: ModelSpec,
        model_url: str,
        required_free_gb: int,
        required_free_kb: int,
        cache_key: str,
        cached_path: str,
        tmp_path: str,
    ) -> str:
        return (
            "#!/bin/bash\n"
            "set -euo pipefail\n"
            f"MODEL_URL={_sh_quote(model_url)}\n"
            f"HF_REPO={_sh_quote(model_spec.hf_repo)}\n"
            f"HF_FILE={_sh_quote(model_spec.filename)}\n"
            f"MIN_FREE_GB={required_free_gb}\n"
            f"MIN_FREE_KB={required_free_kb}\n"
            "pick_cache_parent() {\n"
            "  best_dir=''\n"
            "  best_free=0\n"
            "  for candidate in /workspace /data /mnt /var/lib /tmp /root /; do\n"
            '    [ -d "$candidate" ] || continue\n'
            '    [ -w "$candidate" ] || continue\n'
            "    free_kb=$(df -Pk \"$candidate\" 2>/dev/null | awk 'NR==2 {print $4}')\n"
            '    case "$free_kb" in\n'
            "      ''|*[!0-9]*) continue ;;\n"
            "    esac\n"
            '    if [ "$free_kb" -gt "$best_free" ]; then\n'
            '      best_dir="$candidate"\n'
            '      best_free="$free_kb"\n'
            "    fi\n"
            "  done\n"
            '  if [ -z "$best_dir" ]; then\n'
            "    return 1\n"
            "  fi\n"
            '  CACHE_PARENT="$best_dir"\n'
            '  CACHE_PARENT_FREE_KB="$best_free"\n'
            "  return 0\n"
            "}\n"
            "df -h || true\n"
            "if pick_cache_parent; then\n"
            '  CACHE_ROOT="$CACHE_PARENT/.cache/llama.cpp"\n'
            "else\n"
            "  CACHE_ROOT=/root/.cache/llama.cpp\n"
            "  CACHE_PARENT=/root\n"
            "  CACHE_PARENT_FREE_KB=$(df -Pk /root 2>/dev/null | awk 'NR==2 {print $4}')\n"
            "fi\n"
            'echo "using cache root: $CACHE_ROOT '
            '(parent=$CACHE_PARENT free_kb=${CACHE_PARENT_FREE_KB:-unknown} required_kb=$MIN_FREE_KB)"\n'
            f"CACHE_KEY={_sh_quote(cache_key)}\n"
            f"CACHED_PATH={cached_path}\n"
            f"TMP_PATH={tmp_path}\n"
            'mkdir -p "$CACHE_ROOT"\n'
            'df -h "$CACHE_ROOT" || true\n'
        )

    def _build_onstart_download_section(self, launch_local: str, launch_remote: str) -> str:
        return (
            "ensure_hf_cli() {\n"
            "  if command -v hf >/dev/null 2>&1; then\n"
            "    return 0\n"
            "  fi\n"
            "  if ! command -v python3 >/dev/null 2>&1; then\n"
            "    return 1\n"
            "  fi\n"
            '  echo "installing huggingface_hub (hf CLI + hf_xet)"\n'
            f"  for attempt in $(seq 1 {INSTALL_ATTEMPTS}); do\n"
            '    if python3 -m pip install --no-cache-dir -U "huggingface_hub[hf_xet]"; then\n'
            "      break\n"
            "    fi\n"
            f"    sleep {INSTALL_RETRY_SECONDS}\n"
            "  done\n"
            '  export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"\n'
            "  command -v hf >/dev/null 2>&1\n"
            "}\n"
            "download_with_hf_cli() {\n"
            "  ensure_hf_cli || return 1\n"
            '  export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"\n'
            '  export HF_HOME="$CACHE_ROOT/hf_home"\n'
            '  export HF_HUB_CACHE="$CACHE_ROOT/hf_hub"\n'
            '  export HF_XET_CACHE="$CACHE_ROOT/hf_xet"\n'
            "  export HF_HUB_DISABLE_TELEMETRY=1\n"
            "  export HF_XET_HIGH_PERFORMANCE=1\n"
            '  export HF_XET_NUM_CONCURRENT_RANGE_GETS="${HF_XET_NUM_CONCURRENT_RANGE_GETS:-'
            f"{HF_XET_CONCURRENT_RANGE_GETS_DEFAULT}}}"
            '"\n'
            '  HF_PULL_DIR="$CACHE_ROOT/.hf-download"\n'
            '  mkdir -p "$HF_HOME" "$HF_HUB_CACHE" "$HF_XET_CACHE"\n'
            f"  for attempt in $(seq 1 {HF_DOWNLOAD_ATTEMPTS}); do\n"
            '    rm -rf "$HF_PULL_DIR"\n'
            '    mkdir -p "$HF_PULL_DIR"\n'
            f'    echo "download attempt ${{attempt}}/{HF_DOWNLOAD_ATTEMPTS} via hf download"\n'
            '    if hf download "$HF_REPO" "$HF_FILE" --repo-type model --revision main '
            f'--local-dir "$HF_PULL_DIR" --force-download --max-workers {HF_DOWNLOAD_MAX_WORKERS}; then\n'
            '      hf_path="$HF_PULL_DIR/$HF_FILE"\n'
            '      if [ -f "$hf_path" ]; then\n'
            '        mv "$hf_path" "$CACHED_PATH"\n'
            '        rm -rf "$HF_PULL_DIR"\n'
            "        return 0\n"
            "      fi\n"
            '      hf_path="$(find "$HF_PULL_DIR" -type f -name "$(basename "$HF_FILE")" '
            '2>/dev/null | head -n 1 || true)"\n'
            '      if [ -n "$hf_path" ] && [ -f "$hf_path" ]; then\n'
            '        mv "$hf_path" "$CACHED_PATH"\n'
            '        rm -rf "$HF_PULL_DIR"\n'
            "        return 0\n"
            "      fi\n"
            "    fi\n"
            f"    sleep {DOWNLOAD_RETRY_SECONDS}\n"
            "  done\n"
            '  rm -rf "$HF_PULL_DIR"\n'
            "  return 1\n"
            "}\n"
            "ensure_parallel_downloader() {\n"
            "  if command -v aria2c >/dev/null 2>&1; then\n"
            "    return 0\n"
            "  fi\n"
            "  if ! command -v apt-get >/dev/null 2>&1; then\n"
            "    return 1\n"
            "  fi\n"
            '  echo "installing aria2c for parallel model download"\n'
            "  export DEBIAN_FRONTEND=noninteractive\n"
            f"  for attempt in $(seq 1 {INSTALL_ATTEMPTS}); do\n"
            "    if apt-get update && apt-get install --no-install-recommends -y aria2c; then\n"
            "      command -v aria2c >/dev/null 2>&1 && return 0\n"
            "    fi\n"
            f"    sleep {INSTALL_RETRY_SECONDS}\n"
            "  done\n"
            "  return 1\n"
            "}\n"
            "download_model() {\n"
            '  if [ -s "$CACHED_PATH" ]; then\n'
            "    return 0\n"
            "  fi\n"
            '  case "${CACHE_PARENT_FREE_KB:-}" in\n'
            "    ''|*[!0-9]*) : ;;\n"
            "    *)\n"
            '      if [ "$CACHE_PARENT_FREE_KB" -lt "$MIN_FREE_KB" ]; then\n'
            '        echo "insufficient free disk for model cache: need at least ${MIN_FREE_GB} GB free" >&2\n'
            "        df -h || true\n"
            "        return 1\n"
            "      fi\n"
            "      ;;\n"
            "  esac\n"
            "  if download_with_hf_cli; then\n"
            "    return 0\n"
            "  fi\n"
            "  if ! command -v aria2c >/dev/null 2>&1; then\n"
            "    ensure_parallel_downloader || true\n"
            "  fi\n"
            "  if command -v aria2c >/dev/null 2>&1; then\n"
            f"    for attempt in $(seq 1 {DOWNLOAD_ATTEMPTS}); do\n"
            f'      echo "download attempt ${{attempt}}/{DOWNLOAD_ATTEMPTS} via aria2c"\n'
            "      if aria2c --continue=true --auto-file-renaming=false --allow-overwrite=true "
            f"--split={ARIA2_SPLIT} --max-connection-per-server={ARIA2_MAX_CONNECTIONS} "
            f"--min-split-size={ARIA2_MIN_SPLIT_SIZE} --max-tries={ARIA2_MAX_TRIES} "
            f"--retry-wait={DOWNLOAD_RETRY_SECONDS} --connect-timeout={DOWNLOAD_CONNECT_TIMEOUT_SECONDS} "
            f"--timeout={DOWNLOAD_CONNECT_TIMEOUT_SECONDS} --console-log-level=warn "
            '--summary-interval=15 --dir "$CACHE_ROOT" --out "$(basename "$TMP_PATH")" "$MODEL_URL"; then\n'
            '        if [ -f "$TMP_PATH" ]; then mv "$TMP_PATH" "$CACHED_PATH"; '
            'else mv "$CACHE_ROOT/$(basename "$TMP_PATH")" "$CACHED_PATH"; fi\n'
            "        return 0\n"
            "      fi\n"
            f"      sleep {DOWNLOAD_RETRY_SECONDS}\n"
            "    done\n"
            '    df -h "$CACHE_ROOT" || true\n'
            "    return 1\n"
            "  fi\n"
            "  if command -v curl >/dev/null 2>&1; then\n"
            f"    for attempt in $(seq 1 {DOWNLOAD_ATTEMPTS}); do\n"
            f'      echo "download attempt ${{attempt}}/{DOWNLOAD_ATTEMPTS} via curl"\n'
            '      if curl --fail --location --continue-at - --output "$TMP_PATH" '
            f"--retry {DOWNLOAD_ATTEMPTS} --retry-delay {DOWNLOAD_RETRY_SECONDS} --retry-all-errors "
            f"--connect-timeout {DOWNLOAD_CONNECT_TIMEOUT_SECONDS} --speed-time {DOWNLOAD_SPEED_TIME_SECONDS} "
            f'--speed-limit {DOWNLOAD_SPEED_LIMIT_BPS} "$MODEL_URL"; then\n'
            '        mv "$TMP_PATH" "$CACHED_PATH"\n'
            "        return 0\n"
            "      fi\n"
            f"      sleep {DOWNLOAD_RETRY_SECONDS}\n"
            "    done\n"
            '    df -h "$CACHE_ROOT" || true\n'
            "    return 1\n"
            "  fi\n"
            "  if command -v wget >/dev/null 2>&1; then\n"
            f"    for attempt in $(seq 1 {DOWNLOAD_ATTEMPTS}); do\n"
            f'      echo "download attempt ${{attempt}}/{DOWNLOAD_ATTEMPTS} via wget"\n'
            f"      if wget --continue --tries={ARIA2_MAX_TRIES} --timeout={DOWNLOAD_CONNECT_TIMEOUT_SECONDS} "
            '-O "$TMP_PATH" "$MODEL_URL"; then\n'
            '        mv "$TMP_PATH" "$CACHED_PATH"\n'
            "        return 0\n"
            "      fi\n"
            f"      sleep {DOWNLOAD_RETRY_SECONDS}\n"
            "    done\n"
            '    df -h "$CACHE_ROOT" || true\n'
            "    return 1\n"
            "  fi\n"
            "  return 2\n"
            "}\n"
            "if download_model; then\n" + launch_local + "else\n"
            "  download_status=$?\n"
            '  if [ "$download_status" -eq 2 ]; then\n'
            '    echo "curl/wget unavailable; falling back to llama.cpp direct HF download"\n'
            + launch_remote
            + "  fi\n"
            '  echo "model download failed after retries" >&2\n'
            "  exit 1\n"
            "fi\n"
        )

    def _build_onstart(
        self,
        model_spec: ModelSpec,
        quality_profile: str,
        api_token: str | None = None,
        enable_jinja: bool = True,
    ) -> str:
        """Build onstart script. Binary is at /app/llama-server in the official image."""
        ctx = QUALITY_PROFILES[quality_profile].context_length
        port = DEFAULT_LLAMA_CPP_PORT
        api_key_flag = f" --api-key {api_token}" if api_token else ""
        required_free_gb = self._recommended_free_disk_gb(model_spec)
        required_free_kb = required_free_gb * 1024 * 1024
        model_url = f"https://huggingface.co/{model_spec.hf_repo}/resolve/main/{model_spec.filename}"
        cache_key = f"{model_spec.hf_repo.replace('/', '_')}_{model_spec.filename}"
        cache_root_var = "${CACHE_ROOT}"
        cached_path = f"{cache_root_var}/{cache_key}"
        tmp_path = f"{cached_path}.downloadInProgress"
        launch_local, launch_remote = self._build_launch_commands(
            model_spec=model_spec,
            ctx=ctx,
            port=port,
            cached_path=cached_path,
            api_key_flag=api_key_flag,
            enable_jinja=enable_jinja,
        )
        return self._build_onstart_bootstrap(
            model_spec=model_spec,
            model_url=model_url,
            required_free_gb=required_free_gb,
            required_free_kb=required_free_kb,
            cache_key=cache_key,
            cached_path=cached_path,
            tmp_path=tmp_path,
        ) + self._build_onstart_download_section(launch_local, launch_remote)

    def _build_entrypoint_script(
        self,
        model_spec: ModelSpec,
        quality_profile: str,
        api_token: str | None = None,
        enable_jinja: bool = True,
    ) -> str:
        script = self._build_onstart(
            model_spec,
            quality_profile,
            api_token=api_token,
            enable_jinja=enable_jinja,
        )
        lines = script.splitlines()
        if lines and lines[0].startswith("#!"):
            lines = lines[1:]
        return "\n".join(lines)

    def create_instance(
        self,
        offer_id: int,
        model_spec: ModelSpec,
        quality_profile: str,
        gpu_preset: str,
        image: str = DEFAULT_LLAMA_CPP_IMAGE,
        api_token: str | None = None,
        enable_jinja: bool = True,
    ) -> int:
        _ = GPU_PRESETS[gpu_preset]
        entrypoint_script = self._build_entrypoint_script(
            model_spec,
            quality_profile,
            api_token=api_token,
            enable_jinja=enable_jinja,
        )
        port = DEFAULT_LLAMA_CPP_PORT
        payload: dict[str, Any] = {
            "client_id": "me",
            "image": image,
            "runtype": "args",
            "onstart": "/bin/bash",
            "args": ["-lc", entrypoint_script],
            "env": {f"-p {port}:{port}": "1"},
            "disk": self.estimate_disk_gb(model_spec),
        }

        data = self._request("PUT", f"/asks/{offer_id}/", json=payload).json()
        if not isinstance(data, dict):
            raise VastAPIError(f"Unexpected create response: {data}")
        instance_id = data.get("new_contract")
        if instance_id is None:
            raise VastAPIError(f"Unable to parse instance id from Vast response: {data}")
        return int(instance_id)

    def destroy_instance(self, instance_id: int) -> None:
        self._request("DELETE", f"/instances/{instance_id}/")

    def get_instance_status(self, instance_id: int) -> VastInstance:
        data = self._request("GET", f"/instances/{instance_id}/", params={"owner": "me"}).json()
        row = _unwrap_instances_payload(data)
        if not isinstance(row, dict):
            raise VastAPIError("Unexpected instance status response")
        return cast(VastInstance, row)

    def _worker_url_from_status(self, status: dict[str, Any] | VastInstance) -> str | None:
        public_ip = _extract_public_host(status)
        if not public_ip:
            return None
        port = _parse_worker_port(status.get("ports"), DEFAULT_LLAMA_CPP_PORT)
        if not port:
            return None
        return f"http://{public_ip}:{port}"

    def refresh_worker_url(self, instance_id: int) -> str | None:
        status = self.get_instance_status(instance_id)
        return self._worker_url_from_status(status)

    def wait_for_ready(
        self,
        instance_id: int,
        timeout: int = DEFAULT_STARTUP_TIMEOUT_SECONDS,
        api_token: str | None = None,
        progress: Callable[[str], None] | None = None,
    ) -> str:
        deadline = time.time() + timeout
        health_headers: dict[str, str] = {}
        if api_token:
            health_headers["Authorization"] = f"Bearer {api_token}"
        last_actual = "starting"
        last_status_msg = ""
        last_phase = "waiting for Vast provisioning"
        while time.time() < deadline:
            status = self.get_instance_status(instance_id)
            actual = (status.get("actual_status") or status.get("status") or "").lower()
            status_raw = status.get("status_msg")
            status_msg = status_raw if isinstance(status_raw, str) else ""
            last_actual = actual or last_actual
            last_status_msg = status_msg or last_status_msg
            worker_url = self._worker_url_from_status(status)
            last_phase = _readiness_phase(actual, worker_url)
            if progress:
                summary = _compact_status_detail(status_msg)
                progress(f"{last_phase} ({actual or 'starting'}): {summary}")
            if "error" in status_msg.lower() or "failed" in status_msg.lower():
                raise VastAPIError(f"Instance failed: {status_msg}")
            if actual in {"exited", "dead", "stopped", "deleted"}:
                detail = _compact_status_detail(status_msg)
                raise VastAPIError(f"Instance stopped before becoming ready ({actual}): {detail}")
            if actual in {"running", "loaded", "online"} and worker_url:
                if probe_worker_ready_sync(worker_url, headers=health_headers):
                    return worker_url
            time.sleep(10)
        detail = f"phase={last_phase}, last status={last_actual}"
        if last_status_msg:
            detail += f", detail={_compact_status_detail(last_status_msg)}"
        raise TimeoutError(f"Instance {instance_id} did not become ready within {timeout}s ({detail})")

    def get_billing(self, instance_id: int, estimated_cost: float) -> BillingInfo:
        billed = None
        try:
            status = self.get_instance_status(instance_id)
            billed = _extract_numeric_by_keys(status, BILLED_COST_KEYS, BILLED_COST_NESTED_KEYS)
            if billed is not None:
                billed = max(0.0, billed)
        except Exception:
            pass
        return BillingInfo(estimated_cost=estimated_cost, billed_cost=billed)

    def request_instance_logs(self, instance_id: int) -> str:
        data = self._request("PUT", f"/instances/request_logs/{instance_id}/").json()
        if not isinstance(data, dict):
            raise VastAPIError("Unexpected response from request_logs")
        result_url = data.get("result_url")
        if not isinstance(result_url, str) or not result_url.strip():
            raise VastAPIError(f"Unable to parse result_url from request_logs response: {data}")
        return result_url

    def get_instance_logs(self, instance_id: int, attempts: int = 10, delay_s: float = 1.0) -> str:
        last_err: Exception | None = None
        for _ in range(max(1, attempts)):
            result_url = self.request_instance_logs(instance_id)
            try:
                resp = httpx.get(result_url, follow_redirects=True, timeout=httpx.Timeout(20.0, connect=10.0))
                if resp.status_code in {403, 404}:
                    last_err = VastAPIError(f"log object not ready yet ({resp.status_code})")
                    time.sleep(delay_s)
                    continue
                resp.raise_for_status()
                return resp.text
            except Exception as exc:
                last_err = exc
                time.sleep(delay_s)
        raise VastAPIError(f"Failed to fetch instance logs for {instance_id}: {last_err}")


def recommended_min_cuda_max_good(image: str) -> float | None:
    lowered = image.lower()
    if "ggml-org/llama.cpp" in lowered and "server-cuda" in lowered:
        return DEFAULT_MIN_CUDA_MAX_GOOD
    return None
