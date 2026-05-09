"""Stage 1: create N×M workspaces and wait for each to reach running.

Run-all-and-tally: every (user, m) pair is attempted regardless of earlier
failures so a single bad workspace doesn't mask others. Each create_workspace
result is registered in the ledger BEFORE waiting for running, so cleanup can
still delete workspaces whose wait timed out.

Separate log lines for create and wait phases let operators tell apart
"workspace creation refused" from "workspace stuck pending".
"""
from __future__ import annotations

import time

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "provision"


def run(ctx: StageContext) -> StageResult:
    running: list = []
    failed = 0
    total = ctx.cfg.num_users * ctx.cfg.per_user

    for user in ctx.users:
        for m in range(ctx.cfg.per_user):
            name = f"scaletest-{user.username}-{m}"
            label = f"{user.username}/{name}"

            create_started = time.monotonic()
            try:
                ws = ctx.client.create_workspace(
                    user_id=user.id, name=name,
                    template_name=ctx.cfg.template_name,
                )
            except Exception as e:  # noqa: BLE001
                elapsed_ms = int((time.monotonic() - create_started) * 1000)
                log_op(ctx.log, STAGE_NAME, op=f"create:{label}", ok=False,
                       elapsed_ms=elapsed_ms,
                       err=f"{type(e).__name__}: {e}"[:200])
                failed += 1
                continue
            create_elapsed_ms = int((time.monotonic() - create_started) * 1000)
            log_op(ctx.log, STAGE_NAME, op=f"create:{label}", ok=True,
                   elapsed_ms=create_elapsed_ms)

            # Register BEFORE polling so cleanup deletes it even on wait timeout.
            ctx.ledger.add(ws.id)

            wait_started = time.monotonic()
            try:
                ws_running = ctx.client.wait_for_running(
                    ws.id, timeout=ctx.cfg.timeouts.provision_workspace,
                )
            except Exception as e:  # noqa: BLE001
                elapsed_ms = int((time.monotonic() - wait_started) * 1000)
                log_op(ctx.log, STAGE_NAME, op=f"wait:{label}", ok=False,
                       elapsed_ms=elapsed_ms,
                       err=f"{type(e).__name__}: {e}"[:200])
                failed += 1
                continue
            wait_elapsed_ms = int((time.monotonic() - wait_started) * 1000)
            log_op(ctx.log, STAGE_NAME, op=f"wait:{label}", ok=True,
                   elapsed_ms=wait_elapsed_ms)
            running.append(ws_running)

    if failed:
        return StageResult(
            ok=False,
            err=f"{failed} of {total} workspaces failed to provision",
            workspaces=running,
        )
    return StageResult(ok=True, workspaces=running)
