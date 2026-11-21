"""The CASE CARD (``coach_charts/case_card.py``): one case as a card-html ``index.html``,
frozen beside its case picture at preview, served on the picture's own signed link at
``coach-card/``, and handed to the Coach as ``coach_card_url`` / ``coach_card_call``."""

from __future__ import annotations

import re

import pytest
from django.test import Client
from django.urls import reverse

from connect_labs.semantic import case_states as cs
from connect_labs.workflow import coach_image
from connect_labs.workflow.coach_charts import case_card, store
from connect_labs.workflow.tests.test_case_coaching_action import env  # noqa: F401 -- fixture
from connect_labs.workflow.tests.test_case_coaching_action import user  # noqa: F401 -- fixture
from connect_labs.workflow.tests.test_case_coaching_action import (
    CATALOG,
    DANGER,
    PROPS,
    ROW,
    THRIVING,
    VISITS,
    WEIGHTS,
    FakeCases,
    _danger_row,
    _preview,
)

SERIES = cs.case_series(PROPS)


def _state(name):
    return next(s for s in CATALOG if s["name"] == name)


def _card(name=THRIVING, row=ROW, visits=WEIGHTS, **kw):
    return case_card.card_data(_state(name), row, visits, SERIES, "Baby Amina", as_of="2026-06-10", **kw)


# ---------------------------------------------------------------------------
# What the card carries
# ---------------------------------------------------------------------------


def test_the_card_carries_the_pictures_panels_in_the_registrys_order():
    state = _state(THRIVING)
    card = _card()
    assert [p["title"] for p in card["panels"]] == [
        cs.fill(p.get("title") or "", ROW) for p in state["picture"]["panels"] if p.get("kind") in case_card.KINDS
    ]
    weight = next(p for p in card["panels"] if p["kind"] == "series")
    assert [pt[1] for pt in weight["points"]][:1] == [1350]
    assert card["title"] and card["case_name"] == "Baby Amina" and card["tone"] == "celebrate"


def _visits_card(row_extra):
    row = {**ROW, "next_visit_start": "2026-05-20", "next_visit_end": "2026-05-23", **row_extra}
    card = _card("case_state_weight_check", row=row)
    return next(p for p in card["panels"] if p["kind"] == "visits")


def test_a_due_window_older_than_the_latest_visit_is_not_a_late_visit():
    # The window columns come from the latest visit that RECORDED one. When a later visit
    # happened (here 8 Jun, after a window ending 23 May), that window is stale: no
    # "next visit due" line, no "days overdue" pill.
    visits = _visits_card({"next_visit_overdue_days": 18})
    assert visits["due"] is None and not visits["overdue"] and visits["status"] is None
    html = case_card.render_html(_card("case_state_weight_check", row={**ROW, "next_visit_end": "2026-05-23"}))
    assert "late" not in html.split("Visits", 1)[-1].split("</section>", 1)[0]


def test_a_current_due_window_still_shows_and_counts_overdue():
    visits = _visits_card({"next_visit_start": "2026-06-08", "next_visit_end": "2026-06-08"})
    assert visits["due"] and visits["overdue"] == 2 and visits["status"][0] == "bad"


def test_the_weight_flag_is_not_said_twice():
    card = _card("case_state_weight_check")
    weight = next(p for p in card["panels"] if p["kind"] == "series")
    html = case_card.render_html(card)
    label = (weight.get("highlight") or {}).get("label")
    if label and weight.get("status") and weight["status"][1] == label:
        assert html.count(case_card.E(label)) == 1


def test_the_worker_reads_the_pictures_lead_and_staff_keep_the_facts():
    state = _state("case_state_weight_check")
    assert state["picture"].get("lead"), "the KMC registry words a lead for the worker"
    row = {
        **ROW,
        "any_bad_step": True,
        "step_change_g": 1422,
        "step_days": 7,
        "step_rate": 63.3,
        "step_from_date": "2026-06-01",
        "step_to_date": "2026-06-08",
    }
    card = _card("case_state_weight_check", row=row)
    assert card["facts"] == (
        "Weight changed by +1,422 g in 7 days, from 1 Jun to 8 Jun. A baby does not gain or lose "
        "that much that fast, so this weighing is worth checking."
    )
    no_lead = {**state, "picture": {k: v for k, v in state["picture"].items() if k != "lead"}}
    staff = case_card.card_data(no_lead, row, WEIGHTS, SERIES)["facts"]
    assert staff == cs.facts(state, row) and "g/kg/day" in staff


def test_a_multi_select_is_worded_whole_and_once():
    assert cs.format_value("mother father Mother_in_law", ["list"]) == "mother, father & mother in law"
    assert cs.format_value("mother Mother", ["list"]) == "mother"
    assert cs.format_value("Direct_breastfeed", ["words"]) == "Direct breastfeed"


def test_the_band_label_wraps_inside_the_card_instead_of_running_off_it():
    lines = case_card._wrap("healthy growth", case_card.PR - 6)
    assert lines == ["healthy", "growth"]
    assert all(len(line) * 6.2 <= case_card.PR - 6 for line in lines)
    html = case_card.render_html(_card())
    assert "<tspan" in html and ">healthy</tspan>" in html


