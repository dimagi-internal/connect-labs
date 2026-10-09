"""Coaching a worker about ONE case through ``start_ocs_outreach``: the case briefing
(a contract with the coach bot), its picture, the refusals, the QA send, and what the
task records. The case's states are the KMC registry's (``semantic/registry/kmc``)."""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml
from django.contrib.auth import get_user_model

from connect_labs.semantic import case_states as cs
from connect_labs.workflow import actions
from connect_labs.workflow import case_briefing as cb
from connect_labs.workflow.actions import ActionError, commit, preview
from connect_labs.workflow.coach_charts import case_chart, case_types, render, store
from connect_labs.workflow.coach_charts import types as chart_types
from connect_labs.workflow.tests.test_coach_image import RUN, _definition

PROPS = yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "semantic" / "registry" / "kmc" / "properties.yml").read_text()
)
CATALOG = cs.catalog(PROPS)
DANGER = "case_state_danger_unreferred"
THRIVING = "case_state_thriving"


def _danger_row(**over):
    return {
        "entity_id": "baby-1",
        "opportunity_id": 10,
        "username": "a10",
        "case_name": "Beneficiary 410",
        "birth_weight_g": 1250,
        "reg_date": "2026-05-21",
        "num_visits": 3,
        "last_visit": "2026-06-05",
        "weight_series_believable": True,
        DANGER: True,
        "case_state_weight_check": False,
        "case_state_faltering": False,
        THRIVING: False,
        "unreferred_danger_date": "2026-06-05",
        "unreferred_danger_signs": "fast breathing, pus in the eyes, skin or belly button",
        "unreferred_danger_visits": 1,
        **over,
    }


VISITS = [
    {
        "visit_date": "2026-05-21",
        "weight": 1500.0,
        "skin_to_skin": None,
        "danger_signs": None,
        "referred": "yes",
        "feeds": 26.0,
    },
    {
        "visit_date": "2026-05-28",
        "weight": 1600.0,
        "skin_to_skin": 18.0,
        "danger_signs": None,
        "referred": "no",
        "feeds": 24.0,
        "timeliness": "Late",
        "danger_check": "no",
    },
    {
        "visit_date": "2026-06-05",
        "weight": 1700.0,
        "skin_to_skin": 20.0,
        "danger_signs": "fast breathing, pus in the eyes, skin or belly button",
        "referred": "no",
        "feeds": 22.0,
        "timeliness": "On-time",
        "danger_check": "yes",
    },
]


class FakeCases:
    """A ``case_briefing.CaseSource`` over fixed rows and the KMC registry."""

    def __init__(self, rows, catalog=None):
        self.rows, self._catalog, self.calls = rows, catalog if catalog is not None else CATALOG, []

    def case(self, opp, entity_id):
        self.calls.append(("case", opp, entity_id))
        return next((r for r in self.rows if r["entity_id"] == entity_id and r["opportunity_id"] == opp), None)

    def catalog(self, opp):
        return self._catalog

    def props_doc(self, opp):
        return PROPS

    def label_field(self, opp):
        return "case_name"

    def case_name(self, opp, row, fallback=""):
        from connect_labs.workflow.case_briefing import CaseSource

        return CaseSource.case_name(self, opp, row, fallback)

    def programme(self, opp):
        return "Kangaroo Mother Care"

    def visits(self, opp, entity_id):
        self.calls.append(("visits", opp, entity_id))
        return VISITS

    def series(self, opp):
        return cs.case_series(PROPS)


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
    source = {"cases": FakeCases([_danger_row()])}
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


