"""CLI tests: argparse, --version, --validate-config, --stage, --users, etc."""
from __future__ import annotations
import sys
from unittest.mock import MagicMock

import pytest

from coder_scale_test import __main__ as main_mod
from coder_scale_test import __version__


def _good_toml(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text(
        '''
coder_url = "https://coder.example.com"
template_name = "t"
num_users = 2
per_user = 1
log_file = "./scale-run.log"
users = ["alice", "bob"]

[timeouts]
provision_workspace = 10
ssh_round_trip = 10
web_terminal_round_trip = 10
app_traffic_round_trip = 10
dashboard_ready = 10
delete_workspace = 10

[app]
tcp_port = 7000

[dashboard]
ready_selector = "x"
'''
    )
    return p


def test_main_calls_runner_on_config(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    fake_cfg = MagicMock()
    mocker.patch.object(main_mod, "load_config", return_value=fake_cfg)
    run_spy = mocker.patch.object(main_mod, "run", return_value=0)
    monkeypatch.setattr(sys, "argv", ["coder-scale-test", "--config", str(cfg_path)])
    rc = main_mod.main()
    assert rc == 0
    run_spy.assert_called_once()


def test_main_propagates_exit_code(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    mocker.patch.object(main_mod, "load_config", return_value=MagicMock())
    mocker.patch.object(main_mod, "run", return_value=2)
    monkeypatch.setattr(sys, "argv", ["coder-scale-test", "--config", str(cfg_path)])
    assert main_mod.main() == 2


def test_version_flag_prints_and_exits_zero(monkeypatch, capsys, mocker):
    # --version must NOT load config or call runner
    load_spy = mocker.patch.object(main_mod, "load_config")
    run_spy = mocker.patch.object(main_mod, "run")
    monkeypatch.setattr(sys, "argv", ["coder-scale-test", "--version"])
    rc = main_mod.main()
    assert rc == 0
    assert __version__ in capsys.readouterr().out
    load_spy.assert_not_called()
    run_spy.assert_not_called()


def test_validate_config_returns_zero_on_valid(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    run_spy = mocker.patch.object(main_mod, "run")
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(cfg_path), "--validate-config"])
    assert main_mod.main() == 0
    run_spy.assert_not_called()


def test_validate_config_returns_three_on_invalid(tmp_path, monkeypatch, mocker):
    bad = tmp_path / "bad.toml"
    bad.write_text("coder_url = ''\ntemplate_name = 'x'\nnum_users = 1\nper_user = 1\n")
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    run_spy = mocker.patch.object(main_mod, "run")
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(bad), "--validate-config"])
    assert main_mod.main() == 3
    run_spy.assert_not_called()


def test_stage_flag_intersects_with_skip_stages(tmp_path, monkeypatch, mocker):
    """--stage users --stage provision should add the other 4 to skip_stages."""
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    captured = {}
    def _capture_run(cfg, **kwargs):
        captured["cfg"] = cfg
        captured["kwargs"] = kwargs
        return 0
    mocker.patch.object(main_mod, "run", side_effect=_capture_run)
    monkeypatch.setattr(sys, "argv", [
        "coder-scale-test", "--config", str(cfg_path),
        "--stage", "users", "--stage", "provision",
    ])
    main_mod.main()
    skip = sorted(captured["cfg"].skip_stages)
    assert skip == sorted(["ssh", "web_terminal", "app_traffic", "dashboard"])


def test_stage_flag_rejects_unknown_stage(tmp_path, monkeypatch, capsys):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(cfg_path), "--stage", "bogus"])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code != 0


def test_users_flag_overrides_config_users(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    captured = {}
    def _capture_run(cfg, **kwargs):
        captured["cfg"] = cfg
        return 0
    mocker.patch.object(main_mod, "run", side_effect=_capture_run)
    monkeypatch.setattr(sys, "argv", [
        "coder-scale-test", "--config", str(cfg_path),
        "--users", "carol,dave",
    ])
    main_mod.main()
    assert captured["cfg"].users == ["carol", "dave"]


def test_users_flag_rejects_too_few_for_num_users(tmp_path, monkeypatch):
    cfg_path = _good_toml(tmp_path)  # num_users = 2
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "coder-scale-test", "--config", str(cfg_path), "--users", "carol",
    ])
    rc = main_mod.main()
    assert rc == 3


def test_skip_cleanup_passed_to_runner(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    captured = {}
    def _capture_run(cfg, **kwargs):
        captured["kwargs"] = kwargs
        return 0
    mocker.patch.object(main_mod, "run", side_effect=_capture_run)
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(cfg_path), "--skip-cleanup"])
    main_mod.main()
    assert captured["kwargs"].get("skip_cleanup") is True


def test_cleanup_only_passed_to_runner(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    captured = {}
    def _capture_run(cfg, **kwargs):
        captured["kwargs"] = kwargs
        return 0
    mocker.patch.object(main_mod, "run", side_effect=_capture_run)
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(cfg_path), "--cleanup-only"])
    main_mod.main()
    assert captured["kwargs"].get("cleanup_only") is True


def test_quiet_and_verbose_are_mutually_exclusive(tmp_path, monkeypatch):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "coder-scale-test", "--config", str(cfg_path), "--quiet", "--verbose",
    ])
    with pytest.raises(SystemExit):
        main_mod.main()


def test_quiet_alone_accepted(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    mocker.patch.object(main_mod, "run", return_value=0)
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(cfg_path), "--quiet"])
    assert main_mod.main() == 0


def test_verbose_alone_accepted(tmp_path, monkeypatch, mocker):
    cfg_path = _good_toml(tmp_path)
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    mocker.patch.object(main_mod, "run", return_value=0)
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(cfg_path), "--verbose"])
    assert main_mod.main() == 0


def test_missing_config_returns_three(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(tmp_path / "missing.toml")])
    assert main_mod.main() == 3
    err = capsys.readouterr().err
    assert "error" in err.lower() or "missing" in err.lower() or "no such" in err.lower()


def test_invalid_toml_returns_three(tmp_path, monkeypatch, capsys):
    """Syntactically broken TOML must produce rc=3 and 'error' on stderr (not a traceback)."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = tmp_path / "bad.toml"
    bad.write_text("unclosed = [string")
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(bad)])
    rc = main_mod.main()
    assert rc == 3
    err = capsys.readouterr().err
    assert "error" in err.lower()


def test_skip_cleanup_and_cleanup_only_mutex(tmp_path, monkeypatch):
    """--skip-cleanup and --cleanup-only together must be rejected by argparse."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    monkeypatch.setattr(sys, "argv", [
        "coder-scale-test", "--skip-cleanup", "--cleanup-only",
    ])
    with pytest.raises(SystemExit) as exc:
        main_mod.main()
    assert exc.value.code != 0


def test_oserror_from_load_config_returns_three(tmp_path, monkeypatch, mocker, capsys):
    """A PermissionError from load_config must produce rc=3 and 'error' on stderr."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    mocker.patch.object(main_mod, "load_config",
                        side_effect=PermissionError("permission denied"))
    monkeypatch.setattr(sys, "argv",
                        ["coder-scale-test", "--config", str(tmp_path / "config.toml")])
    rc = main_mod.main()
    assert rc == 3
    err = capsys.readouterr().err
    assert "error" in err.lower()