def test_the_case_number_wins_then_the_supervisor_then_no_one():
    call = cs.case_call(PROPS)
    with_phone = {**ROW, "mother_phone": "+254 712 345-678"}
    assert _card(row=with_phone, call=call)["call"] == {
        "label": "Call the mother",
        "number": "+254712345678",
        "who": "case",
    }
    supervisor = {"label": "Call Grace (supervisor)", "number": "+254700000001"}
    assert _card(call=call, fallback_contact=supervisor)["call"]["who"] == "fallback"
    assert _card(row={**ROW, "mother_phone": "not a number"}, call=call)["call"] is None


def test_the_call_line_is_a_markdown_tel_link():
    card = _card(row={**ROW, "mother_phone": "0712 345 678"}, call=cs.case_call(PROPS))
    assert case_card.call_markdown(card) == "[Call the mother: 0712345678](tel:0712345678)"
    assert case_card.call_markdown(_card()) == ""


def test_the_registry_case_call_is_checked_and_its_columns_ride_in_the_case_index():
    assert cs.case_call_problems(PROPS, frozenset({"mother_phone"})) == []
    bad = {"case_call": [{"column": "nope", "colour": "red"}]}
    problems = cs.case_call_problems(bad, frozenset({"mother_phone"}))
    assert any("colour" in p for p in problems) and any("'nope'" in p for p in problems)
    assert "mother_phone" in cs.case_fields(PROPS)


# ---------------------------------------------------------------------------
# The index.html: the card-html contract
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [THRIVING, "case_state_weight_check", "case_state_faltering", DANGER])
def test_every_kmc_case_state_draws_a_card_within_the_card_html_rules(name):
    row = _danger_row() if name == DANGER else ROW
    visits = VISITS if name == DANGER else WEIGHTS
    page = case_card.render_html(_card(name, row=row, visits=visits, call=cs.case_call(PROPS)))
    assert page.startswith("<!doctype html>")
    # One inline script, for taps; nothing that reaches out, stores or follows.
    assert page.lower().count("<script") <= 1 and "<script src" not in page.lower()
    for api in (
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "localStorage",
        "sessionStorage",
        "indexedDB",
        "location",
        "open(",
    ):
        assert api not in page
    assert not re.search(r"href\s*=", page, re.I)  # nothing to follow
    assert all(m.startswith("data:image/") for m in re.findall(r'src="([^"]*)"', page))  # photos ride inside
    for banned in ("100vh", "height:100%", "position:fixed", "@import", "url("):
        assert banned not in page
    assert "body{margin:0" in page.replace("html,body{margin:0", "body{margin:0")
    assert "color-scheme:light" in page
    assert len(page.encode()) < 200_000  # far under card-html's 2.5 MiB per file


def test_the_card_shows_the_number_and_points_to_the_conversation_to_call():
    page = case_card.render_html(_card(row={**ROW, "mother_phone": "+254712345678"}, call=cs.case_call(PROPS)))
    assert "+254712345678" in page and "Tap the number in the message below to call." in page
    assert "tel:" not in page  # a card cannot dial


def test_every_string_is_escaped():
    page = case_card.render_html(_card(row={**ROW, "case_name": "x"}, visits=WEIGHTS) | {"case_name": "<b>x</b>"})
    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt;" in page


# ---------------------------------------------------------------------------
# Frozen at preview; served on the picture's link
# ---------------------------------------------------------------------------


def test_a_case_preview_freezes_a_card_and_links_it(user, env):  # noqa: F811
    env["cases"] = FakeCases([_danger_row(mother_phone="+254712345678")])
    out = _preview(
        user,
        {
            "workers": [{"key": "10::a10", "case": {"id": "baby-1"}}],
            "supervisor_contact": {"number": "+254700000001"},
        },
    )
    [w] = out["workers"]
    chart = store.get(w["image"]["chart"]["id"]).chart
    assert chart["card"]["call"]["number"] == "+254712345678"
    assert w["image"]["card_url"].startswith("https://labs.connect.dimagi.com/labs/coach-card/")
    assert w["image"]["card_call"] == "[Call the mother: +254712345678](tel:+254712345678)"


def test_the_coaching_view_shows_the_card_the_phone_gets_not_the_picture(user, env):  # noqa: F811
    """The chat's coaching card (the MCP Apps View) previews a case conversation with
    the very index.html the worker's phone draws; its text for the model leaves it out."""
    from connect_labs.mcp.tool_registry import get_tool
    from connect_labs.mcp.tools import workflow_run

    env["cases"] = FakeCases([_danger_row()])
    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    [w] = out["workers"]
    chart = store.get(w["image"]["chart"]["id"]).chart
    shown = workflow_run._inline_image(w["prompt"], w["image"])
    assert shown["card_html"] == case_card.render_html(chart["card"])
    assert shown["card_html"].startswith("<!doctype html>")
    text = get_tool("workflow_action_preview_view").text({"workers": [{"key": "10::a10", "image": shown}]})
    assert "<!doctype" not in text and "case card" in text


