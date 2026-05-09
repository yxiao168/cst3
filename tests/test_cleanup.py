"""Tests for WorkspaceLedger and cleanup.run()."""
from __future__ import annotations
from unittest.mock import MagicMock

from coder_scale_test.cleanup import WorkspaceLedger, run as cleanup_run


def _make_ctx(mocker, fh, ledger, *, token: str = "tok-abc"):
    cfg = mocker.Mock(
        timeouts=mocker.Mock(delete_workspace=120),
        coder_session_token=token,
    )
    return mocker.Mock(cfg=cfg, client=mocker.Mock(), ledger=ledger, log=fh)


def test_ledger_add_and_all():
    led = WorkspaceLedger()
    led.add("ws-1")
    led.add("ws-2")
    assert led.all() == ["ws-1", "ws-2"]


def test_ledger_all_returns_copy():
    led = WorkspaceLedger()
    led.add("ws-1")
    out = led.all()
    out.append("mutate")
    assert led.all() == ["ws-1"]  # not affected by external mutation


def test_cleanup_iterates_and_succeeds(tmp_log, mocker):
    path, fh = tmp_log
    led = WorkspaceLedger()
    led.add("ws-a"); led.add("ws-b"); led.add("ws-c")
    ctx = _make_ctx(mocker, fh, led)
    ctx.client.delete_workspace = MagicMock()  # success
    failed = cleanup_run(ctx)
    assert failed == 0
    assert ctx.client.delete_workspace.call_count == 3
    text = path.read_text()
    assert "CLEANUP_START total=3" in text
    assert "CLEANUP_END deleted=3 failed=0" in text


def test_cleanup_continues_past_per_workspace_failure(tmp_log, mocker):
    path, fh = tmp_log
    led = WorkspaceLedger()
    led.add("ws-a"); led.add("ws-b"); led.add("ws-c")
    ctx = _make_ctx(mocker, fh, led)
    ctx.client.delete_workspace = MagicMock(
        side_effect=[None, RuntimeError("boom"), None]
    )
    failed = cleanup_run(ctx)
    assert failed == 1
    # All three were attempted
    assert ctx.client.delete_workspace.call_count == 3
    assert "CLEANUP_END deleted=2 failed=1" in path.read_text()


def test_cleanup_with_empty_ledger(tmp_log, mocker):
    path, fh = tmp_log
    led = WorkspaceLedger()
    ctx = _make_ctx(mocker, fh, led)
    failed = cleanup_run(ctx)
    assert failed == 0
    assert "CLEANUP_START total=0" in path.read_text()


def test_cleanup_redacts_token_in_error_messages(tmp_log, mocker):
    """Exceptions whose __str__ leaks the token are redacted before being logged.

    Auto-decided in autoplan Eng §5.3: a single redact() pass on the err= string
    prevents requests.HTTPError or similar from leaking the literal token.
    """
    path, fh = tmp_log
    led = WorkspaceLedger()
    led.add("ws-leaky")
    ctx = _make_ctx(mocker, fh, led, token="super-secret-token-XYZ")
    ctx.client.delete_workspace = MagicMock(
        side_effect=RuntimeError(
            "HTTPError 401: Unauthorized "
            "(url=https://coder.example.com/api?token=super-secret-token-XYZ)"
        )
    )
    cleanup_run(ctx)
    text = path.read_text()
    assert "super-secret-token-XYZ" not in text
    assert "[REDACTED]" in text
