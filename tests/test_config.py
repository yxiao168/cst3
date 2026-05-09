"""Tests for config.load(): TOML parsing, env-var token, validation."""
from __future__ import annotations
from pathlib import Path
from textwrap import dedent

import pytest

from coder_scale_test import config as cfg_mod


VALID_TOML = dedent("""
    coder_url = "https://coder.example.com"
    template_name = "ubuntu-base"
    num_users = 10
    per_user = 3
    log_file = "./scale-run.log"

    [timeouts]
    provision_workspace = 300
    ssh_round_trip = 30
    web_terminal_round_trip = 30
    app_traffic_round_trip = 30
    dashboard_ready = 60
    delete_workspace = 120

    [app]
    tcp_port = 7000

    [dashboard]
    ready_selector = "[data-testid='workspaces-table']"
""")


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(body)
    return p


def test_loads_valid_config(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok-abc")
    cfg = cfg_mod.load(_write(tmp_path, VALID_TOML))
    assert cfg.coder_url == "https://coder.example.com"
    assert cfg.template_name == "ubuntu-base"
    assert cfg.num_users == 10
    assert cfg.per_user == 3
    assert cfg.coder_session_token == "tok-abc"
    assert cfg.timeouts.provision_workspace == 300
    assert cfg.app_tcp_port == 7000
    assert cfg.dashboard_ready_selector.startswith("[data-testid")


def test_missing_token_raises(tmp_path, monkeypatch):
    monkeypatch.delenv("CODER_SESSION_TOKEN", raising=False)
    with pytest.raises(cfg_mod.ConfigError, match="CODER_SESSION_TOKEN"):
        cfg_mod.load(_write(tmp_path, VALID_TOML))


def test_rejects_zero_users(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace("num_users = 10", "num_users = 0")
    with pytest.raises(cfg_mod.ConfigError, match="num_users"):
        cfg_mod.load(_write(tmp_path, bad))


def test_rejects_bad_url(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace('"https://coder.example.com"', '"not-a-url"')
    with pytest.raises(cfg_mod.ConfigError, match="coder_url"):
        cfg_mod.load(_write(tmp_path, bad))


def test_rejects_empty_template(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace('"ubuntu-base"', '""')
    with pytest.raises(cfg_mod.ConfigError, match="template_name"):
        cfg_mod.load(_write(tmp_path, bad))


def test_rejects_bad_port(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace("tcp_port = 7000", "tcp_port = 70000")
    with pytest.raises(cfg_mod.ConfigError, match="tcp_port"):
        cfg_mod.load(_write(tmp_path, bad))


def test_rejects_zero_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace("ssh_round_trip = 30", "ssh_round_trip = 0")
    with pytest.raises(cfg_mod.ConfigError, match="ssh_round_trip"):
        cfg_mod.load(_write(tmp_path, bad))


# --- New tests for autoplan-augmented config ---


def test_defaults_when_new_sections_omitted(tmp_path, monkeypatch):
    """[poll], [tls], [retries], [stages] omitted → sensible defaults applied."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    cfg = cfg_mod.load(_write(tmp_path, VALID_TOML))
    assert cfg.users is None              # not set → None
    assert cfg.poll_interval_seconds == 2.0
    assert cfg.tls_verify is True
    assert cfg.retry_count == 3
    assert cfg.retry_backoff_seconds == 1.0
    assert cfg.skip_stages == []


def test_users_allow_list(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    # users must be a top-level key; insert it before the first [section] header
    users_line = 'users = ["alice", "bob", "charlie", "dave", "eve", "frank", "grace", "heidi", "ivan", "judy"]\n'
    body = VALID_TOML.replace("[timeouts]", users_line + "[timeouts]")
    cfg = cfg_mod.load(_write(tmp_path, body))
    assert cfg.users == ["alice", "bob", "charlie", "dave", "eve", "frank", "grace", "heidi", "ivan", "judy"]


def test_users_too_short_raises(tmp_path, monkeypatch):
    """If users list is set but shorter than num_users, fail."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    # users must be a top-level key; 2 < num_users=10
    users_line = 'users = ["alice", "bob"]\n'
    body = VALID_TOML.replace("[timeouts]", users_line + "[timeouts]")
    with pytest.raises(cfg_mod.ConfigError, match="users.*at least"):
        cfg_mod.load(_write(tmp_path, body))


def test_skip_stages_unknown_name_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    body = VALID_TOML + '\n[stages]\nskip = ["does-not-exist"]\n'
    with pytest.raises(cfg_mod.ConfigError, match="skip.*unknown stage"):
        cfg_mod.load(_write(tmp_path, body))


def test_poll_interval_must_be_positive(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    body = VALID_TOML + '\n[poll]\ninterval_seconds = 0\n'
    with pytest.raises(cfg_mod.ConfigError, match="poll.interval_seconds"):
        cfg_mod.load(_write(tmp_path, body))


def test_retry_count_negative_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    body = VALID_TOML + '\n[retries]\ncount = -1\n'
    with pytest.raises(cfg_mod.ConfigError, match="retries.count"):
        cfg_mod.load(_write(tmp_path, body))


# --- Tests for code-quality fixes (offending-value error messages + log dir creation) ---


def test_template_name_error_includes_value(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace('"ubuntu-base"', '""')
    with pytest.raises(cfg_mod.ConfigError, match=r"got ''"):
        cfg_mod.load(_write(tmp_path, bad))


def test_ready_selector_error_includes_value(tmp_path, monkeypatch):
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    bad = VALID_TOML.replace("\"[data-testid='workspaces-table']\"", '""')
    with pytest.raises(cfg_mod.ConfigError, match=r"ready_selector.*got"):
        cfg_mod.load(_write(tmp_path, bad))


def test_log_file_parent_dir_created_if_missing(tmp_path, monkeypatch):
    """Parent directory should be auto-created if it doesn't exist."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    nested_log = tmp_path / "nested" / "deep" / "scale-run.log"
    body = VALID_TOML.replace('"./scale-run.log"', f'"{nested_log}"')
    cfg = cfg_mod.load(_write(tmp_path, body))
    assert nested_log.parent.exists()
    assert cfg.log_file == nested_log


def test_log_file_unwritable_parent_raises(tmp_path, monkeypatch):
    """Parent directory we can't create raises ConfigError."""
    import os
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere; can't test permission failure")
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    # /proc is read-only on Linux; trying to create a subdir there fails
    body = VALID_TOML.replace('"./scale-run.log"', '"/proc/cst3-test/scale-run.log"')
    with pytest.raises(cfg_mod.ConfigError, match="log_file parent directory"):
        cfg_mod.load(_write(tmp_path, body))


def test_invalid_toml_raises_config_error(tmp_path, monkeypatch):
    """Syntactically broken TOML must raise ConfigError, not TOMLDecodeError."""
    monkeypatch.setenv("CODER_SESSION_TOKEN", "tok")
    p = tmp_path / "config.toml"
    p.write_text("unclosed = [string")
    with pytest.raises(cfg_mod.ConfigError, match="not valid TOML"):
        cfg_mod.load(p)
