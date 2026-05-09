"""Tests for CoderClient REST methods (mocked HTTP via mocker.patch on the session)."""
from __future__ import annotations

import pytest

from coder_scale_test.coder_client import (
    CoderClient,
    CoderApiError,
    User,
    Workspace,
)


@pytest.fixture
def client():
    return CoderClient("https://coder.example.com", "tok-abc")


# --- list_active_non_admin_users ---


def test_list_users_filters_admins(client, mocker):
    payload = {"users": [
        {"id": "u1", "username": "alice", "roles": [{"name": "member"}],
         "created_at": "2024-01-01T00:00:00Z", "status": "active"},
        {"id": "u2", "username": "boss", "roles": [{"name": "owner"}],
         "created_at": "2024-01-02T00:00:00Z", "status": "active"},
        {"id": "u3", "username": "bob", "roles": [{"name": "member"}],
         "created_at": "2024-01-03T00:00:00Z", "status": "active"},
        {"id": "u4", "username": "charlie", "roles": [{"name": "admin"}],
         "created_at": "2024-01-04T00:00:00Z", "status": "active"},
    ]}
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = payload

    users = client.list_active_non_admin_users(limit=100)
    assert [u.username for u in users] == ["alice", "bob"]
    assert all(u.created_at for u in users)


def test_list_users_sends_auth_header(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {"users": []}
    client.list_active_non_admin_users(limit=10)
    args, kwargs = mock_get.call_args
    # Per-request header (defense-in-depth: also set on session, but we want
    # explicit per-request behaviour for redirect-following safety)
    assert kwargs["headers"]["Coder-Session-Token"] == "tok-abc"


def test_list_users_raises_on_5xx(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 503
    mock_get.return_value.text = "service unavailable"
    with pytest.raises(CoderApiError, match="status=503"):
        client.list_active_non_admin_users(limit=10)


# --- create_workspace + template cache ---


def test_create_workspace_returns_workspace(client, mocker):
    # Template lookup
    template_resp = mocker.Mock()
    template_resp.status_code = 200
    template_resp.json.return_value = [
        {"id": "tpl-1", "name": "ubuntu-base"},
        {"id": "tpl-2", "name": "other"},
    ]
    mock_get = mocker.patch.object(client._sess, "get", return_value=template_resp)

    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 201
    mock_post.return_value.json.return_value = {
        "id": "ws-1", "name": "scaletest-alice-0",
        "owner_name": "alice",
        "latest_build": {"job": {"status": "pending"}, "transition": "start"},
    }

    ws = client.create_workspace(
        user_id="u1", name="scaletest-alice-0", template_name="ubuntu-base"
    )
    assert ws.id == "ws-1"
    assert ws.name == "scaletest-alice-0"
    assert ws.owner_name == "alice"
    # POST body should reference the resolved template id
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["template_id"] == "tpl-1"
    assert mock_get.call_count == 1  # /templates was called once


def test_template_id_is_cached_per_instance(client, mocker):
    """_resolve_template_id memoizes per-instance — second create_workspace must not GET /templates again.

    Auto-decided in autoplan Eng MEDIUM 1.4: avoid 30 wasteful GETs for 30 workspaces.
    """
    template_resp = mocker.Mock()
    template_resp.status_code = 200
    template_resp.json.return_value = [{"id": "tpl-1", "name": "ubuntu-base"}]
    mock_get = mocker.patch.object(client._sess, "get", return_value=template_resp)

    post_resp = mocker.Mock()
    post_resp.status_code = 201
    post_resp.json.return_value = {
        "id": "ws-1", "name": "n", "owner_name": "alice",
        "latest_build": {"job": {"status": "pending"}, "transition": "start"},
    }
    mocker.patch.object(client._sess, "post", return_value=post_resp)

    client.create_workspace(user_id="u1", name="n1", template_name="ubuntu-base")
    client.create_workspace(user_id="u1", name="n2", template_name="ubuntu-base")
    client.create_workspace(user_id="u1", name="n3", template_name="ubuntu-base")
    assert mock_get.call_count == 1, "template id should be cached after first lookup"


def test_template_not_found_raises(client, mocker):
    template_resp = mocker.Mock()
    template_resp.status_code = 200
    template_resp.json.return_value = [{"id": "tpl-1", "name": "other"}]
    mocker.patch.object(client._sess, "get", return_value=template_resp)
    with pytest.raises(CoderApiError, match="template_not_found"):
        client.create_workspace(user_id="u1", name="n", template_name="ubuntu-base")


# --- get_workspace / wait_for_running ---


def test_get_workspace(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "scaletest-alice-0",
        "owner_name": "alice",
        "latest_build": {"job": {"status": "succeeded"}, "transition": "start"},
    }
    ws = client.get_workspace("ws-1")
    assert ws.latest_build_status == "succeeded"
    assert ws.latest_build_transition == "start"


def test_wait_for_running_succeeds(client, mocker):
    """Returns when build is succeeded+start."""
    statuses = iter([("pending", "start"), ("running", "start"), ("succeeded", "start")])

    def _get(*args, **kwargs):
        r = mocker.Mock()
        r.status_code = 200
        s, t = next(statuses)
        r.json.return_value = {
            "id": "ws-1", "name": "n", "owner_name": "alice",
            "latest_build": {"job": {"status": s}, "transition": t},
        }
        return r

    mocker.patch.object(client._sess, "get", side_effect=_get)
    ws = client.wait_for_running("ws-1", timeout=60)
    assert ws.latest_build_status == "succeeded"


def test_wait_for_running_canceled_is_terminal(client, mocker):
    """Canceled build raises immediately, doesn't poll until timeout.

    Auto-decided in autoplan Eng LOW 4.4: treat canceled and unknown as terminal.
    """
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "n", "owner_name": "alice",
        "latest_build": {"job": {"status": "canceled"}, "transition": "start"},
    }
    with pytest.raises(CoderApiError, match="canceled"):
        client.wait_for_running("ws-1", timeout=60)


def test_wait_for_running_failed_is_terminal(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "n", "owner_name": "alice",
        "latest_build": {"job": {"status": "failed"}, "transition": "start"},
    }
    with pytest.raises(CoderApiError, match="failed"):
        client.wait_for_running("ws-1", timeout=60)


def test_wait_for_running_times_out(client, mocker):
    """When build never reaches succeeded, raises after timeout."""
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "n", "owner_name": "alice",
        "latest_build": {"job": {"status": "pending"}, "transition": "start"},
    }
    # Force monotonic forward; loop should bail out.
    times = iter([0, 0.1, 1, 2, 6, 7, 8])
    mocker.patch(
        "coder_scale_test.coder_client.time.monotonic",
        side_effect=lambda: next(times),
    )
    with pytest.raises(CoderApiError, match="timeout"):
        client.wait_for_running("ws-1", timeout=5)


