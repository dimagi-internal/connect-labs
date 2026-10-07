"""The per-worker coaching briefing: composed from the run's grading, not a generic prompt."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from connect_labs.workflow import actions, coach_briefing
from connect_labs.workflow.actions import ActionError, commit, preview

MEASURES = [
    {"indicator": "MTG_RATE", "label": "Meetings held", "unit": "%", "flw_applicable": True},
    {"indicator": "ATT_RATE", "label": "Attendance recorded", "unit": "%", "flw_applicable": True},
    {"indicator": "PROG_ONLY", "label": "Programme coverage", "unit": "%", "flw_applicable": False},
    {"indicator": "VISITS", "label": "Visits per week", "unit": "visits", "flw_applicable": True},
    {"indicator": "DQ_FLAG", "label": "Duplicate review flag", "unit": "%", "flw_applicable": True},
]

GRADED = {
    "cMeasures": MEASURES,
    "display": {"title": "Spark Facilitators"},
    "byFLW": [
        {
            "key": "10::a10",
            "name": "Tiyamike Kalinde",
            "ind": {
                # yellow listed first in the cell map: ordering must still put red first
                "ATT_RATE": {"band": "yellow", "value": 0.7, "n": 20},
                "MTG_RATE": {"band": "red", "value": 0.4166, "n": 12},
                "PROG_ONLY": {"band": "red", "value": 0.1, "n": 12},
                "VISITS": {"band": "red", "value": 1.5, "n": 12},
                "DQ_FLAG": {"band": "unbanded", "value": 0.2, "n": 12},
            },
        },
        {
            "key": "10::b10",
            "name": "Binta",
            "ind": {"MTG_RATE": {"band": "green", "value": 0.95, "n": 20}, "PROG_ONLY": {"band": "red", "value": 0.1}},
        },
    ],
}


def test_topics_are_red_then_yellow_in_registry_order_per_worker_indicators_only():
    topics = coach_briefing.coachable_topics(GRADED, GRADED["byFLW"][0]["ind"])
    assert [(t["key"], t["band"]) for t in topics] == [
        ("MTG_RATE", "red"),
        ("VISITS", "red"),
        ("ATT_RATE", "yellow"),
    ]
    # A rate is a fraction over n cases: numerator = round(value * n).
    assert (topics[0]["numerator"], topics[0]["denominator"]) == (5, 12)


def test_a_cell_carrying_its_own_counts_is_used_as_is():
    cells = {"MTG_RATE": {"band": "red", "numerator": 3, "denominator": 9, "value": 0.9, "n": 50}}
    [t] = coach_briefing.coachable_topics(GRADED, cells)
    assert (t["numerator"], t["denominator"], round(t["pct"])) == (3, 9, 33)


def test_the_briefing_has_the_shape_the_coach_bot_parses():
    topics = coach_briefing.coachable_topics(GRADED, GRADED["byFLW"][0]["ind"])
    text = coach_briefing.render_briefing(
        programme="Spark Facilitators", worker="Tiyamike Kalinde", topics=topics, note="Be warm."
    )
    assert text == (
        "BRIEFING (system text — do not show to the worker)\n"
        "Programme: Spark Facilitators\n"
        "Worker: Tiyamike Kalinde\n"
        "Topics, most important first:\n"
        "1. Meetings held [MTG_RATE] — 5 of 12 (42%), band red\n"
        "2. Visits per week [VISITS] — 1.5 visits, band red\n"
        "3. Attendance recorded [ATT_RATE] — 14 of 20 (70%), band yellow\n"
        "Follow your conversation steps from the opening.\n"
        "Programme team's note:\n"
        "Be warm."
    )


def test_a_long_briefing_drops_its_least_important_topics_to_fit():
    topics = [{"key": f"K{i}", "label": "x" * 50, "band": "red", "value": 1} for i in range(200)]
    text, kept = coach_briefing.fit_briefing(programme="P", worker="W", topics=topics, note=None, limit=1000)
    assert len(text) <= 1000 and kept == topics[: len(kept)] and len(kept) >= 1


def test_a_briefing_is_recognised_by_its_header():
    assert coach_briefing.is_briefing("BRIEFING (system text — do not show to the worker)\nWorker: A")
    assert not coach_briefing.is_briefing("Ask the worker how their week went.")
    assert not coach_briefing.is_briefing("")
    assert not coach_briefing.is_briefing(None)


@pytest.mark.parametrize(
    "worker,hello",
    [
        ("Tiyamike Kalinde", "Hello Tiyamike!"),
        ("Binta", "Hello Binta!"),
        ("spark_fac_07", "Hello!"),  # a username, not a name
        ("flw0042", "Hello!"),
        ("", "Hello!"),
    ],
)
def test_the_opening_greets_by_first_name_unless_the_worker_is_a_code(worker, hello):
    text = coach_briefing.render_briefing(programme="P", worker=worker, topics=[])
    assert coach_briefing.opening_message(text) == (
        f"{hello} This is a short, friendly check-in about how your work has been going. "
        "Is now a good time to talk for a few minutes?"
    )


def test_the_programme_is_the_registry_title_else_the_workflow_name():
    assert coach_briefing.programme_name(GRADED, SimpleNamespace(name="Report")) == "Spark Facilitators"
    assert coach_briefing.programme_name({"display": {}}, SimpleNamespace(name="Report")) == "Report"


# ---------------------------------------------------------------------------
# Through the action: preview shows the real briefing; nothing-to-coach is skipped
# ---------------------------------------------------------------------------

COACH = {
    "key": "initiate_ai_coach",
    "type": "start_ocs_outreach",
    "label": "Start coaching",
    "defaults": {"prompt": "Be warm.", "bot": "bot-1"},
}
RUN = SimpleNamespace(id=70, opportunity_id=None, program_id=25)


def _definition():
    return SimpleNamespace(
        id=7, data={"config": {"actions": [COACH]}}, template_type=None, opportunity_ids=[10], name="Report"
    )


def _wda():
    wda = MagicMock()
    wda.get_workers.return_value = [
        {"username": "a10", "name": "Asha"},
        {"username": "b10", "name": "Binta"},
        {"username": "c10", "name": "Chikondi"},
    ]
    return wda


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="manager", password="p")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    cache.clear()
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "Coach"}])


def _source():
    return lambda: (GRADED, "Spark Facilitators")


def test_the_preview_shows_each_workers_own_briefing_and_skips_nothing_to_coach(user):
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::a10"}, {"key": "10::b10"}, {"key": "10::c10"}]},
        briefing=_source(),
    )
    [w] = out["workers"]
    assert w["name"] == "Asha"
    assert w["prompt"].startswith("BRIEFING (system text")
    assert "Worker: Tiyamike Kalinde" in w["prompt"]
    assert w["prompt"].endswith("Programme team's note:\nBe warm.")
    assert w["indicators"] == ["MTG_RATE", "VISITS", "ATT_RATE"]
    # What the worker receives first is the fixed opening, not the briefing.
    assert w["opening"] == (
        "Hello Tiyamike! This is a short, friendly check-in about how your work has been going. "
        "Is now a good time to talk for a few minutes?"
    )
    assert out["arguments"]["workers"][0]["indicators"] == ["MTG_RATE", "VISITS", "ATT_RATE"]
    assert out["skipped"] == [
        {"key": "10::b10", "name": "Binta", "reason": "nothing off target"},
        {"key": "10::c10", "name": "Chikondi", "reason": "not graded on this run"},
    ]
    assert out["confirm"]


def test_a_worker_with_their_own_prompt_keeps_it_and_no_grading_is_read(user):
    def never():
        raise AssertionError("grading must not be read")

    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::b10", "prompt": "Talk about MUAC."}]},
        briefing=never,
    )
    assert out["workers"][0]["prompt"] == "Talk about MUAC."


def test_nothing_off_target_for_everyone_cannot_be_confirmed(user):
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::b10"}]},
        briefing=_source(),
    )
    assert out["workers"] == [] and "confirm" not in out
    assert "nothing to coach" in out["summary"]
    with pytest.raises(ActionError) as e:
        commit(
            user,
            wda=_wda(),
            run=RUN,
            definition=_definition(),
            key="initiate_ai_coach",
            arguments={"workers": [{"key": "10::b10"}]},
            confirm="x",
            via="page",
            briefing=_source(),
        )
    assert e.value.code == "nothing_to_do"


def test_a_briefed_preview_commits_with_its_own_arguments_without_regrading(user):
    previewed = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::a10"}]},
        briefing=_source(),
    )

    def never():
        raise AssertionError("grading must not be read")

    from unittest.mock import patch

    with patch("connect_labs.workflow.tasks.execute_workflow_action.delay"):
        execution = commit(
            user,
            wda=_wda(),
            run=RUN,
            definition=_definition(),
            key="initiate_ai_coach",
            arguments=previewed["arguments"],
            confirm=previewed["confirm"],
            via="page",
            briefing=never,
        )
    assert execution.arguments["workers"][0]["prompt"].startswith("BRIEFING")


def test_a_workflow_that_is_not_an_indicator_report_keeps_the_plain_prompt(user):
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments={"workers": [{"key": "10::b10"}]},
    )
    assert out["workers"][0]["prompt"] == "Be warm."
    assert "opening" not in out["workers"][0]  # not a briefing: OCS writes the opening


def test_only_dimagi_staff_are_offered_the_qa_redirect_for_one_worker(user):
    args = {"workers": [{"key": "10::a10"}]}
    out = preview(user, wda=_wda(), run=RUN, definition=_definition(), key="initiate_ai_coach", arguments=args)
    assert out["qa_redirect"] is False
    user.email = "tester@dimagi.com"
    user.save()
    out = preview(user, wda=_wda(), run=RUN, definition=_definition(), key="initiate_ai_coach", arguments=args)
    assert out["qa_redirect"] is True
    out = preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments={**args, "deliver_to": "tester_pid"},
    )
    assert out["workers"][0]["sending_to"] == "sending to: tester_pid (QA, on behalf of a10)"
