"""The coaching picture: a signed link to a worker's own figures, drawn when fetched."""

import io
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.cache import cache
from django.urls import reverse
from PIL import Image

from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.workflow import actions, coach_briefing, coach_image
from connect_labs.workflow.actions import ActionError, preview
from connect_labs.workflow.coach_charts import theme

TOPICS = [
    {"key": "MTG_RATE", "label": "Meetings held", "band": "red", "unit": "%", "numerator": 5, "denominator": 12,
     "pct": 41.66},
    {"key": "ATT_RATE", "label": "Attendance recorded", "band": "yellow", "unit": "%", "numerator": 14,
     "denominator": 20, "pct": 70.0},
]  # fmt: skip


def _payload(n=1, band="red"):
    topics = [
        {"label": f"Indicator number {i} with a fairly long label that needs wrapping", "band": band}
        | {"numerator": i, "denominator": 10, "pct": 10 * i}
        for i in range(1, n + 1)
    ]
    return {"worker": "Tiyamike Kalinde", "topics": topics}


# ---------------------------------------------------------------------------
# The payload: exactly the briefing's topics
# ---------------------------------------------------------------------------


def test_the_payload_carries_the_name_and_each_topics_figures():
    assert coach_image.build_image_payload("Tiyamike Kalinde", TOPICS) == {
        "worker": "Tiyamike Kalinde",
        "topics": [
            {"label": "Meetings held", "band": "red", "numerator": 5, "denominator": 12, "pct": 42},
            {"label": "Attendance recorded", "band": "yellow", "numerator": 14, "denominator": 20, "pct": 70},
        ],
    }


def test_reading_a_briefing_back_gives_the_same_payload_as_its_topics():
    text = coach_briefing.render_briefing(
        programme="Spark", worker="Tiyamike Kalinde", topics=TOPICS, note="1. Not a topic [X] — 1 of 2, band red"
    )
    assert coach_image.payload_from_briefing(text) == coach_image.build_image_payload("Tiyamike Kalinde", TOPICS)


def test_a_trimmed_briefing_pictures_only_the_topics_it_kept():
    many = [{"key": f"IND_{i}", "label": "x" * 60, "band": "red", "numerator": i, "denominator": 9} for i in range(40)]
    text, kept = coach_briefing.fit_briefing(programme="P", worker="W", topics=many, note=None, limit=800)
    assert 1 < len(kept) < len(many)
    assert len(coach_image.payload_from_briefing(text)["topics"]) == len(kept)


def test_a_figure_that_is_not_a_count_keeps_its_words():
    topic = {"key": "VISITS", "label": "Visits per week", "band": "red", "unit": "visits", "value": 1.5}
    text = coach_briefing.render_briefing(programme="P", worker="W", topics=[topic])
    [t] = coach_image.payload_from_briefing(text)["topics"]
    assert t == {"label": "Visits per week", "band": "red", "figure": "1.5 visits"}


def test_free_text_is_not_pictured():
    assert coach_image.payload_from_briefing("Talk with them about their week.") is None


def test_the_caption_names_the_topics_and_no_numbers():
    payload = coach_image.build_image_payload("Tiyamike Kalinde", TOPICS)
    assert coach_image.caption(payload) == (
        "A bar chart of Tiyamike's figures for: Meetings held; Attendance recorded."
    )
    assert coach_image.caption({**payload, "worker": "spark_fac_07"}).startswith("A bar chart of the worker's")


# ---------------------------------------------------------------------------
# The link
# ---------------------------------------------------------------------------


def test_a_link_round_trips_and_is_absolute_on_the_public_origin(settings):
    settings.LABS_PUBLIC_URL = "https://labs.connect.dimagi.com/"
    payload = _payload(2)
    url = coach_image.image_url(payload)
    assert url.startswith("https://labs.connect.dimagi.com/labs/coach-image/")
    token = url.rstrip("/").rsplit("/", 1)[1]
    assert coach_image.unsign(token) == payload


