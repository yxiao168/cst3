"""Workspace ledger and cleanup loop.

`WorkspaceLedger` tracks every workspace ID created during the run.
`cleanup.run()` iterates the ledger and asks the `CoderClient` to delete each
workspace, logging the outcome. Cleanup never aborts on per-workspace
failure — its job is to free as many resources as possible.

The cleanup loop installs its own SIGINT handler with two-stage semantics:
the first Ctrl-C during cleanup logs a warning to stderr; the second
Ctrl-C raises `KeyboardInterrupt`, which breaks out of the loop and lets
the caller (runner.run) finish unwinding. Workspaces whose delete was in
flight when the second Ctrl-C arrived may leak.

The `_categorize` helper applies `log.redact()` so that exception strings
which embed the session token (e.g. `requests.HTTPError` containing a URL
with a token query param) are scrubbed before being written to the log.
"""
from __future__ import annotations

import signal
import sys
import time
from typing import TYPE_CHECKING

from coder_scale_test.log import log_event, log_op, redact

if TYPE_CHECKING:
    from coder_scale_test.runner import StageContext


class WorkspaceLedger:
    def __init__(self) -> None:
        self._ids: list[str] = []

    def add(self, workspace_id: str) -> None:
        self._ids.append(workspace_id)

    def all(self) -> list[str]:
        return list(self._ids)


def run(ctx: "StageContext") -> int:
    """Delete every workspace in the ledger. Returns count of failed deletes."""
    log_event(ctx.log, "CLEANUP_START", total=len(ctx.ledger.all()))
    install_cleanup_sigint_handler()
    failed = 0
    started = time.monotonic()
    token = _get_token(ctx)
    for ws_id in ctx.ledger.all():
        op_started = time.monotonic()
        try:
            ctx.client.delete_workspace(
                ws_id, timeout=ctx.cfg.timeouts.delete_workspace
            )
            elapsed_ms = int((time.monotonic() - op_started) * 1000)
            log_op(ctx.log, "cleanup", ws_id, ok=True, elapsed_ms=elapsed_ms)
        except Exception as e:  # noqa: BLE001 — cleanup never aborts on per-op failure
            elapsed_ms = int((time.monotonic() - op_started) * 1000)
            failed += 1
            log_op(
                ctx.log, "cleanup", ws_id, ok=False, elapsed_ms=elapsed_ms,
                err=_categorize(e, token),
            )
    elapsed_total_ms = int((time.monotonic() - started) * 1000)
    log_event(
        ctx.log, "CLEANUP_END",
        deleted=len(ctx.ledger.all()) - failed,
        failed=failed,
        elapsed_ms=elapsed_total_ms,
    )
    return failed


_int_count = 0


def install_cleanup_sigint_handler() -> None:
    """First Ctrl-C during cleanup warns. Second Ctrl-C aborts."""
    global _int_count
    _int_count = 0

    def _handler(signum, frame):  # noqa: ARG001
        global _int_count
        _int_count += 1
        if _int_count == 1:
            sys.stderr.write(
                "\nCleanup in progress. Press Ctrl-C again to abort and "
                "leak workspaces.\n"
            )
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)


def _categorize(exc: BaseException, token: str) -> str:
    """Convert an exception to a single short err= string with token redacted."""
    raw = f"{type(exc).__name__}: {exc}"
    return redact(raw, token)[:200]


def _get_token(ctx: "StageContext") -> str:
    """Best-effort fetch of the session token from cfg.

    Returns empty string if cfg lacks the field (e.g. test mocks that don't
    set it). Empty token is a no-op for redact().
    """
    tok = getattr(ctx.cfg, "coder_session_token", "")
    return tok if isinstance(tok, str) else ""
