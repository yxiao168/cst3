"""Tests for runner.run(): fail-fast, SIGINT, exit codes, cleanup-always."""
from __future__ import annotations
from dataclasses import dataclass

import pytest

from coder_scale_test import runner as runner_mod
from coder_scale_test.runner import StageResult


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """A minimal Config with a tmp log file."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    from coder_scale_test.config import Config, Timeouts
    return Config(
        coder_url="https://coder.example.com",
        template_name="t",
        num_users=1,
        per_user=1,
        log_file=tmp_path / "scale-run.log",
        timeouts=Timeouts(provision_workspace=10, ssh_round_trip=10,
                          web_terminal_round_trip=10, app_traffic_round_trip=10,
                          dashboard_ready=10, delete_workspace=10),
        dashboard_ready_selector="x",
        app_tcp_port=7000,
        coder_session_token="tok",
        users=None,
        poll_interval_seconds=2.0,
        tls_verify=True,
        retry_count=3,
        retry_backoff_seconds=1.0,
        skip_stages=[],
    )


def _stub(ok: bool):
    def _run(ctx):
        return StageResult(ok=ok)
    return type("FakeStage", (), {"run": staticmethod(_run)})


def test_happy_path_exit_zero(cfg, mocker):
    mocker.patch.object(runner_mod, "_resolve_stages",
                        return_value=[_stub(True)] * 6)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    mocker.patch("coder_scale_test.cleanup.run", return_value=0)
    assert runner_mod.run(cfg) == 0


def test_fail_fast_skips_remaining(cfg, mocker):
    stages = [_stub(True), _stub(True), _stub(False), _stub(True), _stub(True), _stub(True)]
    spies = [mocker.spy(s, "run") for s in stages]
    mocker.patch.object(runner_mod, "_resolve_stages", return_value=stages)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    mocker.patch("coder_scale_test.cleanup.run", return_value=0)
    assert runner_mod.run(cfg) == 1
    assert spies[0].call_count == 1
    assert spies[1].call_count == 1
    assert spies[2].call_count == 1
    assert spies[3].call_count == 0  # never called — fail-fast
    assert spies[4].call_count == 0
    assert spies[5].call_count == 0


def test_sigint_returns_two(cfg, mocker):
    def _interrupt(ctx):
        raise KeyboardInterrupt
    bad_stage = type("Boom", (), {"run": staticmethod(_interrupt)})
    mocker.patch.object(runner_mod, "_resolve_stages",
                        return_value=[bad_stage] + [_stub(True)] * 5)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    cleanup_spy = mocker.patch("coder_scale_test.cleanup.run", return_value=0)
    assert runner_mod.run(cfg) == 2
    assert cleanup_spy.called  # cleanup ran even on Ctrl-C


def test_internal_exception_returns_three(cfg, mocker):
    def _bug(ctx):
        raise RuntimeError("internal-bug")
    bad_stage = type("Boom", (), {"run": staticmethod(_bug)})
    mocker.patch.object(runner_mod, "_resolve_stages",
                        return_value=[bad_stage] + [_stub(True)] * 5)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    mocker.patch("coder_scale_test.cleanup.run", return_value=0)
    assert runner_mod.run(cfg) == 3


def test_stage_success_but_cleanup_failure_returns_four(cfg, mocker):
    mocker.patch.object(runner_mod, "_resolve_stages",
                        return_value=[_stub(True)] * 6)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    mocker.patch("coder_scale_test.cleanup.run", return_value=2)  # 2 cleanup failures
    assert runner_mod.run(cfg) == 4


def test_missing_coder_cli_returns_three(cfg, mocker):
    mocker.patch.object(runner_mod, "_check_coder_cli",
                        side_effect=runner_mod.CoderCliMissing("not found"))
    # _resolve_stages should never be called
    resolve_spy = mocker.patch.object(runner_mod, "_resolve_stages")
    mocker.patch.object(runner_mod, "CoderClient")
    assert runner_mod.run(cfg) == 3
    assert resolve_spy.call_count == 0


def test_skip_cleanup_kwarg_skips_cleanup(cfg, mocker):
    """skip_cleanup=True suppresses the finally cleanup."""
    mocker.patch.object(runner_mod, "_resolve_stages",
                        return_value=[_stub(True)] * 6)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    cleanup_spy = mocker.patch("coder_scale_test.cleanup.run", return_value=0)
    rc = runner_mod.run(cfg, skip_cleanup=True)
    assert rc == 0
    cleanup_spy.assert_not_called()


def test_cleanup_only_kwarg_skips_stages(cfg, mocker):
    """cleanup_only=True skips the stage loop and runs cleanup."""
    stages = [_stub(False) for _ in range(6)]  # would fail if executed; distinct instances
    spies = [mocker.spy(s, "run") for s in stages]
    mocker.patch.object(runner_mod, "_resolve_stages", return_value=stages)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    cleanup_spy = mocker.patch("coder_scale_test.cleanup.run", return_value=0)
    rc = runner_mod.run(cfg, cleanup_only=True)
    assert rc == 0
    for s in spies:
        assert s.call_count == 0  # no stage was run
    cleanup_spy.assert_called_once()


def test_cleanup_only_with_cleanup_failures_returns_four(cfg, mocker):
    mocker.patch.object(runner_mod, "_resolve_stages",
                        return_value=[_stub(True)] * 6)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    mocker.patch("coder_scale_test.cleanup.run", return_value=2)
    assert runner_mod.run(cfg, cleanup_only=True) == 4


def test_resolve_stages_filters_skip_stages(cfg, mocker):
    """If cfg.skip_stages contains a name, that stage is filtered from _resolve_stages."""
    # We patch _resolve_stages with a function that calls the real filter logic.
    # Since _resolve_stages is now cfg-aware, test it directly.
    from dataclasses import replace
    cfg_with_skip = replace(cfg, skip_stages=["ssh", "dashboard"])

    # Build a fake stage module list where each module has a STAGE_NAME attribute.
    def _stage_module(name):
        return type("Mod", (), {"STAGE_NAME": name,
                                 "run": staticmethod(lambda ctx: StageResult(ok=True)),
                                 "__name__": f"coder_scale_test.stages.{name}"})

    all_stages = [_stage_module(n) for n in
                  ["users", "provision", "ssh", "web_terminal", "app_traffic", "dashboard"]]
    mocker.patch.object(runner_mod, "_resolve_all_stages", return_value=all_stages)
    filtered = runner_mod._resolve_stages(cfg_with_skip)
    names = [getattr(s, "STAGE_NAME") for s in filtered]
    assert "ssh" not in names
    assert "dashboard" not in names
    assert names == ["users", "provision", "web_terminal", "app_traffic"]


def test_threads_users_and_workspaces_into_ctx(cfg, mocker):
    """Stage products in StageResult become readable on ctx for later stages."""
    from coder_scale_test.coder_client import User, Workspace
    captured_ctx = {}

    def _stage_users(ctx):
        return StageResult(ok=True, users=[User("u1", "alice", "2024-01-01T00:00:00Z")])

    def _stage_provision(ctx):
        # By this point, ctx.users should have been threaded from stage 0
        captured_ctx["users_at_provision"] = list(ctx.users)
        return StageResult(ok=True, workspaces=[Workspace(
            "ws1", "scaletest-alice-0", "alice",
            latest_build_status="succeeded", latest_build_transition="start",
        )])

    def _stage_reads_workspaces(ctx):
        # By this point, ctx.workspaces should have been threaded from stage 1
        captured_ctx["workspaces_at_stage_2"] = list(ctx.workspaces)
        return StageResult(ok=True)

    stages = [
        type("S0", (), {"run": staticmethod(_stage_users), "STAGE_NAME": "users"}),
        type("S1", (), {"run": staticmethod(_stage_provision), "STAGE_NAME": "provision"}),
        type("S2", (), {"run": staticmethod(_stage_reads_workspaces), "STAGE_NAME": "ssh"}),
    ] + [type("S", (), {"run": staticmethod(lambda ctx: StageResult(ok=True))})] * 3
    mocker.patch.object(runner_mod, "_resolve_stages", return_value=stages)
    mocker.patch.object(runner_mod, "CoderClient")
    mocker.patch.object(runner_mod, "_check_coder_cli")
    mocker.patch("coder_scale_test.cleanup.run", return_value=0)

    assert runner_mod.run(cfg) == 0
    assert [u.username for u in captured_ctx["users_at_provision"]] == ["alice"]
    assert [w.id for w in captured_ctx["workspaces_at_stage_2"]] == ["ws1"]
