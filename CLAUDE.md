# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`coder-scale-test` — a Python tool that walks N×M Coder workspaces through six serial stages (users → provision → SSH → web terminal → app traffic → dashboard) using an admin Coder session token, then deletes everything it created. The product is a transport-conformance check, not a true scale test (per design spec non-goals at [docs/superpowers/specs/2026-05-08-coder-scale-testing-design.md:23-29](docs/superpowers/specs/2026-05-08-coder-scale-testing-design.md#L23-L29)).

## Commands

Tooling is `uv`. The codebase assumes Python 3.11+ and uses stdlib `tomllib` (no `tomli` backport).

```bash
# First-time setup — runs uv venv, installs deps, installs Playwright Chromium
./bootstrap.sh

# Run the full test suite (must finish under 5s; 129 tests)
uv run pytest -q

# Run a single test by file or by node id
uv run pytest tests/test_runner.py -v
uv run pytest tests/test_main.py::test_stage_flag_intersects_with_skip_stages -v

# Run the application (requires CODER_SESSION_TOKEN env var)
uv run coder-scale-test --config config.toml

# Validate config without running stages
uv run coder-scale-test --config config.toml --validate-config

# Run only specific stages (skip the others)
uv run coder-scale-test --config config.toml --stage users --stage provision

# Cleanup-only (skip stages, run the cleanup loop only)
uv run coder-scale-test --config config.toml --cleanup-only
```

`coder` CLI must be on `PATH` — stage 2 (SSH) shells out to it. The runner pre-flight checks this in [`src/coder_scale_test/runner.py`](src/coder_scale_test/runner.py) (`_check_coder_cli`) and exits 3 if missing.

## Architecture

### Stage-per-module pattern

Each of the six load stages lives in `src/coder_scale_test/stages/<name>.py`. Every stage module exports:

- `STAGE_NAME: str` — used by the runner for logging and `--stage` filtering
- `run(ctx: StageContext) -> StageResult` — the stage's entry point

`StageContext` and `StageResult` are defined in [`src/coder_scale_test/runner.py`](src/coder_scale_test/runner.py). `StageResult` is **frozen** with explicit product fields (`users: list[User] | None`, `workspaces: list[Workspace] | None`). The runner threads products from one stage into `ctx` for downstream stages — there is no shared mutable state through the run.

Stages are resolved via `_resolve_stages(cfg)` which filters `_resolve_all_stages()` by `cfg.skip_stages`. Tests monkeypatch `_resolve_stages` to inject stub modules without importing the real stages.

### Config flow

[`src/coder_scale_test/config.py`](src/coder_scale_test/config.py) loads a TOML file into a frozen `Config` dataclass and reads `CODER_SESSION_TOKEN` from the environment — **never from TOML**. CLI overrides (`--users`, `--stage`) use `dataclasses.replace(cfg, ...)` because the dataclass is frozen.

`VALID_STAGES` (a set) is the source of truth for stage names; `argparse choices=` for `--stage` reads from it, as does the `[stages] skip` validator.

`tomllib.TOMLDecodeError` is wrapped as `ConfigError` inside `load()` so the CLI's `except (ConfigError, OSError)` catches malformed TOML cleanly.

### CLI surface

[`src/coder_scale_test/__main__.py`](src/coder_scale_test/__main__.py) has 9 flags. Exit codes (defined by [`runner.run`](src/coder_scale_test/runner.py)):

- 0 = clean run, all stages passed, cleanup clean
- 1 = a stage failed (fail-fast); cleanup still runs
- 2 = SIGINT during run; cleanup still runs
- 3 = internal error (bad config, missing `coder` CLI, OSError on config)
- 4 = stages passed but cleanup left workspaces behind

Mutex flag pairs: `--skip-cleanup`/`--cleanup-only`, `--quiet`/`--verbose` (the verbosity pair is reserved for v1.x — currently no-op).

### Coder API access

[`src/coder_scale_test/coder_client.py`](src/coder_scale_test/coder_client.py) wraps the REST surface (`requests.Session` with `Retry` adapter, per-request token header, `User-Agent`). All non-WebSocket Coder API calls go through this client.

