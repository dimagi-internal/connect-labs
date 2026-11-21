"""The Connect Worker page, the worker_* MCP tools and the public api: all within the reader's access."""

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import Holdings, ScopesUnavailable
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.users.models import User
from connect_labs.workers import access, api
from connect_labs.workers import events as ev
from connect_labs.workers.models import (
    ConnectWorker,
    FactStatus,
    OpportunitySync,
    StaffNote,
    Stage,
    Visibility,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
    WorkerState,
)

pytestmark = pytest.mark.django_db

USERNAME = "alice_pid"


@pytest.fixture(autouse=True)
def _no_cache(settings):
    settings.CACHES = {"default": {"BACKEND": "django.core.cache.backends.dummy.DummyCache"}}


@pytest.fixture
def world():
    worker = ConnectWorker.objects.create(username=USERNAME, name="Alice", phone="+254700000009")
    now = timezone.now()
    for opp, program in ((1, 9), (2, 8)):
        OpportunitySync.objects.create(opportunity_id=opp, name=f"Opp {opp}", program_id=program, last_synced_at=now)
    mine = WorkerMembership.objects.create(
        worker=worker,
        opportunity_id=1,
        opportunity_name="Opp 1",
        program_id=9,
        suspended=True,
        suspension_reason="flagged for duplicate visits",
    )
    theirs = WorkerMembership.objects.create(worker=worker, opportunity_id=2, opportunity_name="Opp 2", program_id=8)
    WorkerState.objects.create(membership=mine, stage=Stage.SUSPENDED, facets={"tasks": {"open": 1}})
    WorkerState.objects.create(membership=theirs, stage=Stage.DELIVERING)
    ev.record_event(worker, ev.SUSPENDED, source="connect", dedupe_key="k1", opportunity_id=1, program_id=9)
    ev.record_event(worker, ev.PAYMENT, source="connect", dedupe_key="k2", opportunity_id=2, program_id=8)
    ev.record_event(
        worker,
        ev.PROFILE_CHANGED,
        source="connect",
        dedupe_key="k3",
        opportunity_id=1,
        visibility=Visibility.RESTRICTED,
        payload={"field": "phone"},
    )
    WorkerFact.objects.create(worker=worker, kind="language", statement="Prefers Swahili", asserted_by_kind="staff")
    WorkerFact.objects.create(
        worker=worker, program_id=8, kind="context", statement="Other programme fact", asserted_by_kind="staff"
    )
    StaffNote.objects.create(worker=worker, program_id=9, body="Visible note", author="pm")
    StaffNote.objects.create(worker=worker, program_id=9, body="Private note", private=True, author="pm")
    StaffNote.objects.create(worker=worker, program_id=8, body="Other programme note", author="pm")
    viewer = User.objects.create(username="viewer", email="viewer@example.com")
    outsider = User.objects.create(username="outsider", email="outsider@example.com")
    return {"worker": worker, "viewer": viewer, "outsider": outsider}


@pytest.fixture
def connect_says(monkeypatch):
    """viewer holds opportunity 1 by listing; opportunity 2 is nobody's; outsider holds nothing."""

    def fake_holdings(caller):
        held = {1} if caller.user.username == "viewer" else set()
        return Holdings(org_slugs=frozenset(), opportunity_ids=frozenset(held), program_ids=frozenset())

    probed = []

    def fake_probe(token, ids):
        probed.append(list(ids))
        return set()

    monkeypatch.setattr(access, "holdings", fake_holdings)
    monkeypatch.setattr(access, "_probe", fake_probe)
    monkeypatch.setattr(access, "_token", lambda user, request=None: "tok")
    monkeypatch.setattr("connect_labs.workers.mcp_tools.require_connect_token", lambda user: "tok")
    return probed


def test_a_viewer_sees_only_their_opportunities(world, connect_says):
    view = access.view_for(world["viewer"], world["worker"], on_page=True)
    assert view.opportunity_ids == {1} and view.program_ids == {9} and view.restricted
    assert connect_says == [[2]]  # asked Connect about the one opportunity labs did not know they hold


def test_a_supervising_or_funder_grant_from_connect_counts(world, connect_says, monkeypatch):
    monkeypatch.setattr(access, "_probe", lambda token, ids: {2})
    view = access.view_for(world["outsider"], world["worker"])
    assert view.opportunity_ids == {2}


def test_unknowable_access_shows_nothing(world, monkeypatch):
    def unavailable(caller):
        raise ScopesUnavailable("down")

    monkeypatch.setattr(access, "holdings", unavailable)
    monkeypatch.setattr(access, "_token", lambda user, request=None: None)
    view = access.view_for(world["viewer"], world["worker"])
    assert not view.any and view.unknowable


def test_page_shows_the_viewers_slice_with_restricted_items(world, connect_says, client):
    client.force_login(world["viewer"])
    response = client.get(reverse("workers:worker", args=[USERNAME]))
    assert response.status_code == 200
    html = response.content.decode()
    assert "Opp 1" in html and "Opp 2" not in html
    assert "flagged for duplicate visits" in html  # restricted, for staff with access
    assert "Private note" in html and "Visible note" in html and "Other programme note" not in html
    assert "Prefers Swahili" in html and "Other programme fact" not in html
    assert "+254700000009" not in html
    assert "What this profile covers" in html and "does not record" in html
    timeline = response.context["timeline"]
    assert {e["kind"] for e in timeline} == {ev.SUSPENDED, ev.PROFILE_CHANGED}


