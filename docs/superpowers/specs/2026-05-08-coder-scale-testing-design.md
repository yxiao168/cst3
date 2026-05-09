# coder-scale-testing — Design

**Status:** approved (brainstorming complete)
**Date:** 2026-05-08
**Source spec:** `docs/README.md`
**Tracking branch:** `main`

## Summary

A Python load-testing tool for a Coder cluster on Kubernetes. Runs six serial
stages against an existing cluster using an admin Coder session token. Each
stage measures one transport in isolation and short-circuits the run on the
first failure. All resources created during a run are cleaned up before the
process exits, including on Ctrl-C.

## Goals

- Exercise the cluster's create/SSH/PTY/app/dashboard paths end-to-end against
  real users and a real template.
- Produce a plain-text log of per-op outcomes that a human can grep and skim.
- Leave no workspaces behind on the cluster after a run, even on Ctrl-C.

## Non-goals

- Realistic concurrent mixed-workload simulation. Stages are serial; ops within
  a stage are also serial.
- Per-stage statistical summaries (p50/p95/p99). The log is the artifact.
- Creating users or templates. The cluster must already have both.
- Surviving SIGKILL or kernel-OOM cleanly. Cleanup runs on SIGINT (Ctrl-C) only.

## Decisions log

These were settled during brainstorming. Listed here so the implementation
plan and any reviewer can see the chosen path without reconstructing the
discussion.

| # | Decision | Choice |
|---|----------|--------|
| 1 | Concurrency within a stage | Strictly sequential. No asyncio. Sync code throughout. |
| 2 | Output format | Plain log file (per-op pass/fail + timing). No summary stats, no JSON. |
| 3 | Failure semantics | Fail-fast: first failed op aborts the stage; remaining stages skipped; cleanup still runs. |
| 4 | Workspace template | Pre-existing on the cluster, referenced by name in `config.toml`. Tool does not create templates. |
| 5 | Token source | `CODER_SESSION_TOKEN` env var only. **Diverges from `docs/README.md`**, which lists the token in `config.toml`. Cleaner secret hygiene. |
| 6 | HTTP / WebSocket client | `requests` + `websocket-client`. Sync. |
| 7 | Browser auth | Inject the admin session cookie into every Chromium. Acknowledged less realistic than per-user tokens. |
| 8 | Browser library | Playwright (sync API). |
| 9 | Cleanup robustness | `try/finally` + SIGINT handler. Does not survive SIGKILL or kernel kills. |
| 10 | User selection | Always first-N non-admin users sorted by `created_at`. Drop the README's `random` option. |
| 11 | Self-tests | `pytest` unit tests for non-Coder logic, with mocks for the Coder client. |
| 12 | Code organization | Small package, stage-per-module. Layout below. |
| 13 | Stage 2 (SSH) transport | Shell out to `coder ssh` CLI. Pure-Python SSH-via-DERP/tailscale is multi-week work and out of scope. |
| 14 | Stage 4 (app traffic) transport | Pure-Python WebSocket to the Coder app proxy. Validated in implementation Phase 1 (small spike) before the full stage 4 module is built. |
| 15 | Python floor | 3.11+. Use stdlib `tomllib`, no `tomli` backport. |

## Architecture

### File layout

