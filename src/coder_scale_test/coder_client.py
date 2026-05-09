"""HTTP client for the subset of the Coder REST API this tool uses.

Exposes only what the stages call. WebSocket transports are opened by individual
stages with `websocket-client` directly because the PTY (stage 3) and app-proxy
(stage 4) protocols differ enough that a shared abstraction would be premature.

Several autoplan auto-decided augmentations beyond the original spec:

* HTTP retry adapter (urllib3 `Retry`) on the `requests.Session` so transient
  5xx don't fail-fast a stage.
* `User-Agent: coder-scale-test/<version>` so cluster audit logs can identify
  this tool's runs.
* `max_redirects = 0` and `allow_redirects=False` per request so the token
  header doesn't leak to a redirected host.
* `_resolve_template_id` cached per-instance so 30 workspace creates issue
  one `/templates` GET, not 30.
* `wait_for_running` and `delete_workspace` treat `failed`, `canceled`, and
  `unknown` as terminal-error (not just `failed`).
* Poll interval is constructor-configurable (was hardcoded `POLL_INTERVAL_S`).
* `tls_verify` propagates to `Session.verify`.

API reference: https://coder.com/docs/reference/api
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from coder_scale_test import __version__

DEFAULT_POLL_INTERVAL_S = 2.0
USER_AGENT = f"coder-scale-test/{__version__}"
TERMINAL_ERROR_STATUSES = ("failed", "canceled", "unknown")


class CoderApiError(RuntimeError):
    """Raised on any Coder REST error: 4xx, 5xx, or operation timeout."""


@dataclass(frozen=True)
class User:
    id: str
    username: str
    created_at: str  # ISO-8601 from the API


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    owner_name: str
    latest_build_status: str  # pending | running | succeeded | failed | canceled | unknown
    latest_build_transition: str  # start | stop | delete


class CoderClient:
    def __init__(
        self,
        coder_url: str,
        session_token: str,
        *,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_S,
        tls_verify: bool = True,
        retry_count: int = 3,
        retry_backoff_seconds: float = 1.0,
    ) -> None:
        self.coder_url = coder_url.rstrip("/")
        self.session_token = session_token
        self.poll_interval_seconds = poll_interval_seconds

        self._sess = requests.Session()
        self._sess.headers.update({
            "Coder-Session-Token": session_token,
            "User-Agent": USER_AGENT,
        })
        self._sess.verify = tls_verify
        self._sess.max_redirects = 0  # token leak protection (autoplan Eng 6.4)

        # HTTP retry on transient 5xx (autoplan DX expansion 13)
        retry = Retry(
            total=retry_count,
            backoff_factor=retry_backoff_seconds,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=frozenset(["GET", "POST", "PUT", "DELETE"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self._sess.mount("https://", adapter)
        self._sess.mount("http://", adapter)

        # Per-instance template cache (autoplan Eng 1.4)
        self._template_id_cache: dict[str, str] = {}

    # ---------- Users ----------

    def list_active_non_admin_users(self, limit: int = 200) -> list[User]:
        """Return active users whose roles array contains neither owner nor admin,
        sorted by created_at ascending."""
        url = f"{self.coder_url}/api/v2/users"
        params = {"status": "active", "limit": limit}
        r = self._sess.get(
            url,
            params=params,
            headers={"Coder-Session-Token": self.session_token},
            allow_redirects=False,
        )
        if r.status_code >= 400:
            raise CoderApiError(
                f"list_users: status={r.status_code} body={r.text[:200]!r}"
            )
        body = r.json()
        out: list[User] = []
        for u in body.get("users", []):
            roles = {role.get("name") for role in u.get("roles", [])}
            if roles & {"owner", "admin"}:
                continue
            out.append(
                User(id=u["id"], username=u["username"], created_at=u["created_at"])
            )
        out.sort(key=lambda u: u.created_at)
        return out

    # ---------- Workspaces ----------

    def create_workspace(self, *, user_id: str, name: str, template_name: str) -> Workspace:
        """Create a workspace for `user_id` from a template referenced by name."""
        template_id = self._resolve_template_id(template_name)
        url = f"{self.coder_url}/api/v2/users/{user_id}/workspaces"
        body = {"template_id": template_id, "name": name}
        r = self._sess.post(url, json=body, allow_redirects=False)
        if r.status_code >= 400:
            raise CoderApiError(
                f"create_workspace: status={r.status_code} body={r.text[:200]!r}"
            )
        return _ws_from_json(r.json())

    def get_workspace(self, ws_id: str) -> Workspace:
        url = f"{self.coder_url}/api/v2/workspaces/{ws_id}"
        r = self._sess.get(url, allow_redirects=False)
        if r.status_code >= 400:
            raise CoderApiError(f"get_workspace: status={r.status_code}")
        return _ws_from_json(r.json())

    def wait_for_running(self, ws_id: str, timeout: int) -> Workspace:
        """Poll get_workspace until latest_build is succeeded+start. Raise on
        timeout, terminal failure (failed/canceled/unknown), or wrong transition."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            ws = self.get_workspace(ws_id)
            if (
                ws.latest_build_status == "succeeded"
                and ws.latest_build_transition == "start"
            ):
                return ws
            if ws.latest_build_status in TERMINAL_ERROR_STATUSES:
                raise CoderApiError(
                    f"wait_for_running: build {ws.latest_build_status} for {ws_id}"
                )
            # Avoid wasting a sleep that pushes us past the deadline (fence-post fix)
            if time.monotonic() + self.poll_interval_seconds >= deadline:
                break
            time.sleep(self.poll_interval_seconds)
        raise CoderApiError(f"wait_for_running: timeout after {timeout}s for {ws_id}")

    def delete_workspace(self, ws_id: str, timeout: int) -> None:
        """Issue a delete-transition build and poll until it succeeds or times out."""
        url = f"{self.coder_url}/api/v2/workspaces/{ws_id}/builds"
        r = self._sess.post(url, json={"transition": "delete"}, allow_redirects=False)
        if r.status_code >= 400:
            raise CoderApiError(
                f"delete_workspace POST: status={r.status_code} "
                f"body={r.text[:200]!r}"
            )
        build_id = r.json()["id"]
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            br = self._sess.get(
                f"{self.coder_url}/api/v2/workspacebuilds/{build_id}",
                allow_redirects=False,
            )
            if br.status_code >= 400:
                raise CoderApiError(
                    f"delete_workspace GET build: status={br.status_code}"
                )
            status = br.json().get("job", {}).get("status")
            if status == "succeeded":
                return
            if status in TERMINAL_ERROR_STATUSES:
                raise CoderApiError(
                    f"delete_workspace: build {status} for {ws_id}"
                )
            if time.monotonic() + self.poll_interval_seconds >= deadline:
                break
            time.sleep(self.poll_interval_seconds)
        raise CoderApiError(f"delete_workspace: timeout after {timeout}s for {ws_id}")

    # ---------- Agents ----------

    def get_agent_id(self, ws_id: str) -> str:
        """Return the first agent ID for the workspace's running build."""
        url = f"{self.coder_url}/api/v2/workspaces/{ws_id}"
        r = self._sess.get(url, allow_redirects=False)
        if r.status_code >= 400:
            raise CoderApiError(f"get_agent_id: status={r.status_code}")
        ws = r.json()
        for resource in ws.get("latest_build", {}).get("resources", []):
            for agent in resource.get("agents", []):
                return agent["id"]
        raise CoderApiError(f"get_agent_id: no agent found for {ws_id}")

    # ---------- Internal ----------

    def _resolve_template_id(self, name: str) -> str:
        """Look up a template by name and return its ID. Cached per-instance."""
        if name in self._template_id_cache:
            return self._template_id_cache[name]
        url = f"{self.coder_url}/api/v2/templates"
        r = self._sess.get(url, allow_redirects=False)
        if r.status_code >= 400:
            raise CoderApiError(f"list_templates: status={r.status_code}")
        for t in r.json():
            if t.get("name") == name:
                self._template_id_cache[name] = t["id"]
                return t["id"]
        raise CoderApiError(f"template_not_found: {name!r}")


def _ws_from_json(body: dict[str, Any]) -> Workspace:
    lb = body.get("latest_build", {}) or {}
    return Workspace(
        id=body["id"],
        name=body["name"],
        owner_name=body["owner_name"],
        latest_build_status=lb.get("job", {}).get("status", ""),
        latest_build_transition=lb.get("transition", ""),
    )
