"""Config loader for coder-scale-test.

Reads ./config.toml (or path passed to load()), reads CODER_SESSION_TOKEN
from the environment (NEVER from the TOML), validates, and returns a
frozen Config dataclass. Validation errors raise ConfigError with a
human-readable message.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


VALID_STAGES = {"users", "provision", "ssh", "web_terminal", "app_traffic", "dashboard"}


class ConfigError(ValueError):
    """Raised when config.toml or its environment is invalid."""


@dataclass(frozen=True)
class Timeouts:
    provision_workspace: int
    ssh_round_trip: int
    web_terminal_round_trip: int
    app_traffic_round_trip: int
    dashboard_ready: int
    delete_workspace: int


@dataclass(frozen=True)
class Config:
    coder_url: str
    template_name: str
    num_users: int
    per_user: int
    log_file: Path
    timeouts: Timeouts
    dashboard_ready_selector: str
    app_tcp_port: int
    coder_session_token: str
    users: list[str] | None
    poll_interval_seconds: float
    tls_verify: bool
    retry_count: int
    retry_backoff_seconds: float
    skip_stages: list[str]


def load(path: Path) -> Config:
    """Load and validate config from a TOML file. Raises ConfigError on any problem."""
    with path.open("rb") as fh:
        try:
            raw = tomllib.load(fh)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f"config.toml is not valid TOML: {e}") from e

    # --- env token (NEVER from TOML) ---
    token = os.environ.get("CODER_SESSION_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "CODER_SESSION_TOKEN env var is required (the tool never reads "
            "the token from config.toml)"
        )

    # --- coder_url ---
    coder_url = str(raw.get("coder_url", "")).strip()
    parsed = urlparse(coder_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"coder_url must be a valid http(s) URL, got {coder_url!r}")

    # --- template_name ---
    template_name = str(raw.get("template_name", "")).strip()
    if not template_name:
        raise ConfigError(f"template_name must be a non-empty string, got {raw.get('template_name')!r}")

    # --- num_users / per_user ---
    num_users = int(raw.get("num_users", 0))
    per_user = int(raw.get("per_user", 0))
    if num_users < 1:
        raise ConfigError(f"num_users must be >= 1, got {num_users}")
    if per_user < 1:
        raise ConfigError(f"per_user must be >= 1, got {per_user}")

    # --- log_file ---
    log_file = Path(str(raw.get("log_file", "./scale-run.log"))).expanduser()
    log_dir = log_file.parent
    if not log_dir.exists():
        # Try to create it; if that fails (e.g., permissions), surface as ConfigError now
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise ConfigError(
                f"log_file parent directory cannot be created: {log_dir} ({e})"
            ) from e

    # --- timeouts ---
    t = raw.get("timeouts", {})
    timeouts = Timeouts(
        provision_workspace=int(t.get("provision_workspace", 0)),
        ssh_round_trip=int(t.get("ssh_round_trip", 0)),
        web_terminal_round_trip=int(t.get("web_terminal_round_trip", 0)),
        app_traffic_round_trip=int(t.get("app_traffic_round_trip", 0)),
        dashboard_ready=int(t.get("dashboard_ready", 0)),
        delete_workspace=int(t.get("delete_workspace", 0)),
    )
    for name, val in vars(timeouts).items():
        if val <= 0:
            raise ConfigError(f"timeouts.{name} must be > 0, got {val}")

    # --- app.tcp_port ---
    app_tcp_port = int(raw.get("app", {}).get("tcp_port", 0))
    if not (1 <= app_tcp_port <= 65535):
        raise ConfigError(f"app.tcp_port must be in [1, 65535], got {app_tcp_port}")

    # --- dashboard.ready_selector ---
    selector = str(raw.get("dashboard", {}).get("ready_selector", "")).strip()
    if not selector:
        raise ConfigError(
            f"dashboard.ready_selector must be non-empty, "
            f"got {raw.get('dashboard', {}).get('ready_selector')!r}"
        )

    # --- users (optional allow-list, top-level) ---
    users_raw = raw.get("users")
    users: list[str] | None
    if users_raw is None:
        users = None
    else:
        if not isinstance(users_raw, list) or not all(isinstance(u, str) for u in users_raw):
            raise ConfigError(f"users must be a list of strings, got {users_raw!r}")
        if len(users_raw) < num_users:
            raise ConfigError(
                f"users list has {len(users_raw)} entries but num_users={num_users}; "
                "must contain at least num_users usernames"
            )
        users = list(users_raw)

    # --- [poll] ---
    poll_section = raw.get("poll", {})
    poll_interval = float(poll_section.get("interval_seconds", 2.0))
    if poll_interval <= 0:
        raise ConfigError(f"poll.interval_seconds must be > 0, got {poll_interval}")

    # --- [tls] ---
    tls_section = raw.get("tls", {})
    tls_verify_raw = tls_section.get("verify", True)
    if not isinstance(tls_verify_raw, bool):
        raise ConfigError(f"tls.verify must be a boolean, got {tls_verify_raw!r}")
    tls_verify = tls_verify_raw

    # --- [retries] ---
    retries_section = raw.get("retries", {})
    retry_count = int(retries_section.get("count", 3))
    if retry_count < 0:
        raise ConfigError(f"retries.count must be >= 0, got {retry_count}")
    retry_backoff = float(retries_section.get("backoff_seconds", 1.0))
    if retry_backoff <= 0:
        raise ConfigError(f"retries.backoff_seconds must be > 0, got {retry_backoff}")

    # --- [stages] skip ---
    stages_section = raw.get("stages", {})
    skip_raw = stages_section.get("skip", [])
    if not isinstance(skip_raw, list) or not all(isinstance(s, str) for s in skip_raw):
        raise ConfigError("stages.skip must be a list of strings")
    for s in skip_raw:
        if s not in VALID_STAGES:
            raise ConfigError(
                f"stages.skip contains unknown stage {s!r}; "
                f"valid stages: {sorted(VALID_STAGES)}"
            )
    skip_stages = list(skip_raw)

    return Config(
        coder_url=coder_url,
        template_name=template_name,
        num_users=num_users,
        per_user=per_user,
        log_file=log_file,
        timeouts=timeouts,
        dashboard_ready_selector=selector,
        app_tcp_port=app_tcp_port,
        coder_session_token=token,
        users=users,
        poll_interval_seconds=poll_interval,
        tls_verify=tls_verify,
        retry_count=retry_count,
        retry_backoff_seconds=retry_backoff,
        skip_stages=skip_stages,
    )