```
coder-scale-testing/
├── pyproject.toml              # uv-managed; deps: requests, websocket-client,
│                               #   playwright, pytest
├── config.toml.example         # annotated sample; users copy to config.toml (gitignored)
├── docs/
│   ├── README.md               # existing high-level spec (lightly updated post-design)
│   └── superpowers/specs/2026-05-08-coder-scale-testing-design.md   # this file
├── src/coder_scale_test/
│   ├── __init__.py
│   ├── __main__.py             # `python -m coder_scale_test --config config.toml`
│   ├── config.py               # Config dataclass; load(path) -> Config; reads env token
│   ├── coder_client.py         # CoderClient: REST + WS helpers around Coder API
│   ├── log.py                  # open_log(path); log_op(stage, op, ok, ms, err?); structural events
│   ├── cleanup.py              # WorkspaceLedger; cleanup(ctx) -> int (failed_count)
│   ├── runner.py               # run(cfg) -> int; orchestrates stages; SIGINT; ensures cleanup
│   └── stages/
│       ├── __init__.py
│       ├── users.py            # stage 0
│       ├── provision.py        # stage 1
│       ├── ssh.py              # stage 2 (shells to `coder` CLI)
│       ├── web_terminal.py     # stage 3
│       ├── app_traffic.py      # stage 4
│       └── dashboard.py        # stage 5 (Playwright)
└── tests/
    ├── conftest.py             # CoderClient mock fixture; tmp log file fixture
    ├── test_config.py
    ├── test_log.py
    ├── test_cleanup.py
    ├── test_runner.py
    └── stages/
        ├── test_users.py
        ├── test_provision.py
        ├── test_ssh.py
        ├── test_web_terminal.py
        ├── test_app_traffic.py
        └── test_dashboard.py
```

### Key types and call shape

```python
# config.py
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
    num_users: int                  # N
    per_user: int                   # M
    log_file: Path
    timeouts: Timeouts
    dashboard_ready_selector: str
    app_tcp_port: int               # tcp-echo app's container port
    coder_session_token: str        # from env, NOT TOML

# runner.py
@dataclass
class StageResult:
    ok: bool
    err: str | None = None

@dataclass
class StageContext:
    cfg: Config
    client: CoderClient
    ledger: WorkspaceLedger
    log: TextIO
    user_ids: list[str]              # populated by stage 0; consumed by stage 1+
    workspaces: list[Workspace]      # populated by stage 1; consumed by stages 2-5

def run(cfg: Config) -> int:
    log = open_log(cfg.log_file)
    log_event(log, "RUN_START", num_users=cfg.num_users, per_user=cfg.per_user,
              total=cfg.num_users * cfg.per_user, template=cfg.template_name)
    client = CoderClient(cfg.coder_url, cfg.coder_session_token)
    ledger = WorkspaceLedger()
    ctx = StageContext(cfg, client, ledger, log, user_ids=[], workspaces=[])
    install_sigint_handler()
    exit_code = 0
    try:
        for stage in [users, provision, ssh, web_terminal, app_traffic, dashboard]:
            result = stage.run(ctx)
            if not result.ok:
                exit_code = 1
                log_event(log, "RUN_END", ok=False, skipped_stages=skipped_after(stage))
                break
        else:
            log_event(log, "RUN_END", ok=True)
    except KeyboardInterrupt:
        exit_code = 2
        log_event(log, "RUN_END", ok=False, reason="sigint")
    except Exception as e:
        exit_code = 3
        log_event(log, "RUN_END", ok=False, reason=f"internal: {type(e).__name__}: {e}")
    finally:
        failed = cleanup.run(ctx)
        if failed and exit_code == 0:
            exit_code = 4              # cleanup-failed-only sentinel
    return exit_code
```

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | All stages passed; cleanup clean. |
| 1 | A stage failed (fail-fast). Subsequent stages skipped. Cleanup attempted. |
| 2 | SIGINT (Ctrl-C). Cleanup attempted. |
| 3 | Uncaught internal exception (programmer bug). Cleanup attempted. |
| 4 | All stages passed but cleanup left workspaces behind. |

## Configuration

### `config.toml`