def test_page_is_a_404_for_a_worker_you_cannot_see(world, connect_says, client):
    client.force_login(world["outsider"])
    assert client.get(reverse("workers:worker", args=[USERNAME])).status_code == 404
    assert client.get(reverse("workers:worker", args=["nobody"])).status_code == 404


def test_page_filters_the_timeline(world, connect_says, client):
    client.force_login(world["viewer"])
    response = client.get(reverse("workers:worker", args=[USERNAME]) + f"?kind={ev.SUSPENDED}")
    assert [e["kind"] for e in response.context["timeline"]] == [ev.SUSPENDED]


def test_staff_add_notes_and_retract_facts(world, connect_says, client):
    client.force_login(world["viewer"])
    url = reverse("workers:worker", args=[USERNAME])
    client.post(url, {"action": "add_note", "body": "Called her", "program_id": "9", "private": "1"})
    note = StaffNote.objects.get(body="Called her")
    assert note.private and note.program_id == 9 and note.author == "viewer"
    # A program the viewer cannot see is refused.
    assert client.post(url, {"action": "add_note", "body": "x", "program_id": "8"}).status_code == 404
    fact = WorkerFact.objects.get(statement="Prefers Swahili")
    client.post(url, {"action": "retract_fact", "fact_id": fact.pk, "reason": "She said English"})
    fact.refresh_from_db()
    assert fact.status == FactStatus.RETRACTED and fact.retracted_by == "viewer"
    other = WorkerFact.objects.get(statement="Other programme fact")
    assert client.post(url, {"action": "retract_fact", "fact_id": other.pk}).status_code == 404


def test_worker_get_never_carries_restricted_or_private_content(world, connect_says):
    out = get_tool("worker_get").handler(user=world["viewer"], username=USERNAME)
    text = str(out)
    assert [m["opportunity_id"] for m in out["memberships"]] == [1]
    assert "flagged for duplicate visits" not in text and "suspension_reason" not in out["memberships"][0]
    assert "Private note" not in text and "+254700000009" not in text
    assert ev.PROFILE_CHANGED not in out["timeline_counts"]
    assert out["url"].endswith(f"/labs/workers/{USERNAME}/")


def test_worker_get_refuses_alike_for_unknown_and_unreadable(world, connect_says):
    tool = get_tool("worker_get")
    with pytest.raises(MCPToolError) as unknown:
        tool.handler(user=world["outsider"], username="nobody")
    with pytest.raises(MCPToolError) as hidden:
        tool.handler(user=world["outsider"], username=USERNAME)
    assert unknown.value.code == hidden.value.code == "NOT_FOUND"
    assert str(unknown.value) == str(hidden.value)
    with pytest.raises(MCPToolError):
        tool.handler(user=world["viewer"], username=USERNAME, program_id=8)


def test_worker_sync_queues_within_access(world, connect_says, monkeypatch):
    queued = []

    class _Result:
        id = "task-1"

    monkeypatch.setattr(
        "connect_labs.workers.tasks.sync_worker_task.delay", lambda *a, **k: queued.append(("w", a, k)) or _Result()
    )
    monkeypatch.setattr(
        "connect_labs.workers.tasks.sync_opportunity_task.delay", lambda *a, **k: queued.append(("o", a)) or _Result()
    )
    tool = get_tool("worker_sync")
    assert tool.is_write
    assert tool.handler(user=world["viewer"], username=USERNAME)["task_id"] == "task-1"
    assert tool.handler(user=world["viewer"], opportunity_id=1)["queued"]
    with pytest.raises(MCPToolError):
        tool.handler(user=world["viewer"], opportunity_id=2)
    with pytest.raises(MCPToolError):
        tool.handler(user=world["outsider"], username=USERNAME)
    with pytest.raises(MCPToolError):
        tool.handler(user=world["viewer"])
    assert [q[0] for q in queued] == ["w", "o"]


def test_public_api_for_messaging(world, monkeypatch):
    worker = world["worker"]
    assert api.stage_for(worker, 1) == Stage.SUSPENDED
    assert api.facets_for(worker, 1) == {"tasks": {"open": 1}}
    assert [s["opportunity_id"] for s in api.states_for(worker, program_id=9)] == [1]
    brief = api.worker_brief_data(worker, 9)
    text = str(brief)
    assert [m["opportunity_id"] for m in brief["memberships"]] == [1]
    assert "Prefers Swahili" in text and "Other programme" not in text
    assert "duplicate visits" not in text and "Private note" not in text and "+2547" not in text
    assert {e["kind"] for e in brief["recent_timeline"]} == {ev.SUSPENDED}
    assert api.worker_brief_data(worker, None)["memberships"] == []
    assert [e["kind"] for e in api.timeline(worker, program_id=8)] == [ev.PAYMENT]

    event, created = api.record_event(
        worker, ev.MESSAGE_OUT, source="labs", dedupe_key="labs:-:message_out:1", ref={"thread": 1}
    )
    assert created and not api.record_event(worker, ev.MESSAGE_OUT, source="labs", dedupe_key=event.dedupe_key)[1]

    queued = []
    monkeypatch.setattr("connect_labs.workers.tasks.sync_worker_task.delay", lambda *a, **k: queued.append(a))
    assert api.get_or_sync_worker(USERNAME) == worker and queued == []
    new = api.get_or_sync_worker("new_pid")
    assert new.username == "new_pid" and queued == [("new_pid",)]
    assert api.get_or_sync_worker("ghost", sync=False) is None
    assert WorkerEvent.objects.filter(worker=worker).count() == 4
