from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any, TypedDict, cast

import httpx

from app.config import GPU_PRESETS, QUALITY_PROFILES
from app.defaults import DEFAULT_LLAMA_CPP_IMAGE, DEFAULT_VAST_BASE_URL
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


def _parse_worker_port(ports: Any) -> int:
    if isinstance(ports, int):
        return ports
    if isinstance(ports, list):
        for item in ports:
            p = _parse_worker_port(item)
            if p:
                return p
        return 8000
    if isinstance(ports, dict):
        for key in ("8000/tcp", "8000", 8000):
            if key in ports:
                return _parse_worker_port(ports[key])
        for value in ports.values():
            p = _parse_worker_port(value)
            if p:
                return p
    if isinstance(ports, str):
        m = re.search(r"(\d{2,5})", ports)
        if m:
            return int(m.group(1))
    return 8000


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
        data = self._request("GET", "/users/current/").json()
        if not isinstance(data, dict):
            raise VastAPIError("Unexpected response from /users/current/")
        return cast(VastUserInfo, data)

    def search_offers(self, gpu_preset: str, instance_type: str = "any", limit: int = 50) -> list[VastOffer]:
        preset = GPU_PRESETS[gpu_preset]
        gpu_name = preset.search.replace(" ", "_")
        q = (
            "verified=true "
            f"gpu_name={gpu_name} "
            f"gpu_ram>={preset.min_vram_gb} "
            "num_gpus=1 rentable=true "
            "reliability>=0.95 inet_down>=200 inet_up>=200"
        )
        rows = self._request("GET", "/bundles/", params={"q": q, "limit": limit}).json()
        offers: list[VastOffer] = rows if isinstance(rows, list) else rows.get("offers", rows.get("results", []))
        if not isinstance(offers, list):
            return []

        if instance_type == "on-demand":
            offers = [o for o in offers if not bool(o.get("interruptible", False))]
        elif instance_type == "spot":
            offers = [o for o in offers if bool(o.get("interruptible", False))]

        return sorted(offers, key=lambda x: float(x.get("dph_total") or x.get("dph") or 1e9))

    def find_template_hash_id(self) -> str | None:
        try:
            data = self._request("GET", "/templates/", params={"q": "llama cpp"}).json()
        except Exception:
            return None
        items = data if isinstance(data, list) else data.get("results", []) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict):
                h = item.get("template_hash_id") or item.get("hash_id")
                if h:
                    return str(h)
        return None

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

    def create_instance(
        self,
        offer_id: int,
        model_spec: ModelSpec,
        quality_profile: str,
        gpu_preset: str,
        image: str = DEFAULT_LLAMA_CPP_IMAGE,
    ) -> int:
        ctx = QUALITY_PROFILES[quality_profile].context_length
        preset = GPU_PRESETS[gpu_preset]
        payload: dict[str, Any] = {
            "ask_id": offer_id,
            "num_gpus": preset.num_gpus,
            "gpu_name": preset.search,
            "env": {
                "MODEL_REPO": model_spec.hf_repo,
                "MODEL_FILE": model_spec.filename,
                "LLAMA_ARG_CTX_SIZE": str(ctx),
                "LLAMA_ARG_N_GPU_LAYERS": "-1",
            },
            "disk": self.estimate_disk_gb(model_spec),
            "ssh": False,
            "jupyter": False,
            "direct": True,
        }
        template_hash_id = self.find_template_hash_id()
        if template_hash_id:
            payload["template_hash_id"] = template_hash_id
        else:
            payload["image"] = image

        data = self._request("PUT", "/asks/", json=payload).json()
        instance_id = data.get("new_contract") or data.get("instance_id") or data.get("id")
        if not instance_id:
            raise VastAPIError(f"Unable to parse instance id from Vast response: {data}")
        return int(instance_id)

    def destroy_instance(self, instance_id: int) -> None:
        try:
            self._request("DELETE", f"/instances/{instance_id}/")
        except VastAPIError as exc:
            if "404" in str(exc):
                return
            raise

    def get_instance_status(self, instance_id: int) -> VastInstance:
        data = self._request("GET", f"/instances/{instance_id}/").json()
        if not isinstance(data, dict):
            raise VastAPIError("Unexpected instance status response")
        return cast(VastInstance, data)

    def refresh_worker_url(self, instance_id: int) -> str | None:
        status = self.get_instance_status(instance_id)
        public_ip = status.get("public_ipaddr") or status.get("ssh_host")
        if not public_ip:
            return None
        port = _parse_worker_port(status.get("ports") or {})
        return f"http://{public_ip}:{port}"

    def wait_for_ready(self, instance_id: int, timeout: int = 600) -> str:
        deadline = time.time() + timeout
        while time.time() < deadline:
            status = self.get_instance_status(instance_id)
            actual = (status.get("actual_status") or status.get("status") or "").lower()
            worker_url = self.refresh_worker_url(instance_id)
            if actual in {"running", "loaded", "online"} and worker_url:
                for path in ("/v1/models", "/health"):
                    try:
                        r = httpx.get(f"{worker_url}{path}", timeout=httpx.Timeout(10.0, connect=5.0))
                        if r.status_code < 500:
                            return worker_url
                    except Exception:
                        pass
            time.sleep(5)
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