def test_the_case_briefing_shape_is_the_contract():
    state = {
        "name": "case_state_x",
        "label": "A thing to talk about",
        "means": "What the state means.",
        "coach": {"approach": "Ask first.", "next_steps": "Agree one step.", "limits": "It cannot say why."},
    }
    text = cb.render_case_briefing(
        programme="P",
        worker="Asha",
        case_name="Baby 1",
        about="Birth weight 1,250 g.",
        state=state,
        facts="Something happened.",
        visit_lines=["- 1 Jun 2026: weight 1,500 g", "- 8 Jun 2026: weight 1,600 g"],
        earlier="Earlier coaching on this case: 1 Jun 2026 — A thing to talk about; agreed: none",
    )
    assert text == (
        "BRIEFING (system text — do not show to the worker)\n"
        "Programme: P\n"
        "Worker: Asha\n"
        "Case: Baby 1\n"
        "About this case: Birth weight 1,250 g.\n"
        "Topic: A thing to talk about [case_state_x]\n"
        "What it means: What the state means.\n"
        "How to talk about it: Ask first.\n"
        "The step to agree: Agree one step.\n"
        "What it does not tell you: It cannot say why.\n"
        "What the data shows: Something happened.\n"
        "Earlier coaching on this case: 1 Jun 2026 — A thing to talk about; agreed: none\n"
        "Visits, oldest first:\n"
        "- 1 Jun 2026: weight 1,500 g\n"
        "- 8 Jun 2026: weight 1,600 g\n"
        "Follow your conversation steps from the opening."
    )
    assert cb.is_case_briefing(text)
    summary = cb.case_briefing_summary(text)
    assert (summary["topic"], summary["case_state"], summary["case"]) == (
        "A thing to talk about",
        "case_state_x",
        "Baby 1",
    )


def test_a_case_preview_carries_the_registry_briefing_and_the_danger_card(user, env):
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    [w] = out["workers"]
    danger = next(s for s in CATALOG if s["name"] == DANGER)
    assert w["prompt"] == (
        "BRIEFING (system text — do not show to the worker)\n"
        "Programme: Kangaroo Mother Care\n"
        "Worker: Asha Banda\n"
        "Case: Beneficiary 410\n"
        "About this case: Birth weight 1,250 g; registered 21 May 2026; 3 visits, the last on 5 Jun 2026.\n"
        "Topic: Danger sign recorded, no referral [case_state_danger_unreferred]\n"
        f"What it means: {danger['means']}\n"
        f"How to talk about it: {danger['coach']['approach'].strip()}\n"
        f"The step to agree: {danger['coach']['next_steps'].strip()}\n"
        f"What it does not tell you: {danger['coach']['limits'].strip()}\n"
        "What the data shows: On 5 Jun 2026 the visit recorded fast breathing, pus in the eyes, skin or belly "
        "button, and the baby was not referred.\n"
        "Visits, oldest first:\n"
        "- 21 May 2026: weight 1,500 g; skin-to-skin not recorded; danger signs: none; referred: yes; "
        "26 successful feeds in the last 24 h\n"
        "- 28 May 2026: weight 1,600 g; skin-to-skin 18 h in the last 24 h; danger signs: none; referred: no; "
        "24 successful feeds in the last 24 h\n"
        "- 5 Jun 2026: weight 1,700 g; skin-to-skin 20 h in the last 24 h; danger signs: fast breathing, pus in "
        "the eyes, skin or belly button; referred: no; 22 successful feeds in the last 24 h\n"
        "Follow your conversation steps from the opening."
    )
    assert w["indicators"] == [DANGER]
    assert w["case"]["case_state"] == DANGER and w["case"]["case"] == "Beneficiary 410"
    assert w["opening"].startswith("Hello Asha!")
    chart = store.get(w["image"]["chart"]["id"]).chart
    assert chart["type"] == "case_summary" and chart["params"] == {"case_state": DANGER}
    text = " ".join(r.get("text") or "" for r in chart["datasets"]["case_text"])  # lines wrap
    assert "Fast breathing" in text and "Pus in eyes, skin or belly button" in text
    assert w["image"]["caption"] == chart["caption"]
    assert out["arguments"]["workers"][0]["case"]["case_state"] == DANGER
    assert out["confirm"]


def test_a_named_case_is_briefed_and_pictured_by_its_names(user, env):
    """The registry's case_name: the mother's and baby's names when the visits record
    them -- in the briefing's Case: line and on the picture -- not "Beneficiary 410"."""
    env["cases"] = FakeCases([_danger_row(child_name="Amina", mother_name="Hauwa Musa")])
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    [w] = out["workers"]
    assert "\nCase: Baby Amina · mother Hauwa Musa\n" in w["prompt"]
    assert w["case"]["case"] == "Baby Amina · mother Hauwa Musa"
    chart = store.get(w["image"]["chart"]["id"]).chart
    texts = [r.get("text") for r in chart["datasets"]["case_text"]]
    assert "Baby Amina · mother Hauwa Musa" in texts


