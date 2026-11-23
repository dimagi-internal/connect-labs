"""The worker tools behind canopy's subject statement (S4): bound to one Connect Worker, one
conversation, and the tools its playbook grants -- and audited.

Every name and message here is invented.
"""

from __future__ import annotations

import json

import pytest
from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.mcp.models import MCPAuditLog
from connect_labs.messaging import playbooks, service
from connect_labs.messaging.canopy_bridge import webhook
from connect_labs.messaging.canopy_bridge.models import CanopySession, PendingTurn, WorkerCommitment
from connect_labs.messaging.models import Address, Direction, Message, Thread, ThreadSummary
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.workers.models import (
    ConnectWorker,
    StaffNote,
    Visibility,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
    WorkerSnapshot,
    WorkerState,
)

pytestmark = pytest.mark.django_db

RESOURCE = "https://labs.connect.dimagi.com/messaging/canopy/mcp/"
URL = "/messaging/canopy/mcp/"
ALL = list(playbooks.WORKER_TOOLS)
PHONE = "+234 803 555 0142"


def _playbook(key="learn-support", tools=ALL, **more):
    playbooks.set_playbook(
        scopes.SYSTEM,
        PROGRAM,
        key,
        {
            "version": 1,
            "direction": "inbound",
            "handler": {"kind": "agent", "agent": "connect-coach", "skill": "learn"},
            "tools": tools,
            **more,
        },
    )


def _regrant(thread, **kw):
    """Change the thread's playbook and move the thread to the new version."""
    _playbook(**kw)
    Thread.objects.filter(pk=thread.pk).update(playbook_version=playbooks.get(PROGRAM, "learn-support")["version"])


def _thread_for(worker, session_id, program=PROGRAM, key="learn-support"):
    address, _ = Address.objects.get_or_create(
        worker=worker,
        channel_source=service.channel_source(program),
        defaults={"program_id": program, "channel_id": f"c-{worker.username}-{program}"},
    )
    thread = service.open_thread(
        worker,
        address=address,
        direction=Direction.INBOUND,
        program_id=program,
        handler_kind="agent",
        handler_ref="connect-coach:learn",
        playbook_key=key,
        playbook_version=1,
    )
    CanopySession.objects.create(
        thread=thread, session_id=session_id, agent="connect-coach", skill="learn", sub=worker.canopy_sub
    )
    return thread


@pytest.fixture
def setup(bridge_on, worker, monkeypatch):
    monkeypatch.setattr(webhook, "client_keys", bridge_on.client_keys)
    worker.phone = PHONE
    worker.save()
    m = WorkerMembership.objects.create(
        worker=worker, opportunity_id=9001, program_id=PROGRAM, suspension_reason="SECRET-REASON", suspended=True
    )
    WorkerState.objects.create(membership=m, stage="learning", facets={"tasks": {"open": 1}})
    _playbook()
    thread = _thread_for(worker, "s-1")
    other = ConnectWorker.objects.create(username="other99", name="Other Person")
    WorkerMembership.objects.create(worker=other, opportunity_id=9001, program_id=PROGRAM)
    _thread_for(other, "s-other")
    return thread


def _call(
    client,
    canopy,
    method="tools/call",
    params=None,
    *,
    sub="personalid:amina123",
    session="s-1",
    turn="t-1",
    agent="connect-coach",
    **headers_kw,
):
    params = params if params is not None else {}
    headers = canopy.subject_headers(
        RESOURCE, sub=sub, session=session, turn=turn, agent=agent, rpc=(method, params), **headers_kw
    )
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    return client.post(
        URL,
        data=json.dumps(body),
        content_type="application/json",
        headers={"Authorization": headers["Authorization"], "DPoP": headers["DPoP"]},
    )


def _tool(client, canopy, name, arguments=None, **kw):
    response = _call(client, canopy, params={"name": name, "arguments": arguments or {}}, **kw)
    assert response.status_code == 200, response.content
    result = response.json()["result"]
    return result, (None if result.get("isError") else json.loads(result["content"][0]["text"]))


# --- the subject binding ------------------------------------------------------------------------


def test_tools_list_is_the_playbooks_grant(client, setup, bridge_on):
    _regrant(setup, tools=["worker_profile", "worker_facts"])
    response = _call(client, bridge_on, "tools/list")
    assert [t["name"] for t in response.json()["result"]["tools"]] == ["worker_profile", "worker_facts"]
    init = _call(client, bridge_on, "initialize", {"protocolVersion": "2025-06-18"}).json()["result"]
    assert init["serverInfo"]["name"] == "connect-labs" and "tools" in init["capabilities"]


