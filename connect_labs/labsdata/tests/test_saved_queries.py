"""Saved queries on the Data page (``labsdata/models.py``): the query, never its rows; saved
only by the person, in their own session, over opportunities they hold, with SQL that
passes the checks; seen and deleted only by its owner; saved again as a new version.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from connect_labs.labs import canopy_panel
from connect_labs.labsdata import scope, service
from connect_labs.labsdata.engine import QueryError
from connect_labs.labsdata.models import SavedQuery
from connect_labs.labsdata.scope import Opportunity, ScopeError

pytestmark = pytest.mark.django_db

OPP = Opportunity(id=975, name="Opp Saved", llo="LLO Saved")
URL = "/labs/data/api/saved/"
BODY = {
    "name": "Visits by place",
    "sql": "SELECT llo, COUNT(*) AS n FROM visits GROUP BY llo",
    "opportunity_ids": [OPP.id],
    "max_rows": 200,
    "display": {"columns": {"n": {"label": "Visits", "format": "number_0"}}, "chart": {"type": "bar", "x": "llo"}},
}


@pytest.fixture
def owner():
    return get_user_model().objects.create_user(username="saved-owner", password="x")


@pytest.fixture
def other():
    return get_user_model().objects.create_user(username="saved-other", password="x")


@pytest.fixture
def connect(monkeypatch, owner):
    seen = {}

    def select(caller, opportunity_ids=None, *, program_ids=None, workflow=None):
        if caller.user is None or caller.user.pk != owner.pk:
            raise ScopeError("not_held", f"you do not have access to opportunity {OPP.id}")
        seen["selection"] = {"opportunity_ids": opportunity_ids, "program_ids": program_ids, "workflow": workflow}
        return service.Selection(opportunities=[OPP], workflow={"id": 5, "name": "W"} if workflow else None)

    def validate_selected(sql, selection):
        if "nope" in sql:
            raise QueryError("sql", "column nope does not exist")
        return {"valid": True}

    monkeypatch.setattr(service, "select", select)
    monkeypatch.setattr(service, "validate_selected", validate_selected)
    return seen


def _client(user, csrf=False):
    c = Client(enforce_csrf_checks=csrf)
    c.force_login(user)
    return c


def _save(client, **changes):
    return client.post(URL, json.dumps({**BODY, **changes}), content_type="application/json")


def test_a_save_keeps_the_query_and_never_rows(owner, connect):
    res = _save(_client(owner))
    assert res.status_code == 200, res.content
    saved = SavedQuery.objects.get()
    assert (saved.owner, saved.name, saved.sql, saved.opportunity_ids) == (owner, BODY["name"], BODY["sql"], [OPP.id])
    assert saved.program_ids == [] and saved.workflow == {}
    assert saved.max_rows == 200 and saved.version == 1
    assert saved.display["chart"] == {"type": "bar", "x": "llo"}
    assert not {"rows", "columns", "row_count"} & set(res.json()["saved"])


def test_saving_the_same_name_adds_a_version_and_the_list_shows_the_latest(owner, connect):
    client = _client(owner)
    _save(client)
    _save(client, sql="SELECT 2 AS n FROM visits")
    first, second = SavedQuery.objects.order_by("version")
    assert second.version == 2 and second.parent == first
    listed = client.get(URL).json()["saved"]
    assert [(q["name"], q["version"], q["sql"]) for q in listed] == [(BODY["name"], 2, "SELECT 2 AS n FROM visits")]


def test_only_the_owner_sees_or_deletes_a_saved_query(owner, other, connect):
    _save(_client(owner))
    saved = SavedQuery.objects.get()
    assert _client(other).get(URL).json() == {"saved": []}
    refused = _client(other).post(f"{URL}{saved.pk}/delete/", "{}", content_type="application/json")
    assert refused.status_code == 404 and SavedQuery.objects.exists()
    done = _client(owner).post(f"{URL}{saved.pk}/delete/", "{}", content_type="application/json")
    assert done.status_code == 200 and not SavedQuery.objects.exists()


def test_a_save_keeps_a_workflow_selection_so_its_pipelines_run_again(owner, connect):
    res = _save(_client(owner), opportunity_ids=[], workflow={"id": 5, "opportunity_id": OPP.id, "extra": "x"})
    assert res.status_code == 200, res.content
    saved = SavedQuery.objects.get()
    assert saved.workflow == {"id": 5, "opportunity_id": OPP.id}
    assert res.json()["saved"]["selection"]["workflow"] == {"id": 5, "opportunity_id": OPP.id}
    # Resolved as the person, as a run would be.
    assert connect["selection"]["workflow"]["id"] == 5


def test_a_save_over_opportunities_the_person_does_not_hold_is_refused(other, connect):
    res = _save(_client(other))
    assert res.status_code == 403 and res.json()["error"] == "not_held"
    assert not SavedQuery.objects.exists()


@pytest.mark.parametrize(
    "changes, code",
    [
        ({"name": "  \x00 "}, "name_required"),
        ({"sql": "   "}, "sql_required"),
        ({"sql": "SELECT nope FROM visits"}, "sql"),
        ({"display": {"columns": {"n": {"format": "number_9"}}}}, "bad_display"),
    ],
)
def test_a_save_that_breaks_a_rule_is_refused(owner, connect, changes, code):
    res = _save(_client(owner), **changes)
    assert res.status_code == 400 and res.json()["error"] == code
    assert not SavedQuery.objects.exists()


def test_a_save_needs_the_persons_session_and_csrf(owner, connect):
    anonymous = Client().post(URL, json.dumps(BODY), content_type="application/json")
    assert anonymous.status_code == 302 and "/login" in anonymous["Location"]
    # A request forged from another site carries the session cookie but not the token.
    forged = _save(_client(owner, csrf=True))
    assert forged.status_code == 403
    assert not SavedQuery.objects.exists()


def test_a_save_is_audit_logged(owner, connect):
    from connect_labs.audit_trail.models import AuditEvent

    _save(_client(owner))
    event = AuditEvent.objects.get(resource_type="labsdata_saved_query")
    assert (event.action, event.opportunity_id, event.user_id) == ("create", OPP.id, owner.pk)
    assert BODY["sql"] not in json.dumps(event.metadata)


def test_a_card_link_highlights_save_with_its_display(client, owner, monkeypatch):
    monkeypatch.setattr(scope, "directory", lambda caller: {OPP.id: OPP})
    monkeypatch.setattr(canopy_panel, "panel", lambda *a, **k: None)
    client.force_login(owner)
    page = client.get(
        "/labs/data/",
        {"opps": OPP.id, "sql": "SELECT 1", "save": "1", "display": json.dumps(BODY["display"])},
    )
    assert page.context["open_intent"]["save"] is True
    assert page.context["open_intent"]["display"]["columns"]["n"]["label"] == "Visits"
    bad = client.get("/labs/data/", {"opps": OPP.id, "sql": "SELECT 1", "save": "1", "display": "{not json"})
    assert bad.context["open_intent"]["display"] is None
    # Nothing is saved by opening the page.
    assert not SavedQuery.objects.exists()