# --- delete_workspace ---


def test_delete_workspace_polls_until_succeeded(client, mocker):
    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "id": "build-9", "transition": "delete",
        "job": {"status": "pending"},
    }
    statuses = iter(["pending", "running", "succeeded"])
    mock_get = mocker.patch.object(client._sess, "get")

    def _get_resp(*args, **kwargs):
        r = mocker.Mock()
        r.status_code = 200
        r.json.return_value = {"id": "build-9", "job": {"status": next(statuses)}}
        return r
    mock_get.side_effect = _get_resp

    client.delete_workspace("ws-1", timeout=5)
    assert mock_post.call_count == 1
    assert mock_get.call_count == 3


def test_delete_workspace_times_out(client, mocker):
    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "id": "build-9", "transition": "delete", "job": {"status": "pending"},
    }
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "build-9", "job": {"status": "pending"},
    }
    times = iter([0, 0.1, 1, 2, 6, 7, 8])
    mocker.patch(
        "coder_scale_test.coder_client.time.monotonic",
        side_effect=lambda: next(times),
    )
    with pytest.raises(CoderApiError, match="timeout"):
        client.delete_workspace("ws-1", timeout=5)


def test_delete_workspace_failed_build_is_terminal(client, mocker):
    mock_post = mocker.patch.object(client._sess, "post")
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {
        "id": "build-9", "transition": "delete", "job": {"status": "pending"},
    }
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "build-9", "job": {"status": "failed"},
    }
    with pytest.raises(CoderApiError, match="failed"):
        client.delete_workspace("ws-1", timeout=60)


# --- get_agent_id ---


def test_get_agent_id_returns_first(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "n", "owner_name": "alice",
        "latest_build": {
            "job": {"status": "succeeded"}, "transition": "start",
            "resources": [
                {"agents": [{"id": "agent-A"}, {"id": "agent-B"}]},
                {"agents": [{"id": "agent-C"}]},
            ],
        },
    }
    assert client.get_agent_id("ws-1") == "agent-A"


def test_get_agent_id_no_agents_raises(client, mocker):
    mock_get = mocker.patch.object(client._sess, "get")
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "id": "ws-1", "name": "n", "owner_name": "alice",
        "latest_build": {"job": {"status": "succeeded"}, "transition": "start", "resources": []},
    }
    with pytest.raises(CoderApiError, match="no agent"):
        client.get_agent_id("ws-1")


# --- session config (autoplan augmentations) ---


def test_user_agent_header_set():
    client = CoderClient("https://coder.example.com", "tok-abc")
    ua = client._sess.headers.get("User-Agent")
    assert ua is not None and ua.startswith("coder-scale-test/")


def test_session_token_header_set():
    client = CoderClient("https://coder.example.com", "tok-abc")
    assert client._sess.headers.get("Coder-Session-Token") == "tok-abc"


def test_retry_adapter_mounted_with_configured_count():
    """Session has a Retry adapter on https:// with the configured retry_count."""
    client = CoderClient(
        "https://coder.example.com", "tok-abc",
        retry_count=5, retry_backoff_seconds=2.0,
    )
    https_adapter = client._sess.get_adapter("https://coder.example.com")
    # urllib3 Retry — total should match
    retries = https_adapter.max_retries
    assert retries.total == 5
    # backoff_factor is the urllib3 attribute
    assert retries.backoff_factor == 2.0


def test_session_max_redirects_zero():
    """Redirects must NOT follow (token leak protection).

    Auto-decided in autoplan Eng MEDIUM 6.4.
    """
    client = CoderClient("https://coder.example.com", "tok-abc")
    assert client._sess.max_redirects == 0


def test_tls_verify_propagates():
    """tls_verify=False must disable cert verification on the Session."""
    client = CoderClient(
        "https://coder.example.com", "tok-abc", tls_verify=False
    )
    assert client._sess.verify is False
    client2 = CoderClient(
        "https://coder.example.com", "tok-abc", tls_verify=True
    )
    assert client2._sess.verify is True


def test_poll_interval_propagates():
    """poll_interval_seconds is stored on the instance and used by wait_for_running."""
    client = CoderClient(
        "https://coder.example.com", "tok-abc", poll_interval_seconds=0.5
    )
    assert client.poll_interval_seconds == 0.5
