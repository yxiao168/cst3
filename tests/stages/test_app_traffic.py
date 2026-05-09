"""Tests for stage 4: WebSocket round-trip to tcp-echo app (Pattern A)."""
from __future__ import annotations
import pytest
import websocket

from coder_scale_test.stages import app_traffic as stage
from coder_scale_test.coder_client import Workspace
from coder_scale_test.runner import StageContext


def _ws(name="scaletest-alice-0") -> Workspace:
    return Workspace(id=f"wsid-{name}", name=name, owner_name="alice",
                     latest_build_status="succeeded", latest_build_transition="start")


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(coder_url="https://coder.example.com",
                      coder_session_token="super-secret-token-XYZ",
                      app_tcp_port=7000,
                      timeouts=mocker.Mock(app_traffic_round_trip=5))
    coder_client.get_agent_id.return_value = "agent-1"
    return StageContext(cfg=cfg, client=coder_client, ledger=mocker.Mock(),
                        log=fh, users=[], workspaces=[_ws()])


def test_success_when_payload_echoed(ctx, mocker):
    fake_ws = mocker.MagicMock()
    sent = {}
    def _send(p):
        sent["b"] = p
    fake_ws.send_binary.side_effect = _send
    fake_ws.recv.side_effect = lambda: sent["b"]
    mocker.patch("coder_scale_test.stages.app_traffic.websocket.create_connection",
                 return_value=fake_ws)
    res = stage.run(ctx)
    assert res.ok is True
    fake_ws.close.assert_called()


def test_url_uses_pattern_a(ctx, mocker):
    fake_ws = mocker.MagicMock()
    sent = {}
    fake_ws.send_binary.side_effect = lambda p: sent.update(b=p)
    fake_ws.recv.side_effect = lambda: sent["b"]
    create = mocker.patch(
        "coder_scale_test.stages.app_traffic.websocket.create_connection",
        return_value=fake_ws,
    )
    stage.run(ctx)
    args, kwargs = create.call_args
    url = args[0]
    assert url == "wss://coder.example.com/api/v2/workspaceagents/agent-1/apps/tcp-echo"
    assert kwargs["header"]["Coder-Session-Token"] == "super-secret-token-XYZ"


def test_fail_on_timeout(ctx, mocker):
    fake_ws = mocker.MagicMock()
    fake_ws.recv.side_effect = websocket.WebSocketTimeoutException("timed out")
    mocker.patch("coder_scale_test.stages.app_traffic.websocket.create_connection",
                 return_value=fake_ws)
    res = stage.run(ctx)
    assert res.ok is False
    assert "timeout" in (res.err or "").lower()


def test_fail_on_payload_mismatch(ctx, mocker):
    fake_ws = mocker.MagicMock()
    fake_ws.send_binary.side_effect = lambda p: None
    fake_ws.recv.side_effect = lambda: b"\x00" * 32  # wrong bytes
    mocker.patch("coder_scale_test.stages.app_traffic.websocket.create_connection",
                 return_value=fake_ws)
    res = stage.run(ctx)
    assert res.ok is False
    assert "mismatch" in (res.err or "").lower()


def test_first_connect_failure_triggers_retry_then_succeeds(ctx, mocker):
    """Autoplan mandate: brief retry-with-backoff on the first WS connect."""
    fake_ws = mocker.MagicMock()
    sent = {}
    fake_ws.send_binary.side_effect = lambda p: sent.update(b=p)
    fake_ws.recv.side_effect = lambda: sent["b"]
    # First create_connection call raises; second returns the fake_ws.
    create = mocker.patch(
        "coder_scale_test.stages.app_traffic.websocket.create_connection",
        side_effect=[
            websocket.WebSocketException("connection refused"),
            fake_ws,
        ],
    )
    res = stage.run(ctx)
    assert res.ok is True
    assert create.call_count == 2  # one fail, one retry success


def test_retry_does_not_help_when_both_fail(ctx, mocker):
    """After one retry, give up — don't loop forever."""
    create = mocker.patch(
        "coder_scale_test.stages.app_traffic.websocket.create_connection",
        side_effect=websocket.WebSocketException("connection refused"),
    )
    res = stage.run(ctx)
    assert res.ok is False
    assert create.call_count == 2  # initial + one retry
    assert "connect_failed" in (res.err or "").lower() or "connection refused" in (res.err or "").lower()


def test_redacts_token_from_underlying_error(ctx, mocker, tmp_log):
    """Errors that embed connection details must not leak the session token."""
    path, _ = tmp_log
    leaky_msg = "wss://coder.example.com/?token=super-secret-token-XYZ failed"
    mocker.patch("coder_scale_test.stages.app_traffic.websocket.create_connection",
                 side_effect=RuntimeError(leaky_msg))
    res = stage.run(ctx)
    assert res.ok is False
    assert "super-secret-token-XYZ" not in (res.err or "")
    log_text = path.read_text()
    assert "super-secret-token-XYZ" not in log_text
