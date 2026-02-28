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
