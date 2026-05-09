<!-- /autoplan restore point: /home/yxiao/.gstack/projects/cst3/main-autoplan-restore-20260508-092757.md -->
# coder-scale-testing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python load-testing tool that exercises a Coder cluster across six serial stages (users, provisioning, SSH echo, web-terminal PTY, app traffic, dashboard) using an admin session token, with fail-fast semantics and guaranteed cleanup of all created workspaces.

**Architecture:** Small Python package, stage-per-module. Sequential, sync code (no asyncio). `CoderClient` wraps Coder REST + WebSocket calls; each stage takes a `StageContext` and returns a `StageResult`. `runner.run()` orchestrates fail-fast and ensures cleanup runs even on Ctrl-C. Tests use `pytest` with mocked Coder, WebSocket, subprocess, and Playwright surfaces — total suite under 5 seconds, no real network.

**Tech Stack:** Python 3.11+, `requests`, `websocket-client`, `playwright` (sync), `tomllib` (stdlib). `coder` CLI on `PATH` for stage 2. Build/dep management via `uv` and `pyproject.toml`. Tests via `pytest` + `pytest-mock`.

**Spec:** Implements [docs/superpowers/specs/2026-05-08-coder-scale-testing-design.md](../specs/2026-05-08-coder-scale-testing-design.md). All 15 design decisions are reflected in the tasks below.

---

## Plan structure

| Phase | Tasks | Outcome |
|-------|-------|---------|
| 1. Foundations | 1–5 | Package skeleton, `config`, `log`, `cleanup`, `coder_client` — all unit-tested. |
| 1.5 Spike | 6 | One-page validation report on stage 4's WebSocket URL pattern against a real cluster. Retires the highest-risk unknown before stage 4 is built. |
| 2. Runner + stage 0 | 7–8 | Orchestrator with SIGINT handler and exit codes; stage 0 lists users. |
| 3. Stages 1–2 | 9–10 | Provisioning and SSH echo. |
| 4. Stages 3–4 | 11–12 | Web terminal and app traffic. |
| 5. Stage 5 | 13 | Playwright dashboard. |
| 6. CLI + docs | 14–15 | `__main__.py`, `config.toml.example`, operator README updates. |

---

## File structure (locked in by this plan)

```
coder-scale-testing/
├── pyproject.toml
├── config.toml.example
├── .gitignore                  # adds config.toml, scale-run.log, .venv/, __pycache__/
├── docs/
│   ├── README.md
│   ├── superpowers/specs/2026-05-08-coder-scale-testing-design.md
│   └── superpowers/plans/2026-05-08-coder-scale-testing.md   # this file
├── src/coder_scale_test/
│   ├── __init__.py             # empty
│   ├── __main__.py             # CLI entry — Task 14
│   ├── config.py               # Task 2
│   ├── coder_client.py         # Task 5
│   ├── log.py                  # Task 3
│   ├── cleanup.py              # Task 4
│   ├── runner.py               # Task 7
│   └── stages/
│       ├── __init__.py
│       ├── users.py            # Task 8
│       ├── provision.py        # Task 9
│       ├── ssh.py              # Task 10
│       ├── web_terminal.py     # Task 11
│       ├── app_traffic.py      # Task 12
│       └── dashboard.py        # Task 13
└── tests/
    ├── conftest.py
    ├── test_config.py
    ├── test_log.py
    ├── test_cleanup.py
    ├── test_coder_client.py
    ├── test_runner.py
    └── stages/
        ├── __init__.py
        ├── test_users.py
        ├── test_provision.py
        ├── test_ssh.py
        ├── test_web_terminal.py
        ├── test_app_traffic.py
        └── test_dashboard.py
```

Each `Files:` block in the tasks below references this layout exactly.

---

# Phase 1 — Foundations

## Task 1: Project scaffolding

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `src/coder_scale_test/__init__.py`
- Create: `src/coder_scale_test/stages/__init__.py`
- Create: `tests/__init__.py`
- Create: `tests/stages/__init__.py`
- Create: `tests/conftest.py`

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "coder-scale-test"
version = "0.1.0"
description = "Serial load-testing tool for Coder clusters."
requires-python = ">=3.11"
dependencies = [
    "requests>=2.32",
    "websocket-client>=1.8",
    "playwright>=1.45",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-mock>=3.12",
]

[project.scripts]
coder-scale-test = "coder_scale_test.__main__:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/coder_scale_test"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q --strict-markers"
```

- [ ] **Step 2: Write `.gitignore`**

```
# Python
__pycache__/
*.pyc
.venv/
.pytest_cache/
*.egg-info/
build/
dist/

# Project
config.toml
scale-run.log
*.log
```

- [ ] **Step 3: Create empty package init files**

`src/coder_scale_test/__init__.py`:
```python
"""Serial load-testing tool for Coder clusters."""
__version__ = "0.1.0"
```

`src/coder_scale_test/stages/__init__.py`:
```python
"""One module per load-testing stage."""
```

`tests/__init__.py` and `tests/stages/__init__.py`: empty files.

- [ ] **Step 4: Write the shared `tests/conftest.py`**

```python
"""Shared pytest fixtures.

CoderClient is mocked everywhere so tests never touch the network. The
tmp_log fixture returns an open writeable file plus its path for assertions.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def coder_client() -> MagicMock:
    """A MagicMock that pretends to be a CoderClient.

    Tests configure return values per case: e.g. coder_client.list_users.return_value = [...]
    """
    return MagicMock(name="CoderClient")


@pytest.fixture
def tmp_log(tmp_path: Path) -> tuple[Path, "object"]:
    """Open a tmp log file in write mode; return (path, file)."""
    path = tmp_path / "scale-run.log"
    fh = path.open("w", encoding="utf-8")
    yield path, fh
    fh.close()
```

- [ ] **Step 5: Verify the package installs and pytest runs (no tests yet)**

Run:
```bash
uv venv
uv pip install -e ".[dev]"
uv run pytest
```
Expected:
- `uv pip install -e` succeeds.
- `pytest` exits 5 (no tests collected) or 0; no errors.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml .gitignore src/coder_scale_test/__init__.py \
        src/coder_scale_test/stages/__init__.py tests/__init__.py \
        tests/stages/__init__.py tests/conftest.py
git commit -m "feat: scaffold coder-scale-test package and pytest harness"
```

---

## Task 2: `config` module

**Files:**
- Create: `src/coder_scale_test/config.py`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test file**

`tests/test_config.py`:
```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: All 7 tests FAIL with `ModuleNotFoundError: No module named 'coder_scale_test.config'` or similar.

- [ ] **Step 3: Write the minimal implementation**

`src/coder_scale_test/config.py`:
```python
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
    coder_session_token: str  # from env, NEVER from TOML


def load(path: Path) -> Config:
    """Load and validate config from a TOML file. Raises ConfigError on any problem."""
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    token = os.environ.get("CODER_SESSION_TOKEN", "").strip()
    if not token:
        raise ConfigError(
            "CODER_SESSION_TOKEN env var is required (the tool never reads "
            "the token from config.toml)"
        )

    coder_url = str(raw.get("coder_url", "")).strip()
    parsed = urlparse(coder_url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ConfigError(f"coder_url must be a valid http(s) URL, got {coder_url!r}")

    template_name = str(raw.get("template_name", "")).strip()
    if not template_name:
        raise ConfigError("template_name must be a non-empty string")

    num_users = int(raw.get("num_users", 0))
    per_user = int(raw.get("per_user", 0))
    if num_users < 1:
        raise ConfigError(f"num_users must be >= 1, got {num_users}")
    if per_user < 1:
        raise ConfigError(f"per_user must be >= 1, got {per_user}")

    log_file = Path(str(raw.get("log_file", "./scale-run.log"))).expanduser()

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

    app_tcp_port = int(raw.get("app", {}).get("tcp_port", 0))
    if not (1 <= app_tcp_port <= 65535):
        raise ConfigError(f"app.tcp_port must be in [1, 65535], got {app_tcp_port}")

    selector = str(raw.get("dashboard", {}).get("ready_selector", "")).strip()
    if not selector:
        raise ConfigError("dashboard.ready_selector must be non-empty")

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
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/config.py tests/test_config.py
git commit -m "feat(config): TOML loader with env-var token and validation"
```

---

## Task 3: `log` module

**Files:**
- Create: `src/coder_scale_test/log.py`
- Test: `tests/test_log.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_log.py`:
```python
"""Tests for log_op format, structural events, error truncation, flush."""
from __future__ import annotations
import re

from coder_scale_test import log as log_mod


def test_log_op_ok_format(tmp_log):
    path, fh = tmp_log
    log_mod.log_op(fh, stage="ssh", op="alice/ws-0", ok=True, elapsed_ms=123)
    fh.flush()
    line = path.read_text().strip()
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z OK    stage=ssh op=alice/ws-0 elapsed_ms=123$",
        line,
    ), f"unexpected line: {line!r}"


def test_log_op_fail_format_with_err(tmp_log):
    path, fh = tmp_log
    log_mod.log_op(fh, stage="ssh", op="bob/ws-1", ok=False, elapsed_ms=30041,
                   err="timeout: coder ssh exit=124")
    fh.flush()
    line = path.read_text().strip()
    assert " FAIL  stage=ssh op=bob/ws-1 elapsed_ms=30041" in line
    assert 'err="timeout: coder ssh exit=124"' in line


def test_log_op_truncates_long_err(tmp_log):
    path, fh = tmp_log
    big = "x" * 1000
    log_mod.log_op(fh, stage="x", op="y", ok=False, elapsed_ms=1, err=big)
    fh.flush()
    line = path.read_text()
    # "x" should appear at most 200 times in the err= field
    err_field = re.search(r'err="([^"]*)"', line).group(1)
    assert len(err_field) == 200


def test_log_op_flushes_per_line(tmp_log, mocker):
    path, fh = tmp_log
    flush_spy = mocker.spy(fh, "flush")
    log_mod.log_op(fh, stage="x", op="y", ok=True, elapsed_ms=1)
    assert flush_spy.call_count == 1


def test_log_event_format(tmp_log):
    path, fh = tmp_log
    log_mod.log_event(fh, "RUN_START", num_users=10, per_user=3, total=30,
                      template="ubuntu-base")
    fh.flush()
    line = path.read_text().strip()
    assert " RUN_START " in line
    assert "num_users=10" in line
    assert "per_user=3" in line
    assert "total=30" in line
    assert "template=ubuntu-base" in line


def test_log_event_array_value(tmp_log):
    path, fh = tmp_log
    log_mod.log_event(fh, "RUN_END", ok=False,
                      skipped_stages=["web_terminal", "app_traffic", "dashboard"])
    fh.flush()
    line = path.read_text().strip()
    assert "skipped_stages=[web_terminal,app_traffic,dashboard]" in line
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_log.py -v`
Expected: All 6 tests FAIL with `ModuleNotFoundError: No module named 'coder_scale_test.log'`.

- [ ] **Step 3: Write the minimal implementation**

`src/coder_scale_test/log.py`:
```python
"""Log file writer.

One line per op (`OK`/`FAIL`) and one line per structural event
(`RUN_START`, `STAGE_START`, etc). Always flushed immediately so a
SIGINT cannot lose the line that explains why the run died.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

ERR_MAX = 200


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_log(path: Path) -> IO[str]:
    """Open the log file for append-and-flush, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def log_op(log_file: IO[str], stage: str, op: str, ok: bool,
           elapsed_ms: int, err: str | None = None) -> None:
    """Write a single per-op line to the log and flush immediately."""
    status = "OK   " if ok else "FAIL "
    err_part = ""
    if err:
        truncated = err[:ERR_MAX]
        err_part = f' err="{truncated}"'
    log_file.write(
        f"{utc_now_iso()} {status} stage={stage} op={op} "
        f"elapsed_ms={elapsed_ms}{err_part}\n"
    )
    log_file.flush()


def log_event(log_file: IO[str], event: str, **fields: Any) -> None:
    """Write a structural event (RUN_START, STAGE_START, etc.). Flushes."""
    parts = []
    for k, v in fields.items():
        parts.append(f"{k}={_render(v)}")
    suffix = (" " + " ".join(parts)) if parts else ""
    log_file.write(f"{utc_now_iso()} {event}{suffix}\n")
    log_file.flush()


def _render(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ",".join(str(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_log.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/log.py tests/test_log.py
git commit -m "feat(log): per-op and structural-event log helpers, flush per line"
```

---

## Task 4: `cleanup` module

**Files:**
- Create: `src/coder_scale_test/cleanup.py`
- Test: `tests/test_cleanup.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cleanup.py`:
```python
"""Tests for WorkspaceLedger and cleanup.run()."""
from __future__ import annotations
from unittest.mock import MagicMock

import pytest

from coder_scale_test.cleanup import WorkspaceLedger, run as cleanup_run


def test_ledger_add_and_all():
    led = WorkspaceLedger()
    led.add("ws-1")
    led.add("ws-2")
    assert led.all() == ["ws-1", "ws-2"]


def test_ledger_all_returns_copy():
    led = WorkspaceLedger()
    led.add("ws-1")
    out = led.all()
    out.append("mutate")
    assert led.all() == ["ws-1"]  # not affected by external mutation


def test_cleanup_iterates_and_succeeds(tmp_log, coder_client, mocker):
    path, fh = tmp_log
    led = WorkspaceLedger()
    led.add("ws-a"); led.add("ws-b"); led.add("ws-c")
    cfg = mocker.Mock(timeouts=mocker.Mock(delete_workspace=120))
    ctx = mocker.Mock(cfg=cfg, client=coder_client, ledger=led, log=fh)

    coder_client.delete_workspace = MagicMock()  # success
    failed = cleanup_run(ctx)
    assert failed == 0
    assert coder_client.delete_workspace.call_count == 3
    assert "CLEANUP_START total=3" in path.read_text()
    assert "CLEANUP_END deleted=3 failed=0" in path.read_text()


def test_cleanup_continues_past_per_workspace_failure(tmp_log, coder_client, mocker):
    path, fh = tmp_log
    led = WorkspaceLedger()
    led.add("ws-a"); led.add("ws-b"); led.add("ws-c")
    cfg = mocker.Mock(timeouts=mocker.Mock(delete_workspace=120))
    ctx = mocker.Mock(cfg=cfg, client=coder_client, ledger=led, log=fh)

    coder_client.delete_workspace = MagicMock(
        side_effect=[None, RuntimeError("boom"), None]
    )
    failed = cleanup_run(ctx)
    assert failed == 1
    # All three were attempted
    assert coder_client.delete_workspace.call_count == 3
    assert "CLEANUP_END deleted=2 failed=1" in path.read_text()


def test_cleanup_with_empty_ledger(tmp_log, coder_client, mocker):
    path, fh = tmp_log
    led = WorkspaceLedger()
    cfg = mocker.Mock(timeouts=mocker.Mock(delete_workspace=120))
    ctx = mocker.Mock(cfg=cfg, client=coder_client, ledger=led, log=fh)
    failed = cleanup_run(ctx)
    assert failed == 0
    assert "CLEANUP_START total=0" in path.read_text()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cleanup.py -v`
Expected: All 4 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the minimal implementation**

`src/coder_scale_test/cleanup.py`:
```python
"""Workspace ledger and cleanup loop.