def test_no_public_origin_means_no_link(settings):
    settings.LABS_PUBLIC_URL = ""
    assert coach_image.image_url(_payload()) is None


def test_an_altered_or_foreign_link_is_refused():
    token = coach_image.sign(_payload())
    with pytest.raises(coach_image.BadImageLink):
        coach_image.unsign(token[:-2] + ("AA" if not token.endswith("AA") else "BB"))
    with pytest.raises(coach_image.BadImageLink):
        coach_image.unsign(signing.dumps(_payload(), salt="something-else"))
    with pytest.raises(coach_image.BadImageLink):
        coach_image.unsign(coach_image.sign({"worker": "x", "topics": []}))


def test_a_link_expires_after_a_week():
    token = coach_image.sign(_payload())
    with patch("django.core.signing.time.time", return_value=__import__("time").time() + 8 * 24 * 3600):
        with pytest.raises(coach_image.BadImageLink):
            coach_image.unsign(token)


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _size(payload):
    return Image.open(io.BytesIO(coach_image.render_png(payload))).size


def _short(n):
    """``n`` topics with one-line labels, as a real briefing usually has."""
    return {
        "worker": "Tiyamike Kalinde",
        "topics": [{"label": "Meetings held", "band": "red", "numerator": 31, "denominator": 73, "pct": 42}] * n,
    }


@pytest.mark.parametrize("n", [1, 4])
def test_one_and_four_topics_draw_a_small_png(n):
    data = coach_image.render_png(_payload(n))
    image = Image.open(io.BytesIO(data))
    assert image.format == "PNG"
    assert image.size[0] == coach_image.WIDTH
    assert len(data) < 300 * 1024


def test_one_topic_is_a_short_card_not_a_mostly_empty_portrait():
    width, height = _size(_short(1))
    assert width == coach_image.WIDTH
    assert coach_image.MIN_HEIGHT <= height <= 700  # was 1350, mostly empty white


def test_the_card_grows_with_each_topic():
    heights = [_size(_short(n))[1] for n in (1, 2, 4, 8)]
    assert heights == sorted(heights) and len(set(heights)) == 4
    # Each topic adds the same block, so the card is sized by content, not padded.
    assert heights[1] - heights[0] == (heights[3] - heights[2]) // 4


def test_a_card_never_shrinks_below_the_minimum():
    assert (
        coach_image.MIN_HEIGHT
        <= _size({"worker": "", "topics": [{"label": "x", "band": "red", "figure": "1"}]})[1]
        < 600
    )


def test_the_figure_reads_count_then_percent():
    from connect_labs.workflow.coach_charts.types import topic_rows

    [a, b] = topic_rows(
        [
            {"label": "x", "numerator": 31, "denominator": 73, "pct": 42},
            {"label": "y", "numerator": 3, "denominator": 9},
        ]
    )
    assert a["figure_text"] == "31 of 73 · 42%"
    assert b["figure_text"] == "3 of 9 · 33%"


def test_drawing_is_deterministic():
    assert coach_image.render_png(_payload(3)) == coach_image.render_png(_payload(3))


def _rgb(hex_colour):
    return tuple(int(hex_colour[i : i + 2], 16) for i in (1, 3, 5))


def _colours(payload):
    image = Image.open(io.BytesIO(coach_image.render_png(payload))).convert("RGB")
    return {colour for _, colour in image.getcolors(maxcolors=1 << 20)}


def test_the_bar_is_the_bands_colour_and_an_unknown_band_is_grey():
    assert _rgb(theme.BAND_COLOURS["red"]) in _colours(_payload(1, band="red"))
    unknown = _colours(_payload(1, band="unbanded"))
    assert _rgb(theme.NEUTRAL) in unknown
    assert not {_rgb(theme.BAND_COLOURS[b]) for b in ("red", "yellow", "green")} & unknown


