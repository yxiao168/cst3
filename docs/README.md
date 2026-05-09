# coder-scale-test

A Python load-testing tool for a Coder cluster on Kubernetes. Exercises six
transport paths in serial stages using an admin session token against an
existing cluster, without creating users or templates. Produces a plain-text
log of per-operation pass/fail timings.

## What this tool does NOT do

These are explicit non-goals, sourced from the design spec:

- **Realistic concurrent mixed-workload simulation.** Stages are serial; operations
  within a stage are also serial. The tool measures per-stage scale in isolation.
- **Statistical summaries (p50/p95/p99).** The log file is the artifact. Bring
  your own analysis tooling.
- **Creating users or templates.** The cluster must already have both before the
  run begins.
- **Surviving SIGKILL or kernel-OOM cleanly.** Cleanup runs on SIGINT (Ctrl-C)
  only, not on SIGKILL.

## Methodology

Six serial stages, numbered 0–5:

```
0. users       — find existing non-admin users, take first-N (or use allow-list)
1. provision   — create N×M workspaces under those users using the configured template
2. ssh         — SSH echo: coder ssh <ws> -- echo <token>, verify round-trip
3. web_terminal— PTY WebSocket echo: open /pty, send echo, verify token in stdout
4. app_traffic — tcp-echo app WebSocket round-trip via Coder app proxy
5. dashboard   — headless Chromium per user: navigate to coder_url, time to ready selector
   cleanup     — delete every workspace this run created (always runs, even on Ctrl-C)
```

The first stage failure aborts all remaining stages. Cleanup always runs.

**Why not `coder exp scaletest`?** The upstream `coder exp scaletest` command
creates users as part of its workflow. This tool targets clusters where user
creation is not permitted — it exercises only existing users and templates via
the Coder REST API and transport interfaces.

## Architecture: stage-to-transport map

```
Stage 0  users        REST GET /api/v2/users
Stage 1  provision    REST POST/GET /api/v2/workspaces (poll until running)
Stage 2  ssh          coder CLI subprocess (coder ssh <ws> -- echo <token>)
Stage 3  web_terminal WebSocket  wss://<host>/api/v2/workspaceagents/{id}/pty
Stage 4  app_traffic  WebSocket  wss://<host>/api/v2/workspaceagents/{id}/apps/tcp-echo
Stage 5  dashboard    Playwright Chromium (headless) — navigate + wait_for_selector
Cleanup  (all stages) REST POST /api/v2/workspaces/{id}/builds  {"transition":"delete"}
```

## Runtime requirements

- Python 3.11+
- `uv` (recommended) or `pip` with a virtual environment
- `coder` CLI on `PATH` (required for stage 2 SSH)
- Playwright Chromium (`uv run playwright install chromium`)
- A Coder cluster with:
  - An existing template that includes a `tcp-echo` app (port exposed via
    `coder_app`) and a PTY-capable agent
  - At least `num_users` existing non-admin active users
  - An admin-scoped `CODER_SESSION_TOKEN`

## Operator quick-start

```bash
# 1. Bootstrap (first time only)
bash bootstrap.sh

# 2. Set your session token in the environment (never in config.toml)
export CODER_SESSION_TOKEN="$(coder tokens create --lifetime 24h)"

# 3. Copy and edit the example config
cp config.toml.example config.toml
$EDITOR config.toml

# 4. Validate the config before a real run
uv run coder-scale-test --validate-config --config config.toml

# 5. Run
uv run coder-scale-test --config config.toml
```

Power users: `python -m coder_scale_test` is an equivalent alternative to the
`coder-scale-test` entry point.

## CLI flags

All flags are optional unless noted.

| Flag | Description |
|------|-------------|
| `--config PATH` | Path to `config.toml`. Default: `./config.toml`. |
| `--version` | Print the version string and exit 0. |
| `--validate-config` | Load the config and exit 0 if valid, 3 if not. Does not run stages. |
| `--stage NAME` | Run only the named stage; all others are skipped. Repeatable. Choices: `app_traffic`, `dashboard`, `provision`, `ssh`, `users`, `web_terminal`. |
| `--skip-cleanup` | Do not run cleanup after stages (useful for debugging workspace state). Mutually exclusive with `--cleanup-only`. |
| `--cleanup-only` | Skip all stages; run cleanup only. Mutually exclusive with `--skip-cleanup`. |
| `--users alice,bob,...` | Comma-separated username allow-list override. Must contain at least `num_users` entries. Overrides the `users` field in config.toml. |
| `--quiet` | Reserved for v1.x — accepted but currently no-op. Mutually exclusive with `--verbose`. |
| `--verbose` | Reserved for v1.x — accepted but currently no-op. Mutually exclusive with `--quiet`. |

## Exit codes

| Code | Meaning |
|------|---------|
| 0 | All stages passed; cleanup succeeded. |
| 1 | At least one stage failed. |
| 2 | Run interrupted by SIGINT (Ctrl-C). |
| 3 | Internal or configuration error (missing CLI, bad config, unexpected exception). |
| 4 | Cleanup left one or more workspaces behind (stages passed, but deletion failed). |

## Log output

Each run appends to the file specified by `log_file` in `config.toml`. Lines
follow the format:

