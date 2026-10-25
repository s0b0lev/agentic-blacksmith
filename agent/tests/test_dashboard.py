import json

import pytest

from blacksmith.dashboard import (
    UID_MAX, Grafana, GrafanaError, build_dashboard, dashboard_link, dashboard_uid, resolve_public,
)


def test_dashboard_uid_is_short_stable_and_issue_scoped():
    uid = dashboard_uid("Acme/Shop", 12)
    assert uid == dashboard_uid("acme/shop", 12)
    assert uid.startswith("bs-12-") and len(uid) <= UID_MAX
    assert uid != dashboard_uid("acme/shop", 13)


def test_build_dashboard_pins_every_query_to_repo_and_issue():
    d = build_dashboard("acme/shop", 12, "blacksmith/issue-12", loki_uid="L", prom_uid="P",
                        agent_stream=True, metrics=True)
    assert d["uid"] == dashboard_uid("acme/shop", 12)
    assert d["title"] == "blacksmith acme/shop #12"
    assert "blacksmith" in d["tags"]
    panels = [p for row in d["panels"] for p in ([row] + row.get("panels", []))]
    exprs = [t["expr"] for p in panels for t in p.get("targets", [])]
    assert exprs
    assert all('repo="acme/shop"' in e and 'issue="12"' in e for e in exprs)
    kinds = {t["datasource"]["uid"] for p in panels for t in p.get("targets", [])}
    assert kinds == {"L", "P"}
    titles = [p["title"] for p in panels]
    assert "Runner events" in titles and "Agent stream" in titles


def test_build_dashboard_can_omit_agent_panel_and_metrics():
    d = build_dashboard("acme/shop", 12, "b", loki_uid="L", prom_uid="P", agent_stream=False, metrics=False)
    panels = [p for row in d["panels"] for p in ([row] + row.get("panels", []))]
    assert all(p["title"] != "Agent stream" for p in panels)
    assert all(t["datasource"]["uid"] == "L" for p in panels for t in p.get("targets", []))


@pytest.mark.parametrize("setting,private,expected", [
    ("auto", False, True), ("auto", True, False), ("true", True, True), ("false", False, False),
])
def test_resolve_public(setting, private, expected):
    assert resolve_public(setting, private) is expected


def test_dashboard_link():
    assert dashboard_link("https://g.example", "bs-1-x", "tok") == "https://g.example/public-dashboards/tok"
    assert dashboard_link("https://g.example/", "bs-1-x", None) == "https://g.example/d/bs-1-x"


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)   # [(status, body_dict)]
        self.calls = []

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, json.loads(body) if body else None, headers))
        status, payload = self.responses.pop(0)
        return status, json.dumps(payload).encode()


def test_upsert_dashboard_overwrites_and_returns_uid():
    http = FakeHttp([(200, {"uid": "bs-12-abc", "status": "success"})])
    g = Grafana("https://g.example", "sa-token", http)
    assert g.upsert_dashboard({"uid": "bs-12-abc", "title": "t"}) == "bs-12-abc"
    method, url, body, headers = http.calls[0]
    assert (method, url) == ("POST", "https://g.example/api/dashboards/db")
    assert body["overwrite"] is True and body["dashboard"]["uid"] == "bs-12-abc"
    assert headers["Authorization"] == "Bearer sa-token"


SHARES = "https://g.example/api/dashboards/uid/bs-12-abc/public-dashboards/"


def test_share_public_creates_share_when_none_exists():
    http = FakeHttp([(404, {"message": "not found"}), (200, {"accessToken": "abc123", "uid": "pd1"})])
    assert Grafana("https://g.example", "t", http).share_public("bs-12-abc") == "abc123"
    assert http.calls[0][:2] == ("GET", SHARES)
    method, url, body, _ = http.calls[1]
    assert (method, url) == ("POST", SHARES)
    assert body == {"isEnabled": True, "share": "public", "timeSelectionEnabled": True}