def test_a_case_state_the_case_is_not_in_is_refused(user, env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1", "case_state": THRIVING}}]})
    assert "not in case state case_state_thriving" in e.value.public_message
    assert "(it is in: case_state_danger_unreferred)" in e.value.public_message


def test_another_workers_case_is_refused(user, env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::b10", "case": {"id": "baby-1"}}]})
    assert "no case 'baby-1' among Binta's cases" in e.value.public_message


def test_a_case_state_without_coach_guidance_is_refused(user, env):
    bare = [{**s, "coach": {**s["coach"], "limits": ""}} for s in CATALOG]
    env["cases"] = FakeCases([_danger_row()], catalog=bare)
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    assert "has no case_state.coach.limits in the registry" in e.value.public_message


def test_a_case_item_with_its_own_prompt_is_refused(user, env):
    with pytest.raises(ActionError):
        _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}, "prompt": "talk about it"}]})


def test_the_earlier_line_comes_after_the_facts_when_given(user, env):
    earlier = {"date": "2026-05-29", "label": DANGER, "agreed": "take the baby to the clinic"}
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1", "earlier": earlier}}]})
    lines = out["workers"][0]["prompt"].splitlines()
    i = lines.index(
        "Earlier coaching on this case: 29 May 2026 — Danger sign recorded, no referral; "
        "agreed: take the baby to the clinic"
    )
    assert lines[i - 1].startswith("What the data shows: ") and lines[i + 1] == "Visits, oldest first:"


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
    assert cb.is_case_briefing(kwargs["prompt_text"])
    assert kwargs["coach_image"]["caption"]
    assert task.data["coaching_indicators"] == [DANGER]
    record = task.data["case_coaching"]
    assert (record["case_id"], record["case_state"], record["case_name"], record["qa_test"]) == (
        "baby-1",
        DANGER,
        "Beneficiary 410",
        True,
    )


# ---------------------------------------------------------------------------
# Each picture type draws from a registry case state
# ---------------------------------------------------------------------------

WEIGHTS = [
    {"visit_date": d, "weight": w, "skin_to_skin": h}
    for d, w, h in [
        ("2026-05-18", 1350, None),
        ("2026-05-25", 1635, 20),
        ("2026-06-01", 1915, 16),
        ("2026-06-08", 1920, 8),
    ]
]
ROW = {
    "case_name": "Baby",
    "check_from_date": "2026-06-01",
    "check_to_date": "2026-06-08",
    "step_change_g": 5,
    "step_days": 7,
    "first_weigh_date": "2026-05-18",
    "last_weigh_date": "2026-06-08",
    "first_weight_g": 1350,
    "last_weight_g": 1920,
    "weight_gain_g": 570,
    "third_last_w": 1635,
    "third_last_date": "2026-05-25",
    "faltering_rate": 4.1,
}


@pytest.mark.parametrize("name", [THRIVING, "case_state_weight_check", "case_state_faltering"])
def test_each_series_case_summary_draws_with_no_numbers_in_its_caption(name):
    state = next(s for s in CATALOG if s["name"] == name)
    chart = case_chart.build_case_chart(state, ROW, visits=WEIGHTS, series=cs.case_series(PROPS), case_name="Baby")
    assert chart["type"] == "case_summary"
    weights = [p["value"] for p in chart["datasets"]["case_points"] if p["series"] == "weight"]
    assert weights[:1] == [1350]
    assert chart["caption"] and not any(ch.isdigit() for ch in chart["caption"])
    if name == "case_state_faltering":
        assert [p["value"] for p in chart["datasets"]["case_points"] if p["series"] == "skin_to_skin"] == [20, 16, 8]