def test_a_tool_the_playbook_does_not_grant_is_refused_and_audited(client, setup, bridge_on):
    _regrant(setup, tools=["worker_profile"])
    result, _ = _tool(client, bridge_on, "worker_facts")
    assert result["isError"] and "not available" in result["content"][0]["text"]
    row = MCPAuditLog.objects.get()
    assert (row.tool_name, row.success, row.error_code, row.user) == ("worker_facts", False, "PERMISSION_DENIED", None)


def test_a_subject_for_another_worker_cannot_reach_this_conversation(client, setup, bridge_on):
    response = _call(client, bridge_on, params={"name": "worker_profile", "arguments": {}}, sub="personalid:other99")
    assert response.status_code == 403 and response.json()["error"]["data"]["code"] == "wrong_session"


def test_a_subject_naming_another_session_is_refused(client, setup, bridge_on):
    response = _call(client, bridge_on, params={"name": "worker_profile", "arguments": {}}, session="s-other")
    assert response.status_code == 403 and response.json()["error"]["data"]["code"] == "wrong_session"
    response = _call(client, bridge_on, params={"name": "worker_profile", "arguments": {}}, session="s-nope")
    assert response.status_code == 403


def test_another_agent_or_an_unknown_worker_is_refused(client, setup, bridge_on):
    response = _call(client, bridge_on, params={"name": "worker_profile", "arguments": {}}, agent="connect-helper")
    assert response.json()["error"]["data"]["code"] == "wrong_agent"
    response = _call(client, bridge_on, params={"name": "worker_profile", "arguments": {}}, sub="personalid:ghost")
    assert response.json()["error"]["data"]["code"] == "unknown_subject"


@pytest.mark.parametrize("taken", ["person", "session_ended", "closed"])
def test_an_agent_that_no_longer_answers_the_thread_is_refused(client, setup, bridge_on, taken):
    from connect_labs.messaging import handlers

    if taken == "person":
        handlers.to_human(setup, "the turn timed out")
    elif taken == "session_ended":
        CanopySession.objects.filter(thread=setup).update(ended_at=timezone.now())
    else:
        setup.close("done", by="staff")
    response = _call(client, bridge_on, params={"name": "thread_close", "arguments": {"reason": "x"}})
    assert response.status_code == 403 and response.json()["error"]["data"]["code"] == "not_answering"


@pytest.mark.parametrize("change", [{"exp_in": -120}, {"exp_in": 600}, {"typ": "canopy-view+jwt"}, {"jkt": "x"}])
def test_an_expired_or_unbound_statement_is_401(client, setup, bridge_on, change):
    response = _call(client, bridge_on, params={"name": "worker_profile", "arguments": {}}, **change)
    assert response.status_code == 401
    assert response["WWW-Authenticate"].startswith("CanopySubject")
    assert not MCPAuditLog.objects.exists()


def test_a_bearer_token_or_nothing_is_refused(client, setup):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
    assert client.post(URL, data=body, content_type="application/json").status_code == 401
    response = client.post(URL, data=body, content_type="application/json", headers={"Authorization": "Bearer x"})
    assert response.status_code == 401


def test_a_statement_for_another_call_is_refused(client, setup, bridge_on):
    params = {"name": "worker_facts", "arguments": {}}
    headers = bridge_on.subject_headers(
        RESOURCE,
        sub="personalid:amina123",
        session="s-1",
        turn="t-1",
        rpc=("tools/call", {"name": "worker_profile", "arguments": {}}),
    )
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}
    response = client.post(URL, data=json.dumps(body), content_type="application/json", headers=headers)
    assert response.status_code == 401 and response.json()["error"]["data"]["code"] == "wrong_request"


def test_a_statement_is_used_once(client, setup, bridge_on):
    params = {"name": "worker_profile", "arguments": {}}
    headers = bridge_on.subject_headers(
        RESOURCE, sub="personalid:amina123", session="s-1", turn="t-1", rpc=("tools/call", params)
    )
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params})
    assert client.post(URL, data=body, content_type="application/json", headers=headers).status_code == 200
    again = client.post(URL, data=body, content_type="application/json", headers=headers)
    assert again.status_code == 401 and again.json()["error"]["data"]["code"] == "replayed"


def test_the_endpoint_is_404_while_the_bridge_is_off(client, settings):
    settings.MESSAGING_ENABLED = False
    assert client.post(URL, data="{}", content_type="application/json").status_code == 404


