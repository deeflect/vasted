import json

import httpx
import pytest

from app.proxy import app


@pytest.mark.asyncio
async def test_proxy_rejects_bad_token(monkeypatch) -> None:
    from app import proxy
    from app.state import RuntimeState
    from app.user_config import UserConfig

    monkeypatch.setattr(
        proxy.ConfigCache, "get", classmethod(lambda cls: (UserConfig(bearer_token_plain="good"), RuntimeState()))
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/chat/completions", headers={"Authorization": "Bearer bad"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_proxy_no_worker(monkeypatch) -> None:
    from app import proxy
    from app.state import RuntimeState
    from app.user_config import UserConfig

    monkeypatch.setattr(
        proxy.ConfigCache, "get", classmethod(lambda cls: (UserConfig(bearer_token_plain="good"), RuntimeState()))
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer good"})
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_proxy_invalid_worker_url(monkeypatch) -> None:
    from app import proxy
    from app.state import RuntimeState
    from app.user_config import UserConfig

    monkeypatch.setattr(
        proxy.ConfigCache,
        "get",
        classmethod(lambda cls: (UserConfig(bearer_token_plain="good"), RuntimeState(worker_url="file:///bad"))),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.post("/v1/chat/completions", headers={"Authorization": "Bearer good"})
    assert r.status_code == 500


@pytest.mark.asyncio
async def test_health_endpoint() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/healthz")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_models_endpoint() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/v1/models")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_proxy_preflight_uses_configured_cors(monkeypatch) -> None:
    from app import proxy
    from app.state import RuntimeState
    from app.user_config import UserConfig

    monkeypatch.setattr(
        proxy.ConfigCache,
        "get",
        classmethod(
            lambda cls: (
                UserConfig(cors_origins=["https://client.example"], bearer_token_plain="good"),
                RuntimeState(),
            )
        ),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.options(
            "/v1/models",
            headers={
                "Origin": "https://client.example",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
    assert r.status_code == 204
    assert r.headers["access-control-allow-origin"] == "https://client.example"
    assert "authorization" in r.headers["access-control-allow-headers"]


def test_normalize_chat_payload_merges_system_and_developer_messages() -> None:
    from app.proxy import _normalize_chat_request_payload

    payload = {
        "model": "qwen3-30b",
        "messages": [
            {"role": "system", "content": "global rules"},
            {"role": "developer", "content": "tool protocol"},
            {"role": "user", "content": [{"type": "input_text", "text": "hello"}]},
        ],
    }
    out = _normalize_chat_request_payload(json.dumps(payload).encode("utf-8"))
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["messages"][0] == {"role": "system", "content": "global rules\n\ntool protocol"}
    assert decoded["messages"][1] == {"role": "user", "content": "hello"}


def test_normalize_chat_payload_maps_legacy_function_role() -> None:
    from app.proxy import _normalize_chat_request_payload

    payload = {
        "messages": [
            {"role": "user", "content": "call tool"},
            {"role": "function", "name": "calc", "content": {"ok": True}},
        ]
    }
    out = _normalize_chat_request_payload(json.dumps(payload).encode("utf-8"))
    decoded = json.loads(out.decode("utf-8"))
    assert decoded["messages"][1]["role"] == "tool"
    assert decoded["messages"][1]["content"] == "{\"ok\":true}"
