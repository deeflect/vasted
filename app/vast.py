from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import httpx

from app.config import GPU_PRESETS, QUALITY_PROFILES
from app.defaults import (
    DEFAULT_LLAMA_CPP_IMAGE,
    DEFAULT_LLAMA_CPP_PORT,
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


def _headers(api_key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}


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


class VastAPI:
    def __init__(self, api_key: str, base_url: str = DEFAULT_VAST_BASE_URL) -> None:
        self.api_key = api_key
        self.client = httpx.Client(base_url=base_url, timeout=httpx.Timeout(20.0, connect=10.0))

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        delay = 1
        last_err: Exception | None = None
        for _ in range(3):
            try:
                resp = self.client.request(method, path, headers=_headers(self.api_key), **kwargs)
                if resp.status_code in {401, 403}:
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

    def search_offers(self, gpu_preset: str, instance_type: str = "any", limit: int = 50) -> list[VastOffer]:
        preset = GPU_PRESETS[gpu_preset]
        query: dict[str, Any] = {
            "verified": {"eq": True},
            "external": {"eq": False},
            "rentable": {"eq": True},
            "rented": {"eq": False},
            "gpu_name": {"eq": preset.search},
            "gpu_ram": {"gte": preset.min_vram_gb},
            "num_gpus": {"eq": preset.num_gpus},
            "reliability": {"gte": 0.95},
            "inet_down": {"gte": 200},
            "inet_up": {"gte": 200},
            "allocated_storage": 1.0,
            "order": [["dph_total", "asc"]],
            "limit": limit,
            "type": instance_type,
        }
        if query["type"] == "spot":
            query["type"] = "bid"
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
            r = httpx.get(f"https://huggingface.co/api/models/{model_spec.hf_repo}", timeout=20.0)
            if r.status_code == 200:
                j = r.json()
                siblings = j.get("siblings", []) if isinstance(j, dict) else []
                for s in siblings:
                    if s.get("rfilename") == model_spec.filename:
                        size_b = int(s.get("size") or 0)
                        if size_b > 0:
                            size_gb = int(size_b / (1024**3)) + 1
                        break
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

    def _build_onstart(self, model_spec: ModelSpec, quality_profile: str, api_token: str | None = None) -> str:
        """Build onstart script. Binary is at /app/llama-server in the official image."""
        ctx = QUALITY_PROFILES[quality_profile].context_length
        port = DEFAULT_LLAMA_CPP_PORT
        api_key_flag = f" --api-key {api_token}" if api_token else ""
        return (
            "#!/bin/bash\nset -e\n"
            f"exec /app/llama-server --host 0.0.0.0 --port {port}"
            f" --hf-repo {model_spec.hf_repo} --hf-file {model_spec.filename}"
            f" -c {ctx} -np 1 -cb --flash-attn on -ngl -1{api_key_flag}\n"
        )

    def create_instance(
        self,
        offer_id: int,
        model_spec: ModelSpec,
        quality_profile: str,
        gpu_preset: str,
        image: str = DEFAULT_LLAMA_CPP_IMAGE,
        api_token: str | None = None,
    ) -> int:
        _ = GPU_PRESETS[gpu_preset]
        onstart = self._build_onstart(model_spec, quality_profile, api_token=api_token)
        port = DEFAULT_LLAMA_CPP_PORT
        payload: dict[str, Any] = {
            "client_id": "me",
            "image": image,
            "runtype": "ssh_direc ssh_proxy",
            "onstart": onstart,
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

    def refresh_worker_url(self, instance_id: int) -> str | None:
        status = self.get_instance_status(instance_id)
        public_ip = _extract_public_host(status)
        if not public_ip:
            return None
        port = _parse_worker_port(status.get("ports"), DEFAULT_LLAMA_CPP_PORT)
        if not port:
            return None
        return f"http://{public_ip}:{port}"

    def wait_for_ready(self, instance_id: int, timeout: int = 600, api_token: str | None = None) -> str:
        deadline = time.time() + timeout
        health_headers: dict[str, str] = {}
        if api_token:
            health_headers["Authorization"] = f"Bearer {api_token}"
        while time.time() < deadline:
            status = self.get_instance_status(instance_id)
            actual = (status.get("actual_status") or status.get("status") or "").lower()
            status_msg = str(status.get("status_msg", ""))
            if "error" in status_msg.lower() or "failed" in status_msg.lower():
                raise VastAPIError(f"Instance failed: {status_msg}")
            worker_url = self.refresh_worker_url(instance_id)
            if actual in {"running", "loaded", "online"} and worker_url:
                for path in ("/health", "/v1/models"):
                    try:
                        r = httpx.get(
                            f"{worker_url}{path}",
                            headers=health_headers,
                            timeout=httpx.Timeout(10.0, connect=5.0),
                        )
                        if r.status_code == 200:
                            return worker_url
                    except Exception:
                        pass
            time.sleep(10)
        raise TimeoutError(f"Instance {instance_id} did not become ready within {timeout}s")

    def get_billing(self, instance_id: int, estimated_cost: float) -> BillingInfo:
        billed = None
        try:
            status = self.get_instance_status(instance_id)
            billed_raw = status.get("total_cost") or status.get("cost")
            if billed_raw is not None:
                billed = float(str(billed_raw))
        except Exception:
            pass
        return BillingInfo(estimated_cost=estimated_cost, billed_cost=billed)
