"""The workflow action `start_playbook` and the messaging_* MCP tools, as the person calling.

Every name and message here is invented.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from connect_labs.labs.access import scopes
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.messaging import playbooks, service
from connect_labs.messaging.models import Message, Thread
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.workers.models import WorkerMembership, WorkerState
from connect_labs.workflow import actions
from connect_labs.workflow.models import WorkflowActionExecution

pytestmark = pytest.mark.django_db

OPP = 9001
KEY = f"{OPP}::amina123"
PLAYBOOK = {
    "version": 1,
    "direction": "outbound",
    "trigger": {"type": "person"},
    "handler": {"kind": "human"},
    "opening": "Hello {first_name}, your flags this week need a look.",
    "approval": {"mode": "per_batch"},
}


@pytest.fixture
def people(django_user_model, monkeypatch):
    staff = django_user_model.objects.create_user(username="nurse.joy", password="x")
    outsider = django_user_model.objects.create_user(username="outsider", password="x")

    def holdings(caller):
        name = scopes._user_of(caller).username
        return scopes.Holdings(frozenset(), frozenset(), frozenset({PROGRAM} if name == "nurse.joy" else {1}))

    monkeypatch.setattr(scopes, "holdings", holdings)
    monkeypatch.setattr("connect_labs.messaging.mcp_tools.require_connect_token", lambda user: "token")
    return {"staff": staff, "outsider": outsider}


@pytest.fixture
def world(fake_connectid, messaging_on, worker, run_now):
    fake_connectid.add_user(worker.username)
    membership = WorkerMembership.objects.create(worker=worker, opportunity_id=OPP, program_id=PROGRAM)
    WorkerState.objects.create(membership=membership, stage="delivering")
    playbooks.set_playbook(scopes.SYSTEM, PROGRAM, "flags", PLAYBOOK)
    return worker


def tool(name, user, **kwargs):
    return get_tool(name).handler(user=user, **kwargs)


# --- the workflow action ------------------------------------------------------------------------

ACTION = {"key": "message_worker", "type": "start_playbook", "defaults": {"playbook": "flags"}}


def _definition():
    return SimpleNamespace(
        id=7, data={"config": {"actions": [ACTION]}}, template_type=None, opportunity_ids=[OPP], name="Report"
    )


RUN = SimpleNamespace(id=70, opportunity_id=OPP, program_id=PROGRAM)


def _wda():
    wda = MagicMock()
    wda.get_workers.side_effect = lambda oid: [{"username": "amina123", "name": "Amina Bello"}]
    return wda


def test_the_action_previews_each_opening_and_starts_a_thread_on_confirm(
    people, world, fake_connectid, django_capture_on_commit_callbacks
):
    staff = people["staff"]
    out = actions.preview(
        staff,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="message_worker",
        arguments={"workers": [{"key": KEY}]},
    )
    assert out["needs"] == [] and out["confirm"]
    (row,) = out["workers"]
    assert row["opening"] == "Hello Amina, your flags this week need a look."
    assert out["arguments"]["program_id"] == PROGRAM
    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay"):
        with django_capture_on_commit_callbacks(execute=True):
            execution = actions.commit(
                staff,
                wda=_wda(),
                run=RUN,
                definition=_definition(),
                key="message_worker",
                arguments=out["arguments"],
                confirm=out["confirm"],
                via="page",
            )
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess"),
    ):
        actions.execute(execution.pk)
        actions.execute(execution.pk)  # a redelivered task starts nothing twice
    execution.refresh_from_db()
    assert execution.status == WorkflowActionExecution.Status.COMPLETED
    thread = Thread.objects.get(pk=execution.results[KEY]["thread_id"])
    assert (
        thread.approval["approved_by"] == "nurse.joy" and thread.approval["via"] == f"workflow_action:{execution.pk}"
    )
    assert Thread.objects.count() == 1 and len(fake_connectid.messages) == 1


def test_the_action_says_who_is_left_out_and_why(people, world):
    WorkerState.objects.update(stage="suspended")
    playbooks.set_playbook(scopes.SYSTEM, PROGRAM, "flags", {**PLAYBOOK, "eligible": {"stages": ["delivering"]}})
    out = actions.preview(
        people["staff"],
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="message_worker",
        arguments={"workers": [{"key": KEY}]},
    )
    assert "stage is suspended" in out["workers"][0]["left_out"]
    assert out["needs"] == ["workers"] and "confirm" not in out


def test_the_action_is_refused_while_messaging_is_off(people, world, settings):
    settings.MESSAGING_ENABLED = False
    with pytest.raises(actions.ActionError) as e:
        actions.preview(
            people["staff"],
            wda=_wda(),
            run=RUN,
            definition=_definition(),
            key="message_worker",
            arguments={"workers": [{"key": KEY}]},
        )
    assert e.value.code == "messaging_off"


def test_a_failed_start_is_reported_in_words(people, world):
    execution = WorkflowActionExecution.objects.create(
        user=people["outsider"],
        via="page",
        definition_id=7,
        run_id=70,
        program_id=PROGRAM,
        action_key="message_worker",
        action_type="start_playbook",
        arguments={"workers": [{"key": KEY}], "playbook": "flags", "program_id": PROGRAM},
    )
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess"),
    ):
        actions.execute(execution.pk)
    execution.refresh_from_db()
    assert execution.results[KEY]["status"] == "failed"
    assert "not accessible" in execution.results[KEY]["error"]
    assert not Thread.objects.exists()


# --- MCP ----------------------------------------------------------------------------------------


def test_playbooks_are_listed_read_and_set_as_the_person(people, world):
    staff = people["staff"]
    listed = tool("messaging_playbook_list", staff, program_id=PROGRAM)
    assert [p["key"] for p in listed["playbooks"]] == ["flags"] and listed["messaging_on"]
    assert tool("messaging_playbook_get", staff, program_id=PROGRAM, key="flags")["playbook"]["version"] == 1
    changed = tool(
        "messaging_playbook_set",
        staff,
        program_id=PROGRAM,
        key="flags",
        playbook={**{k: v for k, v in PLAYBOOK.items() if k != "version"}, "opening": "Hi {name}"},
    )
    assert changed["playbook"]["version"] == 2
    with pytest.raises(MCPToolError) as e:
        tool("messaging_playbook_set", staff, program_id=PROGRAM, key="flags", playbook={"direction": "sideways"})
    assert e.value.code == "INVALID_SCHEMA"
    with pytest.raises(MCPToolError) as e:
        tool("messaging_playbook_list", people["outsider"], program_id=PROGRAM)
    assert e.value.code == "PERMISSION_DENIED"
    with pytest.raises(MCPToolError):
        tool("messaging_playbook_set", people["outsider"], program_id=PROGRAM, key="flags", playbook=None)


def test_dry_run_shows_who_and_the_exact_opening(people, world):
    result = tool("messaging_playbook_dry_run", people["staff"], program_id=PROGRAM, key="flags")
    (row,) = result["eligible"]
    assert row["username"] == "amina123" and row["opening"].startswith("Hello Amina")
    assert not Thread.objects.exists()


AGENT_PLAYBOOK = {
    "version": 1,
    "direction": "inbound",
    "handler": {"kind": "agent", "agent": "connect-helper", "skill": "general-help"},
}


def test_an_agent_playbook_with_no_tools_warns_on_set_and_dry_run(people, world):
    """An agent answering with no tools tells a worker it cannot see their record (program 10236, v1)."""
    staff = people["staff"]
    stored = tool("messaging_playbook_set", staff, program_id=PROGRAM, key="help", playbook=AGENT_PLAYBOOK)
    (warning,) = stored["warnings"]
    assert "playbooks.help v1" in warning and "grants no tools" in warning
    (warning,) = tool("messaging_playbook_dry_run", staff, program_id=PROGRAM, key="help")["warnings"]
    assert "playbooks.help v1" in warning
    # a variant that hands the thread to an agent is the same gap
    variant = {
        **{k: v for k, v in PLAYBOOK.items() if k != "version"},
        "variants": [{"key": "a", "weight": 1, "handler": AGENT_PLAYBOOK["handler"]}],
    }
    assert tool("messaging_playbook_set", staff, program_id=PROGRAM, key="flags", playbook=variant)["warnings"]


def test_an_agent_playbook_that_grants_tools_has_no_warning(people, world):
    staff = people["staff"]
    granted = {**AGENT_PLAYBOOK, "tools": ["worker_profile", "worker_payments", "thread_handoff"]}
    assert tool("messaging_playbook_set", staff, program_id=PROGRAM, key="help", playbook=granted)["warnings"] == []
    assert tool("messaging_playbook_dry_run", staff, program_id=PROGRAM, key="help")["warnings"] == []
    # a playbook no agent answers needs no tools
    assert tool("messaging_playbook_dry_run", staff, program_id=PROGRAM, key="flags")["warnings"] == []


def test_messaging_start_previews_then_sends_on_confirm(people, world, fake_connectid):
    staff = people["staff"]
    args = {"program_id": PROGRAM, "playbook": "flags", "workers": ["amina123"]}
    preview = tool("messaging_start", staff, **args)
    assert preview["workers"][0]["opening"] and not fake_connectid.messages
    result = tool("messaging_start", staff, **args, confirm=preview["confirm"])
    assert len(result["started"]) == 1 and len(fake_connectid.messages) == 1
    with pytest.raises(MCPToolError) as e:
        tool("messaging_start", staff, **args, confirm=preview["confirm"])
    assert e.value.details["code"] == "confirm_used"


def test_threads_are_listed_and_read_only_by_people_who_hold_them(people, world):
    staff = people["staff"]
    preview = tool("messaging_start", staff, program_id=PROGRAM, playbook="flags", workers=["amina123"])
    tool(
        "messaging_start",
        staff,
        program_id=PROGRAM,
        playbook="flags",
        workers=["amina123"],
        confirm=preview["confirm"],
    )
    (row,) = tool("messaging_thread_list", staff)["threads"]
    got = tool("messaging_thread_get", staff, thread_id=row["id"])
    assert got["messages"][0]["text"].startswith("Hello Amina") and got["approval"]["approved_by"] == "nurse.joy"
    assert tool("messaging_thread_list", people["outsider"])["threads"] == []
    with pytest.raises(MCPToolError) as e:
        tool("messaging_thread_get", people["outsider"], thread_id=row["id"])
    assert e.value.code == "NOT_FOUND"


def test_an_inbox_reply_is_sent_as_the_person(people, world, fake_connectid):
    address = service.address_for(world, PROGRAM)
    thread = service.open_thread(world, address=address, direction="in", program_id=PROGRAM, needs_attention=True)
    reply = tool("messaging_inbox_reply", people["staff"], thread_id=thread.pk, text="We will call you today.")
    message = Message.objects.get(pk=reply["message_id"])
    assert message.author_kind == "staff" and message.author_ref == "nurse.joy"
    with pytest.raises(MCPToolError):
        tool("messaging_inbox_reply", people["outsider"], thread_id=thread.pk, text="hi")


def test_the_messaging_tools_are_flagged_and_not_on_the_restricted_endpoint():
    from connect_labs.mcp.token_scopes import RESTRICTED_TOOLS

    for name in ("messaging_playbook_set", "messaging_start", "messaging_inbox_reply"):
        assert get_tool(name).is_write, name
    names = [n for n in RESTRICTED_TOOLS if n.startswith("messaging_")]
    assert names == []


def test_a_summary_is_kept_with_the_thread_and_shown(people, world):
    address = service.address_for(world, PROGRAM)
    thread = service.open_thread(world, address=address, direction="in", program_id=PROGRAM)
    service.record_summary(thread, "Agreed to finish module 3 by Friday.", author_kind="agent", outcome="committed")
    got = tool("messaging_thread_get", people["staff"], thread_id=thread.pk)
    assert got["summaries"][0]["outcome"] == "committed"


def test_closing_a_conversation_frees_the_worker_for_a_new_opening(people, world, fake_connectid):
    staff = people["staff"]
    args = {"program_id": PROGRAM, "playbook": "flags", "workers": ["amina123"]}
    preview = tool("messaging_start", staff, **args)
    tool("messaging_start", staff, **args, confirm=preview["confirm"])
    thread = Thread.objects.get(worker=world)
    (blocked,) = tool("messaging_start", staff, **args)["workers"]
    assert not blocked["eligible"] and "already open" in blocked["blocked"]

    with pytest.raises(MCPToolError) as e:
        tool("messaging_thread_close", people["outsider"], thread_id=thread.pk)
    assert e.value.code == "NOT_FOUND"
    assert Thread.objects.get(pk=thread.pk).is_open

    closed = tool("messaging_thread_close", staff, thread_id=thread.pk, reason="sent to the wrong cohort")
    assert closed["id"] == thread.pk and closed["state"] == "closed" and closed["closed_at"]
    assert "sent to the wrong cohort" in closed["close_reason"] and "nurse.joy" in closed["close_reason"]
    # Not a STOP: the worker's channel stays on.
    assert service.address_for(world, PROGRAM).can_send
    # Closing again changes nothing.
    again = tool("messaging_thread_close", staff, thread_id=thread.pk, reason="other")
    assert (again["close_reason"], again["closed_at"]) == (closed["close_reason"], closed["closed_at"])

    (row,) = tool("messaging_start", staff, **args)["workers"]
    assert row["eligible"], row
    preview = tool("messaging_start", staff, **args)
    result = tool("messaging_start", staff, **args, confirm=preview["confirm"])
    assert len(result["started"]) == 1
    assert Thread.objects.filter(worker=world, state="open").count() == 1


def test_thread_close_is_a_write_tool():
    assert get_tool("messaging_thread_close").is_write
