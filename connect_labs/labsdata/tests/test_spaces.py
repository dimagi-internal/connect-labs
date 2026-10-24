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
        "audience",
        "managers",
        "queries",
        # Pointers to canopy conversations: never a transcript (test_no_transcript_is_ever_stored).
        "conversations",
    }
    assert not {"rows", "columns", "result", "row_count"} & {f.name for f in SavedQuery._meta.get_fields()}
    stored = SavedQuery.objects.get(space_id=sid)
    assert (stored.sql, stored.display, stored.opportunity_ids) == ("SELECT 1 AS n FROM visits", {}, [O1.id, O2.id])
    # Running a query over the space stored nothing new.
    assert SavedQuery.objects.count() == 1 and DataSpace.objects.count() == 1


def test_an_archived_space_is_gone_for_everyone(people):
    sid = _program_space(people)
    assert _post(_client(people["alice"]), f"/labs/data/api/spaces/{sid}/archive/", {}).status_code == 200
    assert _client(people["alice"]).get(f"/labs/data/api/spaces/{sid}/").status_code == 404
    assert _client(people["bob"]).get(f"/labs/data/api/spaces/{sid}/").status_code == 404
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


# --- who can open a space, and who can change it (the ACL) -------------------------------
# The data rule (every source held now) is fixed; the audience narrows it; managers change
# sources, audience and managers; members run, save and delete their own items.


def _space_of(client, sid):
    return client.get(f"/labs/data/api/spaces/{sid}/").json()["space"]


def test_a_new_space_is_open_to_every_holder_and_its_creator_manages_it(people):
    sid = _program_space(people)
    space = DataSpace.objects.get(pk=sid)
    assert space.audience == DataSpace.HOLDERS
    assert list(space.managers.values_list("username", flat=True)) == ["space-alice"]
    mine, bobs = _space_of(_client(people["alice"]), sid), _space_of(_client(people["bob"]), sid)
    assert (mine["audience"], mine["is_manager"], [m["username"] for m in mine["managers"]]) == (
        "holders",
        True,
        ["space-alice"],
    )
    # Bob sees the audience and who manages it, read-only.
    assert (bobs["is_manager"], [m["username"] for m in bobs["managers"]]) == (False, ["space-alice"])


def test_existing_spaces_are_backfilled_holders_with_their_creator_managing():
    """Migration 0004 (run by the test database): every existing space ends up as a new
    one starts -- audience holders, its creator its manager."""
    import importlib

    from django.apps import apps as django_apps

    migration = importlib.import_module("connect_labs.labsdata.migrations.0004_data_space_audience_managers")
    User = get_user_model()
    maker = User.objects.create_user(username="space-maker", password="x")
    old = DataSpace.objects.create(name="Old", opportunity_ids=[O1.id], created_by=maker)
    orphan = DataSpace.objects.create(name="Orphan", opportunity_ids=[O1.id])
    migration.creators_manage(django_apps, None)
    assert old.audience == "holders" and list(old.managers.all()) == [maker]
    assert not orphan.managers.exists()


def test_a_private_space_is_not_found_for_non_managers_who_hold_every_source(people, connect):
    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    done = _post(alice, f"/labs/data/api/spaces/{sid}/audience/", {"audience": "private"})
    assert done.status_code == 200 and done.json()["space"]["audience"] == "private"
    missing = bob.get("/labs/data/api/spaces/999999/").json()
    # Bob holds every source, but the audience narrows past him: exactly a missing space.
    for res in (
        bob.get(f"/labs/data/api/spaces/{sid}/"),
        bob.get(f"/labs/data/api/spaces/{sid}/items/"),
        _post(bob, "/labs/data/api/query/", {"space_id": sid, "sql": "SELECT 1"}),
        _post(bob, f"/labs/data/api/spaces/{sid}/items/", {"name": "x", "sql": "SELECT 1"}),
    ):
        assert res.status_code == 404 and res.json() == missing
    assert bob.get(f"/labs/data/space/{sid}/").status_code == 404
    assert bob.get("/labs/data/api/spaces/").json() == {"groups": []}
    assert connect == []
    with pytest.raises(MCPToolError) as hidden:
        from connect_labs.mcp.tools import labsdata as tools

        tools.query(people["bob"], space_id=sid, sql="SELECT 1")
    assert hidden.value.code == "NOT_FOUND"
    # Alice, its manager, still opens it, and lists it; back to holders, Bob is back.
    assert alice.get(f"/labs/data/api/spaces/{sid}/").status_code == 200
    assert [s["id"] for g in alice.get("/labs/data/api/spaces/").json()["groups"] for s in g["spaces"]] == [sid]
    _post(alice, f"/labs/data/api/spaces/{sid}/audience/", {"audience": "holders"})
    assert bob.get(f"/labs/data/api/spaces/{sid}/").status_code == 200


