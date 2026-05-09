#!/usr/bin/env bash
set -euo pipefail

uv venv
uv pip install -e ".[dev]"
uv run playwright install chromium
echo
echo "Bootstrap complete. Set CODER_SESSION_TOKEN, copy config.toml.example to config.toml,"
echo "then run: uv run coder-scale-test --config config.toml"
