"""CLI entry: `python -m coder_scale_test --config config.toml [flags]`."""
from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from coder_scale_test import __version__
from coder_scale_test.config import VALID_STAGES, ConfigError, load as load_config
from coder_scale_test.runner import run


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="coder-scale-test")
    parser.add_argument("--config", type=Path, default=Path("./config.toml"),
                        help="path to config.toml (default: ./config.toml)")
    parser.add_argument("--version", action="store_true",
                        help="print version and exit")
    parser.add_argument("--validate-config", action="store_true",
                        help="load config, exit 0 if valid or 3 if not")
    parser.add_argument("--stage", action="append", default=None,
                        choices=sorted(VALID_STAGES),
                        help="run only the named stage (repeatable). Other stages "
                             "are skipped.")
    cleanup_group = parser.add_mutually_exclusive_group()
    cleanup_group.add_argument("--skip-cleanup", action="store_true",
                               help="do not run cleanup after stages (debugging)")
    cleanup_group.add_argument("--cleanup-only", action="store_true",
                               help="skip stages; run cleanup only")
    parser.add_argument("--users", default=None,
                        help="comma-separated allow-list override of cfg.users "
                             "(must be at least num_users entries)")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--quiet", action="store_true",
                           help="(reserved for v1.x — accepted but currently no-op)")
    verbosity.add_argument("--verbose", action="store_true",
                           help="(reserved for v1.x — accepted but currently no-op)")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.version:
        print(__version__)
        return 0

    try:
        cfg = load_config(args.config)
    except (ConfigError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 3

    if args.validate_config:
        return 0

    # --users override
    if args.users is not None:
        users_override = [u.strip() for u in args.users.split(",") if u.strip()]
        if len(users_override) < cfg.num_users:
            print(
                f"error: --users supplied {len(users_override)} entries but "
                f"num_users={cfg.num_users}; need at least num_users",
                file=sys.stderr,
            )
            return 3
        cfg = replace(cfg, users=users_override)

    # --stage override: only the named stages run; the rest get added to skip_stages
    if args.stage:
        selected = set(args.stage)
        skip = list(set(cfg.skip_stages) | (VALID_STAGES - selected))
        cfg = replace(cfg, skip_stages=skip)

    return run(cfg, skip_cleanup=args.skip_cleanup, cleanup_only=args.cleanup_only)


if __name__ == "__main__":
    sys.exit(main())