def test_an_unknown_audience_is_refused_and_admits_nobody(people, monkeypatch):
    sid = _program_space(people)
    alice = _client(people["alice"])
    bad = _post(alice, f"/labs/data/api/spaces/{sid}/audience/", {"audience": "everyone"})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_audience"
    # A stored audience the registry does not know fails closed, even for a manager.
    monkeypatch.delitem(spaces.AUDIENCES, DataSpace.HOLDERS)
    assert alice.get(f"/labs/data/api/spaces/{sid}/").status_code == 404


def test_the_audience_cannot_widen_the_data_rule(people, monkeypatch):
    """A manager who lacks a source still cannot open the space -- holders or private --
    and an audience that admits everyone admits nobody who does not hold the sources."""
    sid = _program_space(people)
    space = DataSpace.objects.get(pk=sid)
    space.managers.add(people["carol"])  # carol holds only part of the program
    monkeypatch.setitem(spaces.AUDIENCES, "everyone", lambda space, user: True)
    for audience in ("holders", "private", "everyone"):
        DataSpace.objects.filter(pk=sid).update(audience=audience)
        for who in ("carol", "dave"):
            assert _client(people[who]).get(f"/labs/data/api/spaces/{sid}/").status_code == 404, (audience, who)
            with pytest.raises(spaces.SpaceNotFound):
                spaces.open(scopes.Caller(user=people[who]), sid)
            assert spaces.openable(scopes.Caller(user=people[who])) == []


def test_the_audience_is_asked_only_after_the_data_rule(people, monkeypatch):
    sid = _program_space(people)
    asked = []
    monkeypatch.setitem(spaces.AUDIENCES, "holders", lambda space, user: asked.append(user.username) or True)
    with pytest.raises(spaces.SpaceNotFound):
        spaces.open(scopes.Caller(user=people["carol"]), sid)
    spaces.openable(scopes.Caller(user=people["dave"]))
    assert asked == []
    spaces.open(scopes.Caller(user=people["bob"]), sid)
    assert asked == ["space-bob"]


def test_a_member_saves_and_deletes_their_own_items_but_manages_nothing(people):
    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    _post(alice, f"/labs/data/api/spaces/{sid}/items/", {"name": "Alices", "sql": "SELECT 1"})
    mine = _post(bob, f"/labs/data/api/spaces/{sid}/items/", {"name": "Bobs", "sql": "SELECT 2"})
    assert mine.status_code == 200
    # A new version of someone else's item is a save, which members may make.
    assert _post(bob, f"/labs/data/api/spaces/{sid}/items/", {"name": "Alices", "sql": "SELECT 3"}).status_code == 200
    items = {i["name"]: i for i in bob.get(f"/labs/data/api/spaces/{sid}/items/").json()["items"]}
    assert (items["Bobs"]["can_delete"], items["Alices"]["can_delete"]) == (True, False)
    alices = SavedQuery.objects.filter(space_id=sid, name="Alices").first()
    refused = _post(bob, f"/labs/data/api/spaces/{sid}/items/{alices.pk}/delete/", {})
    assert refused.status_code == 403 and refused.json()["error"] == "not_manager"
    assert SavedQuery.objects.filter(space_id=sid, name="Alices").count() == 2
    ok = _post(bob, f"/labs/data/api/spaces/{sid}/items/{mine.json()['saved']['id']}/delete/", {})
    assert ok.status_code == 200 and not SavedQuery.objects.filter(space_id=sid, name="Bobs").exists()
    # Nor sources, audience, managers or archive.
    for url, body in (
        (f"/labs/data/api/spaces/{sid}/sources/", {"confirm": True, "program_ids": [PROGRAM]}),
        (f"/labs/data/api/spaces/{sid}/audience/", {"audience": "private"}),
        (f"/labs/data/api/spaces/{sid}/managers/", {"person": "space-bob"}),
        (f"/labs/data/api/spaces/{sid}/managers/{people['alice'].pk}/remove/", {}),
        (f"/labs/data/api/spaces/{sid}/archive/", {}),
    ):
        res = _post(bob, url, body)
        assert res.status_code == 403 and res.json()["error"] == "not_manager", url
    space = DataSpace.objects.get(pk=sid)
    assert (space.audience, space.archived_at, list(space.managers.all())) == ("holders", None, [people["alice"]])
    # A manager deletes anyone's item.
    assert _post(alice, f"/labs/data/api/spaces/{sid}/items/{alices.pk}/delete/", {}).status_code == 200


