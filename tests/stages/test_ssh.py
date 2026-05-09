"""Tests for stage 2: shell out to `coder ssh <ws> -- echo <token>`.

Includes the autoplan-mandated stderr token redaction (line 3566 of plan).
"""
from __future__ import annotations
import subprocess

import pytest

from coder_scale_test.stages import ssh as stage
from coder_scale_test.coder_client import Workspace
from coder_scale_test.runner import StageContext


def _ws(name: str) -> Workspace:
    return Workspace(id=f"id-{name}", name=name, owner_name="alice",
                     latest_build_status="succeeded", latest_build_transition="start")


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(coder_url="https://coder.example.com",
                      coder_session_token="super-secret-token-XYZ",
                      timeouts=mocker.Mock(ssh_round_trip=10))
    return StageContext(
        cfg=cfg, client=coder_client, ledger=mocker.Mock(), log=fh,
        users=[],
        workspaces=[_ws("scaletest-alice-0"), _ws("scaletest-bob-0")],
    )


def test_success_when_token_in_stdout(ctx, mocker):
    captured_tokens = []
    def _run(cmd, **kwargs):
        token = cmd[-1]                 # last arg is `<token>`
        captured_tokens.append(token)
        return subprocess.CompletedProcess(cmd, returncode=0,
                                           stdout=token + "\n", stderr="")
    mocker.patch.object(stage.subprocess, "run", side_effect=_run)
    res = stage.run(ctx)
    assert res.ok is True
    assert len(captured_tokens) == 2  # one per workspace
    # Tokens differ between calls (per-workspace freshness)
    assert captured_tokens[0] != captured_tokens[1]


def test_fail_on_token_mismatch(ctx, mocker):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=0,
                                           stdout="DIFFERENT\n", stderr="")
    mocker.patch.object(stage.subprocess, "run", side_effect=_run)
    res = stage.run(ctx)
    assert res.ok is False
    assert "mismatch" in (res.err or "").lower()


def test_fail_on_nonzero_exit(ctx, mocker):
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=2,
                                           stdout="", stderr="ssh: connection refused")
    mocker.patch.object(stage.subprocess, "run", side_effect=_run)
    res = stage.run(ctx)
    assert res.ok is False
    assert "subprocess" in (res.err or "").lower()
    assert "connection refused" in (res.err or "")


def test_fail_on_timeout(ctx, mocker):
    def _run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 10)
    mocker.patch.object(stage.subprocess, "run", side_effect=_run)
    res = stage.run(ctx)
    assert res.ok is False
    assert "timeout" in (res.err or "").lower()


def test_passes_env_vars(ctx, mocker):
    seen_env = {}
    def _run(cmd, env=None, **kwargs):
        seen_env.update(env or {})
        token = cmd[-1]
        return subprocess.CompletedProcess(cmd, returncode=0,
                                           stdout=token, stderr="")
    mocker.patch.object(stage.subprocess, "run", side_effect=_run)
    stage.run(ctx)
    assert seen_env["CODER_URL"] == "https://coder.example.com"
    assert seen_env["CODER_SESSION_TOKEN"] == "super-secret-token-XYZ"


def test_redacts_token_from_stderr_in_error_message(ctx, mocker, tmp_log):
    """The session token must NOT appear in res.err or the log file when it
    leaks into the CLI's stderr (autoplan checklist line 3566)."""
    path, _ = tmp_log
    leaky_stderr = (
        "auth failed for url=https://coder.example.com/api?token=super-secret-token-XYZ"
    )
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode=2,
                                           stdout="", stderr=leaky_stderr)
    mocker.patch.object(stage.subprocess, "run", side_effect=_run)
    res = stage.run(ctx)
    assert res.ok is False
    assert "super-secret-token-XYZ" not in (res.err or "")
    log_text = path.read_text()
    assert "super-secret-token-XYZ" not in log_text
    assert "[REDACTED]" in (res.err or "") or "[REDACTED]" in log_text