WebSocket transports are opened directly with `websocket-client` inside the relevant stage module — `web_terminal.py` (PTY) and `app_traffic.py` (app proxy). They're not unified behind a helper because the protocols are different enough that an abstraction would be premature.

Stage 2 (SSH) shells to the `coder` CLI via `subprocess` rather than implementing SSH-via-DERP in pure Python.

Stage 5 (Dashboard) uses **Playwright sync API**. The admin cookie is injected per-context; there is an explicit guard against `storage_state` / `user_data_dir` because either would leak the token to disk.

### Cleanup discipline

[`src/coder_scale_test/cleanup.py`](src/coder_scale_test/cleanup.py) defines `WorkspaceLedger` (process-local list of workspace IDs the run created). The runner guarantees `cleanup.run(ctx)` fires in a `finally` block, and `install_cleanup_sigint_handler` implements two-stage Ctrl-C (first SIGINT continues cleanup; second SIGINT aborts). Cleanup deletes via `POST /api/v2/workspaces/{id}/builds` with `{"transition": "delete"}` — **not** an HTTP DELETE.

Workspace deletion at run time uses `DELETE`-via-build because Coder requires a build transition for delete; this often confuses readers checking the architecture diagram. The diagram in `docs/README.md` is correct (`POST` with transition body).

### Logging

[`src/coder_scale_test/log.py`](src/coder_scale_test/log.py) writes one line per op (`OK   ` / `FAIL `) plus structural events (`RUN_START`, `STAGE_START`, etc.) and an optional `HINT:` line after `FAIL` for category-specific recovery guidance. `redact(s, token)` scrubs the literal token from any string before logging — every error path that could include the token must apply it.

**Known gap:** `open_jsonl()`, `jsonl_path_for()`, and the `jsonl_file=` kwargs on `log_op`/`log_event` exist in `log.py` but are **not wired** through `runner.py`. No `.jsonl` file is produced by a real run today. Wire-up is a follow-up task, not Task 1-15 work. Don't document JSONL output as a user-visible feature until it's connected.

## Test discipline

The full suite must run in under 5 seconds. Two practices keep it there:

1. **`_no_real_sleep` autouse fixture** in [`tests/conftest.py`](tests/conftest.py) replaces `time.sleep` with a no-op for every test. Without it, poll-loop tests (e.g. `test_delete_workspace_times_out`) would burn real wall-clock seconds.
2. **All network mocked.** `tests/conftest.py` provides a `coder_client` MagicMock fixture; tests configure its return values per case. No test should hit the real Coder cluster, no test should open a real WebSocket, and no test should spawn a real subprocess.

Use `pytest-mock`'s `mocker` fixture for patching. The `tmp_log` fixture gives you a real tmp file for log-content assertions.

## Plan-driven development

This codebase was built from [`docs/superpowers/plans/2026-05-08-coder-scale-testing.md`](docs/superpowers/plans/2026-05-08-coder-scale-testing.md) using the superpowers TDD workflow. Tasks 1-15 (original plan, Phases 1-6) are complete. Tasks 16-19 (autoplan pulled-back items: kubectl/Prometheus metrics correlation, trend dashboard, CI, `bootstrap` subcommand) are scoped in the plan but not started.

The plan contains autoplan-blessed expansions that supersede the plan-as-written for several tasks (e.g. Task 14 grew from one `--config` flag to nine flags). When working on a plan task, **read both the plan-as-written section AND the "Auto-decided DX expansions" / "DX implementation checklist" sections** (around lines 3516-3571) — the expansions are the binding contract.

## Commit style

Recent history uses Conventional Commits with module/feature scopes:

- `feat(stage:NAME): ...` for new stage modules
- `feat(cli): ...`, `feat(runner): ...`, `feat(coder_client): ...`
- `fix(scope): ...` for bug fixes
- `docs:`, `docs(readme): ...`
- `refactor(scope): ...`
- `chore: ...` for tooling/scripts

Match this style for new commits.