def test_a_figure_without_counts_draws_without_a_bar():
    payload = {"worker": "W", "topics": [{"label": "Visits per week", "band": "red", "figure": "1.5 visits"}]}
    assert _rgb(theme.BAND_COLOURS["red"]) not in _colours(payload)


# ---------------------------------------------------------------------------
# The view
# ---------------------------------------------------------------------------


@pytest.fixture
def owner(db):
    return get_user_model().objects.create_user(username="ocs-team", password="p")


def _url(payload=None):
    return reverse("labs:coach_image", args=[coach_image.sign(payload or _payload(2))])


def _get(client, url, raw=None):
    headers = {"HTTP_AUTHORIZATION": f"Bearer {raw}"} if raw else {}
    return client.get(url, **headers)


def test_the_path_is_under_labs():
    assert _url().startswith("/labs/coach-image/")


def test_the_labs_oauth_middleware_leaves_the_picture_path_alone():
    from connect_labs.labs.oauth_session import get_skip_path_prefixes

    assert any(_url().startswith(prefix) for prefix in get_skip_path_prefixes())


def test_without_a_token_the_picture_is_refused(client, db):
    resp = _get(client, _url())
    assert resp.status_code == 401
    assert resp["Content-Type"] != "image/png"


def test_an_unknown_token_is_refused(client, db):
    assert _get(client, _url(), raw="not-a-real-token").status_code == 401


@pytest.mark.parametrize("scope", [token_scopes.FULL, token_scopes.NO_USERVISIT_DATA])
def test_a_token_of_any_other_scope_is_refused(client, owner, scope):
    _, raw = MCPAccessToken.create_token(owner, name="other", scope=scope)
    resp = _get(client, _url(), raw=raw)
    assert resp.status_code == 403
    assert resp["Content-Type"] != "image/png"


def test_a_coaching_pictures_token_gets_the_png(client, owner):
    token, raw = MCPAccessToken.create_token(owner, name="ocs-coach", scope=token_scopes.COACH_IMAGES)
    resp = _get(client, _url(), raw=raw)
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    assert resp["Cache-Control"] == "private, no-store"
    assert "noindex" in resp["X-Robots-Tag"]
    assert Image.open(io.BytesIO(resp.content)).size == _size(_payload(2))
    token.refresh_from_db()
    assert token.last_used_at is not None


def test_altered_and_expired_links_get_the_same_404(client, owner):
    _, raw = MCPAccessToken.create_token(owner, name="ocs-coach", scope=token_scopes.COACH_IMAGES)
    good = coach_image.sign(_payload())
    tampered = reverse("labs:coach_image", args=[good[:-3] + "xyz"])
    assert _get(client, tampered, raw=raw).status_code == 404
    with patch("django.core.signing.time.time", return_value=__import__("time").time() + 8 * 24 * 3600):
        assert _get(client, reverse("labs:coach_image", args=[good]), raw=raw).status_code == 404


def test_a_revoked_or_expired_token_is_refused(client, owner):
    token, raw = MCPAccessToken.create_token(owner, name="ocs-coach", scope=token_scopes.COACH_IMAGES)
    MCPAccessToken.objects.filter(pk=token.pk).update(is_active=False)
    assert _get(client, _url(), raw=raw).status_code == 401


# A signed-in Labs user, in a browser ------------------------------------------


def _signed_in(client, owner, *, expires_in=3600):
    client.force_login(owner)
    session = client.session
    session["labs_oauth"] = {"access_token": "t", "expires_at": __import__("time").time() + expires_in}
    session.save()


def _opp_url(opportunity_id=10092):
    return reverse("labs:coach_image", args=[coach_image.sign({**_payload(1), "opportunity_id": opportunity_id})])


@pytest.fixture
def access():
    with patch("connect_labs.mcp.tools.synthetic._require_opportunity_access") as check:
        yield check