WorkspaceLedger tracks every workspace ID created during the run.
cleanup.run() iterates the ledger and asks the CoderClient to delete each
workspace, logging the outcome. Cleanup never aborts on per-workspace
failure — its job is to free as many resources as possible.
"""
from __future__ import annotations

import signal
import sys
import time
from typing import TYPE_CHECKING

from coder_scale_test.log import log_event, log_op

if TYPE_CHECKING:
    from coder_scale_test.runner import StageContext


class WorkspaceLedger:
    def __init__(self) -> None:
        self._ids: list[str] = []

    def add(self, workspace_id: str) -> None:
        self._ids.append(workspace_id)

    def all(self) -> list[str]:
        return list(self._ids)


def run(ctx: "StageContext") -> int:
    """Delete every workspace in the ledger. Returns count of failed deletes."""
    log_event(ctx.log, "CLEANUP_START", total=len(ctx.ledger.all()))
    install_cleanup_sigint_handler()
    failed = 0
    started = time.monotonic()
    for ws_id in ctx.ledger.all():
        op_started = time.monotonic()
        try:
            ctx.client.delete_workspace(
                ws_id, timeout=ctx.cfg.timeouts.delete_workspace
            )
            elapsed_ms = int((time.monotonic() - op_started) * 1000)
            log_op(ctx.log, "cleanup", ws_id, ok=True, elapsed_ms=elapsed_ms)
        except Exception as e:  # noqa: BLE001 — cleanup never aborts on per-op failure
            elapsed_ms = int((time.monotonic() - op_started) * 1000)
            failed += 1
            log_op(ctx.log, "cleanup", ws_id, ok=False, elapsed_ms=elapsed_ms,
                   err=_categorize(e))
    elapsed_total_ms = int((time.monotonic() - started) * 1000)
    log_event(ctx.log, "CLEANUP_END",
              deleted=len(ctx.ledger.all()) - failed,
              failed=failed,
              elapsed_ms=elapsed_total_ms)
    return failed


_int_count = 0


def install_cleanup_sigint_handler() -> None:
    """First Ctrl-C during cleanup warns. Second Ctrl-C aborts."""
    global _int_count
    _int_count = 0

    def _handler(signum, frame):  # noqa: ARG001
        global _int_count
        _int_count += 1
        if _int_count == 1:
            sys.stderr.write(
                "\nCleanup in progress. Press Ctrl-C again to abort and "
                "leak workspaces.\n"
            )
        else:
            raise KeyboardInterrupt

    signal.signal(signal.SIGINT, _handler)


def _categorize(exc: BaseException) -> str:
    """Convert an exception to a single short err= string."""
    return f"{type(exc).__name__}: {exc}"[:200]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cleanup.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/cleanup.py tests/test_cleanup.py
git commit -m "feat(cleanup): WorkspaceLedger and cleanup loop with two-stage SIGINT"
```

---

## Task 5: `coder_client` skeleton

**Files:**
- Create: `src/coder_scale_test/coder_client.py`
- Test: `tests/test_coder_client.py`

This task implements the REST surface that stages 0, 1, and cleanup need. WebSocket bits are added later in stages 3 and 4 (each stage uses `websocket-client` directly because the WS protocols differ enough that a unified abstraction would be premature).

- [ ] **Step 1: Write the failing tests**

`tests/test_coder_client.py`:
```python
"""Tests for CoderClient REST methods (mocked HTTP via requests-mock)."""
from __future__ import annotations
import json

import pytest
import requests

from coder_scale_test.coder_client import CoderClient, User, Workspace, CoderApiError


@pytest.fixture
def client():
    return CoderClient("https://coder.example.com", "tok-abc")


def test_list_users_filters_admins(client, mocker):
    payload = {"users": [
        {"id": "u1", "username": "alice", "roles": [{"name": "member"}],
         "created_at": "2024-01-01T00:00:00Z", "status": "active"},
        {"id": "u2", "username": "boss", "roles": [{"name": "owner"}],
         "created_at": "2024-01-02T00:00:00Z", "status": "active"},
        {"id": "u3", "username": "bob", "roles": [{"name": "member"}],
         "created_at": "2024-01-03T00:00:00Z", "status": "active"},
        {"id": "u4", "username": "charlie", "roles": [{"name": "admin"}],
         "created_at": "2024-01-04T00:00:00Z", "status": "active"},
    ]}
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = payload

    users = client.list_active_non_admin_users(limit=100)
    assert [u.username for u in users] == ["alice", "bob"]
    assert all(u.created_at for u in users)


def test_list_users_sends_auth_header(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"users": []}
    client.list_active_non_admin_users(limit=10)
    args, kwargs = mock_get.call_args
    assert kwargs["headers"]["Coder-Session-Token"] == "tok-abc"


def test_list_users_raises_on_5xx(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 503
    mock_get.return_value.text = "service unavailable"
    with pytest.raises(CoderApiError, match="status=503"):
        client.list_active_non_admin_users(limit=10)


def test_create_workspace_returns_workspace(client, mocker):
    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 201
    mock_post.return_value.json.return_value = {
        "id": "ws-1", "name": "scaletest-alice-0",
        "owner_name": "alice",
        "latest_build": {"job": {"status": "pending"}, "transition": "start"},
    }
    ws = client.create_workspace(user_id="u1", name="scaletest-alice-0",
                                 template_name="ubuntu-base")
    assert ws.id == "ws-1"
    assert ws.name == "scaletest-alice-0"
    assert ws.owner_name == "alice"


def test_get_workspace(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "scaletest-alice-0",
        "owner_name": "alice",
        "latest_build": {"job": {"status": "succeeded"}, "transition": "start"},
    }
    ws = client.get_workspace("ws-1")
    assert ws.latest_build_status == "succeeded"
    assert ws.latest_build_transition == "start"


def test_delete_workspace_polls_until_succeeded(client, mocker):
    # POST returns the new build
    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "id": "build-9", "transition": "delete",
        "job": {"status": "pending"},
    }
    # Subsequent GETs of the build show pending → running → succeeded
    statuses = iter(["pending", "running", "succeeded"])
    mock_get = mocker.patch.object(client._sess, "get")
    def _get_resp(*args, **kwargs):
        r = mocker.Mock()
        r.status_code = 200
        r.json.return_value = {"id": "build-9", "job": {"status": next(statuses)}}
        return r
    mock_get.side_effect = _get_resp

    client.delete_workspace("ws-1", timeout=5)
    assert mock_post.call_count == 1
    assert mock_get.call_count == 3


def test_delete_workspace_times_out(client, mocker):
    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "id": "build-9", "transition": "delete", "job": {"status": "pending"},
    }
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "build-9", "job": {"status": "pending"},
    }
    # Force the time monotonic clock forward fast so timeout trips
    times = iter([0, 0.1, 1, 2, 6, 7])
    mocker.patch("coder_scale_test.coder_client.time.monotonic",
                 side_effect=lambda: next(times))
    with pytest.raises(CoderApiError, match="timeout"):
        client.delete_workspace("ws-1", timeout=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_coder_client.py -v`
Expected: All 7 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/coder_client.py`:
```python
"""HTTP client for the subset of the Coder REST API this tool uses.

The client exposes only what the stages call. WebSocket transports are
opened by individual stages with `websocket-client` directly because the
PTY (stage 3) and app-proxy (stage 4) protocols differ enough that a
shared abstraction would be premature.

API reference: https://coder.com/docs/reference/api
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests

POLL_INTERVAL_S = 2.0


class CoderApiError(RuntimeError):
    """Raised on any Coder REST error: 4xx, 5xx, or operation timeout."""


@dataclass(frozen=True)
class User:
    id: str
    username: str
    created_at: str  # ISO-8601 from the API


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    owner_name: str
    latest_build_status: str   # "pending" | "running" | "succeeded" | "failed" | "canceled"
    latest_build_transition: str  # "start" | "stop" | "delete"


class CoderClient:
    def __init__(self, coder_url: str, session_token: str) -> None:
        self.coder_url = coder_url.rstrip("/")
        self.session_token = session_token
        self._sess = requests.Session()
        self._sess.headers.update({"Coder-Session-Token": session_token})

    # ---------- Users ----------

    def list_active_non_admin_users(self, limit: int = 200) -> list[User]:
        """Return active users whose roles array contains neither owner nor admin,
        sorted by created_at ascending. Stage 0 takes the first N from this list."""
        url = f"{self.coder_url}/api/v2/users"
        params = {"status": "active", "limit": limit}
        r = self._sess.get(url, params=params, headers={"Coder-Session-Token": self.session_token})
        if r.status_code >= 400:
            raise CoderApiError(f"list_users: status={r.status_code} body={r.text[:200]!r}")
        body = r.json()
        out: list[User] = []
        for u in body.get("users", []):
            roles = {role.get("name") for role in u.get("roles", [])}
            if roles & {"owner", "admin"}:
                continue
            out.append(User(id=u["id"], username=u["username"],
                            created_at=u["created_at"]))
        out.sort(key=lambda u: u.created_at)
        return out

    # ---------- Workspaces ----------

    def create_workspace(self, *, user_id: str, name: str, template_name: str) -> Workspace:
        """Create a workspace for `user_id` from a template referenced by name.

        Resolves template_name → template_id via /templates lookup, then issues
        POST /users/{user_id}/workspaces.
        """
        template_id = self._resolve_template_id(template_name)
        url = f"{self.coder_url}/api/v2/users/{user_id}/workspaces"
        body = {"template_id": template_id, "name": name}
        r = self._sess.post(url, json=body)
        if r.status_code >= 400:
            raise CoderApiError(
                f"create_workspace: status={r.status_code} body={r.text[:200]!r}"
            )
        return _ws_from_json(r.json())

    def get_workspace(self, ws_id: str) -> Workspace:
        url = f"{self.coder_url}/api/v2/workspaces/{ws_id}"
        r = self._sess.get(url)
        if r.status_code >= 400:
            raise CoderApiError(f"get_workspace: status={r.status_code}")
        return _ws_from_json(r.json())

    def wait_for_running(self, ws_id: str, timeout: int) -> Workspace:
        """Poll get_workspace until latest_build is succeeded+start. Raise on timeout."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ws = self.get_workspace(ws_id)
            if (ws.latest_build_status == "succeeded"
                    and ws.latest_build_transition == "start"):
                return ws
            if ws.latest_build_status == "failed":
                raise CoderApiError(f"wait_for_running: build failed for {ws_id}")
            time.sleep(POLL_INTERVAL_S)
        raise CoderApiError(f"wait_for_running: timeout after {timeout}s for {ws_id}")

    def delete_workspace(self, ws_id: str, timeout: int) -> None:
        """Issue a delete-transition build and poll until it succeeds or times out."""
        url = f"{self.coder_url}/api/v2/workspaces/{ws_id}/builds"
        r = self._sess.post(url, json={"transition": "delete"})
        if r.status_code >= 400:
            raise CoderApiError(
                f"delete_workspace POST: status={r.status_code} body={r.text[:200]!r}"
            )
        build_id = r.json()["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            br = self._sess.get(f"{self.coder_url}/api/v2/workspacebuilds/{build_id}")
            if br.status_code >= 400:
                raise CoderApiError(
                    f"delete_workspace GET build: status={br.status_code}"
                )
            status = br.json().get("job", {}).get("status")
            if status == "succeeded":
                return
            if status == "failed":
                raise CoderApiError(f"delete_workspace: build failed for {ws_id}")
            time.sleep(POLL_INTERVAL_S)
        raise CoderApiError(f"delete_workspace: timeout after {timeout}s for {ws_id}")

    # ---------- Agents ----------

    def get_agent_id(self, ws_id: str) -> str:
        """Return the first agent ID for the workspace's running build."""
        url = f"{self.coder_url}/api/v2/workspaces/{ws_id}"
        r = self._sess.get(url)
        if r.status_code >= 400:
            raise CoderApiError(f"get_agent_id: status={r.status_code}")
        ws = r.json()
        for resource in ws.get("latest_build", {}).get("resources", []):
            for agent in resource.get("agents", []):
                return agent["id"]
        raise CoderApiError(f"get_agent_id: no agent found for {ws_id}")

    # ---------- Internal ----------

    def _resolve_template_id(self, name: str) -> str:
        """Look up a template by name and return its ID."""
        # Coder's /templates lists templates the caller can see across orgs
        url = f"{self.coder_url}/api/v2/templates"
        r = self._sess.get(url)
        if r.status_code >= 400:
            raise CoderApiError(f"list_templates: status={r.status_code}")
        for t in r.json():
            if t.get("name") == name:
                return t["id"]
        raise CoderApiError(f"template_not_found: {name!r}")


def _ws_from_json(body: dict[str, Any]) -> Workspace:
    lb = body.get("latest_build", {}) or {}
    return Workspace(
        id=body["id"],
        name=body["name"],
        owner_name=body["owner_name"],
        latest_build_status=lb.get("job", {}).get("status", ""),
        latest_build_transition=lb.get("transition", ""),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_coder_client.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/coder_client.py tests/test_coder_client.py
git commit -m "feat(coder_client): REST surface for users, workspaces, agents"
```

---

## Task 6: Phase 1 spike — validate stage 4 WebSocket URL pattern

**Files:**
- Create: `docs/superpowers/specs/2026-05-08-spike-stage-4-app-traffic.md`

This task is a **manual investigation against a real Coder cluster** — it's the highest-risk unknown identified in the design (decision #14). The output is a one-page report appended to the spec, NOT yet stage 4 code. Stage 4 is implemented in Task 12 once this spike retires the URL-pattern uncertainty.

If you don't have access to a real Coder cluster, skip this task with a note in the file ("spike deferred — implementer will resolve URL pattern during Task 12 with cluster access") and proceed; Task 12 then absorbs the spike work with one extra TDD round-trip.

- [ ] **Step 1: Identify the cluster and template**

Confirm with the operator: which Coder URL, which template that exposes a tcp-echo app, what the app's `display_name`/`slug` is, and what container port the app listens on. Record these.

- [ ] **Step 2: Capture a successful `coder` CLI session as ground truth**

```bash
export CODER_URL=https://coder.example.com
export CODER_SESSION_TOKEN=<token>
coder ssh <workspace> -- nc -l 7000 &     # in workspace, listen on 7000
coder port-forward <workspace> --tcp 9999:7000 &
echo hello | nc localhost 9999             # confirm echo works via CLI
```
This confirms the tcp-echo app actually responds; if it doesn't, the spike fails for a non-WebSocket reason and we fix the template before proceeding.

- [ ] **Step 3: Try URL pattern A (newer agent-app endpoint)**

```bash
# pseudo-curl: open WS to /api/v2/workspaceagents/<agent-id>/apps/<slug>
python -c "
import os, websocket
url = 'wss://coder.example.com/api/v2/workspaceagents/<AGENT_ID>/apps/tcp-echo'
ws = websocket.create_connection(url, header={'Coder-Session-Token': os.environ['CODER_SESSION_TOKEN']})
ws.send_binary(b'ping')
print('recv:', ws.recv())
ws.close()
"
```

Record: success/failure, response body, HTTP upgrade headers seen.

- [ ] **Step 4: Try URL pattern B (path-based proxy)**

```bash
python -c "
import os, websocket
url = 'wss://coder.example.com/@<USER>/<WORKSPACE>.<AGENT>--tcp-echo/'
ws = websocket.create_connection(url, header={'Coder-Session-Token': os.environ['CODER_SESSION_TOKEN']})
ws.send_binary(b'ping')
print('recv:', ws.recv())
ws.close()
"
```

Record: success/failure.

- [ ] **Step 5: If neither A nor B works, try wrapping with `coder port-forward` shell-out**

```bash
python -c "
import socket, subprocess, time
p = subprocess.Popen(['coder', 'port-forward', '<workspace>', '--tcp', '9999:7000'])
time.sleep(2)
s = socket.socket(); s.connect(('127.0.0.1', 9999))
s.sendall(b'ping')
print('recv:', s.recv(64))
s.close(); p.terminate()
"
```

If this works, stage 4 takes the shell-out fallback (with a doc note that we accept the second CLI dependency).

- [ ] **Step 6: Write the spike report**

`docs/superpowers/specs/2026-05-08-spike-stage-4-app-traffic.md`:
```markdown
# Spike report — stage 4 app-traffic transport

**Cluster:** <coder url>, Coder version <X.Y.Z>
**Template / app:** <template name> / app slug `tcp-echo` listening on port 7000
**Date:** YYYY-MM-DD

## URL pattern test results

| Pattern | URL | Result | Notes |
|---------|-----|--------|-------|
| A — agent-app | `wss://<host>/api/v2/workspaceagents/{id}/apps/{slug}` | ✅ / ❌ | <one-line> |
| B — path-proxy | `wss://<host>/@user/ws.agent--app/` | ✅ / ❌ | <one-line> |
| Shell-out fallback | `coder port-forward` + raw socket | ✅ / ❌ | <one-line> |

## Decision

Stage 4 will use **<chosen pattern>** because <one-line reason>.

## Implementation notes for Task 12

- Auth header: `Coder-Session-Token: <token>` on the WS handshake.
- Frame type: <text / binary>.
- Open observations / quirks: <e.g. "agent must be running and app must be in `latest_build.resources[*].agents[*].apps`">.
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/specs/2026-05-08-spike-stage-4-app-traffic.md
git commit -m "docs: spike report — stage 4 app-traffic transport choice"
```

---

# Phase 2 — Runner + stage 0

## Task 7: `runner` orchestration

**Files:**
- Create: `src/coder_scale_test/runner.py`
- Test: `tests/test_runner.py`

`runner.run(cfg)` orchestrates stages, installs the SIGINT handler, ensures cleanup runs in `finally`, and returns the documented exit codes (0/1/2/3/4). Stages are imported lazily so tests can stub them.

- [ ] **Step 1: Write the failing tests**

`tests/test_runner.py`:
```python
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


def test_cleanup_only_failure_returns_four(cfg, mocker):
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_runner.py -v`
Expected: All 6 tests FAIL with `ModuleNotFoundError: No module named 'coder_scale_test.runner'`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/runner.py`:
```python
"""Runner orchestration: SIGINT, exit codes, fail-fast, ensured cleanup."""
from __future__ import annotations

import shutil
import signal
from dataclasses import dataclass, field
from typing import IO, Any

from coder_scale_test import cleanup as cleanup_mod
from coder_scale_test.coder_client import CoderClient, User, Workspace
from coder_scale_test.config import Config
from coder_scale_test.log import log_event, open_log


@dataclass
class StageResult:
    ok: bool
    err: str | None = None


@dataclass
class StageContext:
    cfg: Config
    client: CoderClient
    ledger: cleanup_mod.WorkspaceLedger
    log: IO[str]
    user_ids: list[User] = field(default_factory=list)
    workspaces: list[Workspace] = field(default_factory=list)


class CoderCliMissing(RuntimeError):
    pass


def run(cfg: Config) -> int:
    """Top-level entry. Returns the documented exit code."""
    log = open_log(cfg.log_file)
    log_event(log, "RUN_START",
              num_users=cfg.num_users, per_user=cfg.per_user,
              total=cfg.num_users * cfg.per_user, template=cfg.template_name)

    # Pre-flight: coder CLI required for stage 2.
    try:
        _check_coder_cli()
    except CoderCliMissing as e:
        log_event(log, "RUN_END", ok=False, reason=f"missing_coder_cli: {e}")
        return 3

    install_sigint_handler()
    client = CoderClient(cfg.coder_url, cfg.coder_session_token)
    ledger = cleanup_mod.WorkspaceLedger()
    ctx = StageContext(cfg=cfg, client=client, ledger=ledger, log=log)

    stages = _resolve_stages()
    exit_code = 0
    skipped: list[str] = []
    try:
        for i, stage in enumerate(stages):
            stage_name = _stage_name(stage)
            log_event(log, "STAGE_START", stage=stage_name)
            result = stage.run(ctx)
            log_event(log, "STAGE_END", stage=stage_name,
                      ok=result.ok)
            if not result.ok:
                exit_code = 1
                skipped = [_stage_name(s) for s in stages[i + 1:]]
                break
        if exit_code == 0:
            log_event(log, "RUN_END", ok=True)
        else:
            log_event(log, "RUN_END", ok=False, skipped_stages=skipped)
    except KeyboardInterrupt:
        exit_code = 2
        log_event(log, "RUN_END", ok=False, reason="sigint")
    except Exception as e:  # noqa: BLE001
        exit_code = 3
        log_event(log, "RUN_END", ok=False,
                  reason=f"internal: {type(e).__name__}: {e}")
    finally:
        failed = cleanup_mod.run(ctx)
        if failed and exit_code == 0:
            exit_code = 4

    return exit_code


def _resolve_stages() -> list[Any]:
    """Lazy import of stage modules; returns them in stage order 0..5.

    Tests monkeypatch this to inject stubs.
    """
    from coder_scale_test.stages import (
        users, provision, ssh, web_terminal, app_traffic, dashboard,
    )
    return [users, provision, ssh, web_terminal, app_traffic, dashboard]


def _stage_name(stage: Any) -> str:
    return getattr(stage, "STAGE_NAME", stage.__name__.split(".")[-1])


def install_sigint_handler() -> None:
    def _on_sigint(signum, frame):  # noqa: ARG001
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _on_sigint)


def _check_coder_cli() -> None:
    if shutil.which("coder") is None:
        raise CoderCliMissing(
            "the `coder` CLI is required on PATH for stage 2 (SSH echo). "
            "Install from https://coder.com/docs/install"
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_runner.py -v`
Expected: All 6 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/runner.py tests/test_runner.py
git commit -m "feat(runner): orchestrator with SIGINT, fail-fast, exit codes"
```

---

## Task 8: Stage 0 — `stages/users.py`

**Files:**
- Create: `src/coder_scale_test/stages/users.py`
- Test: `tests/stages/test_users.py`

- [ ] **Step 1: Write the failing tests**

`tests/stages/test_users.py`:
```python
"""Tests for stage 0: pick first-N non-admin active users by created_at."""
from __future__ import annotations
import pytest

from coder_scale_test.stages import users as stage
from coder_scale_test.coder_client import User
from coder_scale_test.runner import StageContext


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(num_users=2)
    return StageContext(cfg=cfg, client=coder_client, ledger=mocker.Mock(),
                        log=fh, user_ids=[], workspaces=[])


def test_picks_first_n(ctx, coder_client):
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "alice", "2024-01-01T00:00:00Z"),
        User("u2", "bob", "2024-01-02T00:00:00Z"),
        User("u3", "charlie", "2024-01-03T00:00:00Z"),
    ]
    res = stage.run(ctx)
    assert res.ok is True
    assert [u.username for u in ctx.user_ids] == ["alice", "bob"]