def test_managers_add_people_who_hold_every_source_and_never_the_last_one_leaves(people):
    from connect_labs.audit_trail.models import AuditEvent

    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    # Carol does not hold every source; nobody is called "nobody"; alice is already herself.
    # One answer whatever the reason.
    for person in ("space-carol", "nobody", "space-alice", ""):
        res = _post(alice, f"/labs/data/api/spaces/{sid}/managers/", {"person": person})
        assert res.status_code == 400 and res.json()["error"] == "not_eligible", person
    last = _post(alice, f"/labs/data/api/spaces/{sid}/managers/{people['alice'].pk}/remove/", {})
    assert last.status_code == 409 and last.json()["error"] == "last_manager"
    people["bob"].email = "bob@invented.example"
    people["bob"].save()
    added = _post(alice, f"/labs/data/api/spaces/{sid}/managers/", {"person": "BOB@invented.example"})
    assert added.status_code == 200, added.content
    assert [m["username"] for m in added.json()["space"]["managers"]] == ["space-alice", "space-bob"]
    assert _space_of(bob, sid)["is_manager"] is True
    # Bob, now a manager, removes Alice; then he is the last.
    assert _post(bob, f"/labs/data/api/spaces/{sid}/managers/{people['alice'].pk}/remove/", {}).status_code == 200
    assert _space_of(alice, sid)["is_manager"] is False
    assert _post(bob, f"/labs/data/api/spaces/{sid}/managers/{people['bob'].pk}/remove/", {}).status_code == 409
    changes = [
        e.metadata
        for e in AuditEvent.objects.filter(resource_type="labsdata_space", action="update").order_by("pk")
        if e.metadata.get("field") == "managers"
    ]
    # One event per opportunity the space reads (two), each with before and after.
    assert [(c["before"], c["after"]) for c in changes[::2]] == [
        (["space-alice"], ["space-alice", "space-bob"]),
        (["space-alice", "space-bob"], ["space-bob"]),
    ]


def test_audience_and_source_changes_are_audited_before_and_after(people):
    from connect_labs.audit_trail.models import AuditEvent

    sid = _program_space(people)
    alice = _client(people["alice"])
    _post(alice, f"/labs/data/api/spaces/{sid}/audience/", {"audience": "private"})
    _post(alice, f"/labs/data/api/spaces/{sid}/sources/", {"confirm": True, "opportunity_ids": [O3.id]})
    meta = [e.metadata for e in AuditEvent.objects.filter(resource_type="labsdata_space", action="update")]
    audience = next(m for m in meta if m.get("field") == "audience")
    sources = next(m for m in meta if m.get("field") == "sources")
    assert (audience["before"], audience["after"]) == ("holders", "private")
    assert sources["before"]["opportunity_ids"] == [O1.id, O2.id] and sources["after"]["opportunity_ids"] == [O3.id]


