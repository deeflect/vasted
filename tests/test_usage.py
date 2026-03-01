from __future__ import annotations

import builtins

import pytest

import app.usage as usage_module
from app.state import RuntimeState


def test_track_usage_counts_timing_only_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    state = RuntimeState()
    saved: list[RuntimeState] = []

    monkeypatch.setattr(usage_module, "load_state", lambda: state)
    monkeypatch.setattr(usage_module, "save_state", lambda new_state: saved.append(new_state))
    monkeypatch.setattr(usage_module.time, "time", lambda: 123.0)

    updated = usage_module.track_usage(0, 0, prompt_ms=5.0, predicted_ms=7.5)

    assert updated.requests == 1
    assert updated.prompt_ms_total == 5.0
    assert updated.predicted_ms_total == 7.5
    assert updated.last_request_at == 123.0
    assert saved and saved[-1].requests == 1


def test_summarize_usage_splits_active_cost_by_timings(monkeypatch: pytest.MonkeyPatch) -> None:
    state = RuntimeState(
        requests=2,
        input_tokens=1000,
        output_tokens=100,
        prompt_ms_total=9000.0,
        predicted_ms_total=1000.0,
        session_start=1.0,
        started_at=1.0,
        price_per_hour=3.6,
    )
    monkeypatch.setattr(usage_module, "load_state", lambda: state)
    monkeypatch.setattr(usage_module.time, "time", lambda: 3601.0)

    summary = usage_module.summarize_usage()

    assert summary.total_cost == pytest.approx(3.6)
    assert summary.input_cost == pytest.approx(0.009)
    assert summary.output_cost == pytest.approx(0.001)
    assert summary.overhead_cost == pytest.approx(3.59)
    assert summary.input_dollars_per_million_tokens == pytest.approx(9.0)
    assert summary.output_dollars_per_million_tokens == pytest.approx(10.0)


def test_state_lock_warning_is_emitted_once_when_locking_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = builtins.__import__
    warnings: list[str] = []
    state = RuntimeState()

    def fake_import(name, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003
        if name in {"fcntl", "msvcrt"}:
            raise ImportError("missing lock module")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(usage_module, "load_state", lambda: state)
    monkeypatch.setattr(usage_module, "save_state", lambda _new_state: None)
    monkeypatch.setattr(usage_module.logger, "warning", lambda message: warnings.append(str(message)))
    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(usage_module, "_LOCK_WARNING_EMITTED", False)

    usage_module.track_usage(1, 1)
    usage_module.track_usage(1, 1)

    assert len(warnings) == 1
