"""Data spaces (``labsdata/spaces.py``): who may open one is decided on every read, from
what each person holds now -- never stored.

A teammate who holds the same sources sees the space and its queries; someone missing one
frozen opportunity, or holding only part of a program, gets ``not_found`` (the same answer
as no such space); changing the sources locks out whoever does not hold the new ones; and
nothing in a space is rows.

Holdings are faked at ``scopes.holdings`` -- the one place labs learns what a person holds
-- so the real ``spaces._resolve`` / ``scope.resolve`` path runs on top of it.

THIS REPOSITORY IS PUBLIC. Every name and value here is invented.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from connect_labs.labs import canopy_panel
from connect_labs.labs.access import scopes
from connect_labs.labsdata import engine, scope, service, spaces
from connect_labs.labsdata.models import DataSpace, SavedQuery
from connect_labs.labsdata.scope import Opportunity
from connect_labs.mcp.tool_registry import MCPToolError

pytestmark = pytest.mark.django_db

PROGRAM = 9701
O1 = Opportunity(id=9711, name="Opp One", llo="LLO A", program_id=PROGRAM, program_name="Prog Invented")
O2 = Opportunity(id=9712, name="Opp Two", llo="LLO B", program_id=PROGRAM, program_name="Prog Invented")
O3 = Opportunity(id=9713, name="Opp Three", llo="LLO C")
DIRECTORY = {o.id: o for o in (O1, O2, O3)}

#: What each invented person holds: (programs, opportunities).
HOLDS = {
    "alice": ({PROGRAM}, {O1.id, O2.id, O3.id}),  # program manager, and O3 too
    "bob": ({PROGRAM}, {O1.id, O2.id}),  # a teammate: the same program
    "carol": ({PROGRAM}, {O1.id}),  # holds the program but only part of it
    "dave": (set(), {O1.id, O2.id}),  # both opportunities, not the program
}


@pytest.fixture
def people():
    User = get_user_model()
    return {name: User.objects.create_user(username=f"space-{name}", password="x") for name in HOLDS}


@pytest.fixture(autouse=True)
def connect(monkeypatch):
    def holdings(caller):
        user = caller.user or getattr(caller.request, "user", None)
        programs, opps = HOLDS.get((getattr(user, "username", "") or "").removeprefix("space-"), (set(), set()))
        return scopes.Holdings(org_slugs=frozenset(), opportunity_ids=frozenset(opps), program_ids=frozenset(programs))

    monkeypatch.setattr(scopes, "holdings", holdings)
    monkeypatch.setattr(scope, "directory", lambda caller: dict(DIRECTORY))
    monkeypatch.setattr(canopy_panel, "panel", lambda *a, **k: None)
    monkeypatch.setattr(service, "validate_selected", lambda sql, selection: {"valid": True})
    ran = []

    def run_query(opps, sql, max_rows=None, relations=None):
        ran.append(sorted(o.id for o in opps))
        return engine.QueryResult(["n"], [[3]], 1, False, sql, 1)

    monkeypatch.setattr(engine, "run_query", run_query)
    monkeypatch.setattr("connect_labs.mcp.tools.labsdata.require_connect_token", lambda user: "tok")
    return ran


def _client(user):
    c = Client()
    c.force_login(user)
    return c


def _post(client, url, body):
    return client.post(url, json.dumps(body), content_type="application/json")


def _program_space(people) -> int:
    res = _post(_client(people["alice"]), "/labs/data/api/spaces/", {"name": "Prog space", "program_ids": [PROGRAM]})
    assert res.status_code == 200, res.content
    return res.json()["space"]["id"]


def test_a_program_source_is_frozen_to_its_opportunities(people):
    space = DataSpace.objects.get(pk=_program_space(people))
    # Alice holds O3 too, but it is not in the program: only the program's opportunities.
    assert (space.program_ids, space.opportunity_ids, space.workflow) == ([PROGRAM], [O1.id, O2.id], {})


def test_a_teammate_holding_the_same_sources_sees_the_space_and_its_queries(people):
    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    saved = _post(alice, f"/labs/data/api/spaces/{sid}/items/", {"name": "Weekly", "sql": "SELECT 1 AS n FROM visits"})
    assert saved.status_code == 200, saved.content
    listed = bob.get("/labs/data/api/spaces/").json()["groups"]
    assert [s["id"] for g in listed for s in g["spaces"]] == [sid]
    assert listed[0]["label"] == "Program: Prog Invented"
    assert bob.get(f"/labs/data/api/spaces/{sid}/").status_code == 200
    assert bob.get(f"/labs/data/space/{sid}/").status_code == 200
    items = bob.get(f"/labs/data/api/spaces/{sid}/items/").json()["items"]
    assert [(i["name"], i["saved_by"]) for i in items] == [("Weekly", "space-alice")]
    # Bob may add a version of Alice's query: versions count per (space, name).
    again = _post(bob, f"/labs/data/api/spaces/{sid}/items/", {"name": "Weekly", "sql": "SELECT 2 AS n FROM visits"})
    assert again.json()["saved"]["version"] == 2
    latest = bob.get(f"/labs/data/api/spaces/{sid}/items/").json()["items"][0]
    assert [v["version"] for v in latest["versions"]] == [2, 1]
    # A space's query is not anyone's personal query.
    assert alice.get("/labs/data/api/saved/").json() == {"saved": []}


def test_queries_over_a_space_read_its_frozen_opportunities(people, connect):
    sid = _program_space(people)
    res = _post(_client(people["bob"]), "/labs/data/api/query/", {"space_id": sid, "sql": "SELECT 1 AS n FROM visits"})
    assert res.status_code == 200, res.content
    assert connect == [[O1.id, O2.id]]
    assert res.json()["open_on_page"].endswith(f"/labs/data/space/{sid}/?sql=SELECT+1+AS+n+FROM+visits&max_rows=500")
    # A space brings its own selection: naming another one beside it is refused, as the
    # data_* tools refuse it.
    mixed = _post(
        _client(people["bob"]),
        "/labs/data/api/query/",
        {"space_id": sid, "opportunity_ids": [O1.id], "sql": "SELECT 1"},
    )
    assert mixed.status_code == 400 and mixed.json()["error"] == "outside_space"


@pytest.mark.parametrize("who", ["carol", "dave"])
def test_holding_only_part_of_a_space_is_not_found(people, connect, who):
    """Carol holds the program but not every frozen opportunity; Dave holds every
    opportunity but not the program. Both get exactly what a missing space gets."""
    sid = _program_space(people)
    c = _client(people[who])
    missing = _client(people["bob"]).get("/labs/data/api/spaces/999999/").json()
    for res in (
        c.get(f"/labs/data/api/spaces/{sid}/"),
        c.get(f"/labs/data/api/spaces/{sid}/items/"),
        _post(c, "/labs/data/api/query/", {"space_id": sid, "sql": "SELECT 1"}),
        _post(c, "/labs/data/api/describe/", {"space_id": sid}),
        _post(c, "/labs/data/api/visits/", {"space_id": sid, "visit_ids": ["v1"]}),
        _post(c, f"/labs/data/api/spaces/{sid}/items/", {"name": "x", "sql": "SELECT 1"}),
        _post(c, f"/labs/data/api/spaces/{sid}/sources/", {"confirm": True, "opportunity_ids": [O1.id]}),
        _post(c, f"/labs/data/api/spaces/{sid}/archive/", {}),
    ):
        assert res.status_code == 404 and res.json() == missing
    assert c.get(f"/labs/data/space/{sid}/").status_code == 404
    assert c.get("/labs/data/api/spaces/").json() == {"groups": []}
    assert connect == []  # nothing ran
    with pytest.raises(spaces.SpaceNotFound):
        spaces.open(scopes.Caller(user=people[who]), sid)


def test_changing_sources_needs_a_confirm_and_locks_out_who_does_not_hold_them(people):
    from connect_labs.audit_trail.models import AuditEvent

    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    body = {"program_ids": [PROGRAM], "opportunity_ids": [O3.id]}
    unconfirmed = _post(alice, f"/labs/data/api/spaces/{sid}/sources/", body)
    assert unconfirmed.status_code == 400 and unconfirmed.json()["error"] == "confirm_required"
    assert bob.get(f"/labs/data/api/spaces/{sid}/").status_code == 200
    done = _post(alice, f"/labs/data/api/spaces/{sid}/sources/", {**body, "confirm": True})
    assert done.status_code == 200, done.content
    assert DataSpace.objects.get(pk=sid).opportunity_ids == [O1.id, O2.id, O3.id]
    # Bob does not hold O3: the whole space is gone for him, not a partial view.
    assert bob.get(f"/labs/data/api/spaces/{sid}/").status_code == 404
    assert alice.get(f"/labs/data/api/spaces/{sid}/").status_code == 200
    assert AuditEvent.objects.filter(resource_type="labsdata_space", action="update").count() == 3


def test_a_program_that_gains_opportunities_says_so_to_people_who_hold_them(people, monkeypatch):
    sid = _program_space(people)
    o4 = Opportunity(id=9714, name="Opp Four", llo="LLO D", program_id=PROGRAM, program_name="Prog Invented")
    monkeypatch.setattr(scope, "directory", lambda caller: {**DIRECTORY, o4.id: o4})
    monkeypatch.setitem(HOLDS, "alice", ({PROGRAM}, {O1.id, O2.id, O3.id, o4.id}))
    alice = _client(people["alice"]).get(f"/labs/data/api/spaces/{sid}/").json()["space"]
    assert alice["new_in_programs"] == [{"program_id": PROGRAM, "opportunity_ids": [o4.id]}]
    # Bob does not hold it: he is told nothing about it, and still opens the frozen space.
    bob = _client(people["bob"]).get(f"/labs/data/api/spaces/{sid}/").json()["space"]
    assert bob["new_in_programs"] == []


def test_nothing_in_a_space_is_rows(people):
    sid = _program_space(people)
    alice = _client(people["alice"])
    _post(alice, f"/labs/data/api/spaces/{sid}/items/", {"name": "Weekly", "sql": "SELECT 1 AS n FROM visits"})
    _post(alice, "/labs/data/api/query/", {"space_id": sid, "sql": "SELECT 1 AS n FROM visits"})
    space_fields = {f.name for f in DataSpace._meta.get_fields()}
    assert space_fields == {
        "id",
        "name",
        "description",
        "opportunity_ids",
        "program_ids",
        "workflow",
        "created_by",
        "created_at",
        "updated_at",
        "archived_at",
        "queries",
    }
    assert not {"rows", "columns", "result", "row_count"} & {f.name for f in SavedQuery._meta.get_fields()}
    stored = SavedQuery.objects.get(space_id=sid)
    assert (stored.sql, stored.display, stored.opportunity_ids) == ("SELECT 1 AS n FROM visits", {}, [O1.id, O2.id])
    # Running a query over the space stored nothing new.
    assert SavedQuery.objects.count() == 1 and DataSpace.objects.count() == 1


def test_an_archived_space_is_gone_for_everyone(people):
    sid = _program_space(people)
    assert _post(_client(people["bob"]), f"/labs/data/api/spaces/{sid}/archive/", {}).status_code == 200
    assert _client(people["alice"]).get(f"/labs/data/api/spaces/{sid}/").status_code == 404
    assert SavedQuery.objects.filter(space_id=sid).count() == 0  # none were saved; it keeps any it had


def test_the_data_tools_hold_an_agent_to_the_space(people, connect):
    from connect_labs.mcp.tools import labsdata as tools

    sid = _program_space(people)
    out = tools.query(people["bob"], space_id=sid, sql="SELECT 1 AS n FROM visits")
    assert out["space_id"] == sid and connect == [[O1.id, O2.id]]
    # The space's own sources beside space_id are fine; anything else is refused.
    tools.query(people["bob"], space_id=sid, opportunity_ids=[O2.id, O1.id], sql="SELECT 1")
    for other in ({"opportunity_ids": [O1.id]}, {"program_ids": [PROGRAM + 1]}, {"workflow": {"id": 5}}):
        with pytest.raises(MCPToolError) as refused:
            tools.query(people["bob"], space_id=sid, sql="SELECT 1", **other)
        assert refused.value.details["reason"] == "outside_space"
    with pytest.raises(MCPToolError) as hidden:
        tools.query(people["carol"], space_id=sid, sql="SELECT 1")
    assert hidden.value.code == "NOT_FOUND"
    with pytest.raises(MCPToolError):
        tools.data_visits(people["carol"], visit_ids=["v1"], space_id=sid)
    described = tools.describe(people["bob"], space_id=sid)
    assert described["space"]["id"] == sid and "space_id" in described["space"]["rules"]
    assert tools.validate(people["bob"], "SELECT 1", space_id=sid) == {"valid": True}


def test_a_restricted_caller_is_checked_on_the_spaces_opportunities(people):
    from connect_labs.mcp import visit_access

    sid = _program_space(people)
    assert visit_access._data_selection(None, {"space_id": sid}) == [O1.id, O2.id]
    assert visit_access._data_selection(None, {"space_id": 999999}) is None


def test_personal_saved_queries_keep_working_beside_spaces(people, monkeypatch):
    sid = _program_space(people)
    alice = _client(people["alice"])
    mine = _post(alice, "/labs/data/api/saved/", {"name": "Weekly", "sql": "SELECT 1", "opportunity_ids": [O3.id]})
    assert mine.status_code == 200, mine.content
    _post(alice, f"/labs/data/api/spaces/{sid}/items/", {"name": "Weekly", "sql": "SELECT 1"})
    # Same name, both version 1: personal versions count per owner, space ones per space.
    assert sorted((q.space_id or 0, q.version) for q in SavedQuery.objects.all()) == [(0, 1), (sid, 1)]
    assert [q["name"] for q in alice.get("/labs/data/api/saved/").json()["saved"]] == ["Weekly"]
    # A personal query cannot be deleted through a space, nor a space query as personal.
    space_q = SavedQuery.objects.get(space_id=sid)
    assert _post(alice, f"/labs/data/api/saved/{space_q.pk}/delete/", {}).status_code == 404
    personal = SavedQuery.objects.get(space__isnull=True)
    assert _post(alice, f"/labs/data/api/spaces/{sid}/items/{personal.pk}/delete/", {}).status_code == 404


def test_with_no_space_open_the_page_reads_an_unsaved_space_generated_as_the_person(people, connect):
    """No space open: the selection is a space generated for the call -- the program resolved
    to what this person holds of it, nothing written -- and its link back asks again for the
    program, not for that call's freeze."""
    res = _post(
        _client(people["bob"]),
        "/labs/data/api/query/",
        {"program_ids": [PROGRAM], "opportunity_ids": [O1.id], "sql": "SELECT 1 AS n FROM visits"},
    )
    assert res.status_code == 200, res.content
    assert connect == [[O1.id, O2.id]]
    assert "space_id" not in res.json()
    assert res.json()["open_on_page"].endswith(
        f"/labs/data/?programs={PROGRAM}&opps={O1.id}&sql=SELECT+1+AS+n+FROM+visits&max_rows=500"
    )
    assert not DataSpace.objects.exists()
    # Carol holds only part of the program: her generated space is only her part.
    _post(_client(people["carol"]), "/labs/data/api/query/", {"program_ids": [PROGRAM], "sql": "SELECT 1 FROM visits"})
    assert connect[-1] == [O1.id]


def test_saving_keeps_exactly_the_space_the_selection_generated(people):
    alice = scopes.Caller(user=people["alice"])
    generated = spaces.generate(alice, [O3.id], program_ids=[PROGRAM])
    assert not generated.saved and generated.id is None
    kept = spaces.save(alice, generated, "Kept")
    assert (kept.program_ids, kept.opportunity_ids) == ([PROGRAM], [O1.id, O2.id, O3.id])
    assert spaces.open(alice, kept.pk).selection.ids == generated.selection.ids
    with pytest.raises(ValueError):
        spaces.save(alice, spaces.open(alice, kept.pk), "Again")
