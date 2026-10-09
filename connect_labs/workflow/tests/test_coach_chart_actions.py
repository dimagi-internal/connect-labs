"""Coaching charts through the action: `picture` previewed, frozen, confirmed and sent."""

import json

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.workflow import actions, coach_image
from connect_labs.workflow.actions import ActionError, commit, preview
from connect_labs.workflow.coach_charts import store
from connect_labs.workflow.tests.test_coach_image import RUN, _definition, _sent

# Asha is off target on two topics; Binta, on the same run, is her peer.
GRADED = {
    "cMeasures": [
        {"indicator": "MTG_RATE", "label": "Meetings held", "unit": "%"},
        {"indicator": "ATT_RATE", "label": "Attendance recorded", "unit": "%"},
    ],
    "byFLW": [
        {
            "key": "10::a10",
            "name": "Asha Banda",
            "ind": {
                "MTG_RATE": {"band": "red", "value": 0.4166, "n": 12},
                "ATT_RATE": {"band": "yellow", "value": 0.7, "n": 20},
            },
        },
        {
            "key": "10::b10",
            "name": "Binta Phiri",
            "ind": {
                "MTG_RATE": {"band": "green", "value": 0.9, "n": 10},
                "ATT_RATE": {"band": "green", "value": 0.95, "n": 20},
            },
        },
    ],
}
NAMES = ["Binta", "Phiri", "b10"]


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="manager", password="p")


@pytest.fixture
def _env(monkeypatch, settings):
    from django.core.cache import cache

    cache.clear()
    settings.LABS_PUBLIC_URL = "https://labs.connect.dimagi.com"
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "Coach"}])


def _wda():
    from unittest.mock import MagicMock

    wda = MagicMock()
    wda.get_workers.return_value = [
        {"username": "a10", "name": "Asha Banda"},
        {"username": "b10", "name": "Binta Phiri"},
    ]
    wda.list_runs.return_value = []
    return wda


def _preview(user, arguments, **kw):
    return preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments=arguments,
        briefing=lambda: (GRADED, "Spark"),
        **kw,
    )


def _commit(user, arguments, confirm):
    return commit(
        user,
        wda=_wda(),
        run=RUN,
        definition=_definition(),
        key="initiate_ai_coach",
        arguments=arguments,
        confirm=confirm,
        via="mcp",
        briefing=lambda: (GRADED, "Spark"),
    )


COMPARE = {"type": "peer_comparison"}