```toml
# Cluster
coder_url = "https://coder.example.com"

# Test parameters
template_name = "ubuntu-base"     # must already exist on the cluster
num_users = 10                    # N: take first-N non-admin users by created_at
per_user = 3                      # M: workspaces per user → N×M = 30 total

# Output
log_file = "./scale-run.log"

# Per-stage timeouts (seconds)
[timeouts]
provision_workspace = 300
ssh_round_trip = 30
web_terminal_round_trip = 30
app_traffic_round_trip = 30
dashboard_ready = 60
delete_workspace = 120

# Stage 4 specifics
[app]
tcp_port = 7000                   # internal container port the tcp-echo app listens on

# Stage 5 specifics
[dashboard]
ready_selector = "[data-testid='workspaces-table']"
```

### Token

`CODER_SESSION_TOKEN` is required. `runner.run()` exits with a clear error
message before opening the log if the env var is unset. The token is never
written to disk by the tool.

### Validation (in `config.load`)

- `coder_url` parses as `http(s)://...`.
- `template_name` non-empty.
- `num_users >= 1`, `per_user >= 1`.
- All timeout values > 0.
- `log_file` parent directory exists or is creatable.
- `app.tcp_port` in `[1, 65535]`.
- `dashboard.ready_selector` non-empty.

### Runtime requirements

- Python 3.11+.
- `coder` CLI on `PATH` (used by stage 2). The runner verifies `which coder`
  during startup; missing CLI → clear error, exit 3 before any stage runs.
- `playwright install chromium` must have been run by the user. The runner
  detects missing browsers and prints the install command on first stage 5
  failure due to `BrowserType.launch` errors.

## Stages

| Stage | What | API surface | Per-op success criterion |
|-------|------|-------------|--------------------------|
| 0 users | List active users whose `roles` array does not contain `owner` or `admin`; take first-N sorted by `created_at` ascending | `GET /api/v2/users?status=active&limit=...` | At least N users matching role filter |
| 1 provision | For each (user, m) in (users × per_user), create workspace from `template_name` and poll until `running` | `POST /api/v2/users/{user}/workspaces`, `GET /api/v2/workspaces/{id}` | Reached `running` before `provision_workspace` timeout |
| 2 SSH echo | `coder ssh <ws-name> echo <token16>` | `subprocess.run` with `CODER_URL` and `CODER_SESSION_TOKEN` in env | stdout contains the token within `ssh_round_trip` |
| 3 web terminal | Open `wss://<host>/api/v2/workspaceagents/{id}/pty?...`; send `echo <token>\n`; read frames until token observed | `websocket-client` | Token observed within `web_terminal_round_trip` |
| 4 app traffic | Open WebSocket to the Coder app proxy for the workspace's `tcp-echo` app on `app.tcp_port`; send/recv random 32-byte payload | `websocket-client`; URL pattern validated in implementation Phase 1 | Echo round-trip within `app_traffic_round_trip` |
| 5 dashboard | Playwright Chromium, set `coder_session_token` cookie on the Coder URL's domain, `goto(coder_url)`, `wait_for_selector(dashboard.ready_selector)` | Playwright sync API | Selector visible within `dashboard_ready` |

### Stage 1 — provision details

- Workspace name pattern: `scaletest-{user}-{m}` where `m` is `0..per_user-1`.
  Stable across runs; collisions are surfaced as FAIL (workspace already exists).
- After `POST /workspaces`, poll `GET /workspaces/{id}` every 2 seconds. Success
  when `latest_build.job.status == "succeeded"` and `latest_build.transition ==
  "start"`.
- Each successful create is registered in the ledger immediately, before the
  poll. If the poll times out, the workspace is still in the ledger and gets
  cleaned up.

### Stage 2 — SSH (CLI shell-out)

- Command: `coder ssh <workspace-name> -- echo <token>` where `<token>` is a
  fresh 16-char random alphanumeric string per op.
- Env: `CODER_URL=<cfg.coder_url>`, `CODER_SESSION_TOKEN=<env token>`,
  inherit `PATH`.
- Timeout: `subprocess.run(timeout=cfg.timeouts.ssh_round_trip)`.
- Success: token substring appears in stdout. Non-zero exit, missing token,
  or `TimeoutExpired` are all FAIL with categorized error.

