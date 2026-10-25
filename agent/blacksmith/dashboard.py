"""Per-issue Grafana dashboard: the JSON model and a small client for the Grafana HTTP API."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

UID_MAX = 40  # Grafana's limit on a dashboard uid
TIMEOUT_S = 30

Http = Callable[[str, str, bytes | None, Mapping[str, str]], tuple[int, bytes]]


# --- Dashboard JSON --------------------------------------------------------------------


def dashboard_uid(repo: str, issue: int) -> str:
    """Stable per (repo, issue), case-insensitive on the repo, within Grafana's uid limit."""
    return f"bs-{issue}-{hashlib.sha1(repo.lower().encode()).hexdigest()[:12]}"


def _ds(kind: str, uid: str) -> dict:
    return {"type": kind, "uid": uid}


def _panel(kind: str, title: str, ds: dict, expr: str, grid: tuple[int, int, int, int],
           **extra) -> dict:
    x, y, w, h = grid
    return {
        "type": kind,
        "title": title,
        "datasource": ds,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [{"refId": "A", "datasource": ds, "expr": expr}],
        **extra,
    }


def _logs(title: str, ds: dict, expr: str, grid: tuple[int, int, int, int], wrap: bool) -> dict:
    return _panel("logs", title, ds, expr, grid,
                  options={"sortOrder": "Descending", "wrapLogMessage": wrap})


def _percent(title: str, ds: dict, expr: str, grid: tuple[int, int, int, int]) -> dict:
    return _panel("stat", title, ds, expr, grid,
                  fieldConfig={"defaults": {"unit": "percent", "min": 0, "max": 100},
                               "overrides": []})


def build_dashboard(repo: str, issue: int, branch: str, *, loki_uid: str, prom_uid: str,
                    agent_stream: bool, metrics: bool) -> dict:
    """The dashboard model; every query is pinned to this repo and issue."""
    sel = f'repo="{repo}", issue="{issue}"'
    loki = _ds("loki", loki_uid)
    prom = _ds("prometheus", prom_uid)
    panels: list[dict] = []
    if metrics:
        panels += [
            _percent("CPU busy", prom,
                     f'100 * (1 - avg(rate(node_cpu_seconds_total{{{sel}, mode="idle"}}[5m])))',
                     (0, 0, 6, 4)),
            _percent("Memory used", prom,
                     f"100 * (1 - sum(node_memory_MemAvailable_bytes{{{sel}}})"
                     f" / sum(node_memory_MemTotal_bytes{{{sel}}}))",
                     (6, 0, 6, 4)),
            _percent("Disk used", prom,
                     f'100 * (1 - sum(node_filesystem_avail_bytes{{{sel}, mountpoint="/"}})'
                     f' / sum(node_filesystem_size_bytes{{{sel}, mountpoint="/"}}))',
                     (12, 0, 6, 4)),
        ]
    # noValue: a quiet runner reads 0 (the "stuck" signal), not "No data".
    panels.append(_panel("stat", "Runner events, last 10 min", loki,
                         f'sum(count_over_time({{{sel}, job="runner"}}[10m]))', (18, 0, 6, 4),
                         fieldConfig={"defaults": {"noValue": "0"}, "overrides": []}))
    y = 4
    panels.append(_logs("Runner events", loki, f'{{{sel}, job="runner"}}', (0, y, 24, 10), False))
    y += 10
    if agent_stream:
        panels.append(_logs(
            "Agent stream", loki,
            # A bare `| json` skips arrays, so content[0] needs explicit expressions.
            f'{{{sel}, job="agent"}} | json type="type", kind="message.content[0].type", '
            'name="message.content[0].name", text="message.content[0].text", '
            'input="message.content[0].input" '
            '| line_format "{{.type}} {{.kind}} {{.name}} {{.text}} {{ .input | trunc 200 }}"',
            (0, y, 24, 12), True))
        y += 12
    panels.append(_logs("Containers", loki, f'{{{sel}, job="docker"}}', (0, y, 24, 10), False))
    y += 10
    panels.append({
        "type": "row",
        "title": "Systemd and bootstrap",
        "collapsed": True,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [_logs("Journal and bootstrap", loki, f'{{{sel}, job=~"journal|bootstrap"}}',
                         (0, y + 1, 24, 10), False)],
    })
    return {
        "uid": dashboard_uid(repo, issue),
        "title": f"blacksmith {repo} #{issue}",
        "description": f"Branch {branch}",
        "tags": ["blacksmith"],
        "time": {"from": "now-6h", "to": "now"},
        "refresh": "30s",
        "schemaVersion": 39,
        "panels": panels,
    }