def test_a_picture_naming_another_worker_is_refused():
    from connect_labs.workflow.coach_charts.datasets import ChartError

    state = next(s for s in CATALOG if s["name"] == THRIVING)
    with pytest.raises(ChartError):
        case_chart.build_case_chart(
            state,
            ROW,
            visits=WEIGHTS,
            series=cs.case_series(PROPS),
            case_name="Binta's baby",
            others=[("Binta", "b10")],
        )


# ---------------------------------------------------------------------------
# Every coach picture is landscape
# ---------------------------------------------------------------------------

#: A drawable example of every picture type: case pictures from a registry case state,
#: worker charts from a run's grading (test_coach_chart_data).
#: The KMC registry's states are all case summaries; the older single-chart types stay
#: drawable for registries (and frozen charts) that name them.
SIGN_CARD = {
    "type": "sign_card",
    "title": "Danger sign, not referred",
    "badge": "NOT REFERRED",
    "date": "unreferred_danger_date",
    "signs": "unreferred_danger_signs",
    "actions_title": "What to do",
    "actions": ["Visit the family today"],
}
CASE_PICTURES = {
    "case_summary": (DANGER, _danger_row(), VISITS, None),
    "series_vs_reference": (
        THRIVING,
        ROW,
        WEIGHTS,
        {
            "type": "series_vs_reference",
            "series": "weight",
            "title": "Growing well",
            "badge": "Great work!",
            "reference": {"low": 15, "high": 20, "label": "healthy growth"},
        },
    ),
    "series_highlight_step": (
        "case_state_weight_check",
        ROW,
        WEIGHTS,
        {
            "type": "series_highlight_step",
            "series": "weight",
            "title": "Check this weighing",
            "highlight": {"from": "check_from_date", "to": "check_to_date", "label": "{step_change_g|signed} g?"},
            "checklist_title": "Weighing checklist",
            "checklist": ["Set the scale to zero"],
        },
    ),
    "series_with_bars": (
        "case_state_faltering",
        ROW,
        WEIGHTS,
        {
            "type": "series_with_bars",
            "series": "weight",
            "title": "Weight has stalled",
            "reference": {"low": 15, "high": 20, "label": "healthy growth"},
            "bars": "skin_to_skin",
            "bars_title": "Skin-to-skin hours each visit",
        },
    ),
    "sign_card": (DANGER, _danger_row(), VISITS, SIGN_CARD),
}
WORKER_PICTURES = {
    "topic_bars": {"type": "topic_bars"},
    "peer_comparison": {"type": "peer_comparison", "params": {"topics": ["X1", "X2"]}},
    "trend": {"type": "trend", "params": {"peers": True}},
}


@pytest.mark.parametrize("kind", sorted(set(case_types.CASE_TYPES) | set(chart_types.TYPES)))
def test_every_coach_picture_is_landscape(kind):
    """Connect's messenger caps a picture's height at half the message list and sizes
    the bubble to the picture's width, so a portrait picture shrinks the whole bubble on
    a phone (#2413). Every type -- a new one included: it needs an example above --
    draws wider than tall, 3:2 or (the case summary) 4:3."""
    import io

    from django.core.cache import cache
    from PIL import Image

    from connect_labs.workflow.coach_charts import chart as charts
    from connect_labs.workflow.tests.test_coach_chart_data import _build

    cache.clear()
    if kind in CASE_PICTURES:
        name, row, visits, picture = CASE_PICTURES[kind]
        state = next(s for s in CATALOG if s["name"] == name)
        state = {**state, "picture": picture} if picture else state
        drawn = case_chart.build_case_chart(state, row, visits=visits, series=cs.case_series(PROPS), case_name="Baby")
    else:
        drawn = _build(WORKER_PICTURES[kind])
    assert drawn["type"] == kind
    width, height = Image.open(io.BytesIO(charts.png(drawn))).size
    assert width > height and 1.3 <= width / height <= 1.8, (kind, width, height)
    # Laid out to fill its frame, not a smaller drawing padded out to it: 3:2, or 4:3
    # for the case summary's banner and four panels.
    frame = (1200, 900) if kind == "case_summary" else (render.PNG_WIDTH, render.PNG_HEIGHT)
    assert (width, height) == frame, kind
