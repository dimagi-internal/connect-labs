"""The result View canopy draws for data_query, and the hand-off to the Data page."""

import re
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest

from connect_labs.labs import canopy_panel
from connect_labs.labs.access import scopes
from connect_labs.labsdata import scope, service, spaces
from connect_labs.labsdata.scope import Opportunity
from connect_labs.mcp.tool_registry import get_tool
from connect_labs.mcp.ui import DATA_RESULT_URI, RESOURCES

pytestmark = pytest.mark.django_db

OPP = Opportunity(id=941, name="Opp V", llo="LLO V")


def _resource():
    return next(r for r in RESOURCES if r.uri == DATA_RESULT_URI)


def test_data_query_is_drawn_by_the_result_view_and_its_alias_is_not():
    assert get_tool("data_query").meta["ui"] == {"resourceUri": DATA_RESULT_URI, "visibility": ["model", "app"]}
    assert not get_tool("explorer_query").meta


def test_the_view_is_self_contained_and_speaks_mcp_apps():
    html = _resource().html()
    assert html.lower().startswith("<!doctype html>")
    assert not re.search(r"""<script[^>]+src=""", html)
    assert not re.search(r"""(src|href)\s*=\s*["']?https?:""", html)
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "eval(", "new Function", "<link", "<iframe"):
        assert banned not in html, banned
    for method in (
        "ui/initialize",
        "ui/notifications/tool-input",
        "ui/notifications/tool-result",
        "ui/notifications/size-changed",
        "ui/open-link",
        "ui/request-display-mode",
        "resources/read",
        "resources/subscribe",
    ):
        assert method in html, method
    # It reads the kept result; it never runs the query again as the viewer.
    assert "tools/call" not in html


def test_withheld_rows_are_withheld_from_the_view_too():
    # The View never asks for rows itself when the agent was refused them.
    html = _resource().html()
    assert "r.withheld" in html and "Sensitive data stays in Labs" in html


def test_an_uncleared_agent_gets_a_hand_off_not_rows(django_user_model):
    from connect_labs.mcp.tools import labsdata as tools

    # A real user: the hand-off is kept for them (mcp/kept_results.py).
    user = django_user_model.objects.create_user(username="visitor", password="x")

    with (
        mock.patch.object(scope, "resolve", return_value=[OPP]),
        mock.patch.object(tools, "_caller", return_value=scopes.Caller(access_token="t")),
        mock.patch.object(tools, "_from_canopy", return_value=True),
        mock.patch.object(
            canopy_panel, "call_sensitive_data_clearance", return_value=canopy_panel.Clearance(False, "no")
        ),
    ):
        out = tools.query(user, [OPP.id], "SELECT COUNT(*) AS n FROM visits")
        bad = None
        try:
            tools.query(user, [OPP.id], "SELECT nope FROM visits")
        except Exception as e:  # noqa: BLE001
            bad = e
    assert out["withheld"] is True and "rows" not in out
    link = urlparse(out["open_on_page"])
    assert link.path == "/labs/data/"
    assert parse_qs(link.query) == {"opps": [str(OPP.id)], "sql": ["SELECT COUNT(*) AS n FROM visits"]}
    # Invalid SQL is still reported as an error, so the agent fixes it before handing it over.
    assert bad is not None and getattr(bad, "code", "") == "INVALID_SCHEMA"


def test_a_person_gets_rows_and_the_link(monkeypatch):
    monkeypatch.setattr(scope, "resolve", lambda caller, ids: [OPP])
    out = service.query(
        scopes.Caller(access_token="t"),
        spaces.generate(scopes.Caller(access_token="t"), [OPP.id]),
        "SELECT 1 AS one FROM visits",
    )
    assert out["open_on_page"].endswith(f"/labs/data/?opps={OPP.id}&sql=SELECT+1+AS+one+FROM+visits")


def test_the_data_page_opens_on_a_linked_query_without_running_it(client, django_user_model):
    user = django_user_model.objects.create_user(username="labsdata-view", password="x")
    client.force_login(user)
    with mock.patch.object(scope, "directory", return_value={OPP.id: OPP}):
        page = client.get(f"/labs/data/?opps={OPP.id}&sql=SELECT+42+AS+answer+FROM+visits")
    assert page.status_code == 200
    assert b"SELECT 42 AS answer FROM visits</textarea>" in page.content
