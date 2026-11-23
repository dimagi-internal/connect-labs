"""The Worker Brief: sections, caps, determinism, and what must never be in it.

Every name and message here is invented.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.access import scopes
from connect_labs.messaging import playbooks, service
from connect_labs.messaging.canopy_bridge import brief as briefs
from connect_labs.messaging.canopy_bridge import visibility
from connect_labs.messaging.canopy_bridge.models import WorkerBrief, WorkerCommitment
from connect_labs.messaging.models import Address, AuthorKind, Direction, Message, ThreadSummary
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.workers.models import StaffNote, Visibility, WorkerFact, WorkerMembership, WorkerState

pytestmark = pytest.mark.django_db

OTHER = 777
PHONE = "+234 803 555 0142"


def _thread(worker, program=PROGRAM, key="learn-support", **playbook):
    playbooks.set_playbook(
        scopes.SYSTEM,
        program,
        key,
        {
            "version": 1,
            "direction": "inbound",
            "handler": {"kind": "agent", "agent": "connect-coach", "skill": "learn"},
            "tools": ["worker_profile", "worker_facts"],
            **playbook,
        },
    )
    address, _ = Address.objects.get_or_create(
        worker=worker,
        channel_source=service.channel_source(program),
        defaults={"program_id": program, "channel_id": f"c-{program}"},
    )
    return service.open_thread(
        worker,
        address=address,
        direction=Direction.INBOUND,
        program_id=program,
        handler_kind="agent",
        handler_ref="connect-coach:learn",
        playbook_key=key,
        playbook_version=playbooks.get(program, key)["version"],
    )


def _say(thread, text, *, direction=Direction.INBOUND, visibility=Visibility.NORMAL):
    return Message.objects.create(
        thread=thread,
        worker=thread.worker,
        direction=direction,
        author_kind=AuthorKind.WORKER if direction == Direction.INBOUND else AuthorKind.AGENT,
        text=text,
        delivery_status="received" if direction == Direction.INBOUND else "sent",
        visibility=visibility,
    )


@pytest.fixture
def member(worker):
    worker.phone = PHONE
    worker.languages = ["ha"]
    worker.save()
    membership = WorkerMembership.objects.create(
        worker=worker,
        opportunity_id=9001,
        program_id=PROGRAM,
        opportunity_name="Bednets North",
        suspension_reason="SECRET-SUSPENSION-REASON",
    )
    WorkerState.objects.create(
        membership=membership, stage="learning", facets={"learn": {"modules_done": 2, "modules_total": 5}}
    )
    return worker


def test_the_brief_has_its_sections_in_order(member):
    thread = _thread(member)
    _say(thread, "How do I finish module 3?")
    WorkerFact.objects.create(
        worker=member, program_id=PROGRAM, kind="caution", statement="Do not call before 9am", asserted_by_kind="staff"
    )
    WorkerCommitment.objects.create(worker=member, program_id=PROGRAM, text="Check the payment by Friday")
    body = briefs.build(thread, inbound_text="module 3").body
    titles = [line[3:] for line in body.splitlines() if line.startswith("## ")]
    assert titles == [
        "Who",
        "This thread",
        "Cautions and corrections",
        "Open commitments",
        "Recent history",
        "State highlights",
        "Pointers",
    ]
    assert "Amina Bello (username amina123)" in body and "Stage: learning" in body
    assert "Do not call before 9am" in body and "Check the payment by Friday" in body
    assert "learn: modules_done 2, modules_total 5" in body
    assert f"labs-worker://amina123/threads/{thread.pk}" in body
    assert "worker_profile, worker_facts" in body


def test_the_brief_is_deterministic_and_stored_by_its_hash(member):
    thread = _thread(member)
    _say(thread, "hello")
    first, second = briefs.build(thread, inbound_text="hello"), briefs.build(thread, inbound_text="hello")
    assert first.body == second.body and first.hash == second.hash
    row = briefs.store(first, thread)
    assert briefs.store(second, thread).pk == row.pk
    assert WorkerBrief.objects.get(hash=first.hash).body == first.body


def test_the_brief_is_capped_however_long_the_record(member):
    thread = _thread(member)
    for i in range(40):
        _say(thread, f"message {i} " + "words " * 80)
        WorkerFact.objects.create(
            worker=member,
            program_id=PROGRAM,
            kind="context",
            statement=f"fact {i} " + "x" * 250,
            asserted_by_kind="agent",
        )
        WorkerFact.objects.create(
            worker=member,
            program_id=PROGRAM,
            kind="caution",
            statement=f"caution {i} " + "y" * 250,
            asserted_by_kind="staff",
        )
        WorkerCommitment.objects.create(worker=member, program_id=PROGRAM, text=f"promise {i} " + "z" * 250)
        ThreadSummary.objects.create(thread=thread, text=f"summary {i} " + "s" * 3000, author_kind="agent")
    brief = briefs.build(thread, inbound_text="fact")
    # The worker's sections are cut to MAX_CHARS; the title and the parseable header lines sit on top.
    assert len(brief.body) <= briefs.MAX_CHARS + 400
    assert sum(brief.sections.values()) <= briefs.MAX_CHARS
    assert brief.body.startswith("# Worker Brief")
    assert "## Who" in brief.body  # the highest priority survives the cut
    assert all(len(line) <= visibility.MAX_LINE + 2 for line in brief.body.splitlines())


def test_no_phone_restricted_or_private_content_ever_appears(member):
    thread = _thread(member)
    _say(thread, f"my number is {PHONE}, call me")
    _say(thread, "SAFETY-DISCLOSURE", visibility=Visibility.RESTRICTED)
    ThreadSummary.objects.create(
        thread=thread, text="RESTRICTED-SUMMARY", author_kind="staff", visibility=Visibility.RESTRICTED
    )
    StaffNote.objects.create(worker=member, program_id=PROGRAM, body="PRIVATE-NOTE", private=True, author="x")
    StaffNote.objects.create(worker=member, program_id=PROGRAM, body="Prefers voice notes", private=False, author="x")
    service.add_note(thread, "INTERNAL-HANDOFF-NOTE")
    body = briefs.build(thread, inbound_text="call").body
    for secret in (
        "803 555 0142",
        "8035550142",
        "SAFETY-DISCLOSURE",
        "RESTRICTED-SUMMARY",
        "PRIVATE-NOTE",
        "SECRET-SUSPENSION-REASON",
        "INTERNAL-HANDOFF-NOTE",
    ):
        assert secret not in body
    assert "[phone removed]" in body and "Prefers voice notes" in body


def test_history_is_this_programs_unless_the_playbook_opts_in(member):
    here = _thread(member)
    there = _thread(member, program=OTHER, key="elsewhere")
    ThreadSummary.objects.create(thread=there, text="OTHER-PROGRAM-SUMMARY", author_kind="agent")
    ThreadSummary.objects.create(thread=here, text="This program's summary", author_kind="agent")
    WorkerFact.objects.create(
        worker=member, program_id=OTHER, kind="context", statement="OTHER-PROGRAM-FACT", asserted_by_kind="agent"
    )
    WorkerFact.objects.create(
        worker=member, program_id=None, kind="language", statement="Prefers Hausa", asserted_by_kind="agent"
    )
    body = briefs.build(here).body
    assert "This program's summary" in body and "Prefers Hausa" in body
    assert "OTHER-PROGRAM-SUMMARY" not in body and "OTHER-PROGRAM-FACT" not in body
    opted = _thread(member, program=PROGRAM, key="coach", context={"other_programs": True})
    assert "OTHER-PROGRAM-SUMMARY" in briefs.build(opted).body


def test_at_most_eight_facts_the_most_relevant_first(member):
    thread = _thread(member)
    for i in range(12):
        WorkerFact.objects.create(
            worker=member,
            program_id=PROGRAM,
            kind="context",
            statement=f"unrelated fact {i}",
            asserted_by_kind="agent",
        )
    WorkerFact.objects.create(
        worker=member,
        program_id=PROGRAM,
        kind="constraint",
        statement="Bicycle broken so visits are slow",
        asserted_by_kind="agent",
    )
    body = briefs.build(thread, inbound_text="my bicycle is still broken").body
    facts = body.split("## Relevant facts\n", 1)[1].split("\n\n", 1)[0].splitlines()
    assert len(facts) == briefs.RELEVANT_FACTS
    assert "Bicycle broken" in facts[0]


def test_only_the_last_six_messages_and_three_summaries(member):
    thread = _thread(member)
    for i in range(10):
        _say(thread, f"msg-{i:02d}")
    for i in range(5):
        ThreadSummary.objects.create(thread=thread, text=f"sum-{i}", author_kind="agent")
    body = briefs.build(thread).body
    assert [f"msg-{i:02d}" in body for i in range(10)] == [False] * 4 + [True] * 6
    assert sum(f"sum-{i}" in body for i in range(5)) == 3


def test_a_handoff_note_is_in_this_thread(member):
    thread = _thread(member)
    body = briefs.build(thread, session_note="Asked about a missing payment").body
    assert "Handed to you with this note: Asked about a missing payment" in body


def test_the_brief_opens_with_lines_an_agent_can_parse(member, settings):
    from connect_labs.messaging import config
    from connect_labs.scope_config.models import ScopeConfig

    ScopeConfig.objects.update_or_create(
        scope_type="program",
        scope_key=str(PROGRAM),
        namespace=config.SETTINGS,
        defaults={"data": {"enabled": True, "channel_name": "Bednet Program"}},
    )
    thread = _thread(member)
    lines = briefs.build(thread).body.splitlines()
    assert lines[2:7] == [
        f"Thread: {thread.pk}",
        f"Program: {PROGRAM} Bednet Program",
        "Playbook: learn-support@1",
        "Skill: learn",
        "Direction: inbound",
    ]


def test_the_programs_own_context_is_its_own_section_after_this_thread(member):
    script = "## Script\nGreet them by first name.\n\n## App\nThe Learn app has 5 modules. " + "x" * 3000
    thread = _thread(member, context={"program": script})
    brief = briefs.build(thread)
    titles = [
        line[3:]
        for line in brief.body.splitlines()
        if line.startswith("## ") and line[3:] in (*briefs.TITLES.values(), briefs.PROGRAM_TITLE)
    ]
    assert titles[:3] == ["Who", "This thread", briefs.PROGRAM_TITLE]
    assert "Greet them by first name." in brief.body and "x" * 3000 in brief.body
    assert brief.sections["program"] > 3000


def test_a_program_context_over_4kb_is_refused_by_the_playbook(member):
    from connect_labs.scope_config.service import Invalid

    with pytest.raises(Invalid):
        _thread(member, key="too-long", context={"program": "x" * 4097})


def test_state_highlights_carry_the_messaging_facets(member):
    thread = _thread(member)
    body = briefs.build(thread).body
    assert "messaging: " in body and "recently_coached False" in body


def test_recently_coached_lets_an_inbound_followup_match(member):
    import datetime as dt

    from django.utils import timezone

    from connect_labs.messaging import router
    from connect_labs.messaging.facets import messaging_facets

    followup = {
        "version": 1,
        "direction": "inbound",
        "handler": {"kind": "agent", "agent": "connect-coach", "skill": "coach-followup"},
        "eligible": {"facets": {"messaging.recently_coached": {"eq": True}}},
    }
    reason, _, _ = router.eligibility(followup, member, PROGRAM)
    assert reason and "messaging.recently_coached" in reason
    coached = _thread(member, key="coach-on-flags")
    coached.close("done", by="agent")
    reason, snapshot, opp = router.eligibility(followup, member, PROGRAM)
    assert reason is None and opp == 9001
    assert snapshot["facets"]["messaging"]["recently_coached"] is True
    # ... for the program's recently_coached_days (default 14), then no longer.
    later = timezone.now() + dt.timedelta(days=15)
    assert messaging_facets(member, PROGRAM, now=later)["recently_coached"] is False
