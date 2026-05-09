"""Stage 5: Playwright headless Chromium dashboard time-to-ready.

Per simulated user: a fresh BrowserContext, the admin session cookie injected,
navigate to <coder_url>, wait for the configured ready selector.

NEVER pass `storage_state=` to `browser.new_context()` and NEVER use
`launch_persistent_context()` / `user_data_dir=...`. The whole point of this
stage is measuring fresh-page time-to-ready — persistent state across runs
(cached HTTP responses, service workers, leftover cookies) would corrupt the
measurement. The unit test `test_no_storage_state_passed_to_new_context`
enforces this at the API call site.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "dashboard"


def run(ctx: StageContext) -> StageResult:
    host = urlparse(ctx.cfg.coder_url).hostname or ""
    timeout_ms = ctx.cfg.timeouts.dashboard_ready * 1000
    selector = ctx.cfg.dashboard_ready_selector
    cookie = {
        "name": "coder_session_token",
        "value": ctx.cfg.coder_session_token,
        "domain": host,
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for user in ctx.users:
                op = user.username
                started = time.monotonic()
                # NO storage_state kwarg — see module docstring.
                context = browser.new_context()
                try:
                    context.add_cookies([cookie])
                    page = context.new_page()
                    page.goto(ctx.cfg.coder_url)
                    page.wait_for_selector(selector, timeout=timeout_ms)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    log_op(ctx.log, STAGE_NAME, op=op, ok=True,
                           elapsed_ms=elapsed_ms)
                except PWTimeout as e:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    err = f"playwright: {str(e)[:160]}"
                    log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                           elapsed_ms=elapsed_ms, err=err)
                    return StageResult(ok=False, err=err)
                except Exception as e:  # noqa: BLE001
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    err = f"playwright: {type(e).__name__}: {e}"[:200]
                    log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                           elapsed_ms=elapsed_ms, err=err)
                    return StageResult(ok=False, err=err)
                finally:
                    context.close()
        finally:
            browser.close()
    return StageResult(ok=True)
