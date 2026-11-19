"""The Investigations Tracker: a public page, so the tests are mostly about what must NOT get onto it.

* the page is a 404 until staff publish, a save never publishes, and anonymous visitors can only GET it;
* its model figures are the explorer's, and only a published Connect model is ever drawn on the scale;
* content is refused unless every link is https and every field is one the page knows;
* visit counts come from the stored count, never from a request.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone

import pytest
from django.urls import reverse

from connect_labs.effectiveness import engine, explore
from connect_labs.effectiveness.investigations import content, figures, page

PUBLIC = "/labs/investigations/"
EDIT = "/labs/cea/investigations/edit/"
PREVIEW = "/labs/cea/investigations/preview/"


def _program(data: dict, name: str) -> dict:
    return next(p for p in data["programs"] if p["name"] == name)


# ------------------------------------------------------------------------------------------------ the starting copy


def test_the_starting_copy_passes_its_own_check_and_cleaning_it_again_changes_nothing():
    data = content.default()
    assert len(data["programs"]) == 16
    assert content.clean(data) == data


def test_the_program_is_called_kangaroo_care():
    text = content.DATA.read_text(encoding="utf-8")
    assert "Kangaroo Care" in text
    assert "Kangaroo Mother Care" not in text


def test_the_starting_notes_quote_the_models_own_base_case():
    """The notes are written by hand; if a model moves, this says the sentence beside its number is now wrong."""
    data = content.default()
    for p in data["programs"]:
        if p["model_key"]:
            assert f"{figures.model(p['model_key'])['base']}x" in p["note"], p["name"]


# ------------------------------------------------------------------------------------------------------ the figures


@pytest.mark.parametrize("key", engine.MODELS)
def test_a_models_figures_are_the_explorers(key):
    f = figures.model(key)
    multiples = [explore.run(key)["headline"]["multiple"]] + [s["multiple"] for s in explore.scenarios(key)]
    assert f["base"] == round(multiples[0], 1)
    assert (f["lo"], f["hi"]) == (round(min(multiples), 1), round(max(multiples), 1))
    assert f["lo"] <= f["base"] <= f["hi"]
    assert f["bar"] == explore.run(key)["headline"]["bar"]


# --------------------------------------------------------------------------------------------------------- the page


def test_only_a_public_connect_model_is_drawn_on_the_scale():
    """A GiveWell reference used the benchmark of its own grant year, so it is text and never gets a ``ce``."""
    built = page.build(content.default(), figures.all_models())
    with_ce = {p["name"] for p in built["programs"] if "ce" in p}
    assert with_ce == {"Child Health Campaign", "Kangaroo Care", "Mother Baby Wellness"}
    assert all(p["model"] == "public" for p in built["programs"] if "ce" in p)
    assert built["stats"]["above_bar"] == sum(1 for f in figures.all_models().values() if f["clears_bar"])
    rutf = _program(built, "Home-based malnutrition treatment (RUTF)")
    assert "ce" not in rutf and rutf["ref"]["short"] == "about 9x"


def test_visits_are_connects_count_where_a_program_names_its_delivery_types_and_the_typed_number_otherwise():
    data = content.default()
    delivery = {"services": {"chc": 1_600_000, "kmc": 50_000}, "total": 2_000_000, "as_of": "2026-12-01"}
    built = page.build(data, figures.all_models(), delivery)
    chc, mbw, malaria = (_program(built, n) for n in ("Child Health Campaign", "Mother Baby Wellness", "Malaria"))
    assert (chc["visits"], chc["visits_live"]) == (1_600_000, True)
    # Named a delivery type Connect has not counted: the typed number stays, rather than a zero.
    assert (mbw["visits"], mbw["visits_live"]) == (_program(data, "Mother Baby Wellness")["visits"], False)
    assert (malaria["visits"], malaria["visits_live"]) == (3414, False)
    assert built["stats"]["visits"] == 2_000_000 and built["as_of"] == "2026-12-01"

    nothing_counted = page.build(data, figures.all_models(), None)
    assert nothing_counted["stats"]["visits"] == data["stats"]["visits"]
    assert nothing_counted["as_of"] == data["snapshot_date"] and not nothing_counted["delivery_live"]


def test_the_payload_carries_no_wiring_only_what_is_shown():
    built = page.build(content.default(), figures.all_models())
    assert all("connect" not in p and "sheet" not in p for p in built["programs"])
    assert "model_link" not in built


def test_a_models_link_is_its_sheet_until_the_content_says_explorer():
    data = content.default()
    sheet = _program(page.build(data, figures.all_models(), explorer_url="/labs/cea/models/"), "Kangaroo Care")
    assert sheet["link"].startswith("https://docs.google.com/") and sheet["link_kind"] == "sheet"
    data["model_link"] = "explorer"
    explorer = _program(page.build(data, figures.all_models(), explorer_url="/labs/cea/models/"), "Kangaroo Care")
    assert (explorer["link"], explorer["link_kind"]) == ("/labs/cea/models/?model=kc", "explorer")


# ------------------------------------------------------------------------------------------------------- the check


def _broken(change) -> list[str]:
    data = content.default()
    change(data)
    with pytest.raises(content.ContentError) as exc:
        content.clean(data)
    return exc.value.messages


def test_a_link_must_be_https():
    def javascript(d):
        _program(d, "Malaria")["ref"]["links"][0][1] = "javascript:alert(1)"

    def plain_http(d):
        _program(d, "Child Health Campaign")["sheet"] = "http://example.org/model"

    assert any("https://" in m for m in _broken(javascript))
    assert any("https://" in m for m in _broken(plain_http))


def test_a_model_that_is_not_public_cannot_show_a_number_of_ours():
    """A draft model's result must not reach the public page: only ``public`` may name a Connect model."""

    def draft_with_a_model(d):
        p = _program(d, "Home-based malnutrition treatment (RUTF)")
        p["model_key"] = "chc"

    assert any("only when the model status is public" in m for m in _broken(draft_with_a_model))