def test_the_supervisor_names_the_card_when_the_case_has_no_number(user, env):  # noqa: F811
    out = _preview(
        user,
        {
            "workers": [{"key": "10::a10", "case": {"id": "baby-1"}}],
            "supervisor_contact": {"label": "Call Grace", "number": "+254700000001"},
        },
    )
    chart = store.get(out["workers"][0]["image"]["chart"]["id"]).chart
    assert chart["card"]["call"] == {"label": "Call Grace", "number": "+254700000001", "who": "fallback"}


@pytest.mark.django_db
def test_the_card_link_serves_sandboxed_html_to_the_coach_token(user, env):  # noqa: F811
    from connect_labs.mcp import token_scopes
    from connect_labs.mcp.models import MCPAccessToken

    out = _preview(user, {"workers": [{"key": "10::a10", "case": {"id": "baby-1"}}]})
    record = store.get(out["workers"][0]["image"]["chart"]["id"])
    token = coach_image.sign({"chart": record.pk, "opportunity_id": 10})
    _row, raw = MCPAccessToken.create_token(user, "ocs", scope=token_scopes.COACH_IMAGES)
    response = Client().get(reverse("labs:coach_card", args=[token]), HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    assert response["Content-Security-Policy"].startswith("sandbox;")
    assert record.chart["card"]["title"].encode() in response.content
    assert "script-src" not in response["Content-Security-Policy"]  # a browser preview runs no script
    nope = Client().get(reverse("labs:coach_card", args=["x" + token]), HTTP_AUTHORIZATION=f"Bearer {raw}")
    assert nope.status_code == 404


# ---------------------------------------------------------------------------
# Interactive: the visits a worker taps through
# ---------------------------------------------------------------------------

PHOTO = "data:image/jpeg;base64,/9j/AAAA"


def _check_card(photos=None):
    visits = [{**v, "visit_id": 100 + i} for i, v in enumerate(WEIGHTS)]
    return case_card.card_data(
        _state("case_state_weight_check"), ROW, visits, SERIES, "Baby Amina", as_of="2026-06-10", photos=photos
    )


def test_each_visit_day_carries_its_weight_its_change_and_its_photo():
    card = _check_card({"101": PHOTO})
    days = card["visits_detail"]
    assert [d["d"] for d in days] == sorted({d["d"] for d in days})
    assert days[0].get("change") is None and days[1]["change"].startswith(("+", "-", "±"))
    assert days[1]["main"] and days[1]["main"].endswith("g") and days[1]["photo"] == PHOTO
    assert all(d["photo"] is None for i, d in enumerate(days) if i != 1)


def test_the_card_opens_on_the_flagged_visit_with_the_rest_a_tap_away():
    card = _check_card()
    page = case_card.render_html(card)
    flag = next(p for p in card["panels"] if p["kind"] == "series").get("highlight", {}).get("to")
    first = flag if flag in [d["d"] for d in card["visits_detail"]] else card["visits_detail"][-1]["d"]
    assert f'data-pane="{first}">' in page  # shown
    for d in card["visits_detail"]:
        assert f'data-tab="{d["d"]}"' in page and f'data-pt="{d["d"]}"' in page
        if d["d"] != first:
            assert f'data-pane="{d["d"]}" hidden' in page


def test_the_registry_words_the_photo_ask_the_checklist_and_the_next_step():
    state = _state("case_state_weight_check")
    weight = next(p for p in state["picture"]["panels"] if p.get("kind") == "series")
    assert weight["photo"]["question"] == "upload_weight_image"
    card = _check_card()
    checklist = next(p for p in card["panels"] if p["kind"] == "list")
    assert len(checklist["details"]) == len(checklist["items"]) and checklist["done"]
    assert card["next_step"]
    page = case_card.render_html(card)
    assert page.count('class="chk"') == len(checklist["items"]) and "Your next step" in page


def test_a_photo_ask_names_the_flagged_reading():
    card = _check_card()
    weight = next(p for p in card["panels"] if p["kind"] == "series")
    if weight.get("highlight"):
        assert "{value}" not in weight["photo"]["ask"] and "\u00a0g" in weight["photo"]["ask"]


def test_the_registry_refuses_a_photo_without_a_question():
    pic = {"type": "case_summary", "title": "t", "panels": [{"kind": "series", "series": "weight", "photo": {}}]}
    problems = cs._picture_problems(pic, "p", frozenset(), {"weight"})
    assert any("photo" in p for p in problems)


def test_a_photo_shrinks_to_a_small_jpeg_and_a_non_image_to_nothing():
    import base64
    import io

    from PIL import Image

    from connect_labs.workflow.coach_charts import case_photos

    buf = io.BytesIO()
    Image.new("RGB", (1600, 2400), "white").save(buf, "PNG")
    uri = case_photos.shrink(buf.getvalue())
    img = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert uri.startswith("data:image/jpeg;base64,") and max(img.size) == case_photos.SIZE[0]
    assert case_photos.shrink(b"not an image") is None
    assert case_photos.visit_photos([], "q", opportunity_id=1, access_token=None) == {}
