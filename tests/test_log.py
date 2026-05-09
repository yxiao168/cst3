"""Tests for log_op, log_event, redact, JSONL writer, and HINT lines."""
from __future__ import annotations
import json
import re

from coder_scale_test import log as log_mod


# --- log_op (plain log) ---


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
    err_field = re.search(r'err="([^"]*)"', line).group(1)
    assert len(err_field) == 200


def test_log_op_flushes_per_line(tmp_log, mocker):
    path, fh = tmp_log
    flush_spy = mocker.spy(fh, "flush")
    log_mod.log_op(fh, stage="x", op="y", ok=True, elapsed_ms=1)
    assert flush_spy.call_count == 1


# --- log_event ---


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


def test_log_event_bool_value(tmp_log):
    path, fh = tmp_log
    log_mod.log_event(fh, "STAGE_END", stage="ssh", ok=True)
    fh.flush()
    line = path.read_text().strip()
    assert "ok=true" in line


# --- HINT lines (autoplan auto-decided) ---


def test_log_op_emits_hint_line_after_fail(tmp_log):
    """When ok=False and hint=... is given, a second HINT line follows the FAIL line."""
    path, fh = tmp_log
    log_mod.log_op(
        fh, stage="ssh", op="alice/ws-0", ok=False, elapsed_ms=30041,
        err="timeout: coder ssh exit=124",
        hint="Cluster overloaded? Template too slow? Timeout too tight in config.toml?",
    )
    fh.flush()
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 2
    assert " FAIL " in lines[0]
    assert lines[1].split()[1] == "HINT"
    assert "stage=ssh" in lines[1]
    assert "op=alice/ws-0" in lines[1]
    assert 'msg="Cluster overloaded?' in lines[1]


def test_log_op_no_hint_line_when_ok_true(tmp_log):
    """hint= is ignored when ok=True (no point hinting on success)."""
    path, fh = tmp_log
    log_mod.log_op(
        fh, stage="ssh", op="alice/ws-0", ok=True, elapsed_ms=123,
        hint="should not appear",
    )
    fh.flush()
    text = path.read_text()
    assert " HINT " not in text


def test_log_op_no_hint_line_when_no_hint(tmp_log):
    """No hint= and ok=False → just the FAIL line, no HINT line."""
    path, fh = tmp_log
    log_mod.log_op(fh, stage="ssh", op="x", ok=False, elapsed_ms=1, err="boom")
    fh.flush()
    lines = path.read_text().strip().split("\n")
    assert len(lines) == 1
    assert " HINT " not in lines[0]


# --- redact (autoplan auto-decided) ---


def test_redact_replaces_literal_token():
    out = log_mod.redact("error: token=abc-123-secret in URL", "abc-123-secret")
    assert "abc-123-secret" not in out
    assert "[REDACTED]" in out


def test_redact_no_match_returns_unchanged():
    out = log_mod.redact("nothing to see here", "abc-123-secret")
    assert out == "nothing to see here"


def test_redact_empty_token_is_noop():
    """Empty token must NOT replace every empty substring (would corrupt the string)."""
    out = log_mod.redact("hello world", "")
    assert out == "hello world"


def test_redact_multiple_occurrences():
    out = log_mod.redact("token=secret&also=secret", "secret")
    assert out.count("secret") == 0
    assert out.count("[REDACTED]") == 2


# --- JSONL writer (autoplan auto-decided) ---


def test_log_op_writes_jsonl_record(tmp_path):
    """When jsonl_file is provided, log_op writes a JSON record alongside the plain line."""
    plain_path = tmp_path / "scale-run.log"
    jsonl_path = tmp_path / "scale-run.jsonl"
    plain = plain_path.open("w", encoding="utf-8")
    jsonl = jsonl_path.open("w", encoding="utf-8")
    try:
        log_mod.log_op(
            plain, stage="ssh", op="alice/ws-0", ok=True, elapsed_ms=123,
            jsonl_file=jsonl,
        )
    finally:
        plain.close()
        jsonl.close()
    record = json.loads(jsonl_path.read_text().strip())
    assert record["kind"] == "op"
    assert record["stage"] == "ssh"
    assert record["op"] == "alice/ws-0"
    assert record["ok"] is True
    assert record["elapsed_ms"] == 123
    assert record["err"] is None
    assert "ts" in record


def test_log_op_jsonl_includes_err_and_hint(tmp_path):
    plain_path = tmp_path / "p.log"
    jsonl_path = tmp_path / "j.jsonl"
    plain = plain_path.open("w", encoding="utf-8")
    jsonl = jsonl_path.open("w", encoding="utf-8")
    try:
        log_mod.log_op(
            plain, stage="ssh", op="bob/ws-1", ok=False, elapsed_ms=30041,
            err="timeout: coder ssh exit=124", hint="check the cluster",
            jsonl_file=jsonl,
        )
    finally:
        plain.close()
        jsonl.close()
    record = json.loads(jsonl_path.read_text().strip())
    assert record["ok"] is False
    assert record["err"] == "timeout: coder ssh exit=124"
    assert record["hint"] == "check the cluster"


def test_log_event_writes_jsonl_record(tmp_path):
    plain = (tmp_path / "p.log").open("w", encoding="utf-8")
    jsonl_path = tmp_path / "j.jsonl"
    jsonl = jsonl_path.open("w", encoding="utf-8")
    try:
        log_mod.log_event(
            plain, "RUN_START", jsonl_file=jsonl,
            num_users=10, per_user=3, total=30, template="ubuntu-base",
        )
    finally:
        plain.close()
        jsonl.close()
    record = json.loads(jsonl_path.read_text().strip())
    assert record["kind"] == "event"
    assert record["event"] == "RUN_START"
    assert record["fields"] == {
        "num_users": 10, "per_user": 3, "total": 30, "template": "ubuntu-base"
    }


# --- open_log / open_jsonl ---


def test_open_log_creates_parent_dir(tmp_path):
    """open_log must create missing parent dirs and return a writable file."""
    nested = tmp_path / "a" / "b" / "scale-run.log"
    fh = log_mod.open_log(nested)
    try:
        fh.write("hello\n")
        fh.flush()
    finally:
        fh.close()
    assert nested.exists()
    assert nested.read_text() == "hello\n"


def test_open_jsonl_uses_sibling_path(tmp_path):
    """open_jsonl converts /path/scale-run.log → /path/scale-run.jsonl."""
    plain_path = tmp_path / "scale-run.log"
    jsonl_path = log_mod.jsonl_path_for(plain_path)
    assert jsonl_path == tmp_path / "scale-run.jsonl"


def test_open_jsonl_writes_json_per_line(tmp_path):
    """open_jsonl returns a writable handle suitable for one JSON record per line."""
    plain_path = tmp_path / "scale-run.log"
    fh = log_mod.open_jsonl(log_mod.jsonl_path_for(plain_path))
    try:
        fh.write('{"a": 1}\n')
        fh.write('{"a": 2}\n')
        fh.flush()
    finally:
        fh.close()
    out = (tmp_path / "scale-run.jsonl").read_text().splitlines()
    assert json.loads(out[0]) == {"a": 1}
    assert json.loads(out[1]) == {"a": 2}
