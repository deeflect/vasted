from __future__ import annotations

import importlib

import click
import pytest

up_module = importlib.import_module("app.commands.up")


def test_enforce_budget_confirmation_allows_when_within_budget() -> None:
    up_module._enforce_budget_confirmation(
        best_price=0.9,
        budget=1.0,
        assume_yes=False,
        non_interactive=False,
        stdin_is_tty=True,
    )


def test_enforce_budget_confirmation_non_interactive_requires_yes() -> None:
    with pytest.raises(click.ClickException) as exc:
        up_module._enforce_budget_confirmation(
            best_price=1.5,
            budget=1.0,
            assume_yes=False,
            non_interactive=True,
            stdin_is_tty=True,
        )

    assert "--yes" in str(exc.value)


def test_enforce_budget_confirmation_non_tty_requires_yes() -> None:
    with pytest.raises(click.ClickException) as exc:
        up_module._enforce_budget_confirmation(
            best_price=1.5,
            budget=1.0,
            assume_yes=False,
            non_interactive=False,
            stdin_is_tty=False,
        )

    assert "--yes" in str(exc.value)


def test_enforce_budget_confirmation_assume_yes_skips_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"confirm": False}
    monkeypatch.setattr(up_module.Confirm, "ask", lambda *_args, **_kwargs: called.__setitem__("confirm", True))

    up_module._enforce_budget_confirmation(
        best_price=1.5,
        budget=1.0,
        assume_yes=True,
        non_interactive=True,
        stdin_is_tty=False,
    )

    assert not called["confirm"]


def test_enforce_budget_confirmation_interactive_respects_decline(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up_module.Confirm, "ask", lambda *_args, **_kwargs: False)
    with pytest.raises(click.ClickException) as exc:
        up_module._enforce_budget_confirmation(
            best_price=1.5,
            budget=1.0,
            assume_yes=False,
            non_interactive=False,
            stdin_is_tty=True,
        )
    assert "Cancelled" in str(exc.value)


def test_enforce_budget_confirmation_interactive_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up_module.Confirm, "ask", lambda *_args, **_kwargs: True)
    up_module._enforce_budget_confirmation(
        best_price=1.5,
        budget=1.0,
        assume_yes=False,
        non_interactive=False,
        stdin_is_tty=True,
    )
