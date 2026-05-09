"""Shared pytest fixtures.

CoderClient is mocked everywhere so tests never touch the network. The
tmp_log fixture returns an open writeable file plus its path for assertions.
"""
from __future__ import annotations
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch):
    """Globally neuter time.sleep so timeout-path tests don't waste real seconds.

    Required because several modules (coder_client, stages) call time.sleep in
    poll loops; without this fixture, the test suite would blow past its
    sub-5-second runtime budget.
    """
    monkeypatch.setattr("time.sleep", lambda _: None)


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
