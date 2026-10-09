"""Coaching a worker about ONE case through ``start_ocs_outreach``: preview, picture,
confirm, the QA send, and what the task records."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model

from connect_labs.workflow import actions
from connect_labs.workflow import case_coaching as cc
from connect_labs.workflow.actions import ActionError, commit, preview
from connect_labs.workflow.coach_charts import store
from connect_labs.workflow.tests.test_case_coaching import danger, steady_gain
from connect_labs.workflow.tests.test_coach_image import RUN, _definition


def _rows(make, opp=10, user="a10", case="baby-1"):
    rows = make()
    for r in rows:
        r.update(opportunity_id=opp, username=user, case_id=case)
    return rows


def _source(rows):
    by_case = {c.case_id: c for c in cc.cases_from_rows(rows, cc.KMC_CASE_COACHING)}
    calls = []

    def cases(worker_key, case_id):
        calls.append((worker_key, case_id))
        found = by_case.get(case_id)
        return found if found is not None and worker_key.endswith("::" + found.username) else None

    cases.programme = "Kangaroo Mother Care"
    cases.calls = calls
    return cases


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="manager", password="p")


@pytest.fixture
def staff(db):
    return get_user_model().objects.create_user(username="qa", password="p", email="qa@dimagi.com")


@pytest.fixture
def env(monkeypatch, settings):
    from django.core.cache import cache

    cache.clear()
    settings.LABS_PUBLIC_URL = "https://labs.connect.dimagi.com"
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "KMC Coach"}])
    source = {"cases": _source(_rows(steady_gain))}
    monkeypatch.setattr(actions, "case_source", lambda *a, **kw: source["cases"])
    return source


def _wda():
    wda = MagicMock()
    wda.get_workers.return_value = [{"username": "a10", "name": "Asha Banda"}, {"username": "b10", "name": "Binta"}]
    return wda


def _preview(user, arguments):
    return preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments=arguments,
        briefing=lambda: None,
    )


def test_a_case_preview_carries_the_case_briefing_its_picture_and_the_story_as_topic(user, env):
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    [w] = out["workers"]
    assert w["prompt"].startswith(
        "BRIEFING (system text — do not show to the worker)\nProgramme: Kangaroo Mother Care\n"
    )
    assert "Topic: Baby is growing well [CASE_THRIVING]" in w["prompt"]
    assert w["indicators"] == [cc.THRIVING]
    assert w["case"]["story"] == cc.THRIVING and w["case"]["case"] == "KMC Demo — Steady Gain"
    assert w["opening"].startswith("Hello Asha!")
    chart = store.get(w["image"]["chart"]["id"]).chart
    assert chart["type"] == "case_thriving"
    assert w["image"]["caption"] == chart["caption"] and not any(ch.isdigit() for ch in chart["caption"])
    assert out["arguments"]["workers"][0]["case"]["story"] == cc.THRIVING
    assert out["confirm"]


def test_a_story_the_visits_do_not_support_is_refused(user, env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1", "story": cc.DANGER}}]})
    assert "supports: CASE_THRIVING" in e.value.public_message


def test_another_workers_case_is_refused(user, env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::b10", "case": {"id": "baby-1"}}]})
    assert "no case 'baby-1' among Binta's cases" in e.value.public_message


def test_a_case_item_with_its_own_prompt_is_refused(user, env):
    with pytest.raises(ActionError):
        _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}, "prompt": "talk about it"}]})


def test_the_earlier_line_rides_on_the_case(user, env):
    earlier = {"date": "2026-06-01", "label": "CASE_THRIVING", "agreed": "keep visiting weekly"}
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1", "earlier": earlier}}]})
    assert (
        "Earlier coaching on this case: 1 Jun 2026 — Baby is growing well; agreed: keep visiting weekly"
        in out["workers"][0]["prompt"]
    )


def test_a_qa_send_commits_and_records_the_case_on_the_task(staff, env, django_capture_on_commit_callbacks):
    with patch("connect_labs.utils.dimagi_user.is_dimagi_user", return_value=True):
        out = preview(
            staff,
            wda=_wda(),
            run=RUN,
            definition=_definition(),
            key="initiate_ai_coach",
            arguments={"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}], "deliver_to": "qa_phone"},
            briefing=lambda: None,
        )
        calls_before = len(env["cases"].calls)
        with django_capture_on_commit_callbacks():
            execution = commit(
                staff,
                wda=_wda(),
                run=RUN,
                definition=_definition(),
                key="initiate_ai_coach",
                arguments=out["arguments"],
                confirm=out["confirm"],
                via="page",
                briefing=lambda: None,
            )
    # The commit re-reads nothing: the preview's briefing and frozen chart are what is sent.
    assert len(env["cases"].calls) == calls_before
    assert execution.arguments["charts"] == out["arguments"]["charts"]

    task = SimpleNamespace(id=5, data={})
    tda = MagicMock()
    tda.create_task.return_value = task
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", return_value=tda),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess"),
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "s"}) as start,
    ):
        actions.execute(execution.pk)
    kwargs = start.call_args.kwargs
    assert kwargs["identifier"] == "qa_phone" and kwargs["on_behalf_of"] == "a10"
    assert cc.is_case_briefing(kwargs["prompt_text"])
    assert kwargs["coach_image"]["caption"].startswith("A growth chart of this baby")
    assert task.data["coaching_indicators"] == [cc.THRIVING]
    record = task.data["case_coaching"]
    assert (record["case_id"], record["story"], record["case_name"], record["qa_test"]) == (
        "baby-1",
        cc.THRIVING,
        "KMC Demo — Steady Gain",
        True,
    )


def test_a_danger_case_draws_the_danger_card(user, env):
    env["cases"] = _source(_rows(danger))
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    chart = store.get(out["workers"][0]["image"]["chart"]["id"]).chart
    assert chart["type"] == "case_danger_sign"
    texts = [r.get("text") for r in chart["datasets"]["case_text"]]
    assert "NOT REFERRED" in texts and "Fast breathing" in texts