def test_share_public_reuses_an_enabled_share_without_posting():
    http = FakeHttp([(200, {"accessToken": "existing", "uid": "pd1", "isEnabled": True})])
    assert Grafana("https://g.example", "t", http).share_public("bs-12-abc") == "existing"
    assert [c[:2] for c in http.calls] == [("GET", SHARES)]


def test_share_public_replaces_a_disabled_share():
    http = FakeHttp([(200, {"accessToken": "old", "uid": "pd1", "isEnabled": False}),
                     (200, {}),
                     (200, {"accessToken": "new", "uid": "pd2", "isEnabled": True})])
    assert Grafana("https://g.example", "t", http).share_public("bs-12-abc") == "new"
    assert [c[:2] for c in http.calls] == [
        ("GET", SHARES), ("DELETE", SHARES + "pd1"), ("POST", SHARES)]


def test_unshare_removes_existing_share():
    http = FakeHttp([(200, {"accessToken": "x", "uid": "pd1"}), (200, {})])
    Grafana("https://g.example", "t", http).unshare("bs-12-abc")
    assert http.calls[1][:2] == ("DELETE", "https://g.example/api/dashboards/uid/bs-12-abc/public-dashboards/pd1")


def test_unshare_and_delete_tolerate_404():
    http = FakeHttp([(404, {"message": "not found"}), (404, {"message": "not found"})])
    g = Grafana("https://g.example", "t", http)
    g.unshare("bs-12-abc")
    g.delete_dashboard("bs-12-abc")


def test_errors_carry_status_and_path_not_token():
    http = FakeHttp([(500, {"message": "boom"})])
    with pytest.raises(GrafanaError) as exc:
        Grafana("https://g.example", "secret-token", http).upsert_dashboard({"uid": "u"})
    assert "500" in str(exc.value) and "/api/dashboards/db" in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_agent_stream_extracts_content_fields_with_explicit_json_paths():
    d = build_dashboard("acme/shop", 12, "b", loki_uid="L", prom_uid="P", agent_stream=True, metrics=False)
    (panel,) = [p for p in d["panels"] if p["title"] == "Agent stream"]
    expr = panel["targets"][0]["expr"]
    assert "message.content[0].type" in expr and "message_content_0" not in expr
    assert 'input="message.content[0].input"' in expr
    assert "{{ .input | trunc 200 }}" in expr


def test_upsert_without_uid_in_response_raises_grafana_error():
    http = FakeHttp([(200, {})])
    with pytest.raises(GrafanaError) as exc:
        Grafana("https://g.example", "secret-token", http).upsert_dashboard({"uid": "u"})
    assert "/api/dashboards/db" in str(exc.value) and "secret-token" not in str(exc.value)


def test_share_post_error_carries_the_message_not_the_token():
    http = FakeHttp([(404, {"message": "not found"}),
                     (400, {"message": "public dashboards are disabled"})])
    with pytest.raises(GrafanaError) as exc:
        Grafana("https://g.example", "secret-token", http).share_public("bs-12-abc")
    assert "400" in str(exc.value) and "public dashboards are disabled" in str(exc.value)
    assert "secret-token" not in str(exc.value)


def test_share_get_error_other_than_404_raises():
    http = FakeHttp([(500, {"message": "boom"})])
    with pytest.raises(GrafanaError) as exc:
        Grafana("https://g.example", "secret-token", http).share_public("bs-12-abc")
    assert "500" in str(exc.value) and "secret-token" not in str(exc.value)


def test_runner_events_stat_reads_zero_when_quiet():
    d = build_dashboard("acme/shop", 12, "b", loki_uid="L", prom_uid="P", agent_stream=True, metrics=True)
    (panel,) = [p for p in d["panels"] if p["title"] == "Runner events, last 10 min"]
    assert panel["type"] == "stat"
    assert panel["fieldConfig"]["defaults"]["noValue"] == "0"


def test_repr_hides_the_token():
    assert "secret-token" not in repr(Grafana("https://g.example", "secret-token"))
