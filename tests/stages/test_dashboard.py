"""Tests for stage 5: Playwright headless Chromium dashboard time-to-ready."""
from __future__ import annotations
from urllib.parse import urlparse
from unittest.mock import MagicMock

import pytest

from coder_scale_test.stages import dashboard as stage
from coder_scale_test.coder_client import User
from coder_scale_test.runner import StageContext


@pytest.fixture
def ctx(tmp_log, coder_client, mocker):
    _, fh = tmp_log
    cfg = mocker.Mock(
        coder_url="https://coder.example.com",
        coder_session_token="tok",
        dashboard_ready_selector="[data-testid='workspaces-table']",
        timeouts=mocker.Mock(dashboard_ready=10),
    )
    return StageContext(
        cfg=cfg, client=coder_client, ledger=mocker.Mock(), log=fh,
        users=[
            User("u1", "alice", "2024-01-01T00:00:00Z"),
            User("u2", "bob", "2024-01-02T00:00:00Z"),
        ],
        workspaces=[],
    )


def _fake_playwright(mocker):
    """Return a fake sync_playwright() chain (context manager + cm.__enter__)."""
    pw = MagicMock(name="Playwright")
    browser = MagicMock(name="Browser")
    pw.chromium.launch.return_value = browser
    contexts: list[MagicMock] = []

    def _new_context(*args, **kwargs):
        c = MagicMock(name="BrowserContext")
        c._cookies = []
        c._new_context_kwargs = kwargs  # capture for the no-storage_state assertion
        c.add_cookies.side_effect = lambda cookies: c._cookies.extend(cookies)
        page = MagicMock(name="Page")
        c.new_page.return_value = page
        contexts.append(c)
        return c
    browser.new_context.side_effect = _new_context

    cm = MagicMock()
    cm.__enter__.return_value = pw
    cm.__exit__.return_value = False
    return cm, pw, browser, contexts


def test_loads_dashboard_per_user(ctx, mocker):
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)
    res = stage.run(ctx)
    assert res.ok is True
    assert browser.new_context.call_count == 2
    for c in contexts:
        page = c.new_page.return_value
        page.goto.assert_called_with("https://coder.example.com")
        page.wait_for_selector.assert_called_with(
            "[data-testid='workspaces-table']", timeout=10_000
        )


def test_sets_admin_cookie_on_each_context(ctx, mocker):
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)
    stage.run(ctx)
    expected_host = urlparse(ctx.cfg.coder_url).hostname
    for c in contexts:
        assert len(c._cookies) == 1
        cookie = c._cookies[0]
        assert cookie["name"] == "coder_session_token"
        assert cookie["value"] == "tok"
        assert cookie["domain"] == expected_host
        assert cookie["path"] == "/"
        assert cookie["secure"] is True


def test_no_storage_state_passed_to_new_context(ctx, mocker):
    """Autoplan mandate (line 3569): browser.new_context() must NOT receive a
    storage_state kwarg. Persistent state across contexts would corrupt
    measurement integrity.
    """
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)
    stage.run(ctx)
    assert len(contexts) == 2
    for c in contexts:
        assert "storage_state" not in c._new_context_kwargs, (
            f"new_context received storage_state={c._new_context_kwargs.get('storage_state')!r}; "
            "this would corrupt measurement integrity (cross-run cookie persistence)."
        )


def test_fail_on_selector_timeout(ctx, mocker):
    from playwright.sync_api import TimeoutError as PWTimeout
    cm, pw, browser, contexts = _fake_playwright(mocker)
    mocker.patch("coder_scale_test.stages.dashboard.sync_playwright",
                 return_value=cm)

    def _new_context(*args, **kwargs):
        c = MagicMock()
        c._new_context_kwargs = kwargs
        page = MagicMock()
        page.wait_for_selector.side_effect = PWTimeout("selector did not appear")
        c.new_page.return_value = page
        return c
    browser.new_context.side_effect = _new_context

    res = stage.run(ctx)
    assert res.ok is False
    assert "playwright" in (res.err or "").lower() or "timeout" in (res.err or "").lower()
