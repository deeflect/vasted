from __future__ import annotations

import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import DEFAULT_STATE_PATH
from app.state import RuntimeState, load_state, save_state


@contextmanager
def _locked_state_file():
    lock_path = str(DEFAULT_STATE_PATH) + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except Exception:
            try:
                import msvcrt  # type: ignore

                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
            except Exception:
                pass
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
        except Exception:
            pass
        os.close(fd)


@dataclass(slots=True)
class UsageSummary:
    requests: int
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    total_cost: float
    dollars_per_million_tokens: float


def reset_usage_for_new_session(price_per_hour: float) -> RuntimeState:
    with _locked_state_file():
        state = load_state()
        now = time.time()
        state.requests = 0
        state.input_tokens = 0
        state.output_tokens = 0
        state.session_start = now
        state.started_at = now
        state.price_per_hour = price_per_hour
        save_state(state)
        return state


def track_usage(input_tokens: int, output_tokens: int) -> RuntimeState:
    with _locked_state_file():
        state = load_state()
        state.requests += 1
        state.input_tokens += max(0, input_tokens)
        state.output_tokens += max(0, output_tokens)
        state.last_request_at = time.time()
        save_state(state)
        return state


def summarize_usage() -> UsageSummary:
    state = load_state()
    start = state.session_start or state.started_at or time.time()
    dur = max(0.0, time.time() - start)
    total_cost = (dur / 3600.0) * state.price_per_hour
    total_tokens = state.input_tokens + state.output_tokens
    per_million = (total_cost / total_tokens * 1_000_000) if total_tokens else 0.0
    return UsageSummary(state.requests, state.input_tokens, state.output_tokens, dur, total_cost, per_million)
