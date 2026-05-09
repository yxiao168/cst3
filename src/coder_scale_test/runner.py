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
    ledger: cleanup_mod.WorkspaceLedger
    log: IO[str]
    # Read-only after stage 0/1; not mutated by later stages.
    users: list[User] = field(default_factory=list)
    workspaces: list[Workspace] = field(default_factory=list)


class CoderCliMissing(RuntimeError):
    pass


def run(cfg: Config, *, skip_cleanup: bool = False, cleanup_only: bool = False) -> int:
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

    stages = _resolve_stages(cfg)
    exit_code = 0
    skipped: list[str] = []
    try:
        if not cleanup_only:
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
                # Thread products from this stage to ctx for downstream stages
                if result.users is not None:
                    ctx.users = result.users
                if result.workspaces is not None:
                    ctx.workspaces = result.workspaces
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
        if not skip_cleanup:
            failed = cleanup_mod.run(ctx)
            if failed and exit_code == 0:
                exit_code = 4

    return exit_code


def _resolve_all_stages() -> list[Any]:
    """Lazy import of all 6 stage modules in stage order 0..5."""
    from coder_scale_test.stages import (
        users, provision, ssh, web_terminal, app_traffic, dashboard,
    )
    return [users, provision, ssh, web_terminal, app_traffic, dashboard]


def _resolve_stages(cfg: Config) -> list[Any]:
    """Return stage modules filtered by cfg.skip_stages.

    Tests monkeypatch this to inject stubs.
    """
    all_stages = _resolve_all_stages()
    if not cfg.skip_stages:
        return all_stages
    skip = set(cfg.skip_stages)
    return [s for s in all_stages if _stage_name(s) not in skip]


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
