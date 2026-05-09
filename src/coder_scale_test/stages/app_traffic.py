"""Stage 4: WebSocket round-trip to the workspace's tcp-echo app.

URL pattern: Pattern A (agent-app endpoint) per design recommendation.
The Task 6 spike was deferred until cluster access is available — the
implementer running against a real cluster may need to swap _build_app_url()
to Pattern B (path-based proxy) or shell-out (`coder port-forward`). See
docs/superpowers/specs/2026-05-08-spike-stage-4-app-traffic.md for the probes.

Brief retry-with-backoff on the FIRST connect attempt per workspace, since
the tcp-echo app server inside the workspace may take a few seconds after
workspace-running before its port is bound (autoplan checklist line 3568).
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import urlparse, urlunparse

import websocket

from coder_scale_test.log import log_op, redact
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "app_traffic"
APP_SLUG = "tcp-echo"
PAYLOAD_SIZE = 32
CONNECT_RETRY_BACKOFF_S = 2.0


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

        url = _build_app_url(base_wss, agent_id, APP_SLUG)
        payload = secrets.token_bytes(PAYLOAD_SIZE)
        timeout_s = ctx.cfg.timeouts.app_traffic_round_trip
        started = time.monotonic()
        ws_conn = None
        try:
            try:
                ws_conn = _connect_with_retry(url, session_token, timeout_s)
            except Exception as e:  # noqa: BLE001
                err = _err("connect_failed", e)
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
                return StageResult(ok=False, err=err)

            try:
                ws_conn.send_binary(payload)
                received = ws_conn.recv()
            except websocket.WebSocketTimeoutException as e:
                err = _err("timeout: app ws", e)
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
                return StageResult(ok=False, err=err)
            except websocket.WebSocketConnectionClosedException as e:
                err = _err("ws_closed", e)
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
                return StageResult(ok=False, err=err)

            elapsed_ms = int((time.monotonic() - started) * 1000)
            if received != payload:
                got_hex = (received[:32].hex()
                           if isinstance(received, (bytes, bytearray)) else "non-binary")
                err = (f'mismatch: expected_hex_prefix="{payload.hex()[:32]}" '
                       f'got_hex_prefix="{got_hex}"')
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=elapsed_ms, err=err)
                return StageResult(ok=False, err=err)
            log_op(ctx.log, STAGE_NAME, op=op, ok=True, elapsed_ms=elapsed_ms)
        finally:
            if ws_conn is not None:
                try:
                    ws_conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return StageResult(ok=True)


def _connect_with_retry(url: str, session_token: str, timeout_s: float):
    """Connect, retrying once with a brief backoff on the first failure.

    The tcp-echo app inside a freshly-running workspace may take a couple
    seconds after the workspace reaches "running" before its port binds.
    A single retry covers that gap without masking real connection bugs.
    """
    header = {"Coder-Session-Token": session_token}
    try:
        return websocket.create_connection(url, header=header, timeout=timeout_s)
    except Exception:  # noqa: BLE001
        time.sleep(CONNECT_RETRY_BACKOFF_S)
        return websocket.create_connection(url, header=header, timeout=timeout_s)


def _build_app_url(base_wss: str, agent_id: str, app_slug: str) -> str:
    """Pattern A — agent-app endpoint. Swap to Pattern B or shell-out if the
    spike against a real cluster shows this pattern doesn't work."""
    return f"{base_wss}/api/v2/workspaceagents/{agent_id}/apps/{app_slug}"


def _http_to_ws(http_url: str) -> str:
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))
