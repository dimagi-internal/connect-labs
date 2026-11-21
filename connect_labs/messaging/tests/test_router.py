"""The router, playbooks, flows and caps, against a fake connect-id.

Every name and message here is invented.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.messaging import handlers, playbooks, router, service
from connect_labs.messaging.handlers import Handler
from connect_labs.messaging.models import Address, DeliveryStatus, Message, PlaybookRun, Thread
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.scope_config.service import Invalid
from connect_labs.workers.models import ConnectWorker, WorkerEvent, WorkerMembership, WorkerState

pytestmark = pytest.mark.django_db

OPP = 9001


def _member(worker, stage="learning", facets=None, opp=OPP):
    membership = WorkerMembership.objects.create(worker=worker, opportunity_id=opp, program_id=PROGRAM)
    WorkerState.objects.create(membership=membership, stage=stage, facets=facets or {})
    return membership


def _set(key, **playbook):
    return playbooks.set_playbook(scopes.SYSTEM, PROGRAM, key, {"version": 1, **playbook})


def _outbound(**over):
    base = {
        "direction": "outbound",
        "trigger": {"type": "person"},
        "handler": {"kind": "human"},
        "opening": "Hello {first_name}, how is Learn going?",
        "approval": {"mode": "per_batch"},
    }
    return {**base, **over}


@pytest.fixture
def staff(django_user_model, monkeypatch):
    user = django_user_model.objects.create_user(username="nurse.joy", password="x")
    outsider = django_user_model.objects.create_user(username="outsider", password="x")

    def holdings(caller):
        name = scopes._user_of(caller).username
        programs = {PROGRAM} if name == "nurse.joy" else {123}
        return scopes.Holdings(frozenset(), frozenset(), frozenset(programs))

    monkeypatch.setattr(scopes, "holdings", holdings)
    user.outsider = outsider
    return user


@pytest.fixture
def world(fake_connectid, messaging_on, worker, run_now):
    fake_connectid.add_user(worker.username)
    _member(worker)
    return worker


def _start(staff, key, workers, context=None):
    p = router.preview(staff, program_id=PROGRAM, playbook_key=key, workers=workers, context=context)
    return p, router.commit(
        staff, program_id=PROGRAM, playbook_key=key, workers=workers, context=context, confirm=p.get("confirm", "")
    )


# --- playbooks ---------------------------------------------------------------------------


def test_a_playbook_is_validated(db):
    with pytest.raises(Invalid, match="auto_within_caps"):
        _set("nudge", **_outbound(trigger={"type": "schedule", "hour": 9}))
    with pytest.raises(Invalid, match="approved_by"):
        _set("nudge", **_outbound(trigger={"type": "schedule", "hour": 9}, approval={"mode": "auto_within_caps"}))
    with pytest.raises(Invalid, match="no flow"):
        _set("nudge", **_outbound(handler={"kind": "flow", "key": "nope"}))
    with pytest.raises(Invalid, match="agent and skill"):
        _set("nudge", **_outbound(handler={"kind": "agent", "agent": "connect-coach"}))
    with pytest.raises(Invalid, match="no trigger or approval"):
        _set("help", direction="inbound", handler={"kind": "human"}, approval={"mode": "per_send"})
    with pytest.raises(Invalid, match="opening"):
        _set("nudge", **{k: v for k, v in _outbound().items() if k != "opening"})
    with pytest.raises(Invalid, match="no playbook 'ghost'"):
        _set("nudge", **_outbound(handoffs=[{"to": "ghost", "when": {"keywords": ["help"]}}]))
    with pytest.raises(Invalid):
        _set("nudge", **_outbound(tools=["send_anything"]))
    with pytest.raises(Invalid):
        _set("Bad Key", **_outbound())


def test_the_version_moves_on_by_itself_and_old_versions_stay_readable(db):
    assert _set("nudge", **_outbound())["version"] == 1
    assert _set("nudge", **_outbound())["version"] == 1  # nothing changed
    second = playbooks.set_playbook(scopes.SYSTEM, PROGRAM, "nudge", {**_outbound(opening="Hi {name}"), "version": 1})
    assert second["version"] == 2 and second["opening"] == "Hi {name}"
    # replacing drops what the new one leaves out
    third = playbooks.set_playbook(scopes.SYSTEM, PROGRAM, "nudge", {**_outbound(), "version": 2})
    assert third["version"] == 3 and "caps" not in third
    assert playbooks.playbook_at(PROGRAM, "nudge", 2)["opening"] == "Hi {name}"
    playbooks.set_playbook(scopes.SYSTEM, PROGRAM, "nudge", None)
    assert playbooks.get(PROGRAM, "nudge") is None


def test_variants_are_stable_and_weighted(db):
    playbook = _outbound(variants=[{"key": "a", "weight": 1}, {"key": "b", "weight": 3}])
    picks = [playbooks.assign_variant(playbook, "nudge", f"worker{i}")["key"] for i in range(400)]
    assert picks == [playbooks.assign_variant(playbook, "nudge", f"worker{i}")["key"] for i in range(400)]
    assert 60 < picks.count("a") < 140


# --- starting: preview, confirm, caps ---------------------------------------------------------


def test_preview_then_confirm_starts_the_thread_and_sends_the_opening(staff, world, fake_connectid):
    _set("learn-stalled", **_outbound(caps={"max_turns": 5}))
    preview, result = _start(staff, "learn-stalled", ["amina123"])
    (row,) = preview["workers"]
    assert row["eligible"] and row["opening"] == "Hello Amina, how is Learn going?"
    (started,) = result["started"]
    thread = Thread.objects.get(pk=started["thread_id"])
    assert thread.playbook_key == "learn-stalled" and thread.playbook_version == 1
    assert thread.approval["approved_by"] == "nurse.joy" and thread.started_by == "person:nurse.joy"
    assert thread.state_snapshot["stage"] == "learning" and thread.opportunity_id == OPP
    (shown,) = fake_connectid.texts_to("amina123", thread.address.key)
    assert shown.startswith("Hello Amina, how is Learn going?") and "STOP" in shown  # with the disclosure


def test_a_confirm_is_single_use_and_bound_to_what_was_previewed(staff, world, worker):
    other = ConnectWorker.objects.create(username="baraka9", name="Baraka")
    _member(other)
    _set("learn-stalled", **_outbound())
    preview = router.preview(staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"])
    with pytest.raises(service.MessagingError) as e:
        router.commit(
            staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["baraka9"], confirm=preview["confirm"]
        )
    assert e.value.code == "confirm_mismatch"
    router.commit(
        staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"], confirm=preview["confirm"]
    )
    with pytest.raises(service.MessagingError) as e:
        router.commit(
            staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"], confirm=preview["confirm"]
        )
    assert e.value.code == "confirm_used"


def test_a_changed_playbook_invalidates_the_preview(staff, world):
    _set("learn-stalled", **_outbound())
    preview = router.preview(staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"])
    playbooks.set_playbook(scopes.SYSTEM, PROGRAM, "learn-stalled", {**_outbound(opening="Changed"), "version": 1})
    with pytest.raises(service.MessagingError) as e:
        router.commit(
            staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"], confirm=preview["confirm"]
        )
    assert e.value.code == "confirm_mismatch"


def test_per_send_starts_one_worker_at_a_time(staff, world):
    _set("claim-help", **_outbound(approval={"mode": "per_send"}))
    with pytest.raises(service.MessagingError, match="one Connect Worker at a time"):
        router.preview(staff, program_id=PROGRAM, playbook_key="claim-help", workers=["amina123", "someone"])


def test_only_people_holding_the_program_may_start(staff, world):
    _set("learn-stalled", **_outbound())
    with pytest.raises(service.MessagingError) as e:
        router.preview(staff.outsider, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"])
    assert e.value.code == "forbidden"


def test_eligibility_is_judged_on_worker_state(staff, world):
    _set("claim-help", **_outbound(eligible={"stages": ["ready_to_claim"]}))
    _set("overdue", **_outbound(eligible={"facets": {"tasks.overdue": {"gte": 1}}}))
    (row,) = router.preview(staff, program_id=PROGRAM, playbook_key="claim-help", workers=["amina123"])["workers"]
    assert not row["eligible"] and "stage is learning" in row["blocked"]
    WorkerState.objects.update(facets={"tasks": {"overdue": 2}})
    (row,) = router.preview(staff, program_id=PROGRAM, playbook_key="overdue", workers=["amina123"])["workers"]
    assert row["eligible"]


def test_nobody_eligible_means_nothing_to_confirm(staff, world):
    _set("claim-help", **_outbound(eligible={"stages": ["at_cap"]}))
    preview = router.preview(staff, program_id=PROGRAM, playbook_key="claim-help", workers=["amina123"])
    assert "confirm" not in preview and preview["needs"] == ["workers"]


def test_the_weekly_cap_and_one_open_conversation_per_channel(staff, world):
    _set("learn-stalled", **_outbound(caps={"threads_per_worker_per_week": 1}))
    _set("checkin", **_outbound())
    _start(staff, "learn-stalled", ["amina123"])
    (row,) = router.preview(staff, program_id=PROGRAM, playbook_key="checkin", workers=["amina123"])["workers"]
    assert "already open" in row["blocked"]
    Thread.objects.update(state="closed")
    (row,) = router.preview(staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"])["workers"]
    assert "cap 1" in row["blocked"]


def test_quiet_hours_hold_the_opening_until_they_end(staff, world, monkeypatch):
    # 22:30 in Lagos (UTC+1) is 21:30 UTC; quiet 21:00-07:00 local ends 06:00 UTC next day.
    now = dt.datetime(2026, 11, 21, 21, 30, tzinfo=dt.UTC)
    monkeypatch.setattr(timezone, "now", lambda: now)
    _set("learn-stalled", **_outbound(caps={"quiet_hours": {"start": "21:00", "end": "07:00"}}))
    preview, result = _start(staff, "learn-stalled", ["amina123"])
    assert preview["workers"][0]["held_until"] == "2026-11-22T06:00:00+00:00"
    message = Message.objects.get(thread_id=result["started"][0]["thread_id"])
    assert message.delivery_status == DeliveryStatus.QUEUED and message.scheduled_for == dt.datetime(
        2026, 11, 22, 6, tzinfo=dt.UTC
    )
    assert service.deliver(message.pk) == "later"


def test_quiet_hours_use_the_programs_zone_when_the_worker_has_none(world):
    world.timezone = ""
    world.save()
    playbook = _outbound(caps={"quiet_hours": {"start": "08:00", "end": "09:00"}})
    at = dt.datetime(2026, 11, 21, 8, 30, tzinfo=dt.UTC)
    assert router.quiet_until(playbook, world, PROGRAM, at) == dt.datetime(2026, 11, 21, 9, tzinfo=dt.UTC)


def test_a_worker_who_asked_us_to_stop_is_left_out(staff, world):
    _set("learn-stalled", **_outbound())
    address = service.address_for(world, PROGRAM)
    Address.objects.filter(pk=address.pk).update(opted_out_at=timezone.now())
    (row,) = router.preview(staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"])["workers"]
    assert "STOP" in row["blocked"]


def test_the_programs_switch_stops_starts(staff, world):
    from connect_labs.scope_config.models import ScopeConfig

    _set("learn-stalled", **_outbound())
    ScopeConfig.objects.filter(namespace="messaging.settings").update(data={"enabled": False})
    with pytest.raises(service.MessagingError) as e:
        router.preview(staff, program_id=PROGRAM, playbook_key="learn-stalled", workers=["amina123"])
    assert e.value.code == "program_off"


# --- flows ---------------------------------------------------------------------------------


def test_flows_fill_their_facts_and_one_way_ones_close(staff, world, fake_connectid):
    playbook = _outbound(handler={"kind": "flow", "key": "stock_low"})
    playbook.pop("opening")
    _set("stock", **playbook)
    preview = router.preview(
        staff,
        program_id=PROGRAM,
        playbook_key="stock",
        workers=["amina123"],
        context={"item": "ORS sachets", "days_left": 3},
    )
    assert preview["workers"][0]["opening"].startswith(
        "Hello Amina, your stock of ORS sachets is running low (about 3 days left)."
    )
    result = router.commit(
        staff,
        program_id=PROGRAM,
        playbook_key="stock",
        workers=["amina123"],
        context={"item": "ORS sachets", "days_left": 3},
        confirm=preview["confirm"],
    )
    thread = Thread.objects.get(pk=result["started"][0]["thread_id"])
    assert not thread.is_open and thread.close_reason == "sent (one way)"
    assert thread.messages.get().author_kind == "flow"


def test_a_flow_missing_its_facts_is_left_out(staff, world):
    playbook = _outbound(handler={"kind": "flow", "key": "payment_notice"})
    playbook.pop("opening")
    _set("paid", **playbook)
    (row,) = router.preview(staff, program_id=PROGRAM, playbook_key="paid", workers=["amina123"])["workers"]
    assert "needs amount" in row["blocked"]


def test_a_code_goes_on_a_channel_of_its_own(staff, world, fake_connectid):
    playbook = _outbound(handler={"kind": "flow", "key": "verify_code"})
    playbook.pop("opening")
    _set("code", **playbook)
    _start(staff, "code", ["amina123"], context={"code": "482913"})
    address = Address.objects.get(worker=world)
    assert address.purpose == "security" and address.channel_source == f"labs-security-{PROGRAM}"
    assert "482913" in fake_connectid.texts_to("amina123", address.key)[0]


def test_every_flow_has_an_opening():
    from connect_labs.messaging import flows

    facts = {"first_name": "A", "program": "P", "code": "1", "item": "x", "task": "t", "amount": "5 USD"}
    for flow in flows.FLOWS.values():
        assert flow.opening(flows.prepare(facts)).strip()
    assert set(flows.FLOWS) == {"welcome", "verify_code", "stock_low", "task_due", "payment_notice"}


# --- inbound routing, debounce, exits and handoffs ------------------------------------------------


class Recorder(Handler):
    def __init__(self):
        self.calls = []

    def start(self, thread, ctx):
        self.calls.append(("start", thread.pk))

    def on_message(self, thread, msgs):
        self.calls.append(("on_message", [m.text for m in msgs]))

    def on_handoff_in(self, thread, from_, note):
        self.calls.append(("handoff_in", from_["kind"], note))

    def stop(self, thread, reason):
        self.calls.append(("stop", reason))


@pytest.fixture
def agent():
    previous = handlers.get("agent")
    rec = handlers.register("agent", Recorder())
    yield rec
    handlers.register("agent", previous)


def _reply(client, fake_connectid, worker, *texts):
    address = Address.objects.get(worker=worker, purpose="program")
    return fake_connectid.reply(client, address.channel_id, address.key, *texts)


def test_inbound_goes_to_the_first_matching_inbound_playbook(client, world, fake_connectid, agent, handle_now):
    service.address_for(world, PROGRAM)
    _set("suspended", direction="inbound", priority=1, eligible={"stages": ["suspended"]}, handler={"kind": "human"})
    _set(
        "learn-support",
        direction="inbound",
        priority=5,
        eligible={"stages": ["learning"]},
        handler={"kind": "agent", "agent": "connect-helper", "skill": "learn"},
    )
    _set("general-help", direction="inbound", priority=9, handler={"kind": "human"})
    _reply(client, fake_connectid, world, "How do I finish module 3?")
    thread = Thread.objects.get()
    assert thread.playbook_key == "learn-support" and thread.handler_kind == "agent"
    assert thread.handler_ref == "connect-helper:learn" and thread.direction == "in"
    handle_now()
    assert agent.calls == [("start", thread.pk), ("on_message", ["How do I finish module 3?"])]


def test_an_open_thread_wins_over_playbooks_and_no_match_goes_to_the_inbox(
    client, world, fake_connectid, agent, handle_now
):
    service.address_for(world, PROGRAM)
    _set(
        "general-help",
        direction="inbound",
        eligible={"stages": ["delivering"]},
        handler={"kind": "agent", "agent": "a", "skill": "b"},
    )
    _reply(client, fake_connectid, world, "hello")
    fallback = Thread.objects.get()
    assert fallback.playbook_key == "" and fallback.handler_kind == "human" and fallback.needs_attention
    _reply(client, fake_connectid, world, "anyone?")
    assert Thread.objects.count() == 1 and fallback.messages.filter(direction="in").count() == 2


def test_messages_in_quick_succession_reach_the_handler_once(client, world, fake_connectid, agent, run_now):
    service.address_for(world, PROGRAM)
    _set("help", direction="inbound", handler={"kind": "agent", "agent": "a", "skill": "b"})
    _reply(client, fake_connectid, world, "hi")
    _reply(client, fake_connectid, world, "my phone")
    _reply(client, fake_connectid, world, "is broken")
    thread = Thread.objects.get()
    assert router.handle_due(thread.pk) == "not yet"  # the worker has not paused yet
    later = timezone.now() + dt.timedelta(seconds=30)
    assert router.handle_due(thread.pk, now=later) == "handled 3"
    assert agent.calls[-1] == ("on_message", ["hi", "my phone", "is broken"])
    assert router.handle_due(thread.pk, now=later) == "nothing due"


def test_an_exit_keyword_closes_and_a_handoff_keyword_moves(client, world, fake_connectid, agent, handle_now):
    service.address_for(world, PROGRAM)
    _set("human-help", direction="inbound", priority=99, eligible={"stages": ["suspended"]}, handler={"kind": "human"})
    _set(
        "help",
        direction="inbound",
        handler={"kind": "agent", "agent": "a", "skill": "b"},
        exit={"keywords": ["thanks bye"]},
        handoffs=[{"to": "human", "when": {"keywords": ["talk to a person"]}, "note": "Asked for a person"}],
    )
    _reply(client, fake_connectid, world, "talk to a person")
    thread = Thread.objects.get()
    assert thread.handler_kind == "human" and thread.attention_reason == "Asked for a person"
    assert ("stop", "handed off") in agent.calls
    thread.close("done")
    _reply(client, fake_connectid, world, "Thanks bye!")
    second = Thread.objects.exclude(pk=thread.pk).get()
    assert not second.is_open and "ended it" in second.close_reason


def test_an_agent_thread_without_canopy_configured_goes_to_a_person_with_a_clear_reason(
    client, world, fake_connectid, handle_now
):
    # The canopy bridge's handler replaces labs' stub; with the connect-messaging site unconfigured
    # (as here) it hands the thread to a person and says why.
    service.address_for(world, PROGRAM)
    _set("help", direction="inbound", handler={"kind": "agent", "agent": "connect-helper", "skill": "general"})
    _reply(client, fake_connectid, world, "hello")
    handle_now()
    thread = Thread.objects.get()
    assert thread.handler_kind == "human" and "Canopy agents are not configured" in thread.attention_reason


def test_an_automated_handler_stops_at_its_turn_limit(staff, world, agent):
    _set(
        "coach",
        **_outbound(handler={"kind": "agent", "agent": "connect-coach", "skill": "flags"}, caps={"max_turns": 2}),
    )
    _, result = _start(staff, "coach", ["amina123"])
    thread = Thread.objects.get(pk=result["started"][0]["thread_id"])
    thread.send("one", "agent", "connect-coach")
    thread.send("two", "agent", "connect-coach")
    with pytest.raises(service.MessagingError) as e:
        thread.send("three", "agent", "connect-coach")
    assert e.value.code == "turn_limit"
    thread.send("a person may still write", "staff", "nurse.joy")


# --- automatic starts -----------------------------------------------------------------------------


def test_an_automatic_start_needs_auto_approval(world):
    _set("learn-stalled", **_outbound())
    assert "not approved" in router.start_automatic(
        program_id=PROGRAM, playbook_key="learn-stalled", username="amina123", trigger="api"
    )


def test_a_state_event_starts_its_playbook(world, fake_connectid):
    _set(
        "claim-help",
        **_outbound(
            trigger={"type": "state_event", "kinds": ["state_changed"], "stages": ["ready_to_claim"]},
            approval={"mode": "auto_within_caps", "approved_by": "nurse.joy"},
        ),
    )

    def event(n, stage):
        # the sync writes events in bulk, with no signals
        WorkerEvent.objects.bulk_create(
            [
                WorkerEvent(
                    worker=world,
                    program_id=PROGRAM,
                    kind="state_changed",
                    source="labs",
                    payload={"stage_from": "learning", "stage_to": stage},
                    dedupe_key=f"x{n}",
                )
            ]
        )

    event(0, "learning")
    assert router.scan_worker_events() == 0  # the first scan starts at the newest event: no replay
    event(1, "learning")
    router.scan_worker_events()
    assert not Thread.objects.exists()
    event(2, "ready_to_claim")
    router.scan_worker_events()
    thread = Thread.objects.get()
    assert thread.started_by == "system:state_event:state_changed" and thread.approval["approved_by"] == "nurse.joy"
    router.scan_worker_events()
    assert Thread.objects.count() == 1


def test_a_schedule_runs_once_per_period(world, fake_connectid):
    _set(
        "weekly-checkin",
        **_outbound(
            trigger={"type": "schedule", "every": "daily", "hour": 9},
            approval={"mode": "auto_within_caps", "approved_by": "nurse.joy"},
            handler={"kind": "none"},
        ),
    )
    at = dt.datetime(2026, 11, 21, 9, 5, tzinfo=dt.UTC)
    assert router.run_schedules(at)[0]["started"] == 1
    assert router.run_schedules(at) == []
    assert PlaybookRun.objects.get().period == "2026-11-21"
    assert router.run_schedules(at.replace(hour=10)) == []


def test_nothing_starts_while_messaging_is_off(world, settings):
    settings.MESSAGING_ENABLED = False
    _set("x", **_outbound(approval={"mode": "auto_within_caps", "approved_by": "j"}, trigger={"type": "api"}))
    assert (
        router.start_automatic(program_id=PROGRAM, playbook_key="x", username="amina123", trigger="api")
        == "messaging is off"
    )


def test_dry_run_lists_who_is_eligible_now_and_their_opening(staff, world):
    other = ConnectWorker.objects.create(username="baraka9", name="Baraka Otieno")
    _member(other, stage="delivering", opp=OPP + 1)
    _set("learn-stalled", **_outbound(eligible={"stages": ["learning"]}))
    result = router.dry_run(staff, program_id=PROGRAM, playbook_key="learn-stalled")
    assert [r["username"] for r in result["eligible"]] == ["amina123"]
    assert result["eligible"][0]["opening"] == "Hello Amina, how is Learn going?"
    assert [r["username"] for r in result["not_eligible"]] == ["baraka9"]
    assert not Thread.objects.exists()


def test_idle_threads_close_by_their_exit_rule(staff, world):
    _set("learn-stalled", **_outbound(exit={"idle_hours": 24}))
    _start(staff, "learn-stalled", ["amina123"])
    assert router.close_idle(timezone.now() + dt.timedelta(hours=25)) == 1
    assert not Thread.objects.get().is_open
