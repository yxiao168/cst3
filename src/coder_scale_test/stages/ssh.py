"""Stage 2: SSH echo round-trip via `coder ssh <ws> -- echo <token>`.

Each workspace gets a fresh 16-char alphanumeric token; success = exit 0 AND
the token round-trips through stdout. Stderr is redacted of the session token
before being included in error messages (autoplan checklist line 3566).
"""
from __future__ import annotations

import os
import secrets
import string
import subprocess
import time

from coder_scale_test.log import log_op, redact
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "ssh"
TOKEN_LEN = 16
ALPHABET = string.ascii_letters + string.digits


def run(ctx: StageContext) -> StageResult:
    session_token = ctx.cfg.coder_session_token
    for ws in ctx.workspaces:
        op = f"{ws.owner_name}/{ws.name}"
        token = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LEN))
        cmd = ["coder", "ssh", ws.name, "--", "echo", token]
        env = {**os.environ,
               "CODER_URL": ctx.cfg.coder_url,
               "CODER_SESSION_TOKEN": session_token}
        started = time.monotonic()
        try:
            cp = subprocess.run(
                cmd, env=env, capture_output=True, text=True,
                timeout=ctx.cfg.timeouts.ssh_round_trip,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            err = "timeout: coder ssh exit=124"
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if cp.returncode != 0:
            stderr_clean = redact(cp.stderr.strip(), session_token)[:120]
            err = f'subprocess: exit={cp.returncode} stderr="{stderr_clean}"'
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)

        if token not in cp.stdout:
            stdout_clean = redact(cp.stdout.strip(), session_token)[:80]
            err = f'mismatch: expected="{token}" got="{stdout_clean}"'
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)

        log_op(ctx.log, STAGE_NAME, op=op, ok=True, elapsed_ms=elapsed_ms)
    return StageResult(ok=True)
