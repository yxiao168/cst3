"""Tests for stage 0: pick active non-admin users (first-N or allow-list)."""
from __future__ import annotations
import pytest

from coder_scale_test.stages import users as stage
from coder_scale_test.coder_client import User
from coder_scale_test.runner import StageContext


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(num_users=2, users=None)
    return StageContext(
        cfg=cfg, client=coder_client, ledger=mocker.Mock(), log=fh,
    )


def test_picks_first_n_when_no_allow_list(ctx, coder_client):
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "alice", "2024-01-01T00:00:00Z"),
        User("u2", "bob", "2024-01-02T00:00:00Z"),
        User("u3", "charlie", "2024-01-03T00:00:00Z"),
    ]
    res = stage.run(ctx)
    assert res.ok is True
    assert [u.username for u in res.users] == ["alice", "bob"]


def test_fails_when_too_few_for_first_n(ctx, coder_client):
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "alice", "2024-01-01T00:00:00Z"),
    ]
    res = stage.run(ctx)
    assert res.ok is False
    assert "need 2" in res.err.lower() or "fewer than 2" in res.err.lower()
    assert res.users is None


def test_allow_list_picks_named_users_in_given_order(ctx, coder_client):
    ctx.cfg.users = ["bob", "alice"]  # explicitly out-of-order
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "alice", "2024-01-01T00:00:00Z"),
        User("u2", "bob", "2024-01-02T00:00:00Z"),
        User("u3", "charlie", "2024-01-03T00:00:00Z"),
    ]
    res = stage.run(ctx)
    assert res.ok is True
    assert [u.username for u in res.users] == ["bob", "alice"]


def test_allow_list_fails_on_missing_user(ctx, coder_client):
    ctx.cfg.users = ["alice", "ghost"]
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "alice", "2024-01-01T00:00:00Z"),
        User("u2", "bob", "2024-01-02T00:00:00Z"),
    ]
    res = stage.run(ctx)
    assert res.ok is False
    assert "ghost" in res.err
    assert res.users is None


def test_logs_op_outcome_on_success(ctx, coder_client, tmp_log):
    path, _ = tmp_log
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "a", "2024-01-01T00:00:00Z"),
        User("u2", "b", "2024-01-02T00:00:00Z"),
    ]
    stage.run(ctx)
    text = path.read_text()
    assert "OK    stage=users op=list_users" in text


def test_api_error_returns_failure(ctx, coder_client):
    from coder_scale_test.coder_client import CoderApiError
    coder_client.list_active_non_admin_users.side_effect = CoderApiError("status=401")
    res = stage.run(ctx)
    assert res.ok is False
    assert res.users is None
