"""Tests for stage 3: PTY WebSocket echo round-trip."""
from __future__ import annotations
import pytest
import websocket

from coder_scale_test.stages import web_terminal as stage
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
                      timeouts=mocker.Mock(web_terminal_round_trip=5))
    coder_client.get_agent_id.return_value = "agent-1"
    return StageContext(cfg=cfg, client=coder_client, ledger=mocker.Mock(),
                        log=fh, users=[], workspaces=[_ws()])


def test_success_when_token_observed(ctx, mocker):
    fake_ws = mocker.MagicMock()
    sent = {}
    def _send(payload):
        if payload.startswith("echo "):
            sent["token"] = payload.split()[1].strip()
    fake_ws.send.side_effect = _send
    fake_ws.recv.side_effect = lambda: (sent["token"] + "\r\n").encode()
    mocker.patch("coder_scale_test.stages.web_terminal.websocket.create_connection",
                 return_value=fake_ws)
    res = stage.run(ctx)
    assert res.ok is True
    fake_ws.close.assert_called()


def test_uses_correct_url_and_header(ctx, mocker):
    fake_ws = mocker.MagicMock()
    sent_token = {}
    def _send(payload):
        if payload.startswith("echo "):
            sent_token["t"] = payload.split()[1].strip()
    fake_ws.send.side_effect = _send
    fake_ws.recv.side_effect = lambda: (sent_token["t"] + "\r\n").encode()
    create = mocker.patch(
        "coder_scale_test.stages.web_terminal.websocket.create_connection",
        return_value=fake_ws,
    )
    stage.run(ctx)
    args, kwargs = create.call_args
    url = args[0]
    assert url.startswith("wss://coder.example.com/api/v2/workspaceagents/agent-1/pty")
    assert kwargs["header"]["Coder-Session-Token"] == "super-secret-token-XYZ"


def test_fails_on_timeout(ctx, mocker):
    fake_ws = mocker.MagicMock()
    fake_ws.recv.side_effect = websocket.WebSocketTimeoutException("timed out")
    mocker.patch("coder_scale_test.stages.web_terminal.websocket.create_connection",
                 return_value=fake_ws)
    res = stage.run(ctx)
    assert res.ok is False
    assert "timeout" in (res.err or "").lower()


def test_fails_on_ws_close(ctx, mocker):
    fake_ws = mocker.MagicMock()
    fake_ws.recv.side_effect = websocket.WebSocketConnectionClosedException(
        "closed: code=1006"
    )
    mocker.patch("coder_scale_test.stages.web_terminal.websocket.create_connection",
                 return_value=fake_ws)
    res = stage.run(ctx)
    assert res.ok is False
    assert "ws_closed" in (res.err or "").lower()


def test_redacts_token_from_underlying_error(ctx, mocker, tmp_log):
    """Errors that embed connection details must not leak the session token."""
    path, _ = tmp_log
    leaky_msg = (
        "Connection failed for wss://coder.example.com/?token=super-secret-token-XYZ"
    )
    mocker.patch("coder_scale_test.stages.web_terminal.websocket.create_connection",
                 side_effect=RuntimeError(leaky_msg))
    res = stage.run(ctx)
    assert res.ok is False
    assert "super-secret-token-XYZ" not in (res.err or "")
    log_text = path.read_text()
    assert "super-secret-token-XYZ" not in log_text