### Stage 3 — web terminal PTY

- Open WebSocket: `wss://<host>/api/v2/workspaceagents/{agent_id}/pty`
  (query params per Coder API: `reconnect`, `width`, `height`, `command`).
- Auth: `Coder-Session-Token` header on the WS handshake (preferred over cookie
  for handshake-time auth).
- Send: `echo <token>\n` after the WS opens.
- Read frames until `token` substring observed. Timeout per `web_terminal_round_trip`.
- Each WS is closed cleanly (close frame) before moving to next workspace.

### Stage 4 — app traffic (validation needed)

- This stage's exact URL pattern is validated against a real cluster in
  implementation Phase 1 before stage 4's full module is built. Two
  candidates from Coder source:
  - `wss://<host>/api/v2/workspaceagents/{agent_id}/apps/{app_slug}` (newer)
  - `wss://<host>/@<user>/<workspace>.<agent>--<app_slug>/` (older path-based)
- Auth via header on the handshake.
- Payload: 32 random bytes. Success: same 32 bytes received within timeout.
- If Phase 1 spike fails (neither URL works on the target cluster version), the
  design loops back with concrete data; possible fallbacks include using
  `coder port-forward` shell-out or skipping stage 4 with a clear note in the
  log.

### Stage 5 — dashboard

- Launch sync Playwright, single Chromium instance shared across users. One
  fresh `BrowserContext` per simulated user; cookies set on the context (not
  globally) so contexts are independent. Per decision #7, every context
  receives the same admin-token cookie value — the "per-user" loop exercises
  cold-context dashboard load N times, not per-user auth.
- Per simulated user: new context, set cookie:
  ```python
  context.add_cookies([{
      "name": "coder_session_token",
      "value": cfg.coder_session_token,
      "domain": urlparse(cfg.coder_url).hostname,
      "path": "/",
      "secure": True,
      "httpOnly": True,
      "sameSite": "Lax",
  }])
  ```
- `page.goto(cfg.coder_url)`; `page.wait_for_selector(cfg.dashboard.ready_selector,
  timeout=cfg.timeouts.dashboard_ready * 1000)`.
- Page closed; context closed; loop to next user.

## Cleanup

```python
class WorkspaceLedger:
    def __init__(self) -> None: self._ids: list[str] = []
    def add(self, ws_id: str) -> None: self._ids.append(ws_id)
    def all(self) -> list[str]: return list(self._ids)


def run(ctx: StageContext) -> int:
    log_event(ctx.log, "CLEANUP_START", total=len(ctx.ledger.all()))
    failed = 0
    # Two-stage SIGINT during cleanup:
    #   first  Ctrl-C → log a warning, keep deleting
    #   second Ctrl-C → raise KeyboardInterrupt, break out, leak remaining
    install_cleanup_sigint_handler()
    for ws_id in ctx.ledger.all():
        try:
            elapsed = time_it(lambda: ctx.client.delete_workspace(
                ws_id, timeout=ctx.cfg.timeouts.delete_workspace))
            log_op(ctx.log, "cleanup", ws_id, ok=True, elapsed_ms=elapsed)
        except Exception as e:
            failed += 1
            log_op(ctx.log, "cleanup", ws_id, ok=False, elapsed_ms=elapsed,
                   err=categorize(e))
    log_event(ctx.log, "CLEANUP_END",
              deleted=len(ctx.ledger.all()) - failed, failed=failed)
    return failed
```

`CoderClient.delete_workspace(ws_id, timeout)` issues
`POST /api/v2/workspaces/{ws_id}/builds` with `{"transition": "delete"}`,
then polls `GET /api/v2/workspacebuilds/{build_id}` until `job.status` is
`succeeded` or `failed`, or until timeout.

## Log format

One file, one line per op. Always flushed immediately so Ctrl-C cannot lose
the line that explains the failure.