def test_a_manager_who_loses_a_source_cannot_open_or_manage_the_space(people, monkeypatch):
    sid = _program_space(people)
    monkeypatch.setitem(HOLDS, "alice", ({PROGRAM}, {O1.id, O3.id}))  # lost O2
    alice = _client(people["alice"])
    for url, body in (
        (f"/labs/data/api/spaces/{sid}/audience/", {"audience": "private"}),
        (f"/labs/data/api/spaces/{sid}/managers/", {"person": "space-bob"}),
        (f"/labs/data/api/spaces/{sid}/sources/", {"confirm": True, "opportunity_ids": [O3.id]}),
        (f"/labs/data/api/spaces/{sid}/archive/", {}),
    ):
        assert _post(alice, url, body).status_code == 404, url
    # The space keeps working for its members.
    assert _client(people["bob"]).get(f"/labs/data/api/spaces/{sid}/").status_code == 200
    assert DataSpace.objects.get(pk=sid).audience == "holders"


def test_a_labs_admin_makes_someone_who_can_open_it_a_manager(people, monkeypatch):
    from connect_labs.labsdata import views
    from connect_labs.utils import dimagi_user

    sid = _program_space(people)
    staff = get_user_model().objects.create_user(username="space-staff", password="x")
    monkeypatch.setattr(dimagi_user, "is_dimagi_staff", lambda user: user.username == "space-staff")
    url = f"/labs/data/api/spaces/{sid}/admin/managers/"
    # Not staff: refused, even a manager.
    refused = _post(_client(people["alice"]), url, {"person": "space-bob"})
    assert refused.status_code == 403 and refused.json()["error"] == "not_admin"
    admin = _client(staff)
    # Someone who cannot open it, an unknown name, or the admin themselves: one answer.
    for person in ("space-carol", "nobody", "space-staff"):
        res = _post(admin, url, {"person": person})
        assert res.status_code == 400 and res.json()["error"] == "not_eligible", person
    # The admin holds nothing of the space and still assigns Bob, who can open it.
    ok = _post(admin, url, {"person": "space-bob"})
    assert ok.status_code == 200 and ok.json()["managers"] == ["space-alice", "space-bob"]
    assert _space_of(_client(people["bob"]), sid)["is_manager"] is True
    # A private space with no manager who can open it stays closed: Dave (not a holder of
    # the program) can't be made one, and nor can Bob once he is not a manager of it.
    DataSpace.objects.get(pk=sid).managers.remove(people["bob"])
    DataSpace.objects.filter(pk=sid).update(audience="private")
    assert _post(admin, url, {"person": "space-bob"}).json()["error"] == "not_eligible"
    assert views._is_labs_admin(staff) is True


# --- saved conversations: pointers to canopy chat sessions (spaces.save_conversation) -----

SESSION = "0f8fad5b-d9cb-469f-a165-70867728950e"
OTHER_SESSION = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def _conversations(sid) -> str:
    return f"/labs/data/api/spaces/{sid}/conversations/"


def _save_conversation(client, sid, session=SESSION, **extra):
    body = {"session_id": session, "resource": f"labs-data-space://{sid}", **extra}
    return _post(client, _conversations(sid), body)


def test_a_saved_conversation_is_a_pointer_its_space_s_people_see(people):
    """Any member saves one; everyone who can open the space sees it; the label is the
    saver's own words, or "Conversation, <date>" -- never anything from canopy."""
    from connect_labs.labsdata.models import SpaceConversation

    sid = _program_space(people)
    bob = _client(people["bob"])
    saved = _save_conversation(bob, sid, label="  Why O2 dipped  ")
    assert saved.status_code == 200, saved.content
    assert saved.json()["conversation"]["label"] == "Why O2 dipped"
    plain = _save_conversation(bob, sid, session=OTHER_SESSION)
    assert plain.json()["conversation"]["label"].startswith("Conversation, ")

    listed = _client(people["alice"]).get(_conversations(sid)).json()["conversations"]
    assert {(c["session_id"], c["saved_by"]) for c in listed} == {
        (SESSION, "space-bob"),
        (OTHER_SESSION, "space-bob"),
    }
    assert SpaceConversation.objects.filter(space_id=sid).count() == 2
    again = _save_conversation(bob, sid)
    assert again.status_code == 409 and again.json()["error"] == "already_saved"


