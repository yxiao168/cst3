# Spike report — stage 4 app-traffic transport

**Status:** DEFERRED. The spike requires access to a real Coder cluster
(specifically, a workspace running a `tcp-echo` app on a known port). No such
cluster is reachable from the implementation environment, so the spike is
deferred until the operator runs Task 12 against their target cluster.

**Per the plan's escape hatch (Task 6 step 6):**

> If you don't have access to a real Coder cluster, skip this task with a note
> in the file ("spike deferred — implementer will resolve URL pattern during
> Task 12 with cluster access") and proceed; Task 12 then absorbs the spike
> work with one extra TDD round-trip.

## What needs to be validated when a cluster is available

1. **Pattern A — agent-app endpoint** (newer Coder versions):
   ```
   wss://<host>/api/v2/workspaceagents/<AGENT_ID>/apps/<slug>
   ```
   Auth via `Coder-Session-Token` header on the WS handshake.

2. **Pattern B — path-based proxy** (older Coder versions):
   ```
   wss://<host>/@<USER>/<WORKSPACE>.<AGENT>--<slug>/
   ```

3. **Shell-out fallback** (only if A and B both fail):
   ```bash
   coder port-forward <workspace> --tcp 9999:7000 &
   nc localhost 9999
   ```

## Probe scripts

These can be run by an operator with the cluster URL, an admin session token, a
running workspace, and an agent ID:

### Probe Pattern A

```bash
python -c "
import os, websocket
url = 'wss://coder.example.com/api/v2/workspaceagents/<AGENT_ID>/apps/tcp-echo'
ws = websocket.create_connection(
    url, header={'Coder-Session-Token': os.environ['CODER_SESSION_TOKEN']}
)
ws.send_binary(b'ping')
print('recv:', ws.recv())
ws.close()
"
```

### Probe Pattern B

```bash
python -c "
import os, websocket
url = 'wss://coder.example.com/@<USER>/<WORKSPACE>.<AGENT>--tcp-echo/'
ws = websocket.create_connection(
    url, header={'Coder-Session-Token': os.environ['CODER_SESSION_TOKEN']}
)
ws.send_binary(b'ping')
print('recv:', ws.recv())
ws.close()
"
```

### Probe shell-out fallback

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

## Decision (to be filled in during Task 12)

Stage 4 will use **<chosen pattern>** because <one-line reason>.

| Pattern | URL/command | Result | Notes |
|---------|-------------|--------|-------|
| A — agent-app | `wss://<host>/api/v2/workspaceagents/{id}/apps/{slug}` | UNTESTED | |
| B — path-proxy | `wss://<host>/@user/ws.agent--app/` | UNTESTED | |
| Shell-out fallback | `coder port-forward` + raw socket | UNTESTED | |

## Implementation notes for Task 12 (placeholder)

- Auth header: `Coder-Session-Token: <token>` on the WS handshake.
- Frame type: `binary` (tcp-echo).
- Open observations / quirks: TBD after probe.

## Related design context

- Original design recommendation: Pattern A first, B as fallback.
- The implementer of Task 12 should:
  1. Run the three probes against the operator's cluster.
  2. Update this file's "Decision" table with results.
  3. Hard-wire the chosen pattern in `stages/app_traffic.py:_build_app_url`.
  4. If shell-out is the chosen path, the test scaffolding in
     `tests/stages/test_app_traffic.py` (which currently mocks
     `websocket.create_connection`) must be replaced with subprocess +
     socket mocks. This was acknowledged as Task 12's risk under autoplan
     T1 (kept as a single task per user override).