def test_a_signed_in_user_who_can_see_the_opportunity_gets_the_png(client, owner, access):
    _signed_in(client, owner)
    resp = client.get(_opp_url(10092))
    assert resp.status_code == 200
    assert resp["Content-Type"] == "image/png"
    assert resp["Cache-Control"] == "private, no-store"
    access.assert_called_once_with(owner, 10092)


def test_a_signed_in_user_without_access_to_the_opportunity_is_refused(client, owner, access):
    from connect_labs.mcp.tool_registry import MCPToolError

    access.side_effect = MCPToolError("PERMISSION_DENIED", "no")
    _signed_in(client, owner)
    resp = client.get(_opp_url())
    assert resp.status_code == 403
    assert resp["Content-Type"] != "image/png"


def test_an_unreachable_access_check_is_not_a_grant(client, owner, access):
    from connect_labs.mcp.tool_registry import MCPToolError

    access.side_effect = MCPToolError("UPSTREAM_ERROR", "down")
    _signed_in(client, owner)
    assert client.get(_opp_url()).status_code == 503


def test_a_link_that_names_no_opportunity_opens_for_the_token_only(client, owner, access):
    _signed_in(client, owner)
    resp = client.get(_url())
    assert resp.status_code == 403
    access.assert_not_called()


def test_an_expired_labs_sign_in_is_refused_not_renewed(client, owner, access):
    _signed_in(client, owner, expires_in=-60)
    assert client.get(_opp_url()).status_code == 401
    access.assert_not_called()


def test_signed_in_to_django_without_a_labs_sign_in_is_refused(client, owner, access):
    client.force_login(owner)
    assert client.get(_opp_url()).status_code == 401
    access.assert_not_called()


def test_a_signed_in_user_gets_the_same_404_for_an_altered_link(client, owner, access):
    _signed_in(client, owner)
    good = _opp_url()
    assert client.get(good[:-4] + "xyz/").status_code == 404
    access.assert_not_called()


def test_a_bearer_header_is_judged_as_a_token_even_when_signed_in(client, owner, access):
    _signed_in(client, owner)
    assert _get(client, _opp_url(), raw="not-a-real-token").status_code == 401
    access.assert_not_called()


# ---------------------------------------------------------------------------
# Through the action
# ---------------------------------------------------------------------------

GRADED = {
    "cMeasures": [
        {"indicator": "MTG_RATE", "label": "Meetings held", "unit": "%"},
        {"indicator": "ATT_RATE", "label": "Attendance recorded", "unit": "%"},
    ],
    "byFLW": [
        {
            "key": "10::a10",
            "name": "Tiyamike Kalinde",
            "ind": {
                "MTG_RATE": {"band": "red", "value": 0.4166, "n": 12},
                "ATT_RATE": {"band": "yellow", "value": 0.7, "n": 20},
            },
        }
    ],
}
RUN = SimpleNamespace(id=70, opportunity_id=None, program_id=25)


def _definition(defaults=None):
    coach = {
        "key": "initiate_ai_coach",
        "type": "start_ocs_outreach",
        "label": "Start coaching",
        "defaults": {"bot": "bot-1", **(defaults or {})},
    }
    return SimpleNamespace(
        id=7, data={"config": {"actions": [coach]}}, template_type=None, opportunity_ids=[10], name="Report"
    )


def _wda():
    wda = MagicMock()
    wda.get_workers.return_value = [{"username": "a10", "name": "Asha"}, {"username": "b10", "name": "Binta"}]
    return wda


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="manager", password="p")


@pytest.fixture
def _env(monkeypatch):
    cache.clear()
    monkeypatch.setattr("connect_labs.labs.synthetic.registry.get_synthetic_opp", lambda opp: None)
    monkeypatch.setattr(actions, "_ocs_bots", lambda user, request: [{"id": "bot-1", "name": "Coach"}])