```
2026-05-08T12:00:01Z OK    stage=provision op=create:alice/scaletest-alice-0 elapsed_ms=312
2026-05-08T12:00:02Z FAIL  stage=ssh op=alice/scaletest-alice-0 elapsed_ms=31000 err="timeout: coder ssh exit=124"
2026-05-08T12:00:02Z HINT  stage=ssh op=alice/scaletest-alice-0 msg="..."
```

## Troubleshooting

Grep `scale-run.log` for the prefix shown in the first column to find the relevant FAIL lines.

| If you see this in the log | Likely cause | Try |
|----------------------------|--------------|-----|
| `status=4` (any 4xx REST error, e.g. `list_users: status=403`) | Token revoked or scoped wrong | Check `CODER_SESSION_TOKEN`; mint a new admin token with `coder tokens create --lifetime 24h` |
| `status=5` (any 5xx REST error, e.g. `create_workspace: status=503`) | Cluster control plane unhealthy | `kubectl get pods -n coder`; check Coder server logs |
| `timeout: coder ssh exit=124` | SSH stage timed out | Increase `timeouts.ssh_round_trip` in `config.toml`; check agent logs via `coder ssh <ws>` interactively |
| `timeout: pty ws` | PTY WebSocket open timed out | Check agent connectivity; increase `timeouts.web_terminal_round_trip` |
| `timeout: token not seen within` | PTY echo timed out waiting for shell response | Agent shell startup slow; increase `timeouts.web_terminal_round_trip` |
| `timeout: app ws` | App proxy WebSocket timed out | Increase `timeouts.app_traffic_round_trip`; verify tcp-echo is bound on `app.tcp_port` |
| `ws_closed: WebSocketConnectionClosedException` | WebSocket closed by agent or proxy | Check workspace `latest_build` status; check agent logs for crashes or DERP loss |
| `mismatch: expected="` (SSH) or `mismatch: expected_hex_prefix=` (app traffic) | Echo round-trip returned wrong content; agent stdout polluted or app dropped bytes | Check template startup script for noisy logging; verify the `tcp-echo` app is healthy |
| `subprocess: exit=` | `coder ssh` exited with non-zero code | Reproduce with `coder ssh <ws> -- echo test` from operator's terminal |
| `playwright:` | Selector timeout or browser crash in dashboard stage | Update `dashboard.ready_selector` in `config.toml`; rerun `uv run playwright install chromium` |
| `agent_lookup:` | Workspace running but agent not registered | Check workspace `latest_build` status; agent registration may have stalled |
| `connect_failed:` | TCP connect to app proxy failed before WebSocket handshake | Confirm `tcp-echo` app is configured on the template at the configured `app.tcp_port` |

## How to mint a Coder session token

The tool requires an admin-scoped session token. Set it in the environment —
never put the literal token value in `config.toml`.

```bash
# Create a token with a 24-hour lifetime (requires admin role)
coder tokens create --lifetime 24h

# Export for the current shell session
export CODER_SESSION_TOKEN="<token from above>"
```

The token must belong to a user with admin privileges. Without admin scope, the
`/api/v2/users` endpoint returns only the calling user, and workspace creation
on behalf of other users will be refused.

**Warning:** do not commit the token value to `config.toml` or any file tracked
by version control. Keep it in your shell environment or a secrets manager.

## How to set up a tcp-echo template

Stage 4 (`app_traffic`) requires the template to declare a `coder_app` resource
with slug `tcp-echo` that exposes a TCP echo server on a known port. Below is a
minimal Terraform snippet that adds this to an existing template:

```hcl
# In your template's main.tf — add these resources alongside your existing
# coder_agent resource.

resource "coder_app" "tcp_echo" {
  agent_id     = coder_agent.main.id
  slug         = "tcp-echo"
  display_name = "TCP Echo"
  url          = "http://localhost:7000"
  share        = "owner"
}

# In your existing main.tf, add the startup_script attribute to your
# coder_agent block (do NOT add a second coder_agent resource):
resource "coder_agent" "main" {
  # ... your existing agent attributes (arch, os, etc.) ...

  startup_script = <<-EOT
    #!/bin/bash
    # Start a TCP echo server on port 7000 using socat.
    # socat must be available in the workspace image.
    socat TCP-LISTEN:7000,fork,reuseaddr PIPE &
  EOT
}
```

Set `app.tcp_port = 7000` in `config.toml` to match. If you use a different
port, update both the template and `config.toml`.

**Prerequisites:**
- `socat` must be installed in your workspace image (`apt-get install socat` or equivalent).
- The template must be published to the cluster before running `coder-scale-test`.
- After editing the template, create a new version and update existing workspaces if needed.

## Oh-my-mermaid
```bash
$ omm view
oh-my-mermaid viewer running at http://localhost:3000

```


## References

- [Coder scale testing (upstream)](https://coder.com/docs/admin/infrastructure/scale-testing)
- [Coder REST API reference](https://coder.com/docs/reference/api)
- [Design spec](superpowers/specs/2026-05-08-coder-scale-testing-design.md)
- [Implementation plan](superpowers/plans/2026-05-08-coder-scale-testing.md)
- [Oh-my-mermaid](https://github.com/oh-my-mermaid/oh-my-mermaid)
