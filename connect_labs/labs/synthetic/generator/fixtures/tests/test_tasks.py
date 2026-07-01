import datetime as dt

from connect_labs.labs.synthetic.generator.fixtures.manifest import CoachingArc, CoachingMessage, TaskSpec, Timeline
from connect_labs.labs.synthetic.generator.fixtures.tasks import build_task_records


def _make_timeline() -> Timeline:
    return Timeline(
        start_date=dt.date(2026, 1, 1),
        end_date=dt.date(2026, 1, 29),
        weeks=4,
        visit_cadence_per_week_per_flw={"mean": 10, "stddev": 2},
    )


def test_build_task_records_basic():
    tasks = [TaskSpec(flw_id="flw_001", title="Follow up", priority="high", status="completed", created_week=2)]
    records = build_task_records(
        opportunity_id=814,
        tasks=tasks,
        coaching_arcs=[],
        timeline=_make_timeline(),
        persona_names={"flw_001": "Amina Y."},
    )
    assert len(records) == 1
    r = records[0]
    assert r["assigned_to"] == "flw_001"
    assert r["title"] == "Follow up"
    assert r["priority"] == "high"
    assert r["status"] == "completed"
    created = dt.datetime.fromisoformat(r["created_at"])
    assert created >= dt.datetime(2026, 1, 8)
    assert created < dt.datetime(2026, 1, 15)


def test_build_task_records_with_coaching_arc():
    arcs = [
        CoachingArc(
            flw_id="flw_014",
            week_triggered=3,
            persona="high_flag_rate",
            target_behavior="Improve MUAC technique",
            transcript=[],
        )
    ]
    records = build_task_records(
        opportunity_id=814,
        tasks=[],
        coaching_arcs=arcs,
        timeline=_make_timeline(),
        persona_names={"flw_014": "Nuhu D."},
    )
    assert len(records) == 1
    r = records[0]
    # Tasks schema uses `username` (Connect's canonical FLW identifier) — the
    # previous `assigned_to` field was a holdover from the synthetic-only
    # shape that the Tasks UI couldn't render.
    assert r["username"] == "flw_014"
    assert r["flw_name"] == "Nuhu D."
    assert r["status"] == "completed"
    assert "ocs_conversation" in r
    assert len(r["ocs_conversation"]) >= 3
    assert any("Nuhu" in msg["text"] for msg in r["ocs_conversation"])
    # Events match the Task schema (created + resolved entries).
    assert any(e["event_type"] == "created" for e in r["events"])
    assert any(e["event_type"] == "resolved" for e in r["events"])


def test_build_task_records_explicit_transcript():
    arcs = [
        CoachingArc(
            flw_id="flw_001",
            week_triggered=2,
            persona="custom",
            target_behavior="Custom",
            transcript=[
                CoachingMessage(role="bot", text="Hello", ts=dt.datetime(2026, 1, 10, 9, 0)),
                CoachingMessage(role="flw", text="Hi", ts=dt.datetime(2026, 1, 10, 9, 1)),
            ],
        )
    ]
    records = build_task_records(
        opportunity_id=814,
        tasks=[],
        coaching_arcs=arcs,
        timeline=_make_timeline(),
        persona_names={"flw_001": "Test"},
    )
    assert len(records) == 1
    assert len(records[0]["ocs_conversation"]) == 2
    assert records[0]["ocs_conversation"][0]["text"] == "Hello"