def test_fails_when_too_few(ctx, coder_client):
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "alice", "2024-01-01T00:00:00Z"),
    ]
    res = stage.run(ctx)
    assert res.ok is False
    assert "fewer than 2" in res.err.lower() or "need 2" in res.err.lower()


def test_logs_op_outcome(ctx, coder_client, tmp_log):
    path, _ = tmp_log
    coder_client.list_active_non_admin_users.return_value = [
        User("u1", "a", "2024-01-01T00:00:00Z"),
        User("u2", "b", "2024-01-02T00:00:00Z"),
    ]
    stage.run(ctx)
    text = path.read_text()
    assert "OK    stage=users op=list_users" in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/stages/test_users.py -v`
Expected: All 3 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/stages/users.py`:
```python
"""Stage 0: pick first-N active non-admin users sorted by created_at."""
from __future__ import annotations

import time

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "users"


def run(ctx: StageContext) -> StageResult:
    started = time.monotonic()
    try:
        all_users = ctx.client.list_active_non_admin_users(
            limit=max(200, ctx.cfg.num_users * 5)
        )
    except Exception as e:  # noqa: BLE001
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log_op(ctx.log, STAGE_NAME, op="list_users", ok=False,
               elapsed_ms=elapsed_ms, err=f"{type(e).__name__}: {e}")
        return StageResult(ok=False, err=str(e))

    elapsed_ms = int((time.monotonic() - started) * 1000)
    if len(all_users) < ctx.cfg.num_users:
        msg = (f"need {ctx.cfg.num_users} active non-admin users, "
               f"cluster has fewer than {ctx.cfg.num_users} ({len(all_users)})")
        log_op(ctx.log, STAGE_NAME, op="list_users", ok=False,
               elapsed_ms=elapsed_ms, err=msg)
        return StageResult(ok=False, err=msg)

    ctx.user_ids = all_users[: ctx.cfg.num_users]
    log_op(ctx.log, STAGE_NAME, op="list_users", ok=True, elapsed_ms=elapsed_ms)
    return StageResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/stages/test_users.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/stages/users.py tests/stages/test_users.py
git commit -m "feat(stage:users): pick first-N active non-admin users by created_at"
```

---

# Phase 3 — Stages 1 & 2

## Task 9: Stage 1 — `stages/provision.py`

**Files:**
- Create: `src/coder_scale_test/stages/provision.py`
- Test: `tests/stages/test_provision.py`

- [ ] **Step 1: Write the failing tests**

`tests/stages/test_provision.py`:
```python
"""Tests for stage 1: create N×M workspaces, register in ledger, wait for running."""
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
        user_ids=[
            User("u1", "alice", "2024-01-01T00:00:00Z"),
            User("u2", "bob", "2024-01-02T00:00:00Z"),
        ],
        workspaces=[],
    )


def test_creates_n_times_m_and_registers_in_ledger(ctx, coder_client):
    coder_client.create_workspace.side_effect = [_ws(i) for i in range(4)]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(int(ws_id.split("-")[1]))
    res = stage.run(ctx)
    assert res.ok is True
    assert coder_client.create_workspace.call_count == 4
    assert sorted(ctx.ledger.all()) == [f"ws-{i}" for i in range(4)]
    assert len(ctx.workspaces) == 4


def test_uses_correct_workspace_name_pattern(ctx, coder_client):
    coder_client.create_workspace.side_effect = [_ws(i) for i in range(4)]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(int(ws_id.split("-")[1]))
    stage.run(ctx)
    names = [c.kwargs["name"] for c in coder_client.create_workspace.call_args_list]
    assert names == [
        "scaletest-alice-0", "scaletest-alice-1",
        "scaletest-bob-0", "scaletest-bob-1",
    ]


def test_fail_fast_on_create_error(ctx, coder_client):
    # First create succeeds, second fails
    coder_client.create_workspace.side_effect = [
        _ws(0), CoderApiError("status=409 already exists"),
    ]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(0)
    res = stage.run(ctx)
    assert res.ok is False
    # ws-0 was registered before failure so cleanup will delete it
    assert ctx.ledger.all() == ["ws-0"]


def test_partial_create_still_registers_successes(ctx, coder_client):
    # First create succeeds + ws appears running, second create fails
    coder_client.create_workspace.side_effect = [
        _ws(0), CoderApiError("boom"),
    ]
    coder_client.wait_for_running.side_effect = lambda ws_id, timeout: _ws(0)
    stage.run(ctx)
    assert ctx.ledger.all() == ["ws-0"]


def test_wait_for_running_timeout_is_failure(ctx, coder_client):
    coder_client.create_workspace.side_effect = [_ws(0, status="pending")]
    coder_client.wait_for_running.side_effect = CoderApiError("timeout after 60s")
    res = stage.run(ctx)
    assert res.ok is False
    # Workspace was registered before timeout so cleanup will delete it
    assert ctx.ledger.all() == ["ws-0"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/stages/test_provision.py -v`
Expected: All 5 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/stages/provision.py`:
```python
"""Stage 1: create N×M workspaces from `template_name` and wait for running."""
from __future__ import annotations

import time

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "provision"


def run(ctx: StageContext) -> StageResult:
    for user in ctx.user_ids:
        for m in range(ctx.cfg.per_user):
            name = f"scaletest-{user.username}-{m}"
            op_label = f"{user.username}/{name}"
            started = time.monotonic()
            try:
                ws = ctx.client.create_workspace(
                    user_id=user.id, name=name,
                    template_name=ctx.cfg.template_name,
                )
                ctx.ledger.add(ws.id)            # register BEFORE polling
                ws_running = ctx.client.wait_for_running(
                    ws.id, timeout=ctx.cfg.timeouts.provision_workspace,
                )
                ctx.workspaces.append(ws_running)
            except Exception as e:  # noqa: BLE001
                elapsed_ms = int((time.monotonic() - started) * 1000)
                log_op(ctx.log, STAGE_NAME, op=op_label, ok=False,
                       elapsed_ms=elapsed_ms,
                       err=f"{type(e).__name__}: {e}"[:200])
                return StageResult(ok=False, err=str(e))
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log_op(ctx.log, STAGE_NAME, op=op_label, ok=True,
                   elapsed_ms=elapsed_ms)
    return StageResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/stages/test_provision.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/stages/provision.py tests/stages/test_provision.py
git commit -m "feat(stage:provision): create N×M workspaces, register in ledger, wait for running"
```

---

## Task 10: Stage 2 — `stages/ssh.py`

**Files:**
- Create: `src/coder_scale_test/stages/ssh.py`
- Test: `tests/stages/test_ssh.py`

- [ ] **Step 1: Write the failing tests**

`tests/stages/test_ssh.py`:
```python
"""Tests for stage 2: shell out to `coder ssh <ws> echo <token>`."""
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
                      coder_session_token="tok",
                      timeouts=mocker.Mock(ssh_round_trip=10))
    return StageContext(
        cfg=cfg, client=coder_client, ledger=mocker.Mock(), log=fh,
        user_ids=[], workspaces=[_ws("scaletest-alice-0"), _ws("scaletest-bob-0")],
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
    assert seen_env["CODER_SESSION_TOKEN"] == "tok"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/stages/test_ssh.py -v`
Expected: All 5 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/stages/ssh.py`:
```python
"""Stage 2: SSH echo round-trip via `coder ssh <ws> -- echo <token>`."""
from __future__ import annotations

import os
import secrets
import string
import subprocess
import time

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "ssh"
TOKEN_LEN = 16
ALPHABET = string.ascii_letters + string.digits


def run(ctx: StageContext) -> StageResult:
    for ws in ctx.workspaces:
        op = f"{ws.owner_name}/{ws.name}"
        token = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LEN))
        cmd = ["coder", "ssh", ws.name, "--", "echo", token]
        env = {**os.environ,
               "CODER_URL": ctx.cfg.coder_url,
               "CODER_SESSION_TOKEN": ctx.cfg.coder_session_token}
        started = time.monotonic()
        try:
            cp = subprocess.run(
                cmd, env=env, capture_output=True, text=True,
                timeout=ctx.cfg.timeouts.ssh_round_trip,
            )
        except subprocess.TimeoutExpired:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            err = f"timeout: coder ssh exit=124"
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)

        elapsed_ms = int((time.monotonic() - started) * 1000)
        if cp.returncode != 0:
            err = (f'subprocess: exit={cp.returncode} '
                   f'stderr="{cp.stderr.strip()[:120]}"')
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)

        if token not in cp.stdout:
            err = f'mismatch: expected="{token}" got="{cp.stdout.strip()[:80]}"'
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=elapsed_ms, err=err)
            return StageResult(ok=False, err=err)

        log_op(ctx.log, STAGE_NAME, op=op, ok=True, elapsed_ms=elapsed_ms)
    return StageResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/stages/test_ssh.py -v`
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/stages/ssh.py tests/stages/test_ssh.py
git commit -m "feat(stage:ssh): SSH echo round-trip via coder CLI shell-out"
```

---

# Phase 4 — Stages 3 & 4

## Task 11: Stage 3 — `stages/web_terminal.py`

**Files:**
- Create: `src/coder_scale_test/stages/web_terminal.py`
- Test: `tests/stages/test_web_terminal.py`

- [ ] **Step 1: Write the failing tests**

`tests/stages/test_web_terminal.py`:
```python
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
                      coder_session_token="tok",
                      timeouts=mocker.Mock(web_terminal_round_trip=5))
    coder_client.get_agent_id.return_value = "agent-1"
    return StageContext(cfg=cfg, client=coder_client, ledger=mocker.Mock(),
                        log=fh, user_ids=[], workspaces=[_ws()])


def test_success_when_token_observed(ctx, mocker):
    fake_ws = mocker.MagicMock()
    sent = {}
    def _send(payload):
        # capture token sent
        if payload.startswith("echo "):
            sent["token"] = payload.split()[1].strip()
    fake_ws.send.side_effect = _send
    # recv returns the echo response with the token
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
    assert kwargs["header"]["Coder-Session-Token"] == "tok"


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/stages/test_web_terminal.py -v`
Expected: All 4 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/stages/web_terminal.py`:
```python
"""Stage 3: PTY WebSocket echo round-trip.

Opens wss://<host>/api/v2/workspaceagents/{agent_id}/pty, sends `echo <token>\\n`,
reads frames until the token is observed in stdout.
"""
from __future__ import annotations

import secrets
import string
import time
from urllib.parse import urlparse, urlunparse

import websocket  # from websocket-client

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "web_terminal"
TOKEN_LEN = 16
ALPHABET = string.ascii_letters + string.digits


def run(ctx: StageContext) -> StageResult:
    base_wss = _http_to_ws(ctx.cfg.coder_url)
    for ws in ctx.workspaces:
        op = f"{ws.owner_name}/{ws.name}"
        try:
            agent_id = ctx.client.get_agent_id(ws.id)
        except Exception as e:  # noqa: BLE001
            err = f"agent_lookup: {type(e).__name__}: {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False, elapsed_ms=0, err=err)
            return StageResult(ok=False, err=err)

        url = (f"{base_wss}/api/v2/workspaceagents/{agent_id}/pty"
               f"?reconnect={secrets.token_hex(8)}&width=80&height=24")
        token = "".join(secrets.choice(ALPHABET) for _ in range(TOKEN_LEN))
        started = time.monotonic()
        timeout_s = ctx.cfg.timeouts.web_terminal_round_trip
        ws_conn = None
        try:
            ws_conn = websocket.create_connection(
                url,
                header={"Coder-Session-Token": ctx.cfg.coder_session_token},
                timeout=timeout_s,
            )
            ws_conn.send(f"echo {token}\n")
            deadline = time.monotonic() + timeout_s
            buf = ""
            while time.monotonic() < deadline:
                frame = ws_conn.recv()
                buf += frame.decode("utf-8", "replace") if isinstance(frame, (bytes, bytearray)) else frame
                if token in buf:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    log_op(ctx.log, STAGE_NAME, op=op, ok=True, elapsed_ms=elapsed_ms)
                    break
            else:
                err = f"timeout: token not seen within {timeout_s}s"
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
                return StageResult(ok=False, err=err)
        except websocket.WebSocketTimeoutException as e:
            err = f"timeout: pty ws {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        except websocket.WebSocketConnectionClosedException as e:
            err = f"ws_closed: {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        finally:
            if ws_conn is not None:
                try:
                    ws_conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return StageResult(ok=True)


def _http_to_ws(http_url: str) -> str:
    """https://x → wss://x; http://x → ws://x."""
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/stages/test_web_terminal.py -v`
Expected: All 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/stages/web_terminal.py tests/stages/test_web_terminal.py
git commit -m "feat(stage:web_terminal): PTY WebSocket echo round-trip"
```

---

## Task 12: Stage 4 — `stages/app_traffic.py`

**Files:**
- Create: `src/coder_scale_test/stages/app_traffic.py`
- Test: `tests/stages/test_app_traffic.py`

This task implements the URL pattern chosen in **Task 6's spike report**. The plan below assumes Pattern A (`/api/v2/workspaceagents/{id}/apps/<slug>`) — if the spike chose B or shell-out fallback, swap the URL builder in `_build_app_url()` accordingly. The test structure is unchanged.

- [ ] **Step 1: Write the failing tests**

`tests/stages/test_app_traffic.py`:
```python
"""Tests for stage 4: WebSocket round-trip to tcp-echo app."""
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
                      coder_session_token="tok",
                      app_tcp_port=7000,
                      timeouts=mocker.Mock(app_traffic_round_trip=5))
    coder_client.get_agent_id.return_value = "agent-1"
    return StageContext(cfg=cfg, client=coder_client, ledger=mocker.Mock(),
                        log=fh, user_ids=[], workspaces=[_ws()])


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/stages/test_app_traffic.py -v`
Expected: All 3 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/stages/app_traffic.py`:
```python
"""Stage 4: WebSocket round-trip to the workspace's tcp-echo app.

URL pattern is the one chosen in Task 6's spike report. This module assumes
Pattern A — `wss://<host>/api/v2/workspaceagents/{agent_id}/apps/<slug>`.
If the spike picked Pattern B or shell-out, replace _build_app_url accordingly.
"""
from __future__ import annotations

import secrets
import time
from urllib.parse import urlparse, urlunparse

import websocket

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "app_traffic"
APP_SLUG = "tcp-echo"          # matches the template's app definition
PAYLOAD_SIZE = 32


def run(ctx: StageContext) -> StageResult:
    base_wss = _http_to_ws(ctx.cfg.coder_url)
    for ws in ctx.workspaces:
        op = f"{ws.owner_name}/{ws.name}"
        try:
            agent_id = ctx.client.get_agent_id(ws.id)
        except Exception as e:  # noqa: BLE001
            err = f"agent_lookup: {type(e).__name__}: {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False, elapsed_ms=0, err=err)
            return StageResult(ok=False, err=err)

        url = _build_app_url(base_wss, agent_id, APP_SLUG)
        payload = secrets.token_bytes(PAYLOAD_SIZE)
        started = time.monotonic()
        timeout_s = ctx.cfg.timeouts.app_traffic_round_trip
        ws_conn = None
        try:
            ws_conn = websocket.create_connection(
                url,
                header={"Coder-Session-Token": ctx.cfg.coder_session_token},
                timeout=timeout_s,
            )
            ws_conn.send_binary(payload)
            received = ws_conn.recv()
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if received != payload:
                err = (f'mismatch: expected="{payload.hex()[:32]}..." '
                       f'got="{(received[:32] if isinstance(received, (bytes, bytearray)) else b"").hex()}..."')
                log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                       elapsed_ms=elapsed_ms, err=err)
                return StageResult(ok=False, err=err)
            log_op(ctx.log, STAGE_NAME, op=op, ok=True, elapsed_ms=elapsed_ms)
        except websocket.WebSocketTimeoutException as e:
            err = f"timeout: app ws {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        except websocket.WebSocketConnectionClosedException as e:
            err = f"ws_closed: {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        except Exception as e:  # noqa: BLE001
            err = f"{type(e).__name__}: {e}"[:200]
            log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                   elapsed_ms=int((time.monotonic() - started) * 1000), err=err)
            return StageResult(ok=False, err=err)
        finally:
            if ws_conn is not None:
                try:
                    ws_conn.close()
                except Exception:  # noqa: BLE001
                    pass
    return StageResult(ok=True)


def _build_app_url(base_wss: str, agent_id: str, app_slug: str) -> str:
    return f"{base_wss}/api/v2/workspaceagents/{agent_id}/apps/{app_slug}"


def _http_to_ws(http_url: str) -> str:
    parsed = urlparse(http_url)
    scheme = "wss" if parsed.scheme == "https" else "ws"
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/stages/test_app_traffic.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/stages/app_traffic.py tests/stages/test_app_traffic.py
git commit -m "feat(stage:app_traffic): WebSocket round-trip to tcp-echo app"
```

---

# Phase 5 — Stage 5

## Task 13: Stage 5 — `stages/dashboard.py`

**Files:**
- Create: `src/coder_scale_test/stages/dashboard.py`
- Test: `tests/stages/test_dashboard.py`

- [ ] **Step 1: Write the failing tests**

`tests/stages/test_dashboard.py`:
```python
"""Tests for stage 5: Playwright headless Chromium dashboard load."""
from __future__ import annotations
from urllib.parse import urlparse
from unittest.mock import MagicMock

import pytest

from coder_scale_test.stages import dashboard as stage
from coder_scale_test.coder_client import User
from coder_scale_test.runner import StageContext


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(
        coder_url="https://coder.example.com",
        coder_session_token="tok",
        dashboard_ready_selector="[data-testid='workspaces-table']",
        timeouts=mocker.Mock(dashboard_ready=10),
    )
    return StageContext(
        cfg=cfg, client=coder_client, ledger=mocker.Mock(), log=fh,
        user_ids=[
            User("u1", "alice", "2024-01-01T00:00:00Z"),
            User("u2", "bob", "2024-01-02T00:00:00Z"),
        ],
        workspaces=[],
    )


def _fake_playwright(mocker):
    """Return a fake sync_playwright() chain Playwright API."""
    pw = MagicMock(name="Playwright")
    browser = MagicMock(name="Browser")
    pw.chromium.launch.return_value = browser
    contexts: list[MagicMock] = []

    def _new_context():
        c = MagicMock(name="BrowserContext")
        c._cookies = []
        c.add_cookies.side_effect = lambda cookies: c._cookies.extend(cookies)
        page = MagicMock(name="Page")
        c.new_page.return_value = page
        contexts.append(c)
        return c
    browser.new_context.side_effect = _new_context

    cm = MagicMock()
    cm.__enter__.return_value = pw
    cm.__exit__.return_value = False
    return cm, pw, browser, contexts


def test_loads_dashboard_per_user(ctx, mocker):
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)
    res = stage.run(ctx)
    assert res.ok is True
    assert browser.new_context.call_count == 2  # one context per user
    # Every page.goto'd the coder URL and waited for the selector
    for c in contexts:
        page = c.new_page.return_value
        page.goto.assert_called_with("https://coder.example.com")
        page.wait_for_selector.assert_called_with(
            "[data-testid='workspaces-table']", timeout=10_000
        )


def test_sets_admin_cookie_on_each_context(ctx, mocker):
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)
    stage.run(ctx)
    expected_host = urlparse(ctx.cfg.coder_url).hostname
    for c in contexts:
        assert len(c._cookies) == 1
        cookie = c._cookies[0]
        assert cookie["name"] == "coder_session_token"
        assert cookie["value"] == "tok"
        assert cookie["domain"] == expected_host
        assert cookie["path"] == "/"
        assert cookie["secure"] is True


def test_fail_on_selector_timeout(ctx, mocker):
    from playwright.sync_api import TimeoutError as PWTimeout
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)

    def _new_context():
        c = MagicMock()
        page = MagicMock()
        page.wait_for_selector.side_effect = PWTimeout("selector did not appear")
        c.new_page.return_value = page
        return c
    browser.new_context.side_effect = _new_context

    res = stage.run(ctx)
    assert res.ok is False
    assert "playwright" in (res.err or "").lower() or "timeout" in (res.err or "").lower()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/stages/test_dashboard.py -v`
Expected: All 3 tests FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/stages/dashboard.py`:
```python
"""Stage 5: Playwright headless Chromium dashboard time-to-ready.

