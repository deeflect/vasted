from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from app.config import DEFAULT_STATE_PATH
from app.state import RuntimeState, load_state, save_state

logger = logging.getLogger(__name__)
_LOCK_WARNING_EMITTED = False


@contextmanager
def _locked_state_file() -> Iterator[None]:
    global _LOCK_WARNING_EMITTED

    lock_path = str(DEFAULT_STATE_PATH) + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    lock_mode: str | None = None
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
            lock_mode = "fcntl"
        except Exception:
            try:
                import msvcrt  # type: ignore

                lock_fn = getattr(msvcrt, "locking", None)
                lock_flag = getattr(msvcrt, "LK_LOCK", None)
                if callable(lock_fn) and isinstance(lock_flag, int):
                    lock_fn(fd, lock_flag, 1)
                    lock_mode = "msvcrt"
                else:
                    raise AttributeError("msvcrt.locking unavailable")
            except Exception:
                if not _LOCK_WARNING_EMITTED:
                    logger.warning("State file lock unavailable; proceeding without inter-process lock safety")
                    _LOCK_WARNING_EMITTED = True
        yield
    finally:
        if lock_mode == "fcntl":
            try:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_UN)
            except Exception:
                pass
        elif lock_mode == "msvcrt":
            try:
                import msvcrt  # type: ignore

                lock_fn = getattr(msvcrt, "locking", None)
                unlock_flag = getattr(msvcrt, "LK_UNLCK", None)
                if callable(lock_fn) and isinstance(unlock_flag, int):
                    lock_fn(fd, unlock_flag, 1)
            except Exception:
                pass
        os.close(fd)


@dataclass(slots=True)
class UsageSummary:
    requests: int
    input_tokens: int
    output_tokens: int
    prompt_ms_total: float
    predicted_ms_total: float
    duration_seconds: float
    total_cost: float
    input_cost: float
    output_cost: float
    overhead_cost: float
    blended_dollars_per_million_tokens: float
    input_dollars_per_million_tokens: float
    output_dollars_per_million_tokens: float


def reset_usage_for_new_session(price_per_hour: float) -> RuntimeState:
    with _locked_state_file():
        state = load_state()
        now = time.time()
        state.requests = 0
        state.input_tokens = 0
        state.output_tokens = 0
        state.prompt_ms_total = 0.0
        state.predicted_ms_total = 0.0
        state.session_start = now
        state.started_at = now
        state.price_per_hour = price_per_hour
        save_state(state)
        return state


def track_usage(
    input_tokens: int,
    output_tokens: int,
    prompt_ms: float = 0.0,
    predicted_ms: float = 0.0,
) -> RuntimeState:
    with _locked_state_file():
        state = load_state()
        state.input_tokens += max(0, input_tokens)
        state.output_tokens += max(0, output_tokens)
        state.prompt_ms_total += max(0.0, prompt_ms)
        state.predicted_ms_total += max(0.0, predicted_ms)
        if input_tokens or output_tokens or prompt_ms > 0 or predicted_ms > 0:
            state.requests += 1
        state.last_request_at = time.time()
        save_state(state)
        return state


def summarize_usage() -> UsageSummary:
    state = load_state()
    start = state.session_start or state.started_at or time.time()
    dur = max(0.0, time.time() - start)
    total_cost = (dur / 3600.0) * state.price_per_hour
    active_ms = max(0.0, state.prompt_ms_total + state.predicted_ms_total)
    active_cost = 0.0
    if state.price_per_hour > 0 and active_ms > 0:
        active_cost = (active_ms / 3_600_000.0) * state.price_per_hour
    attributable_cost = min(total_cost, active_cost)
    input_cost = 0.0
    output_cost = 0.0
    if active_ms > 0 and attributable_cost > 0:
        input_cost = attributable_cost * (state.prompt_ms_total / active_ms)
        output_cost = attributable_cost * (state.predicted_ms_total / active_ms)
    overhead_cost = max(0.0, total_cost - attributable_cost)
    total_tokens = state.input_tokens + state.output_tokens
    blended_per_million = (total_cost / total_tokens * 1_000_000) if total_tokens else 0.0
    input_per_million = (input_cost / state.input_tokens * 1_000_000) if state.input_tokens else 0.0
    output_per_million = (output_cost / state.output_tokens * 1_000_000) if state.output_tokens else 0.0
    return UsageSummary(
        requests=state.requests,
        input_tokens=state.input_tokens,
        output_tokens=state.output_tokens,
        prompt_ms_total=state.prompt_ms_total,
        predicted_ms_total=state.predicted_ms_total,
        duration_seconds=dur,
        total_cost=total_cost,
        input_cost=input_cost,
        output_cost=output_cost,
        overhead_cost=overhead_cost,
        blended_dollars_per_million_tokens=blended_per_million,
        input_dollars_per_million_tokens=input_per_million,
        output_dollars_per_million_tokens=output_per_million,
    )