def _preview(user, arguments, definition=None):
    return preview(
        user,
        wda=_wda(),
        run=RUN,
        definition=definition or _definition(),
        key="initiate_ai_coach",
        arguments=arguments,
        briefing=lambda: (GRADED, "Spark"),
    )


def _sent(user, args):
    """Execute the resolved ``args`` and return what each worker's ``start_ai_session`` got."""
    from connect_labs.workflow.models import WorkflowActionExecution

    execution = WorkflowActionExecution.objects.create(
        user=user,
        via="page",
        definition_id=7,
        run_id=70,
        program_id=25,
        action_key="initiate_ai_coach",
        action_type="start_ocs_outreach",
        arguments=args,
    )
    tda = MagicMock()
    tda.create_task.side_effect = lambda **kw: SimpleNamespace(id=1, data={})
    with (
        patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="ct"),
        patch("connect_labs.tasks.data_access.TaskDataAccess", return_value=tda),
        patch("connect_labs.labs.integrations.ocs.api_client.OCSDataAccess"),
        patch("connect_labs.tasks.ai_sessions.start_ai_session", return_value={"session_id": "s"}) as start,
    ):
        actions.execute(execution.pk)
    return [c.kwargs for c in start.call_args_list]


def test_without_include_image_no_picture_is_attached_or_previewed(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}]})
    assert "image" not in out["workers"][0]
    [sent] = _sent(user, out["arguments"])
    assert sent["coach_image"] is None


def test_include_image_previews_and_sends_a_picture_of_the_briefings_topics(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}], "include_image": True})
    [w] = out["workers"]
    assert w["image"]["caption"] == "A bar chart of Tiyamike's figures for: Meetings held; Attendance recorded."
    assert w["image"]["url"].startswith("https://labs.connect.dimagi.com/labs/coach-image/")
    assert out["arguments"]["include_image"] is True  # so the confirm token covers it

    [sent] = _sent(user, out["arguments"])
    token = sent["coach_image"]["url"].rstrip("/").rsplit("/", 1)[1]
    assert coach_image.unsign(token) == {
        "opportunity_id": 10,
        "worker": "Tiyamike Kalinde",
        "topics": [
            {"label": "Meetings held", "band": "red", "numerator": 5, "denominator": 12, "pct": 42},
            {"label": "Attendance recorded", "band": "yellow", "numerator": 14, "denominator": 20, "pct": 70},
        ],
    }
    assert sent["coach_image"]["caption"] == w["image"]["caption"]


def test_a_workflow_default_can_turn_pictures_on(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10"}]}, definition=_definition({"include_image": True}))
    assert "image" in out["workers"][0]
    assert actions.declaration_problems([_definition({"include_image": True}).data["config"]["actions"][0]]) == []


def test_a_workers_own_text_keeps_the_picture_of_their_graded_topics(user, _env):
    out = _preview(user, {"workers": [{"key": "10::a10", "prompt": "Talk about MUAC."}], "include_image": True})
    assert "image" in out["workers"][0]
    [sent] = _sent(user, out["arguments"])
    assert sent["coach_image"]["url"]


def test_own_text_for_a_worker_with_nothing_off_target_has_no_picture(user, _env):
    out = _preview(user, {"workers": [{"key": "10::b10", "prompt": "Talk about MUAC."}], "include_image": True})
    assert "image" not in out["workers"][0]
    [sent] = _sent(user, out["arguments"])
    assert sent["coach_image"] is None


def test_include_image_must_be_a_boolean(user, _env):
    with pytest.raises(ActionError) as e:
        _preview(user, {"workers": [{"key": "10::a10"}], "include_image": "yes"})
    assert e.value.code == "invalid"
    assert "include_image" in e.value.public_message
    assert actions.declaration_problems(
        [{"key": "c", "type": "start_ocs_outreach", "defaults": {"include_image": "yes"}}]
    )