```
2026-05-08T15:42:01Z RUN_START num_users=10 per_user=3 total=30 template=ubuntu-base
2026-05-08T15:42:01Z STAGE_START stage=users
2026-05-08T15:42:01Z OK    stage=users op=list_users elapsed_ms=87
2026-05-08T15:42:01Z STAGE_END   stage=users ok=true elapsed_ms=89
2026-05-08T15:42:01Z STAGE_START stage=provision
2026-05-08T15:42:14Z OK    stage=provision op=alice/scaletest-alice-0 elapsed_ms=12834
...
2026-05-08T15:46:02Z FAIL  stage=ssh op=bob/scaletest-bob-2 elapsed_ms=30041 err="timeout: coder ssh exit=124"
2026-05-08T15:46:02Z STAGE_END   stage=ssh ok=false elapsed_ms=180551
2026-05-08T15:46:02Z RUN_END ok=false skipped_stages=[web_terminal,app_traffic,dashboard]
2026-05-08T15:46:02Z CLEANUP_START total=30
...
2026-05-08T15:48:14Z CLEANUP_END deleted=30 failed=0 elapsed_ms=132441
```

### Helper

```python
def log_op(log_file, stage: str, op: str, ok: bool,
           elapsed_ms: int, err: str | None = None) -> None:
    status = "OK   " if ok else "FAIL "
    err_part = f' err="{err}"' if err else ""
    log_file.write(f"{utc_now_iso()} {status} stage={stage} op={op} "
                   f"elapsed_ms={elapsed_ms}{err_part}\n")
    log_file.flush()
```

### Error categories

`err=` strings follow a categorized prefix; full text truncated to 200 chars.

| Prefix | Source |
|--------|--------|
| `timeout: <what>` | Any timeout exceeded |
| `http: status=<N> body="<...>"` | REST 4xx/5xx |
| `ws_closed: code=<N> reason="<...>"` | WebSocket abnormal close |
| `mismatch: expected="<token>" got="<...>"` | Echo round-trip wrong content |
| `subprocess: exit=<N> stderr="<...>"` | `coder ssh` non-zero |
| `playwright: <msg>` | Playwright timeout / selector errors |

## SIGINT handling

```python
def install_sigint_handler() -> None:
    def _on_sigint(signum, frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGINT, _on_sigint)
```