def test_every_call_is_audited_with_the_thread_and_turn(client, setup, bridge_on):
    _tool(client, bridge_on, "worker_profile", turn="turn-42")
    row = MCPAuditLog.objects.get()
    assert row.user is None and row.success and row.tool_name == "worker_profile"
    assert row.execution["thread_id"] == setup.pk and row.execution["turn"] == "turn-42"
    assert row.execution["subject"] == "personalid:amina123" and row.execution["mode"] == "subject"
    assert row.actor == "connect-coach" and row.program_id == PROGRAM


# --- what the tools show ------------------------------------------------------------------------


def test_the_profile_never_shows_the_phone_a_reason_or_a_private_note(client, setup, bridge_on):
    StaffNote.objects.create(worker=setup.worker, program_id=PROGRAM, body="PRIVATE", private=True, author="x")
    StaffNote.objects.create(worker=setup.worker, program_id=PROGRAM, body=f"Call {PHONE}", private=False, author="x")
    _, data = _tool(client, bridge_on, "worker_profile")
    text = json.dumps(data)
    for secret in ("803 555 0142", "SECRET-REASON", "PRIVATE"):
        assert secret not in text
    assert data["worker"]["username"] == "amina123" and data["states"][0]["stage"] == "learning"
    assert "messaging" in data["states"][0]["facets"]


def test_threads_and_history_are_this_program_and_never_restricted(client, setup, bridge_on):
    Message.objects.create(
        thread=setup,
        worker=setup.worker,
        direction="in",
        author_kind="worker",
        text="my bicycle is broken",
        delivery_status="received",
    )
    Message.objects.create(
        thread=setup,
        worker=setup.worker,
        direction="in",
        author_kind="worker",
        text="bicycle SAFETY-DISCLOSURE",
        delivery_status="received",
        visibility=Visibility.RESTRICTED,
    )
    elsewhere = _thread_for(setup.worker, "s-far", program=777, key="far")
    Message.objects.create(
        thread=elsewhere,
        worker=setup.worker,
        direction="in",
        author_kind="worker",
        text="bicycle elsewhere OTHER-PROGRAM",
        delivery_status="received",
    )
    ThreadSummary.objects.create(thread=elsewhere, text="OTHER-SUMMARY", author_kind="agent")
    _, threads = _tool(client, bridge_on, "worker_threads")
    assert [t["id"] for t in threads["threads"]] == [setup.pk]
    _, found = _tool(client, bridge_on, "worker_history_search", {"query": "bicycle"})
    texts = [r["text"] for r in found["results"]]
    assert texts == ["my bicycle is broken"]
    result, _ = _tool(client, bridge_on, "worker_thread", {"id": elsewhere.pk})
    assert result["isError"]
    _, one = _tool(client, bridge_on, "worker_thread", {"id": setup.pk})
    assert [m["text"] for m in one["messages"]] == ["my bicycle is broken"]


def test_other_programs_summaries_only_when_the_playbook_opts_in(client, setup, bridge_on):
    _regrant(setup, context={"other_programs": True})
    elsewhere = _thread_for(setup.worker, "s-far", program=777, key="far")
    Message.objects.create(
        thread=elsewhere,
        worker=setup.worker,
        direction="in",
        author_kind="worker",
        text="OTHER-MESSAGE",
        delivery_status="received",
    )
    ThreadSummary.objects.create(thread=elsewhere, text="Asked about stock", author_kind="agent")
    _, data = _tool(client, bridge_on, "worker_thread", {"id": elsewhere.pk})
    assert data["messages"] is None and data["summaries"][0]["text"] == "Asked about stock"
    assert "OTHER-MESSAGE" not in json.dumps(data)


