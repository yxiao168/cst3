"""Log file writer.

One line per op (`OK`/`FAIL`) and one line per structural event
(`RUN_START`, `STAGE_START`, etc). Always flushed immediately so a
SIGINT cannot lose the line that explains why the run died.

Two augmentations beyond the original spec, both auto-decided during
the autoplan review:

* `log_op(..., hint=...)` emits a second `HINT` line after a `FAIL`
  line so the operator gets recovery direction without grepping docs.
* `log_op(..., jsonl_file=...)` and `log_event(..., jsonl_file=...)`
  also emit a JSON record to a sibling `.jsonl` file for downstream
  trend analysis.

`redact(s, token)` is a pure helper that scrubs the literal session
token from any string before it is logged. Callers are responsible for
applying it; this module never inspects environment state.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

ERR_MAX = 200


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def open_log(path: Path) -> IO[str]:
    """Open the plain-text log for append-and-flush, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def jsonl_path_for(plain_log_path: Path) -> Path:
    """Return the JSONL sibling path for a given plain-log path.

    `/x/y/scale-run.log` -> `/x/y/scale-run.jsonl`. If the suffix isn't
    `.log`, just appends `.jsonl`.
    """
    if plain_log_path.suffix == ".log":
        return plain_log_path.with_suffix(".jsonl")
    return plain_log_path.with_name(plain_log_path.name + ".jsonl")


def open_jsonl(path: Path) -> IO[str]:
    """Open the JSONL log for append-and-flush, creating parent dirs as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open("a", encoding="utf-8")


def redact(s: str, token: str) -> str:
    """Replace every occurrence of `token` in `s` with `[REDACTED]`.

    Empty token is a no-op (avoids replacing every empty substring,
    which would corrupt the string).
    """
    if not token:
        return s
    return s.replace(token, "[REDACTED]")


def log_op(
    log_file: IO[str],
    stage: str,
    op: str,
    ok: bool,
    elapsed_ms: int,
    err: str | None = None,
    hint: str | None = None,
    jsonl_file: IO[str] | None = None,
) -> None:
    """Write a single per-op line to the log and flush immediately.

    If `ok` is False and `hint` is provided, a second `HINT` line is emitted
    on the next line so the operator gets a recovery suggestion in-band.
    If `jsonl_file` is provided, a JSON record is also appended.
    """
    status = "OK   " if ok else "FAIL "
    err_part = ""
    if err:
        truncated = err[:ERR_MAX]
        err_part = f' err="{truncated}"'
    ts = utc_now_iso()
    log_file.write(
        f"{ts} {status} stage={stage} op={op} elapsed_ms={elapsed_ms}{err_part}\n"
    )
    if hint and not ok:
        log_file.write(
            f'{ts} HINT  stage={stage} op={op} msg="{hint}"\n'
        )
    log_file.flush()

    if jsonl_file is not None:
        record = {
            "ts": ts,
            "kind": "op",
            "stage": stage,
            "op": op,
            "ok": ok,
            "elapsed_ms": elapsed_ms,
            "err": err,
            "hint": hint,
        }
        jsonl_file.write(json.dumps(record, ensure_ascii=False) + "\n")
        jsonl_file.flush()


def log_event(
    log_file: IO[str],
    event: str,
    jsonl_file: IO[str] | None = None,
    **fields: Any,
) -> None:
    """Write a structural event (RUN_START, STAGE_START, etc.). Flushes."""
    parts = []
    for k, v in fields.items():
        parts.append(f"{k}={_render(v)}")
    suffix = (" " + " ".join(parts)) if parts else ""
    ts = utc_now_iso()
    log_file.write(f"{ts} {event}{suffix}\n")
    log_file.flush()

    if jsonl_file is not None:
        record = {
            "ts": ts,
            "kind": "event",
            "event": event,
            "fields": dict(fields),
        }
        jsonl_file.write(json.dumps(record, ensure_ascii=False, default=_json_default) + "\n")
        jsonl_file.flush()


def _render(v: Any) -> str:
    if isinstance(v, list):
        return "[" + ",".join(str(x) for x in v) + "]"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _json_default(obj: Any) -> Any:
    """Fallback for non-serializable values in JSONL records."""
    if isinstance(obj, Path):
        return str(obj)
    return str(obj)