def test_no_transcript_is_ever_stored(people):
    """A pointer holds the session id, a label, who and when -- nothing the conversation
    said. A body carrying a title or messages is not read."""
    from connect_labs.labsdata.models import SpaceConversation

    fields = {f.name for f in SpaceConversation._meta.get_fields()}
    assert fields == {"id", "space", "session_id", "label", "saved_by", "created_at"}
    sid = _program_space(people)
    res = _save_conversation(
        _client(people["bob"]),
        sid,
        title="Patient Ada's visits",
        transcript=[{"role": "user", "text": "secret"}],
        messages=["secret"],
    )
    assert res.status_code == 200, res.content
    row = SpaceConversation.objects.get(space_id=sid)
    assert "secret" not in json.dumps(res.json()) and "Ada" not in row.label


def test_only_a_conversation_started_on_this_space_s_page_is_saved_here(people):
    sid = _program_space(people)
    other = _post(_client(people["alice"]), "/labs/data/api/spaces/", {"name": "Other", "opportunity_ids": [O3.id]})
    bob = _client(people["bob"])
    for resource in (f"labs-data-space://{other.json()['space']['id']}", "labs-data://opportunities/9711", "", None):
        res = _post(bob, _conversations(sid), {"session_id": SESSION, "resource": resource})
        assert res.status_code == 400 and res.json()["error"] == "not_this_space", resource
    bad = _post(bob, _conversations(sid), {"session_id": "../me", "resource": f"labs-data-space://{sid}"})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_conversation"


@pytest.mark.parametrize("who", ["carol", "dave"])
def test_a_saved_conversation_inherits_the_space_s_rules(people, who):
    """Someone who may not open the space can neither list nor save its conversations:
    the same not_found as no such space."""
    sid = _program_space(people)
    _save_conversation(_client(people["bob"]), sid)
    c = _client(people[who])
    assert c.get(_conversations(sid)).status_code == 404
    assert _save_conversation(c, sid, session=OTHER_SESSION).status_code == 404


def test_a_private_space_s_conversations_are_its_managers_alone(people):
    sid = _program_space(people)
    _save_conversation(_client(people["alice"]), sid)
    assert (
        _post(_client(people["alice"]), f"/labs/data/api/spaces/{sid}/audience/", {"audience": "private"}).status_code
        == 200
    )
    assert _client(people["bob"]).get(_conversations(sid)).status_code == 404


def test_a_member_deletes_their_own_pointer_and_a_manager_any(people):
    sid = _program_space(people)
    alice, bob = _client(people["alice"]), _client(people["bob"])
    mine = _save_conversation(bob, sid).json()["conversation"]["id"]
    hers = _save_conversation(alice, sid, session=OTHER_SESSION).json()["conversation"]["id"]
    flags = {c["id"]: c["can_delete"] for c in bob.get(_conversations(sid)).json()["conversations"]}
    assert flags == {mine: True, hers: False}

    refused = _post(bob, f"{_conversations(sid)}{hers}/delete/", {})
    assert refused.status_code == 403 and refused.json()["error"] == "not_manager"
    assert _post(bob, f"{_conversations(sid)}{mine}/delete/", {}).status_code == 200
    # Alice manages the space: she may delete anyone's (here, her own; bob's is gone).
    again = _save_conversation(bob, sid).json()["conversation"]["id"]
    assert _post(alice, f"{_conversations(sid)}{again}/delete/", {}).status_code == 200
    assert [c["id"] for c in alice.get(_conversations(sid)).json()["conversations"]] == [hers]


def test_saving_and_deleting_a_conversation_is_audited_without_its_words(people, monkeypatch):
    recorded = []
    monkeypatch.setattr("connect_labs.audit_trail.service.record", lambda action, **kw: recorded.append((action, kw)))
    sid = _program_space(people)
    recorded.clear()
    pk = _save_conversation(_client(people["bob"]), sid, label="mine").json()["conversation"]["id"]
    _post(_client(people["bob"]), f"{_conversations(sid)}{pk}/delete/", {})
    mine = [(a, kw) for a, kw in recorded if kw.get("resource_type") == "labsdata_space_conversation"]
    # One event per opportunity the space reads, for the save and for the delete.
    assert sorted(kw["opportunity_id"] for _, kw in mine) == sorted([O1.id, O2.id] * 2)
    assert all(kw["metadata"] == {"space_id": sid, "session_id": SESSION} for _, kw in mine)