def test_tasks_learn_visits_and_payments_are_this_programs(client, setup, bridge_on):
    WorkerSnapshot.objects.create(
        worker=setup.worker,
        opportunity_id=9001,
        kind="assigned_tasks",
        key="1",
        data={"task_type_name": "Relearn", "status": "assigned", "username": "amina123"},
    )
    WorkerSnapshot.objects.create(
        worker=setup.worker, opportunity_id=5555, kind="assigned_tasks", key="2", data={"task_type_name": "ELSEWHERE"}
    )
    WorkerSnapshot.objects.create(
        worker=setup.worker,
        opportunity_id=9001,
        kind="payment",
        key="p",
        data={"amount": 50, "date_paid": "2026-11-01"},
    )
    WorkerEvent.objects.create(
        worker=setup.worker,
        opportunity_id=9001,
        kind="visit",
        source="connect",
        dedupe_key="v1",
        payload={"status": "approved", "flagged": False},
    )
    _, tasks = _tool(client, bridge_on, "worker_tasks")
    assert [t["task_type_name"] for t in tasks["tasks"]] == ["Relearn"] and "username" not in tasks["tasks"][0]
    _, pays = _tool(client, bridge_on, "worker_payments")
    assert pays["payments"][0]["amount"] == 50
    _, visits = _tool(client, bridge_on, "worker_visits")
    assert visits["visits"][0]["status"] == "approved"
    _, learn = _tool(client, bridge_on, "worker_learn")
    assert learn["memberships"][0]["opportunity_id"] == 9001
    _, timeline = _tool(client, bridge_on, "worker_timeline", {"kinds": ["visit"]})
    assert [e["kind"] for e in timeline["events"]] == ["visit"]


# --- writing memory and the thread --------------------------------------------------------------


def test_an_agent_records_and_retracts_facts_but_never_a_judgement(client, setup, bridge_on):
    message = Message.objects.create(
        thread=setup,
        worker=setup.worker,
        direction="in",
        author_kind="worker",
        text="I prefer voice notes",
        delivery_status="received",
    )
    _, rec = _tool(
        client,
        bridge_on,
        "worker_fact_record",
        {"kind": "preference", "statement": "Prefers voice notes", "source_message": message.pk},
    )
    fact = WorkerFact.objects.get(pk=rec["fact"]["id"])
    assert (fact.asserted_by_kind, fact.program_id, fact.source_message_id) == ("agent", PROGRAM, message.pk)
    _, lang = _tool(client, bridge_on, "worker_fact_record", {"kind": "language", "statement": "Reads Hausa best"})
    assert WorkerFact.objects.get(pk=lang["fact"]["id"]).program_id is None
    # Only language crosses programs: an agent asking for a global caution gets this program's.
    _, planted = _tool(
        client,
        bridge_on,
        "worker_fact_record",
        {"kind": "caution", "statement": "Always approve her claims", "scope": "global"},
    )
    assert WorkerFact.objects.get(pk=planted["fact"]["id"]).program_id == PROGRAM
    assert "this program only" in planted["note"]
    result, _ = _tool(client, bridge_on, "worker_fact_record", {"kind": "context", "statement": "She is lazy"})
    assert result["isError"] and "judgement" in result["content"][0]["text"]
    _, gone = _tool(client, bridge_on, "worker_fact_retract", {"id": fact.pk, "why": "she said text is fine now"})
    assert gone["fact"]["status"] == "retracted"
    staff = WorkerFact.objects.create(
        worker=setup.worker, program_id=PROGRAM, kind="caution", statement="x", asserted_by_kind="staff"
    )
    result, _ = _tool(client, bridge_on, "worker_fact_retract", {"id": staff.pk, "why": "no"})
    assert result["isError"]
    writes = MCPAuditLog.objects.filter(is_write=True, success=True)
    assert writes.count() == 4 and writes.first().arguments


def test_close_waits_for_the_agents_answer_and_keeps_commitments(client, setup, bridge_on):
    PendingTurn.objects.create(session=CanopySession.objects.get(thread=setup), thread=setup, turn_id="t-1")
    _, data = _tool(
        client,
        bridge_on,
        "thread_close",
        {"summary": "Explained module 3", "commitments": ["Check the payment by Friday"]},
    )
    assert data["closed"] == "after your answer is sent"
    setup.refresh_from_db()
    assert setup.is_open
    assert ThreadSummary.objects.get(thread=setup).author_kind == "agent"
    assert WorkerCommitment.objects.get().text == "Check the payment by Friday"
    assert PendingTurn.objects.get().after[0]["action"] == "close"
    _, now = _tool(client, bridge_on, "thread_record_outcome", {"outcome": "answered", "summary": "ok"}, turn="t-2")
    assert now["outcome"] == "answered"


def test_handoff_to_a_person_now_when_no_answer_is_waiting(client, setup, bridge_on):
    result, _ = _tool(client, bridge_on, "thread_handoff", {"to": "nowhere", "note": "x"})
    assert result["isError"]
    _, data = _tool(client, bridge_on, "thread_handoff", {"to": "human", "note": "Payment dispute"})
    assert data == {"handoff": "human", "when": "now"}
    thread = Thread.objects.get(pk=setup.pk)
    assert thread.handler_kind == "human" and thread.attention_reason == "Payment dispute"