def resolve_public(setting: str, repo_private: bool) -> bool:
    """``auto`` shares publicly only for a public repo; ``true``/``false`` are explicit."""
    if setting == "auto":
        return not repo_private
    return setting == "true"


def dashboard_link(grafana_url: str, uid: str, access_token: str | None) -> str:
    base = grafana_url.rstrip("/")
    if access_token:
        return f"{base}/public-dashboards/{access_token}"
    return f"{base}/d/{uid}"


# --- Grafana HTTP client ---------------------------------------------------------------


class GrafanaError(RuntimeError):
    """A Grafana API call failed. The text carries status and path, never the token."""


def _urllib_http(method: str, url: str, body: bytes | None,
                 headers: Mapping[str, str]) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        # 4xx/5xx are data to the client (404 = not shared yet, or already gone).
        return exc.code, exc.read()


def _message(raw: bytes) -> str:
    try:
        payload = json.loads(raw or b"{}")
    except ValueError:
        return raw[:200].decode(errors="replace")
    if isinstance(payload, dict):
        return str(payload.get("message", ""))
    return ""


@dataclass(frozen=True)
class Grafana:
    url: str
    token: str = field(repr=False)
    http: Http = _urllib_http

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        data = json.dumps(body).encode() if body is not None else None
        try:
            status, raw = self.http(method, self.url.rstrip("/") + path, data, headers)
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}".replace(self.token, "***")
            raise GrafanaError(f"{method} {path} failed: {detail}") from None
        if status >= 400:
            message = _message(raw).replace(self.token, "***")
            raise _StatusError(status, f"{method} {path} returned {status}: {message}")
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            payload = {}
        return payload if isinstance(payload, dict) else {}

    def _tolerate(self, status: int, method: str, path: str,
                  body: dict | None = None) -> dict | None:
        """The response, or None when Grafana answered with ``status``."""
        try:
            return self._request(method, path, body)
        except _StatusError as exc:
            if exc.status == status:
                return None
            raise

    def upsert_dashboard(self, dashboard: dict) -> str:
        payload = self._request("POST", "/api/dashboards/db",
                                {"dashboard": dashboard, "overwrite": True})
        return _require(payload, "uid", "POST", "/api/dashboards/db")

    def _shares(self, uid: str) -> str:
        return f"/api/dashboards/uid/{uid}/public-dashboards/"

    def share_public(self, uid: str) -> str:
        """The access token of an enabled public share, reusing or replacing an existing one."""
        path = self._shares(uid)
        existing = self._tolerate(404, "GET", path) or {}
        if existing.get("accessToken") and existing.get("isEnabled") is True:
            return existing["accessToken"]
        if existing.get("uid"):
            # A disabled share would hand out a link that shows nothing: replace it.
            self._tolerate(404, "DELETE", path + existing["uid"])
        body = {"isEnabled": True, "share": "public", "timeSelectionEnabled": True}
        payload = self._request("POST", path, body)
        return _require(payload, "accessToken", "POST", path)

    def unshare(self, uid: str) -> None:
        existing = self._tolerate(404, "GET", self._shares(uid))
        share_uid = (existing or {}).get("uid")
        if share_uid:
            self._tolerate(404, "DELETE", self._shares(uid) + share_uid)

    def delete_dashboard(self, uid: str) -> None:
        self._tolerate(404, "DELETE", f"/api/dashboards/uid/{uid}")


def _require(payload: dict, key: str, method: str, path: str) -> str:
    value = payload.get(key)
    if not value:
        raise GrafanaError(f"{method} {path} returned no {key}")
    return value


class _StatusError(GrafanaError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
