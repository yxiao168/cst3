"""Stage 3: PTY WebSocket echo round-trip.

Opens wss://<host>/api/v2/workspaceagents/{agent_id}/pty, sends `echo <token>\\n`,
reads frames until the token is observed in stdout. Errors are redacted of
the session token before being logged or returned (autoplan checklist 3567).
"""
from __future__ import annotations

import secrets
import string
import time
from urllib.parse import urlparse, urlunparse

import websocket  # from websocket-client

from coder_scale_test.log import log_op, redact
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "web_terminal"
TOKEN_LEN = 16
ALPHABET = string.ascii_letters + string.digits


def run(ctx: StageContext) -> StageResult:
    base_wss = _http_to_ws(ctx.cfg.coder_url)
    session_token = ctx.cfg.coder_session_token

    def _err(prefix: str, exc: BaseException) -> str:
        raw = f"{prefix}: {type(exc).__name__}: {exc}"
        return redact(raw, session_token)[:200]

    for ws in ctx.workspaces:
        op = f"{ws.owner_name}/{ws.name}"
        try:
            agent_id = ctx.client.get_agent_id(ws.id)
        except Exception as e:  # noqa: BLE001
            err = _err("agent_lookup", e)
            log_op(ctx.log, STAGE_NAME, op=op, ok=False, elapsed_ms=0, err=err)
            return StageResult(ok=False, err=err)

        url = (f"{base_wss}/api/v2/workspaceagents/{agent_id}/pty"
               f"?reconnect={secrets.token_hex(8)}&width=80&height=24")
        token = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LEN))
        started = time.monotonic()
        timeout_s = ctx.cfg.timeouts.web_terminal_round_trip
        ws_conn = None
        try:
            ws_conn = websocket.create_connection(
                url,
                header={"Coder-Session-Token": session_token},
                timeout=timeout_s,
            )
            ws_conn.send(f"echo {token}\n")
            deadline = time.monotonic() + timeout_s
            buf = ""
            seen = False
            while time.monotonic() < deadline:
                frame = ws_conn.recv()
                buf += (frame.decode("utf-8", "replace")
                        if isinstance(frame, (bytes, bytearray)) else frame)
                if token in buf:
                    seen = True
                    break
            if seen:
                elapsed_ms = int((time.monotonic() - started) * 1000)
                log_op(ctx.log, STAGE_NAME, op=op, ok=True, elapsed_ms=elapsed_ms)
            else:
                err = f"timeout: token not seen within {timeout_s}s"
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
                return StageResult(ok=False, err=err)
        except websocket.WebSocketTimeoutException as e:
            err = _err("timeout: pty ws", e)
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        except websocket.WebSocketConnectionClosedException as e:
            err = _err("ws_closed", e)
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        except Exception as e:  # noqa: BLE001
            err = _err("error", e)
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        finally:
            if ws_conn is not None:
                try:
                    ws_conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return StageResult(ok=True)


def _http_to_ws(http_url: str) -> str:
    """https://x → wss://x; http://x → ws://x."""
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))