Per simulated user: a fresh BrowserContext, the admin session cookie
injected, navigate to <coder_url>, wait for the configured ready selector.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

from coder_scale_test.log import log_op
from coder_scale_test.runner import StageContext, StageResult

STAGE_NAME = "dashboard"


def run(ctx: StageContext) -> StageResult:
    host = urlparse(ctx.cfg.coder_url).hostname or ""
    timeout_ms = ctx.cfg.timeouts.dashboard_ready * 1000
    selector = ctx.cfg.dashboard_ready_selector
    cookie = {
        "name": "coder_session_token",
        "value": ctx.cfg.coder_session_token,
        "domain": host,
        "path": "/",
        "secure": True,
        "httpOnly": True,
        "sameSite": "Lax",
    }

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            for user in ctx.user_ids:
                op = user.username
                started = time.monotonic()
                context = browser.new_context()
                try:
                    context.add_cookies([cookie])
                    page = context.new_page()
                    page.goto(ctx.cfg.coder_url)
                    page.wait_for_selector(selector, timeout=timeout_ms)
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    log_op(ctx.log, STAGE_NAME, op=op, ok=True,
                           elapsed_ms=elapsed_ms)
                except PWTimeout as e:
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    err = f"playwright: {str(e)[:160]}"
                    log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                           elapsed_ms=elapsed_ms, err=err)
                    return StageResult(ok=False, err=err)
                except Exception as e:  # noqa: BLE001
                    elapsed_ms = int((time.monotonic() - started) * 1000)
                    err = f"playwright: {type(e).__name__}: {e}"[:200]
                    log_op(ctx.log, STAGE_NAME, op=op, ok=False,
                           elapsed_ms=elapsed_ms, err=err)
                    return StageResult(ok=False, err=err)
                finally:
                    context.close()
        finally:
            browser.close()
    return StageResult(ok=True)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/stages/test_dashboard.py -v`
Expected: All 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/coder_scale_test/stages/dashboard.py tests/stages/test_dashboard.py
git commit -m "feat(stage:dashboard): Playwright Chromium time-to-ready per user"
```

---

# Phase 6 — CLI + docs

## Task 14: CLI entry — `__main__.py`

**Files:**
- Create: `src/coder_scale_test/__main__.py`
- Test (smoke): `tests/test_main.py`

- [ ] **Step 1: Write the failing test**

`tests/test_main.py`:
```python
"""Smoke test: __main__ parses --config and calls runner.run."""
from __future__ import annotations
import sys
from unittest.mock import MagicMock

import pytest


def test_main_calls_runner(tmp_path, monkeypatch, mocker):
    from coder_scale_test import __main__ as main_mod

    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text("# placeholder")

    fake_cfg = MagicMock()
    mocker.patch.object(main_mod, "load_config", return_value=fake_cfg)
    run_spy = mocker.patch.object(main_mod, "run", return_value=0)

    monkeypatch.setattr(sys, "argv", ["coder-scale-test", "--config", str(cfg_path)])
    rc = main_mod.main()
    assert rc == 0
    run_spy.assert_called_once_with(fake_cfg)


def test_main_propagates_exit_code(tmp_path, monkeypatch, mocker):
    from coder_scale_test import __main__ as main_mod
    cfg_path = tmp_path / "c.toml"
    cfg_path.write_text("# placeholder")
    mocker.patch.object(main_mod, "load_config", return_value=MagicMock())
    mocker.patch.object(main_mod, "run", return_value=2)
    monkeypatch.setattr(sys, "argv", ["coder-scale-test", "--config", str(cfg_path)])
    assert main_mod.main() == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_main.py -v`
Expected: 2 tests FAIL with `ModuleNotFoundError` or `AttributeError: module ... has no attribute 'main'`.

- [ ] **Step 3: Write the implementation**

`src/coder_scale_test/__main__.py`:
```python
"""CLI entry: `python -m coder_scale_test --config config.toml`."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from coder_scale_test.config import ConfigError, load as load_config
from coder_scale_test.runner import run


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="coder-scale-test")
    parser.add_argument("--config", type=Path, default=Path("./config.toml"),
                        help="path to config.toml (default: ./config.toml)")
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
    except (ConfigError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    return run(cfg)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_main.py -v`
Expected: 2 tests PASS.

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `uv run pytest -v`
Expected: All tests across the project pass. No skips, no errors.

- [ ] **Step 6: Commit**

```bash
git add src/coder_scale_test/__main__.py tests/test_main.py
git commit -m "feat(cli): __main__ entry parses --config and calls runner.run"
```

---

## Task 15: Operator docs — `config.toml.example` + README updates

**Files:**
- Create: `config.toml.example`
- Modify: `docs/README.md`

- [ ] **Step 1: Write `config.toml.example`**

```toml
# Annotated example. Copy to config.toml and fill in values.
# config.toml is gitignored — do not commit secrets-adjacent paths.

# Cluster URL (no trailing slash)
coder_url = "https://coder.example.com"

# Pre-existing template name on the cluster (must already exist).
template_name = "ubuntu-base"

# How many active non-admin users to take (first-N by created_at) and how
# many workspaces per user. Total workspaces created = num_users * per_user.
num_users = 10
per_user = 3

# Plain-text log file. Append-mode; one line per op + structural events.
log_file = "./scale-run.log"

# Per-stage timeouts in seconds.
[timeouts]
provision_workspace = 300
ssh_round_trip = 30
web_terminal_round_trip = 30
app_traffic_round_trip = 30
dashboard_ready = 60
delete_workspace = 120

# Stage 4 — internal container port the tcp-echo app listens on.
[app]
tcp_port = 7000

# Stage 5 — selector that means "dashboard fully loaded".
[dashboard]
ready_selector = "[data-testid='workspaces-table']"

# Token: NOT in this file. Set CODER_SESSION_TOKEN in the environment.
#   export CODER_SESSION_TOKEN=...
```

- [ ] **Step 2: Update `docs/README.md`**

Append an "Operator quick-start" section to docs/README.md and add a note clarifying token handling. The existing methodology section stays. Replace the file's contents with:

```markdown
<!-- Original README.md-->
# coder-scale-testing

Python load testing tool for a Coder cluster on Kubernetes. Tests are
executed with a Coder session token of admin role.

## Summary and Design Constraints

All non-secret parameters (cluster URL, num_users, per_user, log file,
template name, per-stage timeouts) are saved in TOML configuration file
`config.toml`. **The session token is read from the `CODER_SESSION_TOKEN`
environment variable, never from `config.toml`**, so the file is safe to
keep in version-controlled-but-private locations.

In the approach described in [Coder scale testing](https://coder.com/docs/admin/infrastructure/scale-testing),
the command line `coder exp scaletest …` can't be used because it would
create users first, which is not allowed in this testing.

Use Python packages to implement the required functionality based on the
[Coder REST API](https://coder.com/docs/reference/api). Stage 2 (SSH echo)
shells out to the `coder` CLI; stages 0, 1, 3, 4, 5 are pure Python.

## Methodology

Six serial stages:

0. Find existing users (filter, exclude owners/admins, take first-N by `created_at`).
1. Provision N×M workspaces (`per_user`) under those users using one configured template.
2. SSH echo: `coder ssh <ws> -- echo <random-alnum>`, verify round-trip.
3. Web terminal: open PTY WebSocket, `echo <random>`, verify round-trip.
4. Workspace app traffic: WebSocket round-trip to the workspace's `tcp-echo` app.
5. Dashboard: open the Coder dashboard in a headless Chromium per simulated user (load-only, time to ready selector).
6. Cleanup: delete every workspace this run created (always runs, even on Ctrl-C).

**Important:** stages run serially, and ops within a stage run serially too.
Each stage measures per-stage scale in isolation. The tool does NOT simulate
realistic concurrent mixed load.

## Operator quick-start

```bash
# 1. Install
git clone <this repo> && cd coder-scale-testing
uv venv
uv pip install -e ".[dev]"
uv run playwright install chromium       # first time only, for stage 5

# 2. Configure
cp config.toml.example config.toml
$EDITOR config.toml                       # set coder_url, template_name, num_users, per_user

# 3. Auth
export CODER_SESSION_TOKEN=<admin token>

# 4. Run
uv run python -m coder_scale_test --config config.toml
echo "exit=$?  see ./scale-run.log"
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All stages passed; cleanup clean. |
| 1 | A stage failed (fail-fast). Subsequent stages skipped. Cleanup attempted. |
| 2 | SIGINT (Ctrl-C). Cleanup attempted. |
| 3 | Internal error (e.g. config invalid, `coder` CLI missing). Cleanup attempted. |
| 4 | All stages passed but cleanup left workspaces behind. |

### Runtime requirements

- Python 3.11+ (`uv` recommended).
- `coder` CLI on `PATH` (used by stage 2).
- `playwright install chromium` (used by stage 5).
- Pre-existing template on the cluster with a `tcp-echo` app exposing a
  TCP port (referenced in `config.toml [app] tcp_port`).

## References