The default Python handler also raises `KeyboardInterrupt`, but installing it
explicitly defends against Playwright's signal-mask behaviour during sync
calls. The runner's `try/finally` catches `KeyboardInterrupt`, logs `RUN_END
reason=sigint`, and runs cleanup. Cleanup installs its own two-stage handler:
the first Ctrl-C during cleanup logs a warning to stderr ("cleanup in
progress, Ctrl-C again to abort and leak workspaces"); the second Ctrl-C
raises `KeyboardInterrupt`, which breaks out of the cleanup loop and
propagates out of `run()`. Workspaces deleted before the abort stay deleted;
the rest leak.

## Testing strategy

`pytest` over fully-mocked Coder, WebSocket, subprocess, and Playwright. Total
suite runtime under 5 seconds. No real network, no Chromium download, no live
cluster.

### Coverage by file

| File | Tests |
|------|-------|
| `tests/test_config.py` | TOML parsing; missing `CODER_SESSION_TOKEN` raises clearly; validation rejects `num_users<1`, bad URL, missing `template_name`, bad port; defaults applied. |
| `tests/test_log.py` | `log_op` produces documented format including OK/FAIL spacing; `flush()` called per line; `err` string truncated at 200 chars; structural events render correctly. |
| `tests/test_cleanup.py` | `WorkspaceLedger` add/all; cleanup iterates all entries; cleanup continues past per-workspace failures; returns failed count; logs each outcome. |
| `tests/test_runner.py` | Fail-fast: stage 3 fails ⇒ stages 4/5 not called, cleanup runs, exit=1. SIGINT mid-stage ⇒ cleanup runs, exit=2. Happy path exit=0. Missing `coder` CLI ⇒ exit=3 before stage 0. Cleanup-only failure ⇒ exit=4. |
| `tests/stages/test_users.py` | Picks first-N excluding `role=admin`; sorted by `created_at`; raises if cluster has fewer than N matching users. |
| `tests/stages/test_provision.py` | Creates N×M workspaces; registers each in ledger before polling; polls until `running`; partial-create still leaves successes in ledger; timeout path returns FAIL. |
| `tests/stages/test_ssh.py` | `subprocess.run` mocked; OK on token in stdout; FAIL on `TimeoutExpired`; FAIL on non-zero exit with `err="subprocess: ..."`. |
| `tests/stages/test_web_terminal.py` | `websocket-client` mocked; sends `echo <token>\n`; reads until token observed; timeout path; mismatch path. |
| `tests/stages/test_app_traffic.py` | WebSocket mocked; round-trip random bytes; timeout path; mismatch path. |
| `tests/stages/test_dashboard.py` | `sync_playwright` mocked at the module-import boundary; cookie is set on the Coder URL's host with correct attributes; navigate + `wait_for_selector` called with config-driven values; selector timeout path. |

### Fixtures

`tests/conftest.py` provides:

- `coder_client`: a `MagicMock` typed against the `CoderClient` protocol with
  `list_users`, `create_workspace`, `get_workspace`, `delete_workspace`,
  `agent_for`, etc.
- `tmp_log`: a `tmp_path / "scale-run.log"` opened in `w` mode, returned along
  with the path so tests can assert on file contents.

## Implementation phases (preview)

These are the phases the writing-plans step will turn into tasks. Listed
here so the design's intent is visible.

1. **Phase 1 — spike + foundations.** `pyproject.toml`, `config.py`,
   `log.py`, `cleanup.py`, `coder_client.py` skeleton. Manual spike: confirm
   stage 4 WebSocket URL pattern against the target cluster's Coder version.
   Output: spike notes added to this design doc; `coder_client` API shape
   finalized.
2. **Phase 2 — runner + stage 0.** `runner.py` with mocked stages; SIGINT
   handler; exit codes. `stages/users.py`. Tests for both.
3. **Phase 3 — stages 1, 2.** Provisioning and SSH echo. Tests.
4. **Phase 4 — stages 3, 4.** Web terminal and app traffic. Tests.
5. **Phase 5 — stage 5.** Playwright dashboard. Tests.
6. **Phase 6 — docs and example config.** `config.toml.example`, expanded
   `docs/README.md` (operator-facing how-to), this design doc finalized.

## Open items / risks

- **Stage 4 URL pattern** is the highest-risk item. Phase 1 spike retires this
  risk before stage 4 is implemented.
- **`coder` CLI version skew**: stage 2 calls `coder ssh`. Across Coder
  versions the CLI is stable, but the design names this dependency
  explicitly so an operator can pin a CLI version if needed.
- **README divergence**: items 5 (token via env) and 10 (drop random user
  selection) are deliberate departures from `docs/README.md`. The README
  will be updated in Phase 6 to match the chosen design.
- **Per-user simulation in stage 5** uses the admin cookie, not per-user
  tokens. Acknowledged less realistic; chosen for simplicity. If realistic
  per-user dashboard load is needed later, it's a follow-on (mint per-user
  API keys via admin, inject per context, rotate tokens after run).

## Out of scope (deliberately)

- Running stages in parallel.
- Concurrency knobs of any kind in `config.toml`.
- Statistical summaries in the log (use a separate parser if needed).
- Live integration tests against a real Coder cluster in CI.
- Surviving SIGKILL / kernel kills with a persistent cleanup ledger.
- Creating users or templates.
- Per-user authentication via per-user API keys (stage 5 uses admin cookie).