def test_a_peer_comparison_is_previewed_with_anonymous_peers(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    [w] = out["workers"]
    assert w["image"]["chart"]["type"] == "peer_comparison"
    assert "Peer A" in w["image"]["caption"]
    chart = store.get(w["image"]["chart"]["id"]).chart
    assert {r["who"] for r in chart["datasets"]["peers"]} == {"Peer A"}
    # Nowhere a worker could see -- the chart, its caption and alt text, the opening --
    # names the peer.
    seen = json.dumps(chart) + w["image"]["caption"] + w["opening"]
    assert [n for n in NAMES if n in seen] == []
    assert out["arguments"]["include_image"] is True  # a picture implies it


def test_the_conversation_gets_the_frozen_chart_and_labs_caption(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    [sent] = _sent(user, out["arguments"])
    token = sent["coach_image"]["url"].rstrip("/").rsplit("/", 1)[1]
    assert coach_image.unsign(token)["chart"] == out["workers"][0]["image"]["chart"]["id"]
    assert sent["coach_image"]["caption"] == out["workers"][0]["image"]["caption"]
    assert [n for n in NAMES if n in sent["coach_image"]["caption"]] == []


def test_the_confirm_covers_the_picture(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    swapped = {**out["arguments"], "picture": {"type": "topic_bars"}}
    with pytest.raises(ActionError) as e:
        _commit(user, swapped, out["confirm"])
    assert e.value.code == "confirm_mismatch"


def test_a_commit_of_the_previews_arguments_reuses_its_chart(user, _env, django_capture_on_commit_callbacks):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    with django_capture_on_commit_callbacks():
        execution = _commit(user, out["arguments"], out["confirm"])
    assert execution.arguments["charts"] == out["arguments"]["charts"]


def test_someone_elses_chart_is_never_reused(user, _env):
    other = get_user_model().objects.create_user(username="other", password="p")
    theirs = _preview(other, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    mine = _preview(
        user, {"workers": [{"key": "10::a10"}], "picture": COMPARE, "charts": theirs["arguments"]["charts"]}
    )
    assert mine["arguments"]["charts"] != theirs["arguments"]["charts"]


def test_a_forged_chart_reference_is_ignored(user, _env, django_capture_on_commit_callbacks):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    forged = {**out["arguments"], "charts": {"10::a10": "0" * 32}}
    # Not a chart this person previewed: Labs draws the chart again from the run, so
    # what is sent is Labs' chart -- here the very one that was previewed.
    with django_capture_on_commit_callbacks():
        execution = _commit(user, forged, out["confirm"])
    assert execution.arguments["charts"] == out["arguments"]["charts"]


def test_inline_data_in_a_custom_picture_is_stripped_and_reported(user, _env):
    spec = {
        "data": {"values": [{"value": 0.99, "label": "Meetings held"}]},
        "layer": [
            {
                "data": {"name": "worker_topics"},
                "mark": "bar",
                "encoding": {
                    "x": {"field": "value", "type": "quantitative"},
                    "y": {"field": "label", "type": "nominal"},
                },
            }
        ],
    }
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": {"type": "custom", "spec": spec}})
    image = out["workers"][0]["image"]
    chart = store.get(image["chart"]["id"]).chart
    assert "0.99" not in json.dumps(chart["spec"])
    assert any("data" in note for note in image["chart"]["notes"])


def test_a_custom_picture_naming_a_peer_is_refused(user, _env):
    spec = {"title": "You and Binta", "data": {"name": "worker_topics"}, "mark": "bar"}
    with pytest.raises(ActionError, match="Peer A"):
        _preview(user, {"workers": [{"key": "10::a10"}], "picture": {"type": "custom", "spec": spec}})


def test_a_note_naming_a_peer_is_refused(user, _env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::a10", "prompt": "Ask her to shadow Binta Phiri next week."}]})
    assert e.value.code == "peer_identity"


def test_a_trend_is_not_drawn_for_a_restricted_caller(user, _env):
    with pytest.raises(ActionError, match="history"):
        _preview(user, {"workers": [{"key": "10::a10"}], "picture": {"type": "trend"}}, restricted=True)


def test_an_unknown_picture_type_is_refused(user, _env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::a10"}], "picture": {"type": "pie"}})
    assert e.value.code == "invalid"


def test_a_workflow_may_default_its_picture_but_never_its_charts():
    entry = _definition({"picture": {"type": "peer_comparison"}}).data["config"]["actions"][0]
    assert actions.declaration_problems([entry]) == []
    bad = _definition({"charts": {"10::a10": "x" * 32}}).data["config"]["actions"][0]
    assert any("charts" in p for p in actions.declaration_problems([bad]))


# The link ---------------------------------------------------------------------


def test_a_chart_link_draws_the_stored_chart_for_the_coaching_token(client, user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "picture": COMPARE})
    [sent] = _sent(user, out["arguments"])
    path = sent["coach_image"]["url"].split("labs.connect.dimagi.com", 1)[1]
    _, raw = MCPAccessToken.create_token(user, name="ocs", scope=token_scopes.COACH_IMAGES)
    resp = client.get(path, HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert resp.status_code == 200 and resp["Content-Type"] == "image/png"


def test_a_link_to_a_chart_that_is_gone_is_a_404(client, user, db):
    _, raw = MCPAccessToken.create_token(user, name="ocs", scope=token_scopes.COACH_IMAGES)
    url = reverse("labs:coach_image", args=[coach_image.sign({"chart": "f" * 32, "opportunity_id": 10})])
    assert client.get(url, HTTP_AUTHORIZATION=f"Bearer {raw}").status_code == 404


def test_a_legacy_link_still_draws(client, user, db):
    _, raw = MCPAccessToken.create_token(user, name="ocs", scope=token_scopes.COACH_IMAGES)
    legacy = {
        "worker": "Asha Banda",
        "topics": [{"label": "Meetings held", "band": "red", "numerator": 5, "denominator": 12}],
    }
    resp = client.get(reverse("labs:coach_image", args=[coach_image.sign(legacy)]), HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert resp.status_code == 200 and resp["Content-Type"] == "image/png"