def test_the_check_names_what_is_wrong():
    def many(d):
        d["programs"][0]["stage"] = "Thriving"
        d["programs"][1]["since"] = "May 2025"
        d["programs"][2]["name"] = d["programs"][0]["name"]
        d["programs"][3]["visits"] = -4

    messages = " ".join(_broken(many))
    for expected in ("stage must be one of", "must look like 2026-09", "both called", "whole number"):
        assert expected in messages


def test_a_field_the_page_does_not_know_is_dropped():
    data = content.default()
    data["programs"][0]["pipeline_usd"] = 1_000_000
    data["funder_notes"] = "internal"
    cleaned = content.clean(data)
    assert "pipeline_usd" not in cleaned["programs"][0] and "funder_notes" not in cleaned


# --------------------------------------------------------------------------------------- the public page, over HTTP


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user(username="it-staff", email="it-staff@dimagi.com", password="x")


@pytest.fixture
def outsider(django_user_model):
    return django_user_model.objects.create_user(username="it-out", email="someone@example.org", password="x")


def test_the_addresses():
    assert reverse("investigations") == PUBLIC
    assert reverse("cea:investigations_edit") == EDIT
    assert reverse("cea:investigations_preview") == PREVIEW


@pytest.mark.django_db
def test_the_page_is_not_found_until_someone_publishes_and_again_when_it_is_taken_down(client, staff):
    from connect_labs.effectiveness.investigations import store

    assert client.get(PUBLIC).status_code == 404
    version = store.save(staff, content.default())
    assert client.get(PUBLIC).status_code == 404  # saved is not published

    store.publish(staff, version.pk)
    resp = client.get(PUBLIC)
    assert resp.status_code == 200
    html = resp.content.decode()
    assert "Kangaroo Care" in html and "Connect Investigations Tracker" in html
    assert "/labs/login/" not in html

    store.unpublish(staff)
    assert client.get(PUBLIC).status_code == 404


@pytest.mark.django_db
def test_a_later_draft_does_not_change_the_public_page(client, staff):
    from connect_labs.effectiveness.investigations import store

    store.publish(staff, store.save(staff, content.default()).pk)
    changed = content.default()
    changed["title"] = "A title nobody approved yet"
    store.save(staff, changed)
    assert "A title nobody approved yet" not in client.get(PUBLIC).content.decode()


@pytest.mark.django_db
def test_an_anonymous_visitor_can_only_read(client, staff):
    from connect_labs.effectiveness.investigations import store

    store.publish(staff, store.save(staff, content.default()).pk)
    for method in (client.post, client.put, client.delete, client.patch):
        assert method(PUBLIC).status_code == 405


@pytest.mark.django_db
def test_the_public_page_shows_the_stored_count_and_runs_no_count_of_its_own(
    client, staff, django_assert_max_num_queries
):
    from connect_labs.effectiveness.investigations import store
    from connect_labs.effectiveness.models import InvestigationsPage

    store.publish(staff, store.save(staff, content.default()).pk)
    InvestigationsPage.objects.filter(pk=store.PAGE_PK).update(
        delivery={"services": {"chc": 1_234_567}, "total": 2_222_222, "as_of": "2026-12-01"}
    )
    with django_assert_max_num_queries(4):
        resp = client.get(PUBLIC)
    payload = json.loads(
        resp.content.decode()
        .split('<script id="investigations-data" type="application/json">')[1]
        .split("</script>")[0]
    )
    assert _program(payload, "Child Health Campaign")["visits"] == 1_234_567
    assert payload["stats"]["visits"] == 2_222_222


# ------------------------------------------------------------------------------------------------- the editor's gate


@pytest.mark.django_db
def test_only_dimagi_staff_reach_the_editor_and_the_preview(client, staff, outsider):
    for url in (EDIT, PREVIEW):
        resp = client.get(url)
        assert resp.status_code == 302 and "/labs/login/" in resp["Location"]
    client.force_login(outsider)
    assert client.get(EDIT).status_code == 403
    assert client.get(PREVIEW).status_code == 403
    assert client.post(EDIT, {"action": "unpublish"}).status_code == 403
    client.force_login(staff)
    assert client.get(EDIT).status_code == 200
    assert client.get(PREVIEW).status_code == 200


