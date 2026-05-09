"""Tests for stage 1: create N×M workspaces, register in ledger, wait for running.

Run-all-and-tally semantics: every (user, m) is attempted regardless of earlier
failures. Successful workspaces still end up in result.workspaces and the ledger.
"""
from __future__ import annotations
import pytest

from coder_scale_test.stages import provision as stage
from coder_scale_test.coder_client import User, Workspace, CoderApiError
from coder_scale_test.cleanup import WorkspaceLedger
from coder_scale_test.runner import StageContext


def _ws(idx: int, status="succeeded", transition="start") -> Workspace:
    return Workspace(id=f"ws-{idx}", name=f"scaletest-x-{idx}",
                     owner_name="x", latest_build_status=status,
                     latest_build_transition=transition)


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(num_users=2, per_user=2, template_name="t",
                      timeouts=mocker.Mock(provision_workspace=60))
    return StageContext(
        cfg=cfg, client=coder_client, ledger=WorkspaceLedger(), log=fh,
        users=[
            User("u1", "alice", "2024-01-01T00:00:00Z"),
            User("u2", "bob", "2024-01-02T00:00:00Z"),
        ],
    )


def test_creates_n_times_m_and_registers_in_ledger(ctx, coder_client):
    coder_client.create_workspace.side_effect = [_ws(i) for i in range(4)]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(int(ws_id.split("-")[1]))
    res = stage.run(ctx)
    assert res.ok is True
    assert coder_client.create_workspace.call_count == 4
    assert sorted(ctx.ledger.all()) == [f"ws-{i}" for i in range(4)]
    assert len(res.workspaces) == 4


def test_uses_correct_workspace_name_pattern(ctx, coder_client):
    coder_client.create_workspace.side_effect = [_ws(i) for i in range(4)]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(int(ws_id.split("-")[1]))
    stage.run(ctx)
    names = [c.kwargs["name"] for c in coder_client.create_workspace.call_args_list]
    assert names == [
        "scaletest-alice-0", "scaletest-alice-1",
        "scaletest-bob-0", "scaletest-bob-1",
    ]


def test_run_all_and_tally_continues_past_create_error(ctx, coder_client):
    """Second create fails — remaining (user, m) pairs are still attempted."""
    # Index 1 (alice/1) raises; the other three succeed.
    side = [
        _ws(0),
        CoderApiError("status=409 already exists"),
        _ws(2),
        _ws(3),
    ]
    coder_client.create_workspace.side_effect = side
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(int(ws_id.split("-")[1]))
    res = stage.run(ctx)
    assert res.ok is False
    assert "1 of 4" in (res.err or "") or "1/4" in (res.err or "")
    # All 4 creates were attempted
    assert coder_client.create_workspace.call_count == 4
    # Successes are in workspaces and ledger; the failed one is not
    assert sorted(ctx.ledger.all()) == ["ws-0", "ws-2", "ws-3"]
    assert sorted(w.id for w in res.workspaces) == ["ws-0", "ws-2", "ws-3"]


def test_wait_for_running_failure_does_not_lose_ledger_entry(ctx, coder_client):
    """If wait_for_running fails, the workspace was still registered in ledger
    so cleanup will delete it."""
    coder_client.create_workspace.side_effect = [_ws(i) for i in range(4)]
    # Index 0 (ws-0) wait times out; others succeed.
    def _wait(ws_id, timeout):
        if ws_id == "ws-0":
            raise CoderApiError("timeout after 60s")
        return _ws(int(ws_id.split("-")[1]))
    coder_client.wait_for_running.side_effect = _wait
    res = stage.run(ctx)
    assert res.ok is False
    # ws-0 was registered before wait failed
    assert "ws-0" in ctx.ledger.all()
    # ws-0 NOT in result.workspaces (it never reached running)
    assert "ws-0" not in [w.id for w in res.workspaces]


def test_emits_separate_create_and_wait_log_lines(ctx, coder_client, tmp_log):
    """Each (user, m) emits ONE create:* line and ONE wait:* line."""
    path, _ = tmp_log
    coder_client.create_workspace.side_effect = [_ws(i) for i in range(4)]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(int(ws_id.split("-")[1]))
    stage.run(ctx)
    text = path.read_text()
    # 4 creates × 1 line each
    assert text.count(" op=create:") == 4
    # 4 waits × 1 line each
    assert text.count(" op=wait:") == 4


def test_all_creates_fail_returns_failure(ctx, coder_client):
    coder_client.create_workspace.side_effect = CoderApiError("status=500")
    res = stage.run(ctx)
    assert res.ok is False
    assert "4 of 4" in (res.err or "") or "4/4" in (res.err or "")
    assert ctx.ledger.all() == []
    assert res.workspaces == [] or res.workspaces is None
