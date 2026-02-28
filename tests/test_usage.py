import time

from app.state import RuntimeState
from app.usage import summarize_usage


def test_usage_cost_math(monkeypatch) -> None:
    from app import usage

    now = time.time()
    monkeypatch.setattr(
        usage,
        "load_state",
        lambda: RuntimeState(
            session_start=now - 3600, price_per_hour=2.0, input_tokens=1000, output_tokens=1000, requests=1
        ),
    )
    summary = summarize_usage()
    assert 1.9 < summary.total_cost < 2.1
    assert 900 < summary.dollars_per_million_tokens < 1100


def test_usage_zero_tokens(monkeypatch) -> None:
    from app import usage

    now = time.time()
    monkeypatch.setattr(
        usage, "load_state", lambda: RuntimeState(session_start=now - 60, price_per_hour=1.0, requests=0)
    )
    summary = summarize_usage()
    assert summary.dollars_per_million_tokens == 0.0


def test_usage_duration_non_negative(monkeypatch) -> None:
    from app import usage

    now = time.time()
    monkeypatch.setattr(usage, "load_state", lambda: RuntimeState(session_start=now + 60, price_per_hour=1.0))
    summary = summarize_usage()
    assert summary.duration_seconds >= 0