- [Coder scale testing](https://coder.com/docs/admin/infrastructure/scale-testing)
- [Coder REST API](https://coder.com/docs/reference/api)
- [Design spec](./superpowers/specs/2026-05-08-coder-scale-testing-design.md)
- [Implementation plan](./superpowers/plans/2026-05-08-coder-scale-testing.md)
```

- [ ] **Step 3: Verify the README renders**

Run:
```bash
# A simple sanity check — markdownlint if available, otherwise just preview length.
wc -l docs/README.md
grep -c '^##' docs/README.md   # expect 5+ section headers
```
Expected: file is 60+ lines, 5+ second-level headers (`Summary…`, `Methodology`, `Operator quick-start`, `References`, plus an Exit codes table that is under H3).

- [ ] **Step 4: Run the full test suite once more**

Run: `uv run pytest -v`
Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add config.toml.example docs/README.md
git commit -m "docs: operator quick-start and config.toml.example"
```

---

# Self-Review

## 1. Spec coverage

Walking each spec section, the task that implements it:

| Spec section | Covered by |
|--------------|-----------|
| Decision #1 (sequential within stage) | Tasks 8–13 (stage modules use plain `for` loops, no asyncio) |
| Decision #2 (plain log file) | Task 3 (`log.py`) |
| Decision #3 (fail-fast) | Task 7 (`runner.run`) tests `test_fail_fast_skips_remaining` |
| Decision #4 (pre-existing template by name) | Task 5 (`_resolve_template_id`) and Task 9 (`stages/provision.py`) |
| Decision #5 (env var only token) | Task 2 (`config.load` requires `CODER_SESSION_TOKEN`) |
| Decision #6 (requests + websocket-client) | Task 1 dependencies; Tasks 5, 11, 12 use them |
| Decision #7 (admin cookie in every Chromium) | Task 13 (`stages/dashboard.py`) and `test_sets_admin_cookie_on_each_context` |
| Decision #8 (Playwright sync) | Task 13 |
| Decision #9 (try/finally + SIGINT) | Task 7 (`install_sigint_handler`); Task 4 (`install_cleanup_sigint_handler`) |
| Decision #10 (first-N non-admin by created_at) | Task 8 (`stages/users.py`) |
| Decision #11 (pytest + mocked Coder client) | Tasks 2, 3, 4, 5, 7–14 each have a TDD pair |
| Decision #12 (small package, stage-per-module) | Task 1 layout, mirrored by every subsequent task |
| Decision #13 (stage 2 shells to `coder` CLI) | Task 10 |
| Decision #14 (stage 4 spike before impl) | Task 6, then Task 12 |
| Decision #15 (Python 3.11 floor, stdlib `tomllib`) | Task 1 (`pyproject.toml`); Task 2 imports `tomllib` |
| Exit codes 0/1/2/3/4 | Task 7 tests cover each exit code path |
| Log format (`OK   ` / `FAIL ` lines, structural events) | Task 3 tests + format used by every stage |
| Error categorization (`timeout:`, `subprocess:`, `playwright:`, etc.) | Tasks 10–13 produce these prefixes; tested in each |
| Workspace name pattern `scaletest-{user}-{m}` | Task 9 `test_uses_correct_workspace_name_pattern` |
| Two-stage SIGINT during cleanup | Task 4 `install_cleanup_sigint_handler` (runtime behaviour; not directly unit-tested because signal handlers are awkward to assert on — relies on manual smoke testing) |
| Operator quick-start docs | Task 15 |
| `config.toml.example` | Task 15 |

**Gaps identified:**

- The two-stage SIGINT during cleanup is not unit-tested. This is acceptable
  for a design doc requirement (signal handlers are awkward to test in
  pytest without spawning subprocesses) but I'm recording it explicitly.
  An optional add: a subprocess-based smoke test in Task 4 that runs the
  cleanup loop in a child process and sends SIGINT twice. Decision: defer
  to manual testing — adding signal-via-subprocess fixtures inflates suite
  runtime past the 5-second target.

- The runner tests use stub stages (no real network), so the `STAGE_START`
  / `STAGE_END` event lines are not asserted directly. The format of those
  events is exercised in `tests/test_log.py` (Task 3). Acceptable.

## 2. Placeholder scan

Searching this plan file for `TBD`, `TODO`, `FIXME`, "implement later",
"fill in details", "Add appropriate", "similar to Task":

- No `TBD` or `TODO` instances.
- Task 6 (the spike) deliberately contains placeholder `<...>` markers in the
  manual command examples — these are intentional template fields the operator
  fills with their cluster-specific values, not plan-author placeholders.
- Task 12 references "Pattern A from Task 6's spike report" — this is not a
  plan-failure placeholder; it's an explicit call-out that the URL builder
  may need to swap based on the spike outcome, with the alternative behaviour
  defined.

## 3. Type consistency

Walking type and method names across tasks:

- `User`: defined Task 5, used Tasks 7 (StageContext.user_ids), 8, 9, 13. Same shape everywhere.
- `Workspace`: defined Task 5, used Tasks 7, 9, 10, 11, 12. Same fields (`id`, `name`, `owner_name`, `latest_build_status`, `latest_build_transition`).
- `StageResult`: defined Task 7, returned by Tasks 8–13. `ok: bool`, `err: str | None = None`.
- `StageContext`: defined Task 7. Imported by every stage module's tests.
- `CoderClient` methods used by stages: `list_active_non_admin_users`, `create_workspace`, `wait_for_running`, `delete_workspace`, `get_agent_id`. All defined in Task 5.
- `WorkspaceLedger`: defined Task 4, used Task 9 (provision adds), Task 4 (cleanup iterates).
- `STAGE_NAME` constant: every stage module exports its own (`users`, `provision`, `ssh`, `web_terminal`, `app_traffic`, `dashboard`). Runner uses it via `_stage_name`.
- Log helper signatures: `log_op(log_file, stage, op, ok, elapsed_ms, err=None)` and `log_event(log_file, event, **fields)`. Used identically across tasks.

No drift detected.

---

# /autoplan Review — Phase 1: CEO

**Run date:** 2026-05-08 13:44 EDT
**Voice mode:** `[subagent-only]` — Codex CLI unavailable; consensus table reflects single voice.

## Step 0A: Premise challenge

The plan rests on premises that the CEO subagent's review (below) directly contests:

| # | Stated premise | Subagent verdict | This review |
|---|---------------|-----------------|------------|
| P1 | Project name "coder-scale-testing" | **Wrong.** Sequential 30-workspace walk is not "scale testing"; it's conformance/smoke testing. | Agree. The README and design call it scale testing; the Non-goals explicitly disclaim concurrent load. Mismatch between name and product. |
| P2 | Upstream `coder exp scaletest` is rejected because it creates users | **Wrong.** Only the `create-workspaces` subcommand creates users; `workspace-traffic` and `dashboard` operate on existing workspaces. | Agree. README:9 makes the rejection global when it only applies to one subcommand. |
| P3 | Sequential within stage is appropriate | Conditional. Right for conformance, wrong for scale. Depends on P1. | Conditional on P1. |
| P4 | Admin cookie in every Chromium = "per-user simulation" | **Wrong.** N cold-cache loads with admin identity. Admin dashboards render different UI than regular-user dashboards. | Agree. Stage 5 is mislabeled in the spec/log. |
| P5 | Op-level fail-fast within a stage is the right default | **Wrong.** Hides per-fleet failure rate. If ws-7 fails SSH, you learn nothing about ws-8..30. | Agree. Spec section 5 keeps fail-fast across stages but should run-all-and-tally within. |
| P6 | First-N non-admin users by `created_at` is reproducible enough | **Wrong.** Different clusters have different first-N → results not cross-comparable. | Agree. Should be an explicit allow-list in `config.toml`. |
| P7 | Plain log file is sufficient output | **Insufficient.** No trend analysis without structured output. | Agree. JSONL alongside human log is ~20 lines, no cost. |

## Step 0B: Existing-code leverage map

Greenfield project. No prior code in the repo (other than `docs/`). However, **upstream code** is the leverage point being underused:

| Sub-problem | Existing solution | Currently leveraged? |
|-------------|-------------------|---------------------|
| Workspace provisioning at scale | `coder exp scaletest create-workspaces` | No — but rejected for valid reasons (creates users) |
| Workspace traffic round-trip | `coder exp scaletest workspace-traffic` | **No — likely should be** |
| Dashboard load test | `coder exp scaletest dashboard` | **No — likely should be** |
| HTTP+WS load test framework | `k6` (with xk6-websockets), Locust, Artillery | No — design jumps straight to hand-rolled Python |
| Coder REST client | `requests` + hand-rolled (per spec decision #6) | Yes |
| Browser automation | Playwright sync API (per spec decision #8) | Yes |

## Step 0C: Dream state diagram

```
CURRENT (today)
  └── docs/README.md, docs/superpowers/specs/, docs/superpowers/plans/
      No code yet. No baseline measurements. No understanding of cluster failure modes.

THIS PLAN (15 tasks, 6 phases)
  └── src/coder_scale_test/{config,coder_client,log,cleanup,runner,stages/*}.py
      tests/ (mocked Coder, ~30 tests, <5s suite)
      OUTCOME: a tool that walks 30 workspaces serially and reports per-op pass/fail+timing
      VALUE: confirms each transport (REST/SSH/PTY/app/dashboard) works end-to-end against a real cluster
      LIMITATION: does NOT detect provisioner queue saturation, DB load, agent-DERP capacity, or any concurrent-load failure mode

12-MONTH IDEAL
  └── Tool that:
      (a) ramps concurrent workspace creation until control plane breaks
      (b) correlates op latencies with kubectl/Prometheus metrics (provisioner queue depth, DB QPS)
      (c) emits JSONL → trend dashboards → "is provisioning getting slower week-over-week?"
      (d) leverages `coder exp scaletest workspace-traffic`/`dashboard` where they fit, hand-rolls only the gaps

DREAM STATE DELTA
  THIS PLAN solves a *fraction* of the 12-month ideal — specifically, the conformance-check fraction.
  The plan does NOT close the gap toward "real scale testing." Calling it "scale testing" sets the wrong expectation.
```

## Step 0C-bis: Implementation alternatives table

| Approach | Effort (CC time) | Risk | Value |
|----------|-----------------|------|-------|
| **A: Plan as written** (hand-rolled Python, sequential, plain log) | ~2 days CC | Low (well-scoped) | Confirms transports work for 30 workspaces serially. Does NOT find scale problems. |
| **B: Wrap upstream `coder exp scaletest`** (orchestrate `workspace-traffic` + `dashboard` subcommands; hand-roll only stage 0/1/2) | ~1 day CC | Medium (depends on flag compatibility) | Inherits Coder's maintenance of the hard transports. Smaller code surface. May not match exact log format. |
| **C: Pivot to k6 + xk6-websockets** | ~3 days CC | Medium (new tool, team learning curve) | Industry-standard load tool. Built-in p50/p95/ramping. WebSocket extension exists. JSONL native. |
| **D: Pivot to actual scale testing** (concurrent ops + Prometheus integration + ramp-to-failure) | ~5–7 days CC | High (real engineering work) | Detects what the cluster actually breaks under |

## Step 0D: Mode-specific analysis (SELECTIVE EXPANSION)

Auto-decided expansions (in blast radius, <1d CC):

- **JSONL output alongside plain log** (P1 completeness, P7 premise rejection): ~20 lines in `log.py`, one extra parameter. Recommended.
- **Allow-list users in `config.toml`** (P6 premise rejection): one extra config field. Recommended.
- **Run-all-and-tally within a stage** (P5 premise rejection): each stage tracks per-op results, returns aggregate; runner still fails the run if any op failed. ~30 lines across stages. Recommended.
- **Rename stage 5 in log/spec to "dashboard cold-load"** (P4 premise rejection): doc + log-string change. Trivial. Recommended.

Deferred (would need a re-plan, not in current blast radius):

- **Project rename to `coder-cluster-conformance`** (P1 premise rejection): touches every doc, the package name, the CLI entry point. Material rework. Surface to premise gate.
- **Investigate `coder exp scaletest workspace-traffic`/`dashboard` viability** (P2 premise rejection): could collapse stages 4 and 5 to shell-outs. Material rework. Surface to premise gate.
- **Concurrent ops or ramp-to-failure** (12-month ideal): out of scope for this plan. Defer to TODOS.md.

## Step 0E: Temporal interrogation

| Time horizon | What this plan delivers | What it does NOT deliver |
|--------------|------------------------|-------------------------|
| Hour 1 (first run) | A pass/fail report for 30-workspace transport conformance | No latency baseline (single sample per op) |
| Hour 6 | A second run; differences vs first only visible by `diff`-ing logs | No regression detection without manual log parsing |
| Week 2 | Maybe-flaky stage 4 if the spike (Task 6) ran into protocol drift | No upgrade-safety story across Coder versions |
| Month 6 | Tool is still useful for transport conformance | Tool will lag upstream Coder if new transports/endpoints land |

## Step 0F: Mode confirmation

**Mode: SELECTIVE EXPANSION.** Hold scope on the conformance-test core; cherry-pick the four trivial expansions above; surface the two material premise issues to the user gate.

## Step 0.5: Dual voices

### CLAUDE SUBAGENT (CEO — strategic independence)

Findings (concrete, severity-tagged):

- **CRITICAL 1.1 — Category error in framing.** The product is conformance/smoke testing, not scale testing. Rename and reset expectations.
- **CRITICAL 4.1 — Upstream rejection unexamined.** Only `coder exp scaletest create-workspaces` creates users; the other subcommands operate on existing workspaces. Re-evaluate `workspace-traffic` and `dashboard` for stages 4 and 5.
- **HIGH 1.2 — 10x reframe untouched.** Real scale testing requires concurrency + cluster-internal metrics (kubectl/Prometheus). The plan finds nothing under 30 serial workspaces that `curl + kubectl top` doesn't.
- **HIGH 2.1 — Stage 5 mislabeling.** "Per-user" with admin cookie is N cold loads with admin identity. Either rename the stage or mint per-user tokens.
- **HIGH 3.1 — No structured output.** Plain log blocks every future trend analysis.
- **HIGH 5.1 — Maintenance lag risk.** Coder updates `coder exp scaletest` over time; hand-rolled Python will lag.
- **MEDIUM 2.2 — First-N coupling.** Different clusters → different first-N → non-comparable runs. Use allow-list.
- **MEDIUM 2.3 — Op-level fail-fast hides failure rate.** Run-all-and-tally within a stage; fail-fast across stages.
- **MEDIUM 4.2 — k6/Locust/Artillery not justified-against.** Add an "alternatives considered" paragraph.

### CODEX SAYS (CEO — strategy challenge)

`[codex-unavailable: binary not found]` — single-voice review. No second-voice consensus available.

### CEO consensus table

```
CEO DUAL VOICES — CONSENSUS TABLE:    [voice mode: subagent-only]
═══════════════════════════════════════════════════════════════
  Dimension                            Claude  Codex   Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Premises valid?                   NO      N/A     SINGLE-NO   (CRITICAL: framing, upstream rejection)
  2. Right problem to solve?           PARTIAL N/A     SINGLE-PARTIAL (right shape if renamed; wrong if "scale" is goal)
  3. Scope calibration correct?        NO      N/A     SINGLE-NO   (4 expansions auto-applied; 2 surfaced to gate)
  4. Alternatives sufficiently explored? NO    N/A     SINGLE-NO   (k6/Locust/Artillery not mentioned; upstream subcommands unchecked)
  5. Competitive/market risks covered? NO      N/A     SINGLE-NO   (upstream maintenance lag risk not in spec)
  6. 6-month trajectory sound?         PARTIAL N/A     SINGLE-PARTIAL (depends on premise outcomes)
═══════════════════════════════════════════════════════════════
```

## Sections 1–10 (CEO review skill — abbreviated)

For each, what was examined and whether anything was flagged:

- **§1 Strategic foundation:** Examined the README's framing, the spec's 15 decisions, and the plan's task list. Flagged at premise level (P1, P2, P4–P7); see Step 0A.
- **§2 Error & rescue paths:** Examined the spec's error-categorization table (timeout/http/ws_closed/mismatch/subprocess/playwright). The categories are sound, but error messages don't include recovery hints (e.g., "stage 1 timeout" doesn't say cluster-overloaded vs template-broken vs timeout-too-tight). Auto-decided: add a short "likely cause" hint to each error category. P1 completeness.
- **§3 Failure modes:** See Failure Modes Registry below.
- **§4 Operational story:** Quick-start docs are good but TTHW is realistic 15–30 min first time (covered in Phase 3.5 DX review).
- **§5 Data model:** No persistent data model. Workspaces created, deleted in same run. N/A.
- **§6 Auth & secrets:** Token via env var (decision #5). Sound. No findings.
- **§7 Integrations:** Three external dependencies — `coder` CLI, Coder REST API, Coder agent transports. CLI dependency is tactical (only stage 2). Documented. No findings beyond what's already flagged.
- **§8 Migrations:** No data to migrate. N/A.
- **§9 Observability:** Plain log file. Flagged in P7. JSONL output auto-decided.
- **§10 Rollout & flags:** Tool is run on demand by an operator. No staged rollout needed. N/A.

§11 Design: skipped (no UI scope, per Phase 0 detection).

## Mandatory outputs

### NOT in scope (deferred items)

| Item | Why deferred | Disposition |
|------|--------------|------------|
| Concurrent ops within a stage | Out of scope (decision #1, sequential by design) | Deferred to follow-on plan |
| Ramp-to-failure scale testing | 12-month ideal, requires Prometheus integration | Deferred to follow-on plan |
| Project rename `coder-cluster-conformance` | Premise-level decision, surface to gate | **Premise gate** |
| Re-evaluate `coder exp scaletest workspace-traffic`/`dashboard` | Premise-level decision; could collapse stages 4–5 | **Premise gate** |
| Per-user tokens for stage 5 | Stage relabel auto-decided; per-user-tokens pivot deferred | Deferred to follow-on |
| Trend dashboard / log parser | Out of scope for v1; JSONL output unblocks future work | Deferred |

### What already exists

| Sub-problem | Existing | Status |
|-------------|----------|--------|
| Workspace creation API | `POST /api/v2/users/{user}/workspaces` | Used by stage 1 |
| Workspace deletion via build | `POST /api/v2/workspaces/{id}/builds` (transition: delete) | Used by cleanup |
| PTY WebSocket | `wss://.../api/v2/workspaceagents/{id}/pty` | Used by stage 3 (well-trodden path) |
| App proxy WebSocket | URL pattern still uncertain (Task 6 spike retires) | Used by stage 4 (after spike) |
| `coder ssh` CLI | Tunnels SSH via DERP to agent | Used by stage 2 |
| Playwright Chromium | Sync API, cookies-per-context | Used by stage 5 |

### Error & Rescue Registry

| Error category | Source | Recovery hint (auto-added) |
|----------------|--------|---------------------------|
| `timeout: <what>` | Any timeout | Cluster overloaded? Template too slow? Timeout too tight in `config.toml`? |
| `http: status=4xx` | REST 4xx | Token revoked or scoped wrong; check `CODER_SESSION_TOKEN` |
| `http: status=5xx` | REST 5xx | Cluster control plane unhealthy; check `kubectl get pods -n coder` |
| `ws_closed: code=N` | WebSocket abnormal close | Agent likely down; check workspace `latest_build` status |
| `mismatch: expected="..." got="..."` | Echo round-trip wrong content | Agent stdout polluted; check template's startup script for noisy logging |
| `subprocess: exit=N` | `coder ssh` non-zero | Likely template/agent issue; reproduce with `coder ssh <ws> -- echo test` from operator's terminal |
| `playwright: <msg>` | Selector timeout | Dashboard UI changed; update `dashboard.ready_selector` in `config.toml` |

### Failure Modes Registry

| # | Failure mode | Likelihood | Impact | Mitigation in plan? |
|---|--------------|-----------|--------|---------------------|
| 1 | `coder` CLI missing on PATH | Medium | Stage 2 fails | ✅ Pre-flight check in `runner.run` (Task 7) |
| 2 | Stage 4 spike fails (URL pattern not viable) | Medium | Stage 4 not implementable as designed | ✅ Task 6 retires before Task 12 starts |
| 3 | Playwright Chromium not installed | Medium | Stage 5 fails | ⚠ Plan calls for `playwright install chromium` in quick-start; runtime detection only fires on first stage 5 failure. **Gap:** add pre-flight check in Task 7. |
| 4 | SIGINT during Playwright sync call (Playwright masks signals) | Low | Cleanup might not fire | ⚠ Spec acknowledges; not unit-tested. **Gap:** smoke test recommended. |
| 5 | Workspace name collision (`scaletest-{user}-{m}` already exists) | Medium | Stage 1 fails | ✅ Surfaced as FAIL with clear error; cleanup works on existing-named-but-not-our-id ledger |
| 6 | Cluster doesn't have ≥ N non-admin users | Medium | Stage 0 fails | ✅ Stage 0 returns FAIL with clear message |
| 7 | Template `template_name` doesn't exist | Medium | Stage 1 fails immediately | ✅ `_resolve_template_id` raises `template_not_found` |
| 8 | `tcp-echo` app not configured on template | Medium | Stage 4 fails | ⚠ No pre-flight; first stage 4 op surfaces it. Acceptable. |
| 9 | Run abort-mid-stage leaves workspace in `pending` build state | Low | Cleanup tries to delete a workspace whose build is in flight | ⚠ `delete_workspace` issues a delete-build; concurrent in-flight builds may cause Coder to reject. **Gap:** consider waiting briefly for in-flight build before delete. |
| 10 | Op-level fail-fast hides per-fleet rate (premise P5) | High | Limited diagnostic value | ✅ Auto-decided run-all-and-tally per-stage |

### Dream state delta

This plan delivers the **conformance-check fraction** of the 12-month ideal. It does **not** deliver concurrent load, kubectl/Prometheus correlation, structured trend output (without the JSONL auto-expansion), or coverage of new Coder agent transports beyond what's hand-rolled.

### CEO completion summary

| Item | Result |
|------|--------|
| Premises challenged | 7 (P1–P7) — 4 auto-rejected with expansions, 3 surfaced to premise gate |
| Critical findings | 2 (1.1 framing, 4.1 upstream rejection) |
| High findings | 4 (1.2, 2.1, 3.1, 5.1) |
| Medium findings | 3 (2.2, 2.3, 4.2) |
| Auto-decided expansions | 4 (JSONL output, allow-list users, run-all-and-tally, error recovery hints) |
| Mode | SELECTIVE EXPANSION |
| Voice mode | `[subagent-only]` (Codex unavailable) |
| Deferred to TODOS.md | 4 items (concurrent ops, ramp-to-failure, per-user tokens, trend dashboard) |

**Phase 1 complete.** Single-voice review (codex unavailable). 2 critical, 4 high, 3 medium findings. 5 expansions auto-applied; 2 critical premise issues surfaced to gate. **User decision (D1):** accept framing, proceed; project rename and upstream re-evaluation deferred to TODOS.md. Passing to Phase 3 (Phase 2 skipped — no UI scope).

<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail (autoplan Phase 1)

| # | Phase | Decision | Class | Principle | Rationale | Rejected alternative |
|---|-------|----------|-------|-----------|-----------|---------------------|
| 1 | CEO §0D | Mode = SELECTIVE EXPANSION | Mechanical | P5 | Plan is well-scoped; cherry-pick the trivial wins, surface the material reworks | SCOPE EXPANSION (full pivot), HOLD SCOPE (ignore findings) |
| 2 | CEO §0D | Auto-expand: JSONL output alongside plain log | Mechanical | P1 (completeness) | ~20 LOC in `log.py`; unblocks all future trend analysis | None (P7 premise rejection — clear win) |
| 3 | CEO §0D | Auto-expand: allow-list users in `config.toml` | Mechanical | P5 (explicit) | One extra config field; results reproducible across clusters | None (P6 premise rejection) |
| 4 | CEO §0D | Auto-expand: run-all-and-tally within a stage; fail-fast across stages | Mechanical | P1 (completeness) | Operator sees per-fleet failure rate, not just first-failure | Op-level fail-fast (P5 premise rejection) |
| 5 | CEO §0D | Auto-expand: relabel stage 5 from "per-user simulation" to "dashboard cold-load (admin)" | Mechanical | P5 (explicit) | Doc/log-string change only; removes false advertising | Mint per-user tokens (deferred — material rework) |
| 6 | CEO §2 | Auto-expand: add "likely cause" recovery hint per error category | Mechanical | P1 (completeness) | Doc/code-string changes; operator gets diagnostic direction without parsing logs | None |
| 7 | CEO §0A | Premise P1 (project name) | User Challenge | n/a | Surfaced to gate D1 → user kept framing | Rename to `coder-cluster-conformance` (deferred to TODOS.md) |
| 8 | CEO §0A | Premise P2 (upstream `coder exp scaletest`) | User Challenge | n/a | Surfaced to gate D1 → user kept framing | Re-evaluate `workspace-traffic`/`dashboard` subcommands (deferred to TODOS.md) |
| 9 | CEO §3 | Failure mode #3 (Playwright Chromium not installed) | Mechanical | P1 (completeness) | Add pre-flight check in Task 7's `_check_coder_cli` neighborhood | First-failure detection only (current plan) |
| 10 | CEO §3 | Failure mode #9 (delete-during-in-flight-build) | Taste | P3 (pragmatic) | Defer; Coder API will return a useful error if it rejects the delete | Add pre-flight wait-for-quiescent check |

## TODOS.md (deferred from Phase 1)

A `TODOS.md` will be created in the repo root capturing:

1. **Project rename to `coder-cluster-conformance`** — premise gate deferred. Touches docs, package name, CLI entry, README.
2. **Re-evaluate `coder exp scaletest workspace-traffic` and `dashboard` subcommands** — premise gate deferred. Could collapse stages 4 and 5 to shell-outs to upstream.
3. **Concurrent ops or ramp-to-failure for true scale testing** — out of scope for v1. 12-month ideal.
4. **Mint per-user API tokens for stage 5** — would make stage 5 a real per-user simulation. Out of scope for v1.
5. **kubectl/Prometheus metric correlation during runs** — out of scope for v1. 12-month ideal.
6. **Trend dashboard / log parser for JSONL output** — out of scope for v1; JSONL expansion in this plan unblocks future work.

---

# /autoplan Review — Phase 3: Eng

**Run date:** 2026-05-08 13:54 EDT
**Voice mode:** `[subagent-only]` — Codex CLI unavailable; consensus reflects single voice.
**Test-plan artifact:** [`~/.gstack/projects/cst3/yxiao-main-test-plan-20260508-135401.md`](file:///home/yxiao/.gstack/projects/cst3/yxiao-main-test-plan-20260508-135401.md) (62 codepaths/flows mapped to coverage; 24 gaps identified)

## Step 0: Scope challenge

Read the plan's actual code blocks (every `Step 3: Write the implementation` in Tasks 2–14). Mapped each sub-problem to existing libraries (`requests`, `websocket-client`, `playwright`, `tomllib`) and the upstream `coder` CLI. The plan is well-scoped — every task has a clear deliverable, and no task expands its mandate beyond what the design spec calls for. Complexity check: ~1500 LOC across 13 modules (~115 LOC each average), well under the threshold where a refactor would be needed mid-implementation.

**Scope challenge result:** plan-level scope is sound. No reduction needed; no expansion auto-added at the plan-architecture level. (CEO-level expansions from Phase 1 stand: JSONL output, allow-list users, run-all-and-tally per stage, error recovery hints, stage 5 relabel.)

## Step 0.5: Dual voices

### CLAUDE SUBAGENT (eng — independent review)

Findings, severity-classified, file:section-referenced where applicable:

**CRITICAL:**

- **3.2 — `test_delete_workspace_times_out` mocks `time.monotonic` but not `time.sleep`.** Plan Task 5 line ~918, `times = iter([0, 0.1, 1, 2, 6, 7])`. Real `time.sleep(2.0)` × 3 = 6 seconds of real wall-clock time during a unit test. **Blows the <5s suite-runtime budget.** Fix: add an autouse `_no_real_sleep` fixture in `tests/conftest.py` (Task 1).

**HIGH:**

- **1.1 — Type drift on `StageContext.user_ids`.** Spec line 135 declares `user_ids: list[str]`; plan Task 7 declares `user_ids: list[User]`; stage 0 (Task 8) and stage 1 (Task 9) read `User` fields. Spec is wrong. Fix: rename to `users: list[User]` everywhere.
- **1.2 — Mutable shared state via StageContext.** Stages mutate `ctx.user_ids` and `ctx.workspaces` as side effects with no contract enforcing order or non-emptiness. Fix: each stage explicitly returns its products via `StageResult` (richer dataclass), runner threads them to next stage. Or at minimum, each stage asserts preconditions.
- **2.1 — Provision stage emits one log line per (user, m) op covering both create and wait_for_running.** If the create succeeds and the poll times out, no `OK` line ever logs the create. Fix: emit two `log_op` lines: `create` and `wait_for_running`.
- **2.2 — SIGINT during Playwright sync calls is wishfully solved.** Playwright runs a Node subprocess; SIGINT during `page.goto()` won't cleanly raise `KeyboardInterrupt` until the IPC call returns. Fix: document the limitation; add a thread-with-timeout wrapper if it bites operationally.
- **3.1 — Two-stage cleanup SIGINT explicitly untested + global state hazard.** `_int_count` global persists across multiple `install_cleanup_sigint_handler()` calls in the same process (test isolation bug). Fix: subprocess-based test or refactor to class-instance state.
- **3.3 — Stage 4 spike fallback shell-out invalidates test scaffolding.** Tests mock `websocket.create_connection`; shell-out path uses `subprocess` + raw socket. Plan Task 12 hides the conditional in a comment. Fix: split into Task 12a (Pattern A/B) and Task 12b (shell-out fallback) with separate test files.
- **4.1 — 150 min worst-case for stage 1 with no progress indicator.** 30 workspaces × 300s timeout = silent terminal for hours. Fix: emit one progress line per workspace to **stderr** (not the log file).
- **5.1 — Stage 5 cookie has no `expires`; risk if `user_data_dir` introduced later.** Plaintext token in browser context. Fix: explicit comment forbidding `user_data_dir`; assertion in test that no `storage_state` is wired.
- **5.2 — Token in subprocess env visible via `/proc/<pid>/environ`.** Multi-user-host risk. Fix: document the risk in the README's auth section; consider preferring `coder ssh` reading from `~/.config/coderv2/session` if available.
- **5.3 — Error stringification can leak tokens.** `categorize(e)` truncates to 200 chars but doesn't redact. If `requests.HTTPError.__str__` includes a URL with a token query param, or if `coder ssh` ever prints `--token=...` to stderr, the literal token enters the log file. Fix: single `_redact(s)` helper that replaces the token literal with `[REDACTED]`; apply in `log_op` and `categorize`.
- **6.1 — Clock skew between client and Coder server.** `created_at`-based user sort and build-status timing comparisons assume monotonic comparable timestamps. Fix: log a warning if local clock disagrees with server `Date:` header by >5 minutes.
- **6.2 — Workspace agent `ready` ≠ app `tcp-echo` ready.** Stage 4 first-op fail rate may be high. Fix: add a brief retry-with-backoff wrapper around the first WS connect, or a `wait_for_app_healthy` step.

**MEDIUM:**

- **1.3 — Circular import risk** between `cleanup.py` and `runner.py` via `StageContext`. Fix: extract `StageContext` to its own `context.py`. Alternatively defer; doesn't break today.
- **1.4 — `_resolve_template_id` GET-list-templates per workspace** (30 unnecessary GETs). Fix: cache on the client instance.
- **2.3 — Coder CLI version skew.** Stage 2 parses stdout for token substring; future CLI version changes could break. Fix: log `coder --version` at startup; warn outside tested range.
- **3.4 — Runner tests assert call counts, not log content / event ordering.** Fix: assert on log file substrings.
- **4.2 — `POLL_INTERVAL_S = 2.0` hardcoded** in `coder_client.py`; not in `config.toml`. Fix: move to `[poll] interval_seconds`.
- **4.3 — Fence-post bug in `wait_for_running` deadline.** After last `time.sleep(2.0)`, `monotonic` may be `deadline + epsilon`. Effective timeout is `timeout - 2`. Fix: poll once before checking deadline, or use `for attempt in range(...)`.
- **5.4 — No `User-Agent` on `requests.Session`.** Coder audit log can't distinguish this tool's runs. Fix: `User-Agent: coder-scale-test/0.1.0`.
- **6.3 — Disk full on log file crashes runner.** No try/except around `log_op`. Fix: wrap log writes; on failure, fall back to stderr.
- **6.4 — `requests.Session` follows redirects with token header.** If Coder URL redirects to a different host, token leaks. Fix: per-request header instead of session-level, or `allow_redirects=False`.
- **6.5 — Coder `/users?status=active` server-side semantics may change.** Fix: add explicit role-and-status filter client-side too (defense in depth).

**LOW:**

- **2.4 — Workspace name collision (409 from create) doesn't pick up the existing workspace into the ledger.** Fix: on 409, GET workspace by name+owner and add to ledger before failing.
- **4.4 — `wait_for_running` raises only on `failed`, not `canceled`.** Fix: treat `failed`, `canceled`, `unknown` as terminal-error.
- **5.5 — Token on session headers** vs per-request. Minor. Fix: per-request.
- **6.6 — Cleanup ledger is process-local.** Already in non-goals (decision #9). Add to failure modes registry explicitly.
- **6.7 — Two simultaneous runs collide on workspace names.** Fix: include a short run-ID in `scaletest-{run_id}-{user}-{m}`.

### CODEX SAYS (eng — architecture challenge)

`[codex-unavailable: binary not found]` — single-voice review.

### Eng consensus table

```
ENG DUAL VOICES — CONSENSUS TABLE:    [voice mode: subagent-only]
═══════════════════════════════════════════════════════════════
  Dimension                            Claude  Codex   Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Architecture sound?               PARTIAL N/A     SINGLE-PARTIAL (mutable StageContext, type drift, circular-import risk)
  2. Test coverage sufficient?         NO      N/A     SINGLE-NO   (CRITICAL test sleep bug; 24 gaps total)
  3. Performance risks addressed?      PARTIAL N/A     SINGLE-PARTIAL (no progress indicator, fence-post in deadline, hardcoded poll)
  4. Security threats covered?         NO      N/A     SINGLE-NO   (token leak via err strings; subprocess env; redirect-following)
  5. Error paths handled?              PARTIAL N/A     SINGLE-PARTIAL (categorization good; recovery hints missing — auto-fixed in CEO §2; canceled-build path missing)
  6. Deployment risk manageable?       YES     N/A     SINGLE-YES   (single-binary CLI, env-var token, no migrations)
═══════════════════════════════════════════════════════════════
```

## Section 1: Architecture (ASCII dependency graph)

```
                              ┌──────────────────────────────────┐
                              │  __main__.py  (CLI entry)        │
                              │  argparse --config → load → run  │
                              └────────────┬─────────────────────┘
                                           │
                                           ▼
       ┌─────────────────┐     ┌────────────────────────────────────┐
       │  config.py      │◄────┤  runner.py                         │
       │  - Config       │     │  - run(cfg) → exit code            │
       │  - Timeouts     │     │  - StageContext (cfg, client,      │
       │  - load(path)   │     │       ledger, log, users[],        │
       │  - ConfigError  │     │       workspaces[])                │
       └─────────────────┘     │  - install_sigint_handler          │
                               │  - _check_coder_cli                │
                               │  - _resolve_stages → 6 modules     │
                               └────────┬───────────────────────────┘
                                        │ uses
                ┌───────────────────────┼─────────────────────────────┐
                ▼                       ▼                             ▼
       ┌─────────────────┐     ┌────────────────────┐    ┌──────────────────────┐
       │  coder_client   │     │  log.py            │    │  cleanup.py          │
       │  - CoderClient  │     │  - open_log        │    │  - WorkspaceLedger   │
       │  - User         │     │  - log_op          │    │  - run(ctx) → failed │
       │  - Workspace    │     │  - log_event       │    │  - install_cleanup_  │
       │  - CoderApiError│     │  (planned: JSONL,  │    │       sigint_handler │
       │  list_users     │     │  _redact helper)   │    │  - _categorize       │
       │  create_workspace                          │    └──────┬───────────────┘
       │  wait_for_running                          │           │ uses
       │  delete_workspace                          │           ▼
       │  get_agent_id   │                          │    ┌──────────────────────┐
       │  _resolve_template_id (cache: HIGH 1.4)    │    │  signal (stdlib)     │
       └────────┬────────┘                          │    └──────────────────────┘
                │
                ▼
       ┌─────────────────┐
       │  requests       │ (HTTP)
       │  websocket-     │ (WS, used by stages 3, 4)
       │     client      │
       └─────────────────┘

       stages/  ───────────────────────────────────────────────────────────────
       ▼ each takes StageContext, returns StageResult, mutates ctx.users/workspaces

       users.py     ──── coder_client.list_active_non_admin_users
       provision.py ──── coder_client.{create_workspace, wait_for_running}
       ssh.py       ──── subprocess.run(["coder", "ssh", ...])
       web_terminal.py ─ websocket.create_connection (PTY)
       app_traffic.py ── websocket.create_connection (app proxy; spike-validated)
       dashboard.py ──── playwright.sync_api  →  Chromium
                         (cookie: admin token; one BrowserContext per user)
```

**Coupling concerns:**

- Stages depend on `StageContext` mutable state (HIGH 1.2). Cross-stage contract is implicit.
- `cleanup.py` imports `StageContext` from `runner.py` via `TYPE_CHECKING` (MEDIUM 1.3). Today fine; one refactor away from circular.
- Otherwise stage modules are leaf nodes. No cross-stage imports.

**Auto-decision (P5 explicit):** keep mutable `StageContext` as-is for greenfield v1; document the implicit ordering contract in module docstrings. Refactor to explicit `StageResult.products` deferred to TODOS. Acknowledged taste call — surface to gate.

## Section 2: Code quality

- **DRY violations:** `_http_to_ws` is defined in both `stages/web_terminal.py` (Task 11) and `stages/app_traffic.py` (Task 12). Two identical 3-line functions. **Auto-decided fix:** move to `coder_client.py` as a module-level helper, import from both.
- **Naming consistency:** `STAGE_NAME` constants are present in every stage. Good. `_resolve_stages` returns modules; `_stage_name(stage)` extracts the constant. Clean.
- **Cyclomatic complexity:** `runner.run` is the most branchy function (~25 lines, 3 try/except levels, 2 nested conditionals). At the upper edge of acceptable for one function. Auto-decided: leave as-is; the alternative (split into helpers) loses the `try/finally` clarity.
- **Unused imports:** none flagged; plan task code is tight.

## Section 3: Test review (NOT SKIPPED — see test-plan artifact)

A full test-plan artifact was written to disk (link at top of Phase 3 section). It maps every codepath/flow to its test type and identifies 24 gaps:

- **12 auto-add gaps** (small additions to existing test files):
  - allow-list users in config (test 4)
  - JSONL output (test 8)
  - token redaction helper (test 9)
  - `_resolve_template_id` cache (test 22)
  - `wait_for_running` canceled status (test 23)
  - Playwright Chromium pre-flight (test 30)
  - allow-list in users stage (test 34)
  - separate create + wait_for_running log lines (test 37)
  - 409 collision picks up existing ws (test 38)
  - run-all-and-tally per-stage (test 39)
  - SSH stderr token redaction (test 46)
  - dashboard cookie no-expires assertion (test 59)
- **4 recommend gaps** (acknowledged but not auto-added):
  - subprocess-based two-stage SIGINT test (test 14) — **surfaced to gate**
  - STAGE_START/END event ordering (test 31)
  - app health pre-stage-4 retry (test 55)
  - coder CLI version log at startup
- **1 surface-to-gate gap** (HIGH 3.3):
  - Stage 4 spike fallback branching — Task 12 should be split. **Surfaced to gate as TASTE DECISION.**

**CRITICAL test bug (3.2):** `test_delete_workspace_times_out` doesn't mock `time.sleep`, blowing the <5s suite budget. **Auto-decided fix:** add `tests/conftest.py` autouse fixture:

```python
@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
```

Apply to Task 1's conftest.

## Section 4: Performance

Sequential by design — no N+1 in the multi-request sense, but a few real issues:

- **HIGH 4.1 (no progress indicator):** auto-decided — emit one stderr line per workspace per stage start.
- **MEDIUM 4.2 (POLL_INTERVAL_S):** auto-decided — move to `config.toml [poll] interval_seconds`.
- **MEDIUM 4.3 (fence-post in deadline):** auto-decided — restructure as `for attempt in range(...)` loop.
- **MEDIUM 1.4 (template lookup cached):** auto-decided — memoize `_resolve_template_id`.

No memory leaks (sync code, finite `for` loops, contexts always closed in `try/finally`). No caching beyond the template-ID memoize.

## Mandatory outputs (Phase 3)

### NOT in scope

| Item | Why deferred | Disposition |
|------|--------------|------------|
| StageContext refactor to explicit StageResult.products | Material rework; greenfield v1 ships with mutable state | TODOS.md |
| `cleanup.py` class-instance state (vs global `_int_count`) | Refactor not needed if we ship subprocess test | Deferred to follow-on |
| Thread-with-timeout wrapper for Playwright sync calls (HIGH 2.2) | Only fires under SIGINT-during-stage-5; document and accept for v1 | Documented in spec |
| Per-user API tokens for stage 5 (would fix HIGH 5.1 root cause) | Accepted in CEO premise gate | TODOS.md |
| kubectl/Prometheus metric correlation | 12-month ideal | TODOS.md |

### What already exists

(Carried from Phase 1; no new entries.)

### Architecture diagram

See Section 1 above (ASCII dependency graph).

### Test diagram → coverage

See test-plan artifact at `~/.gstack/projects/cst3/yxiao-main-test-plan-20260508-135401.md` (62 codepaths/flows mapped).

### Failure modes registry (updated from Phase 1)

| # | Failure mode | Likelihood | Impact | Mitigation in plan? |
|---|--------------|-----------|--------|---------------------|
| 1 | `coder` CLI missing on PATH | Medium | Stage 2 fails | ✅ Task 7 pre-flight |
| 2 | Stage 4 spike fails | Medium | Stage 4 not implementable as designed | ✅ Task 6 retires before Task 12 |
| 3 | Playwright Chromium not installed | Medium | Stage 5 fails | ⚠ → ✅ Auto-decided: add Task 7 pre-flight |
| 4 | SIGINT during Playwright sync call | Low | Cleanup might delay, not skip | ⚠ Documented; accept for v1 |
| 5 | Workspace name collision | Medium | Stage 1 fails | ⚠ → ✅ Auto-decided: 409 picks up existing ws into ledger |
| 6 | Cluster has < N non-admin users | Medium | Stage 0 fails | ✅ Stage 0 returns FAIL clearly |
| 7 | Template `template_name` doesn't exist | Medium | Stage 1 fails immediately | ✅ `_resolve_template_id` raises |
| 8 | `tcp-echo` app not configured on template | Medium | Stage 4 fails | ✅ First-op surfaces clearly |
| 9 | Workspace deletion during in-flight build | Low | Cleanup may be rejected | ⚠ Accept; Coder API returns useful error |
| 10 | Op-level fail-fast hides per-fleet rate | High | Limited diagnostic | ✅ Auto-decided: run-all-and-tally per stage |
| 11 | **NEW:** Token leak via err strings (HIGH 5.3) | Medium | Token in log file | ✅ Auto-decided: `_redact()` helper |
| 12 | **NEW:** Token via /proc/<pid>/environ (HIGH 5.2) | Low | Token visible to same-uid procs | ⚠ Documented; accept multi-user-host risk |
| 13 | **NEW:** Token follows redirect (MEDIUM 6.4) | Low | Token leak to redirect target | ✅ Auto-decided: `allow_redirects=False` on session |
| 14 | **NEW:** Workspace agent ready ≠ app ready (HIGH 6.2) | High | Stage 4 first-op flake | ⚠ → ✅ Auto-decided: brief retry on first-connect |
| 15 | **NEW:** Clock skew (HIGH 6.1) | Low | User-sort ordering | ⚠ → ✅ Auto-decided: warn-only at startup |
| 16 | **NEW:** Disk full on log file (MEDIUM 6.3) | Low | Runner crashes | ⚠ → ✅ Auto-decided: try/except → stderr fallback |
| 17 | **NEW:** Two simultaneous runs collide (LOW 6.7) | Low | Stage 1 collision | ⚠ → ✅ Auto-decided: run-id prefix in workspace names |
| 18 | **NEW:** Coder CLI version skew (MEDIUM 2.3) | Low | Stage 2 parse breaks | ⚠ → ✅ Auto-decided: log `coder --version` at startup |
| 19 | **NEW:** `wait_for_running` doesn't handle `canceled` (LOW 4.4) | Low | Polls until timeout | ⚠ → ✅ Auto-decided: terminal-error on canceled/unknown |
| 20 | **NEW:** Cleanup ledger process-local (LOW 6.6) | Low | SIGKILL leaks workspaces | ✅ Already in non-goals |

### Eng completion summary

| Item | Result |
|------|--------|
| CRITICAL findings | 1 (test sleep mock budget bug) |
| HIGH findings | 12 |
| MEDIUM findings | 8 |
| LOW findings | 5 |
| Auto-decided fixes | 18 (most are small / per-failure-mode) |
| Surface-to-gate (TASTE DECISIONS) | 2 (Stage 4 spike branching split into 12a/12b; subprocess SIGINT test) |
| Mode | FULL_REVIEW |
| Voice mode | `[subagent-only]` |

**Phase 3 complete.** Single-voice review (codex unavailable). 1 critical, 12 high, 8 medium, 5 low. 18 fixes auto-applied; 2 taste decisions surfaced to final gate. Passing to Phase 3.5 (DX review).

---

# /autoplan Review — Phase 3.5: DX

**Run date:** 2026-05-08 14:05 EDT
**Voice mode:** `[subagent-only]` — Codex CLI unavailable.
**Persona:** SRE/platform engineer who maintains a Coder deployment. Comfortable with `kubectl` and `helm`. Has used `coder` CLI a few times. Reads Python; not necessarily debugs it. Will run this tool weekly or after Coder upgrades.

## Step 0: DX scope assessment

**Product type:** Operator CLI tool. The user installs it on a workstation or jumphost, runs it on demand against a Coder cluster, parses the log file, optionally re-runs after a config tweak.

**Initial DX completeness:** 4/10 across 8 dimensions before this review. The plan's Task 15 ships a quick-start but no troubleshooting, no architecture-for-operators, no FAQ, no post-run summary. CLI is one flag (`--config`).

**Initial TTHW estimate:** 5 min (the plan's implicit target). **Realistic TTHW (subagent):** 20–40 min on first run, including the iterative "fix config, re-run, watch fail, fix again" loop.

## Step 0.5: Dual voices

### CLAUDE SUBAGENT (DX — independent review)

Findings, by DX dimension, severity-classified:

**Getting started friction (subagent score: 3/10).**

- **HIGH:** Plan never tells operator to install `uv` first. Add 1–3 min on a clean workstation.
- **HIGH:** `playwright install chromium` is the dominant first-run cost (~170 MB; 30 sec to 5 min depending on network). README treats it as a one-liner.
- **HIGH:** Template prerequisite (`tcp-echo` app on `app.tcp_port`) is buried in Runtime Requirements. First failure on stage 4 will be `ws_closed: code=1006`, not a clear "your template is missing the tcp-echo app."
- **MEDIUM:** No `coder tokens create` hint near the `CODER_SESSION_TOKEN` export. Operator spelunks docs for 2–5 min.
- **MEDIUM:** No `--validate-config` mode. Only validation is a full run.

**API/CLI ergonomics (subagent score: 3/10).**

- **HIGH:** Single `--config` flag is under-designed for a tool that takes 5–30 min per run. Missing flags an SRE reaches for in week one:
  - `--version`
  - `--validate-config` / `--dry-run`
  - `--stage <name>` (re-run one stage without re-provisioning)
  - `--skip-cleanup` / `--cleanup-only`
  - `--users alice,bob` (CLI override of allow-list)
  - `--quiet` / `--verbose`
- **MEDIUM:** Hyphen-vs-underscore confusion: `pyproject.toml` declares `coder-scale-test = ...` but README only shows `python -m coder_scale_test`. Two different invocation surfaces for the same tool.

**Error messages actionable (subagent score: 4/10).**

- **HIGH:** Recovery hints from CEO §2 live in the autoplan section of the plan doc, not in the shipped tool's output or README. When stage 4 fails with `ws_closed: code=1006` at 3am, the operator sees one log line and an exit code. No runbook.
- **MEDIUM:** Errors are categorized (timeout/http/ws_closed/mismatch/subprocess/playwright) but not bound to a hint at log time. Fix: emit the hint as a second log line right after FAIL.

**Docs findability (subagent score: 4/10).**

- **HIGH:** No troubleshooting section in Task 15's prescribed README.
- **HIGH:** No "How to create a `tcp-echo` template" instructions. Just "must already exist."
- **MEDIUM:** "Where do I get a session token?" not in docs.
- **MEDIUM:** "What does the log file look like?" — no example in README; buried in design spec.

**Docs completeness (subagent score: 2/10).**

- **HIGH:** No "What this tool doesn't do" section in README. SREs will try to use this for "find the breaking point of my cluster" and waste a day.
- **HIGH:** No architecture-for-operators diagram (REST vs WS vs CLI vs browser per stage).
- **HIGH:** No FAQ or common-errors table.
- **MEDIUM:** JSONL output (auto-expansion) not yet mentioned in README — operator can't discover it.

**Upgrade path / escape hatches (subagent score: 3/10).**

- **HIGH:** Poll interval hardcoded (2 sec). Slow cluster wastes load; fast cluster wastes time per op.
- **HIGH:** Workspace name pattern `scaletest-{user}-{m}` hardcoded. If a previous run leaked workspaces with these names, stage 1 fails with no override.
- **HIGH:** No HTTP retries / backoff. Transient 502s fail-fast a stage.
- **HIGH:** No TLS verify-off for self-signed clusters. Internal CAs require monkey-patch.
- **MEDIUM:** No proxy support documented (`HTTPS_PROXY` works via `requests` by default but not noted).
- **MEDIUM:** No per-stage skip list — operator can't say "skip stage 5, I don't care about dashboard today."
- **MEDIUM:** `coder` CLI path assumed on `PATH`. No env override.
- **MEDIUM:** Progress output destination — none.

**Dev environment friction-free (subagent score: 8/10).**

- ✅ `uv` + pytest is clean.
- ✅ Mocked tests are fast.
- ⚠ No CI configuration in plan (no `.github/workflows/test.yml`). Out of scope for v1; flag for follow-on.

**Magical-moment delivery (subagent score: 2/10).**

- **HIGH:** No post-run summary. Operator gets exit code 0 and an empty stdout. Has to grep their own log to know what happened. Deflating moment, not magical.
- **MEDIUM:** No baseline-vs-last-run comparison. SREs want trend tracking; the JSONL auto-expansion unblocks this but the tool itself doesn't surface it.

### CODEX SAYS (DX — developer experience challenge)

`[codex-unavailable: binary not found]` — single-voice review.

### DX consensus table

```
DX DUAL VOICES — CONSENSUS TABLE:    [voice mode: subagent-only]
═══════════════════════════════════════════════════════════════
  Dimension                            Claude  Codex   Consensus
  ──────────────────────────────────── ─────── ─────── ─────────
  1. Getting started < 5 min?          NO      N/A     SINGLE-NO   (realistic 20-40 min)
  2. API/CLI naming guessable?         PARTIAL N/A     SINGLE-PARTIAL (--config alone insufficient; many missing flags)
  3. Error messages actionable?        NO      N/A     SINGLE-NO   (hints not in tool output)
  4. Docs findable & complete?         NO      N/A     SINGLE-NO   (no troubleshooting, FAQ, architecture diagram)
  5. Upgrade path safe?                PARTIAL N/A     SINGLE-PARTIAL (many hardcoded knobs)
  6. Dev environment friction-free?    YES     N/A     SINGLE-YES   (uv + pytest clean)
═══════════════════════════════════════════════════════════════
```

## Developer journey map (9-stage)

| Stage | Operator action | Pain points | Auto-decided fix |
|-------|----------------|------------|------------------|
| 1. Hear about tool | Read repo description | "Scale testing" mislabel (CEO P1) | Documented limitation in non-goals |
| 2. Install prerequisites | `uv` install, then this package, then `playwright install chromium`, then `coder` CLI | 4 install steps; no consolidated script; Playwright chromium is the killer | Add `make install` or `./bootstrap.sh` script (auto-decided) |
| 3. Configure | Copy `config.toml.example`, fill in 7 fields | Operator doesn't know `tcp-echo` app prerequisite, doesn't know how to mint token, doesn't know required selector | Add inline comments to `config.toml.example` with explicit `coder tokens create` hint, link to template requirements (auto-decided) |
| 4. First run | `coder-scale-test --config config.toml` | Silent terminal for ~10+ min; no progress | stderr progress lines auto-decided in Eng §4.1 |
| 5. First failure | Read log, decode error category | Hints not in log; no runbook | Auto-decided: emit `HINT:` line after each FAIL; add troubleshooting section to README |
| 6. Iterate | Fix config, re-run | Has to re-run all 6 stages even for one fix | Auto-decided: add `--stage <name>` flag |
| 7. First success | Tool exits 0 | No summary; have to grep log | Auto-decided: post-run 5-line summary to stdout |
| 8. Adopt as routine | Schedule weekly cron / manual rotation | No baseline storage, no trend dashboard | Deferred: JSONL output unblocks; trend tooling out of scope |
| 9. Upgrade Coder | Re-run after cluster upgrade | Selector or URL pattern changes silently break the tool; no version log to compare | Auto-decided: log `coder --version` and tool version at startup (already in Eng §2.3) |

## Developer empathy narrative (first-person SRE)

> *I'm running on-call this week. Production Coder cluster started rejecting workspace creates intermittently around 2am. I want to know if every transport is healthy or just provisioning is slow.*
>
> *I `git clone` the tool. README says install `uv`, run `pip install`, install Chromium. The Chromium download takes 4 minutes on the corp VPN. While I wait, I edit `config.toml` — but I don't know what `tcp-echo` app means, and I don't know what `dashboard.ready_selector` is. I check our cluster's templates: nothing called `tcp-echo`. The README says "must already exist." Great.*
>
> *I skip stage 4 and stage 5 mentally and just run the tool. It runs silently for 8 minutes. I open a second terminal to `tail -f scale-run.log`. Stage 1 finishes 30 workspaces. Stage 2 fails on workspace 3: `ws_closed: code=1006`. I don't know what code 1006 means. I open the design spec, find the error category table, find a hint about agent down. I `kubectl logs` the agent — it's fine. I re-run. Same failure on workspace 7 this time.*
>
> *I lose 90 minutes before I realize the SSH timeout is too tight for our cluster. There's no `--stage 2 --workspace alice/foo` mode to iterate. Every retry is 8 minutes. I give up at 3:30am, file a ticket, go back to bed.*
>
> *Did the tool tell me my cluster is unhealthy? Yes — but it took me 4 hours to extract that signal.*

## Sections / passes 1–8 (DX skill — abbreviated)

For each, what was examined and the auto-decided fix:

- **§1 First impression:** README opens with project description, methodology, quick-start. **Missing:** "What this is and isn't" up top. Auto-decided: prepend a 3-line "TL;DR what / who / what-it-doesn't-do" block.
- **§2 Onboarding flow:** see journey-map above.
- **§3 Naming consistency:** `coder-scale-test` (CLI) vs `coder_scale_test` (module) vs `coder-scale-testing` (repo) vs `coder-scale-test` (package in pyproject). Auto-decided: Task 15 README and Task 14 entry point both point users to the entry-point (`coder-scale-test`) — drop `python -m` from operator-facing docs.
- **§4 Defaults:** Most defaults are sensible. Hardcoded poll interval and workspace name pattern auto-decided as configurable (already in Eng review).
- **§5 Errors:** see §3 finding above; auto-decided HINT-after-FAIL pattern.
- **§6 Documentation:** auto-decided expansions to README in Task 15.
- **§7 Upgrade safety:** Tool version logged at startup; `coder --version` logged. Auto-decided.
- **§8 Magical moment:** Post-run 5-line summary. Auto-decided.

## Auto-decided DX expansions

| # | Expansion | File / task affected | Effort |
|---|-----------|---------------------|--------|
| 1 | New CLI flags: `--version`, `--validate-config`, `--stage <name>`, `--skip-cleanup`, `--cleanup-only`, `--users alice,bob`, `--quiet`, `--verbose` | Task 14 (`__main__.py`) | ~50 LOC + tests |
| 2 | Drop `python -m` from operator-facing docs; standardize on `coder-scale-test` entry point | Task 15 (README) | Doc-only |
| 3 | Post-run summary (5 lines: total ops, ok/fail, total time, slowest stage, log path) | Task 7 (`runner.py`) print to stdout after `RUN_END` | ~20 LOC + 1 test |
| 4 | Progress lines to stderr per workspace per stage start | Task 7 + each stage | ~10 LOC across stages |
| 5 | `HINT:` log line after each FAIL with category-specific recovery hint | Task 3 (`log.py`) — add `log_op(..., hint=...)` overload | ~15 LOC + tests |
| 6 | README troubleshooting section: per error category → causes + commands to run | Task 15 step 2 | Doc-only |
| 7 | README "What this tool doesn't do" section (Non-goals from spec) | Task 15 step 2 | Doc-only |
| 8 | README architecture-for-operators diagram (1 ASCII or mermaid: per-stage transport) | Task 15 step 2 | Doc-only |
| 9 | README "How to mint a Coder session token" + `coder tokens create` example | Task 15 step 2 | Doc-only |
| 10 | README "How to set up a `tcp-echo` template" with example template TF or recipe | Task 15 step 2 | Doc-only (one example) |
| 11 | `config.toml.example` inline comments per field with the prerequisite each implies | Task 15 step 1 | Doc-only |
| 12 | Move poll interval to `config.toml [poll] interval_seconds` (already in Eng) | Task 5 + Task 2 | ~10 LOC |
| 13 | HTTP `Retry` adapter on `requests.Session` (3 retries, exponential backoff, only on 5xx) | Task 5 (`coder_client.py`) | ~10 LOC + 1 test |
| 14 | TLS verify-off via `config.toml [tls] verify = true` (default true) | Task 5 + Task 2 | ~5 LOC + 1 test |
| 15 | `[poll]`, `[tls]`, `[retries]` sections added to `config.toml.example` | Task 15 step 1 | Doc-only |
| 16 | Per-stage skip list `config.toml [stages] skip = ["dashboard"]` | Task 7 (runner) + Task 2 (config) | ~10 LOC + 1 test |
| 17 | `CODER_BIN` env override for `coder` CLI path (`shutil.which("coder")` fallback) | Task 7 (`_check_coder_cli`) | ~5 LOC |
| 18 | Tool version + `coder --version` logged at startup (already in Eng §2.3) | Task 7 | ~5 LOC |
| 19 | Bootstrap script `./bootstrap.sh` that does `uv venv && uv pip install -e ".[dev]" && uv run playwright install chromium` | New file at repo root | ~10 lines |

**Total LOC estimate: ~150 LOC + corresponding tests + doc expansion (~200 lines of README).** All in blast radius (the file structure is unchanged), all <1 day CC effort each.

## DX scorecard (post-autoplan auto-decisions)

| Dimension | Initial | Post-autoplan target | Status |
|-----------|---------|---------------------|--------|
| Getting started friction | 3/10 | 6/10 | Bootstrap script + clearer prereqs help; Chromium download still ~3 min on VPN |
| API/CLI ergonomics | 3/10 | 8/10 | 8 new flags address every documented week-one need |
| Error messages actionable | 4/10 | 8/10 | HINT lines + troubleshooting section close the gap |
| Docs findability | 4/10 | 7/10 | Troubleshooting + architecture diagram findable in <2 min |
| Docs completeness | 2/10 | 7/10 | What-it-doesn't-do, FAQ, mint-token, template-recipe added |
| Upgrade path / escape hatches | 3/10 | 7/10 | TLS, retries, poll, per-stage-skip, CODER_BIN configurable |
| Dev environment friction-free | 8/10 | 8/10 | Already good |
| Magical-moment delivery | 2/10 | 7/10 | Post-run summary + JSONL trend-tracking unblock |
| **Aggregate** | **3.6/10** | **7.25/10** | |

## DX implementation checklist (added to plan execution)

- [ ] Task 1 conftest.py adds `_no_real_sleep` autouse fixture (CRITICAL Eng 3.2)
- [ ] Task 2 config: add `[poll] interval_seconds`, `[tls] verify`, `[retries] count/backoff_seconds`, `[stages] skip` sections; `users` optional allow-list field
- [ ] Task 3 log.py: add JSONL writer alongside plain log; `_redact()` helper for token scrubbing; `log_op(..., hint=...)` overload
- [ ] Task 5 coder_client.py: cache `_resolve_template_id`; add `Retry` adapter on Session; add User-Agent; per-request token (defense in depth); fix fence-post in `wait_for_running`; treat `canceled`/`unknown` as terminal
- [ ] Task 6 spike: unchanged; outcome may force Task 12 split
- [ ] Task 7 runner.py: post-run summary to stdout; stderr progress lines; CODER_BIN env override; tool + `coder --version` startup log; clock-skew warning; Playwright Chromium pre-flight
- [ ] Task 8 users: respect allow-list when set
- [ ] Task 9 provision: emit separate create + wait_for_running log lines; run-all-and-tally per stage; 409 picks up existing ws into ledger
- [ ] Task 10 ssh: stderr token redaction
- [ ] Task 11 web_terminal: no changes beyond token redaction in errors
- [ ] Task 12 app_traffic: brief retry-with-backoff on first WS connect (HIGH 6.2); spike-driven branching (12a/12b) — surface to gate
- [ ] Task 13 dashboard: assertion that no `storage_state` is wired; explicit comment forbidding `user_data_dir`
- [ ] Task 14 __main__: 8 new CLI flags
- [ ] Task 15 README: troubleshooting, architecture diagram, what-this-doesn't-do, mint-token, tcp-echo template recipe, JSONL note; bootstrap.sh script

## TTHW assessment

- **Current (per spec/plan as-written):** 5 min target; 20–40 min realistic for a first-time SRE
- **Post-autoplan auto-decisions:** 8–15 min realistic (bootstrap script + better config docs + clearer prereqs cut the discovery time; Chromium download still dominant)
- **Target for v1.5 (deferred):** <5 min via `coder-scale-test bootstrap` subcommand that mints token, finds users, picks template — out of scope for v1

## Mandatory outputs (Phase 3.5)

- ✅ Developer journey map (9 stages)
- ✅ Developer empathy narrative (first-person SRE)
- ✅ DX scorecard (8 dimensions with initial + target scores)
- ✅ DX implementation checklist (above)
- ✅ TTHW assessment with target

## DX completion summary

| Item | Result |
|------|--------|
| HIGH findings | 12 |
| MEDIUM findings | 9 |
| Auto-decided expansions | 19 (totaling ~150 LOC + doc work) |
| Surface-to-gate (TASTE) | 0 (DX expansions all in blast radius + clear wins) |
| Mode | DX POLISH |
| Voice mode | `[subagent-only]` |
| Initial DX score | 3.6/10 |
| Target DX score (post-auto) | 7.25/10 |
| TTHW current → target | 20-40 min → 8-15 min |

**Phase 3.5 complete.** Single-voice review (codex unavailable). 12 high, 9 medium. 19 expansions auto-applied. No DX-specific gate items beyond what Eng surfaced. Passing to Phase 4 (Final Approval Gate).

---

# /autoplan Revisions from Final-Gate Overrides

User overrode 3 taste decisions and pulled 4 deferred items back into scope. This section records both.

## Taste-decision overrides (D2 from final gate)

| ID | Original recommendation | User override | Plan impact |
|---|------------------------|---------------|-------------|
| T1 | Split Task 12 into 12a (Pattern A/B) + 12b (shell-out fallback) | **Keep Task 12 as one task**; swap URL builder mid-implementation if spike outcome demands shell-out | Task 12 stays single. If spike picks shell-out, the test scaffolding (`websocket.create_connection` mocks) becomes invalid and must be rewritten as part of Task 12 step 3. Acknowledged risk: tests-as-written are a lie if spike says shell-out. |
| T2 | Add subprocess-based two-stage SIGINT test to `tests/test_cleanup_sigint_subprocess.py` | **Skip the subprocess test**; rely on manual smoke testing of cleanup-during-Ctrl-C | Test plan artifact gap #14 stays open. Documented in spec's "What's NOT tested" already. Acknowledged: zero coverage of the two-stage handler at unit-test level. |
| T3 | Keep `StageContext` mutable lists for v1; document implicit ordering contract | **Refactor to explicit StageResult.products** | Tasks 7–13 each change. New shape below. |

## T3 refactor — StageContext to explicit StageResult.products

Replaces the mutable `ctx.user_ids` and `ctx.workspaces` with explicit per-stage products threaded through the runner.

### New shape

```python
# runner.py — replaces the existing StageResult and StageContext

@dataclass(frozen=True)
class StageResult:
    ok: bool
    err: str | None = None
    # Each stage that produces downstream-consumed data sets exactly one field below:
    users: list[User] | None = None              # set by stages/users.py
    workspaces: list[Workspace] | None = None    # set by stages/provision.py

@dataclass
class StageContext:
    cfg: Config
    client: CoderClient
    ledger: WorkspaceLedger
    log: IO[str]
    # Read-only after stage 0/1; not mutated by later stages.
    users: list[User] = field(default_factory=list)
    workspaces: list[Workspace] = field(default_factory=list)
```

### Runner flow change

```python
# runner.py:run() — relevant excerpt

for stage in stages:
    stage_name = _stage_name(stage)
    log_event(log, "STAGE_START", stage=stage_name)
    result = stage.run(ctx)
    log_event(log, "STAGE_END", stage=stage_name, ok=result.ok)
    if not result.ok:
        ...; break
    # Thread products from this stage to ctx for downstream stages
    if result.users is not None:
        ctx.users = result.users
    if result.workspaces is not None:
        ctx.workspaces = result.workspaces
```

### Stage signature changes (Tasks 8, 9, 11, 12, 13)

- **Task 8 (users.run)**: returns `StageResult(ok=True, users=picked)` instead of `ctx.user_ids = picked`. Test `test_picks_first_n` updates: `assert res.users == [...]` instead of `assert ctx.user_ids == [...]`.
- **Task 9 (provision.run)**: returns `StageResult(ok=True, workspaces=running)` instead of `ctx.workspaces.append(...)`. Tests update similarly.
- **Tasks 10, 11, 12, 13 (ssh, web_terminal, app_traffic, dashboard)**: read `ctx.workspaces` (or `ctx.users` for stage 13) — no change to read-side; mutation removed from precondition.

### Test impact

Each stage test that asserts on `ctx.user_ids` / `ctx.workspaces` now asserts on `res.users` / `res.workspaces`. ~6 tests updated, ~12 lines each. Fixture `ctx` in each test no longer pre-populates `user_ids`/`workspaces` — tests construct a `StageContext` with empty defaults and pass workspaces via the prior stage's mock-return.

### Estimated LOC impact

- runner.py: +20 LOC (StageResult fields, threading logic)
- Each of 6 stage modules: ~5 LOC change (return shape) → 30 LOC
- Tests: ~6 stage tests × 12 LOC = 72 LOC
- **Total: ~120 LOC delta** across 13 files. ~3 hours CC effort.

---

## New tasks from pulled-back deferred items

The user pulled deferred items 5–8 from TODOS.md back into v1 scope. These become Tasks 16–19. Each is a new feature with its own TDD pair.

## Phase 7 — Pulled-back features (Tasks 16–19)

### Task 16: Cluster metric correlation (`stages/metrics.py` — new orchestrator-level concept)

**Was deferred-item 5: kubectl/Prometheus metric correlation during runs.**

This is **not** a stage in the per-workspace sense. It's a sidecar that samples cluster metrics at stage boundaries and emits them to the log. Two sub-features:

1. **kubectl-side sampling**: at `STAGE_START` and `STAGE_END`, run `kubectl top pods -n coder --no-headers` and emit a `METRICS` log event with parsed CPU/memory per pod.
2. **Prometheus-side sampling** (optional, gated on `[metrics] prometheus_url` config): at the same boundaries, query `up`, `coder_provisioner_jobs_pending`, `coder_database_connections_open` (configurable list), emit a `METRICS` log event with values.

**Files:**
- Create: `src/coder_scale_test/metrics.py` (~150 LOC: `KubectlSampler`, `PrometheusSampler`, `MetricsCollector` orchestrator)
- Modify: `src/coder_scale_test/runner.py` (call `metrics.sample(stage_name, "start")` / `"end"` if configured)
- Modify: `src/coder_scale_test/config.py` (add `[metrics]` section: `kubectl_namespace = "coder"`, `prometheus_url = ""`, `prometheus_queries = [...]`)
- Test: `tests/test_metrics.py` (~10 tests with `subprocess.run` and `requests.get` mocked)

**Steps:**
1. Write failing tests asserting `KubectlSampler.sample()` parses kubectl-top stdout into `dict[pod_name, {cpu_m, mem_mi}]`; `PrometheusSampler.sample()` queries multiple metrics in parallel and returns a flat dict; `MetricsCollector` is no-op when neither is configured.
2. Run tests, verify failure.
3. Implement: subprocess for kubectl, `requests.get` for Prometheus, fail-soft (log a warning, never abort the runner).
4. Verify pass.
5. Wire into `runner.run`: call collector at each STAGE_START / STAGE_END.
6. Add log event format: `<ts> METRICS stage=ssh phase=start kubectl=[...] prometheus=[...]`.
7. Commit: `feat(metrics): kubectl + prometheus sampling at stage boundaries`.

**Risk:** kubectl-top requires `metrics-server` installed on the cluster. If absent, sampler logs warning and emits empty metrics. Prometheus URL is optional. Both fail-soft.

---

### Task 17: Trend dashboard / log parser (`tools/trend.py` — new operator script)

**Was deferred-item 6: trend dashboard / log parser for JSONL output.**

A small CLI that parses N JSONL log files (e.g., last 10 weekly runs), aggregates per-stage p50/p95/p99 latency and pass-rate, and prints a tabular trend report. Optionally writes a static HTML page with a sparkline per stage.

**Files:**
- Create: `tools/trend.py` (~120 LOC; uses stdlib `argparse`, `json`, `statistics`; HTML output uses inline templating)
- Test: `tests/test_trend.py` (~6 tests with synthetic JSONL fixtures)

**CLI:**
```bash
coder-scale-test-trend --logs run-1.jsonl run-2.jsonl run-3.jsonl  # tabular
coder-scale-test-trend --logs run-*.jsonl --html out.html           # HTML output
```

**Steps:**
1. Write failing tests asserting trend table columns (`stage`, `n_runs`, `pass_rate`, `p50_ms`, `p95_ms`, `p99_ms`) and HTML output contains a `<table>` with sparkline `<svg>` per stage.
2. Run tests, verify failure.
3. Implement: read JSONL, group by stage+op_id, compute statistics across runs.
4. Verify pass.
5. Add to `pyproject.toml` `[project.scripts]`: `coder-scale-test-trend = "coder_scale_test.tools.trend:main"`. Move file to `src/coder_scale_test/tools/trend.py`.
6. Commit: `feat(trend): aggregate JSONL logs into trend table + HTML`.

**Risk:** depends on JSONL output expansion (auto-decided in CEO §0D). Cannot ship before JSONL is implemented.

---

### Task 18: CI configuration (`.github/workflows/test.yml`)

**Was deferred-item 7.**

A simple GitHub Actions workflow: on push and PR, run lint (ruff), type check (mypy optional), and `pytest`.

**Files:**
- Create: `.github/workflows/test.yml`
- Modify: `pyproject.toml` (add `ruff` and optionally `mypy` to `[project.optional-dependencies] dev`)

**Steps:**
1. Add `ruff>=0.5` and `mypy>=1.10` to dev deps. Configure in `pyproject.toml`:
   ```toml
   [tool.ruff]
   line-length = 100
   target-version = "py311"
   [tool.ruff.lint]
   select = ["E", "F", "I", "B", "UP", "SIM"]
   ```
2. Run `ruff check .` and `pytest` locally; fix any flagged issues.
3. Write `.github/workflows/test.yml`:
   ```yaml
   name: test
   on: [push, pull_request]
   jobs:
     test:
       runs-on: ubuntu-latest
       strategy:
         matrix:
           python-version: ["3.11", "3.12", "3.13"]
       steps:
         - uses: actions/checkout@v4
         - name: Install uv
           uses: astral-sh/setup-uv@v3
         - name: Install Python
           run: uv python install ${{ matrix.python-version }}
         - name: Install deps
           run: uv pip install --system -e ".[dev]"
         - name: Lint
           run: ruff check .
         - name: Test
           run: pytest -q
   ```
4. Push branch and verify the workflow runs green on a Coder repo PR.
5. Commit: `ci: add GitHub Actions workflow for lint + pytest`.

**Risk:** requires GitHub remote (this repo currently has none). Workflow file is harmless until pushed; commit it now and it activates on first push to a GitHub-hosted remote.

---

### Task 19: `coder-scale-test bootstrap` subcommand (TTHW-killer)

**Was deferred-item 8: v1.5 bootstrap subcommand.**

A subcommand that takes a Coder URL + admin token and produces a working `config.toml`. Auto-discovers: existing templates with a `tcp-echo` app, active non-admin users, sensible timeouts based on cluster responsiveness.

**Files:**
- Create: `src/coder_scale_test/bootstrap.py` (~150 LOC)
- Modify: `src/coder_scale_test/__main__.py` (subparser routing — `coder-scale-test bootstrap` vs `coder-scale-test run`)
- Modify: `src/coder_scale_test/config.py` (add `Config.write(path)` method that round-trips back to TOML)
- Test: `tests/test_bootstrap.py` (~5 tests with `CoderClient` mocked)

**CLI:**
```bash
export CODER_SESSION_TOKEN=...
coder-scale-test bootstrap --coder-url https://coder.example.com --output config.toml
```

**Steps:**
1. Write failing tests asserting `bootstrap.run(coder_url, token, out_path)`:
   - Lists templates and picks the first one with an app slug `tcp-echo`
   - Lists active non-admin users and picks the first 10 (defaults `num_users=10`, `per_user=3`)
   - Probes `/api/v2/users` and times the request to set `provision_workspace` timeout (10× round-trip latency, min 60s)
   - Writes a valid `config.toml` to `out_path`
   - Fails clearly if no template has `tcp-echo` app
2. Run tests, verify failure.
3. Implement using `CoderClient` REST methods.
4. Verify pass.
5. Wire into `__main__.py` subparser:
   ```python
   parser = argparse.ArgumentParser(prog="coder-scale-test")
   sub = parser.add_subparsers(dest="cmd", required=True)
   run_p = sub.add_parser("run"); run_p.add_argument("--config", ...)
   boot_p = sub.add_parser("bootstrap"); boot_p.add_argument("--coder-url", required=True); boot_p.add_argument("--output", default="./config.toml")
   ```
6. Update README quickstart: `coder-scale-test bootstrap` is the new step 2.
7. Commit: `feat(bootstrap): auto-generate config.toml from a Coder URL + token`.

**Risk:** depends on `CoderClient` (Task 5) and config TOML write capability (small new method). The "find a tcp-echo app" logic may miss valid templates if the operator's app slug isn't literally `tcp-echo` — make it configurable (`--app-slug tcp-echo`).

---

## Updated phase / task structure

| Phase | Tasks | Net LOC delta |
|-------|-------|---------------|
| 1 — Foundations | 1–5 | +0 (Task 1 conftest gains autouse `_no_real_sleep` fixture, ~5 LOC) |
| 1.5 — Spike | 6 | +0 |
| 2 — Runner + stage 0 | 7–8 | +120 (T3 refactor) |
| 3 — Stages 1–2 | 9–10 | +30 (T3 refactor + Eng auto-decisions: separate log lines, run-all-and-tally, 409 ledger) |
| 4 — Stages 3–4 | 11–12 | +50 (T3 refactor + first-connect retry; Task 12 swap-URL-builder if spike picks shell-out) |
| 5 — Stage 5 | 13 | +25 (T3 refactor + cookie-no-storage_state assertion) |
| 6 — CLI + docs | 14–15 | +200 (8 new flags + bootstrap subparser + comprehensive docs) |
| **7 — Pulled-back features** | **16–19** | **+540** (metrics +250, trend +130, ci +30, bootstrap +180; LOC includes tests) |
| **Total delta** | **+~965 LOC** | |

**Plan grew from 15 tasks to 19 tasks; from ~1500 LOC to ~2465 LOC.** Scope ~65% larger than the original plan.

## Updated audit trail

| # | Phase | Decision | Class | Principle | Rationale | Rejected |
|---|-------|----------|-------|-----------|-----------|---------|
| 11 | Final gate D2 | T1 override: keep Task 12 single | User Override | n/a | User accepted the risk that tests become invalid if spike picks shell-out | Split into 12a/12b (my recommendation) |
| 12 | Final gate D2 | T2 override: skip subprocess SIGINT test | User Override | n/a | User accepts manual smoke testing of cleanup-during-Ctrl-C | Add subprocess test (my recommendation) |
| 13 | Final gate D2 | T3 override: refactor StageContext to explicit StageResult.products | User Override | n/a | User wants explicit contract over greenfield-pragmatism | Keep mutable (my recommendation) |
| 14 | Final gate D2 | Pull deferred-5 (kubectl/Prometheus metrics) into v1 → Task 16 | User Override | n/a | User wants metric correlation in v1 | Defer to follow-on |
| 15 | Final gate D2 | Pull deferred-6 (trend dashboard/log parser) into v1 → Task 17 | User Override | n/a | User wants trend tooling in v1 | Defer to follow-on |
| 16 | Final gate D2 | Pull deferred-7 (CI workflow) into v1 → Task 18 | User Override | n/a | User wants CI in v1 | Defer to follow-on |
| 17 | Final gate D2 | Pull deferred-8 (bootstrap subcommand) into v1 → Task 19 | User Override | n/a | User wants TTHW-killer in v1 | Defer to follow-on |
| 18 | Final gate D2 (clarification) | Reject all 4 remaining TODOS items (rename, upstream re-eval, concurrent ops, per-user tokens) | User Override | n/a | User explicitly closed all deferred items — none come back | Keep as deferred (my prior recommendation) |

## Updated TODOS.md — REJECTED

User rejected (not deferred) all remaining TODOS items at final gate. These are explicitly closed for v1 AND future versions; they will not be revisited unless the user re-opens them.

| # | Item | Status | Implication |
|---|------|--------|-------------|
| 1 | Project rename to `coder-cluster-conformance` | **REJECTED** | The "scale testing" name is locked in despite CEO §1.1 finding it a category error. Documentation will not be updated to clarify "this is conformance, not load testing." |
| 2 | Re-evaluate `coder exp scaletest workspace-traffic`/`dashboard` subcommands | **REJECTED** | The hand-rolled Python implementation of stages 4 and 5 is locked in despite CEO §4.1 finding the upstream rejection over-broad. Maintenance burden of stage 4/5 protocol drift is accepted. |
| 3 | Concurrent ops or ramp-to-failure for true scale testing | **REJECTED** | The 12-month ideal of real scale testing is closed out. Tool stays at conformance-test scope permanently. |
| 4 | Per-user API tokens for stage 5 | **REJECTED** | Stage 5's "false advertising" (CEO §2.1) is locked in. Stage 5 is now permanently labeled `dashboard cold-load (admin)` per CEO §0D auto-decision; the per-user-simulation aspiration is closed. |

**TODOS.md will not be created in the repo.** No deferred items.

(Items 5–8 from earlier gate moved into Tasks 16–19; that pull-back stands.)

## Note on review fidelity for Tasks 16–19

These four tasks did **not** receive independent CEO/Eng/DX subagent review at the same depth as Tasks 1–15 — the autoplan voices were dispatched against the original plan. New-feature risks worth flagging without re-running a full review:

- **Task 16 (metrics)** introduces a new external dependency (kubectl + optional Prometheus). Makes the tool require kubeconfig OR documentation for "kubectl part is optional, sampler logs warning if `kubectl` not on PATH."
- **Task 17 (trend)** assumes JSONL output stabilizes; document the JSONL schema in `docs/superpowers/specs/` if the schema isn't already locked.
- **Task 18 (CI)** activates only on push to a GitHub remote; this repo has no remote. Inert until pushed.
- **Task 19 (bootstrap)** changes the CLI surface (subcommands `run` vs `bootstrap`) — README quickstart needs significant rewrite. The "8 new flags" auto-decided in DX §1 attach to the `run` subcommand specifically.

---

# Execution Handoff (deferred until autoplan completes)

Plan complete and saved to [docs/superpowers/plans/2026-05-08-coder-scale-testing.md](./2026-05-08-coder-scale-testing.md). Two execution options:

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks, fast iteration. Good fit because tasks 2–5 and 8–13 are independently scoped; each can be implemented and reviewed cleanly before the next starts.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints.

Which approach?
