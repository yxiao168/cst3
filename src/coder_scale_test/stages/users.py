"""Stage 0: pick active non-admin users for the run.

Two modes:
- Allow-list (cfg.users is set): pick those usernames in the given order; fail
  if any are missing from the cluster's active non-admin set.
- First-N (cfg.users is None): pick first cfg.num_users by created_at ascending.
"""
from __future__ import annotations

import time

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "users"


def run(ctx: StageContext) -> StageResult:
    started = time.monotonic()
    try:
        all_users = ctx.client.list_active_non_admin_users(
            limit=max(200, ctx.cfg.num_users * 5)
        )
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log_op(ctx.log, STAGE_NAME, op="list_users", ok=False,
               elapsed_ms=elapsed_ms, err=f"{type(e).__name__}: {e}")
        return StageResult(ok=False, err=str(e))

    elapsed_ms = int((time.monotonic() - started) * 1000)
    allow_list = ctx.cfg.users  # list[str] | None

    if allow_list:
        by_name = {u.username: u for u in all_users}
        missing = [name for name in allow_list if name not in by_name]
        if missing:
            err = (f"allow-list users not found among active non-admin: "
                   f"{', '.join(missing)}")
            log_op(ctx.log, STAGE_NAME, op="list_users", ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)
        picked = [by_name[name] for name in allow_list]
    else:
        if len(all_users) < ctx.cfg.num_users:
            err = (f"need {ctx.cfg.num_users} active non-admin users, "
                   f"cluster has {len(all_users)} (fewer than {ctx.cfg.num_users})")
            log_op(ctx.log, STAGE_NAME, op="list_users", ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)
        picked = all_users[: ctx.cfg.num_users]

    log_op(ctx.log, STAGE_NAME, op="list_users", ok=True, elapsed_ms=elapsed_ms)
    return StageResult(ok=True, users=picked)