@pytest.mark.django_db
def test_saving_publishing_and_restoring_from_the_editor(client, staff):
    from connect_labs.effectiveness.investigations import store

    client.force_login(staff)
    first = content.default()
    assert client.post(EDIT, {"action": "save", "data": json.dumps(first), "note": "first"}).status_code == 302
    v1 = store.latest()
    assert store.live_content() is None

    assert client.post(EDIT, {"action": "publish", "version": v1.pk}).status_code == 302
    assert store.live_content() == v1.data and store.state().published_by == staff

    second = copy.deepcopy(first)
    second["title"] = "Second"
    client.post(EDIT, {"action": "save", "data": json.dumps(second)})
    v2 = store.latest()
    assert v2.pk != v1.pk and store.state().live_id == v1.pk

    client.post(EDIT, {"action": "restore", "version": v1.pk})
    v3 = store.latest()
    assert v3.pk not in (v1.pk, v2.pk) and v3.data == v1.data and v3.note == f"Restored version {v1.pk}"


@pytest.mark.django_db
def test_content_that_fails_the_check_is_not_saved_and_says_why(client, staff):
    from connect_labs.effectiveness.investigations import store

    client.force_login(staff)
    bad = content.default()
    bad["programs"][0]["sheet"] = "javascript:alert(1)"
    resp = client.post(EDIT, {"action": "save", "data": json.dumps(bad)})
    assert resp.status_code == 400 and "https://" in resp.content.decode()
    assert store.latest() is None
    assert client.post(EDIT, {"action": "save", "data": "{not json"}).status_code == 400
    assert client.post(EDIT, {"action": "publish", "version": 999}).status_code == 400


# -------------------------------------------------------------------------------------------------------- the count


@pytest.mark.django_db
def test_the_count_is_verified_visits_by_delivery_type_without_test_opportunities():
    from connect_labs.effectiveness.investigations import store
    from connect_labs.effectiveness.tasks import refresh_investigations_delivery
    from connect_labs.pulse.models import PulseEvent, PulseOpportunity

    PulseOpportunity.objects.create(opportunity_id=1, name="CHC real", service_slug="chc")
    PulseOpportunity.objects.create(opportunity_id=2, name="CHC test", service_slug="chc", is_test=True)
    when = datetime(2026, 11, 1, tzinfo=timezone.utc)
    rows = [(1, "chc", "approved"), (1, "chc", "approved"), (1, "chc", "rejected"), (1, "chc", "pending")]
    rows += [(2, "chc", "approved"), (3, "kmc", "approved")]
    for n, (opp, service, status) in enumerate(rows, 1):
        PulseEvent.objects.create(
            connect_visit_id=n, opportunity_id=opp, field_ts=when, sync_ts=when, status=status, service_slug=service
        )

    assert store.delivery() is None
    assert refresh_investigations_delivery() == 3
    kept = store.delivery()
    assert kept["services"] == {"chc": 2, "kmc": 1} and kept["total"] == 3

    # Pulse emptied (a rebuild, say) is not Connect at zero: the last real count stays.
    PulseEvent.objects.all().delete()
    refresh_investigations_delivery()
    assert store.delivery()["total"] == 3


def test_only_the_public_page_is_outside_the_sign_in_boundary():
    from connect_labs.labs.oauth_session import get_skip_path_prefixes

    prefixes = get_skip_path_prefixes()
    assert "/labs/investigations/" in prefixes
    assert not any(EDIT.startswith(p) for p in prefixes)


@pytest.mark.parametrize("visits", [float("inf"), float("nan"), 1e300, -1, 2.5, True, "12"])
def test_a_visit_count_that_is_not_a_sane_whole_number_is_refused_not_a_crash(visits):
    def change(d):
        d["programs"][0]["visits"] = visits

    assert any("whole number" in m for m in _broken(change))


def test_odd_shapes_are_refused_not_a_crash():
    def bad_group_key(d):
        d["groups"] = [{"key": []}]

    def bad_date(d):
        d["snapshot_date"] = "2026-99-99"

    assert any("Group active title is empty" in m for m in _broken(bad_group_key))
    assert any("Snapshot date" in m for m in _broken(bad_date))
    for not_content in (None, "text", [1, 2]):
        with pytest.raises(content.ContentError):
            content.clean(not_content)


@pytest.mark.django_db
def test_only_the_newest_version_can_be_published(staff):
    """The preview shows the newest draft, so that is the only one the button may make public."""
    from connect_labs.effectiveness.investigations import store

    v1 = store.save(staff, content.default())
    later = content.default()
    later["title"] = "Later"
    store.save(staff, later)
    with pytest.raises(content.ContentError):
        store.publish(staff, v1.pk)
    assert store.live_content() is None


def test_the_public_page_takes_no_labs_context():
    from connect_labs.labs.context import CONTEXT_FREE_PREFIXES

    assert PUBLIC.startswith(CONTEXT_FREE_PREFIXES)
