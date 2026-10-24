"""Where a call's work happens, and where a respondent offers to do it.

The boundary resolver is replaced by a few invented squares: Kano (a state),
Gwale (a ward inside it), Lagos (a state elsewhere).
"""

from __future__ import annotations

import json

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone

from connect_labs.solicitations import service_areas
from connect_labs.solicitations.data_access import SolicitationsDataAccess
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse
from connect_labs.solicitations.tests.factories import grant, make_call, make_org, make_user

pytestmark = pytest.mark.django_db


def _square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


SHAPES = {
    "Kano": _square(8, 11, 9, 12),
    "Gwale": _square(8.4, 11.4, 8.5, 11.5),
    "Lagos": _square(3, 6, 4, 7),
}


def area(name, level, **kw):
    return {"name": name, "level": level, "source": "labs", "country": "NGA", "ref": {"boundary_id": name}, **kw}


KANO, GWALE, LAGOS = area("Kano", 1), area("Gwale", 3, parent_name="Kano › Gwale"), area("Lagos", 1)


@pytest.fixture(autouse=True)
def boundaries(monkeypatch):
    monkeypatch.setattr(service_areas, "_resolve_geometry", lambda desc: SHAPES.get(desc["name"]))


@pytest.fixture
def world():
    owner_org = make_org("owner-org")
    owner = make_user("owner")
    grant(owner, owner_org)
    firm_org = make_org("firm-org")
    applicant = make_user("applicant")
    grant(applicant, firm_org)
    return {"owner_org": owner_org, "owner": owner, "firm_org": firm_org, "applicant": applicant}


def da(user):
    return SolicitationsDataAccess(user=user)


def within_kano(world):
    return da(world["owner"]).create_solicitation(
        {
            "title": "Survey",
            "description": "d",
            "solicitation_type": "rfp",
            "status": "active",
            "is_public": True,
            "service_area_limits": [KANO],
        },
        owner_org=world["owner_org"],
    )


def respond(world, call, areas, status="submitted"):
    return da(world["applicant"]).create_response(
        call.public_id, llo_entity_id="firm-org", data={"responses": {}, "status": status, "service_areas": areas}
    )


# --- resolving -----------------------------------------------------------------------


def test_an_area_is_stored_with_the_resolved_outline_and_its_countrys_word_for_the_level():
    [kano] = service_areas.normalise([KANO], field="service_areas")
    assert kano["level_label"] == "State"
    assert kano["geometry"]["type"] == "Polygon"
    assert kano["key"] == "Kano"


def test_a_ward_inside_a_picked_state_is_dropped_and_duplicates_collapse():
    stored = service_areas.normalise([GWALE, KANO, KANO, LAGOS], field="service_areas")
    assert [a["name"] for a in stored] == ["Kano", "Lagos"]


def test_an_area_the_resolver_cannot_find_is_refused_by_name():
    with pytest.raises(ValidationError, match="could not find the boundary of Atlantis"):
        service_areas.normalise([area("Atlantis", 1)], field="service_areas")


def test_an_area_missing_its_level_or_source_is_refused():
    with pytest.raises(ValidationError, match="needs a name, level, source and country"):
        service_areas.normalise([{"name": "Kano"}], field="service_areas")


def test_too_many_areas_are_refused():
    with pytest.raises(ValidationError, match="at most"):
        service_areas.normalise([area(f"A{i}", 3) for i in range(service_areas.MAX_AREAS + 1)], field="x")


# --- the call -------------------------------------------------------------------------


def test_a_call_limited_to_a_state_stores_it_and_says_so(world):
    call = within_kano(world)
    call.refresh_from_db()
    assert call.area_mode == "within"
    assert call.area_limit_names == ["Kano (State)"]
    assert call.service_area_limits[0]["geometry"] == json.loads(json.dumps(call.service_area_limits[0]["geometry"]))


def test_a_call_cannot_offer_plans_and_area_limits_together(world):
    plans = [{"plan_id": 1, "name": "Study A"}]
    with pytest.raises(ValidationError, match="not both"):
        da(world["owner"]).create_solicitation(
            {
                "title": "S",
                "description": "d",
                "solicitation_type": "rfp",
                "plans": plans,
                "service_area_limits": [KANO],
            },
            owner_org=world["owner_org"],
        )


def test_clearing_the_limits_opens_the_call_to_anywhere(world):
    call = within_kano(world)
    da(world["owner"]).update_solicitation(call.public_id, {"service_area_limits": []})
    call.refresh_from_db()
    assert call.area_mode == "any"


# --- responding ------------------------------------------------------------------------


def test_a_ward_inside_the_calls_state_is_accepted(world):
    response = respond(world, within_kano(world), [GWALE])
    assert [a["name"] for a in response.service_areas] == ["Gwale"]
    assert response.coverage_names == ["Gwale (Ward) — Kano › Gwale"]


def test_an_area_outside_the_calls_limits_is_refused_by_name(world):
    with pytest.raises(ValidationError, match="Lagos is not inside the areas this call covers \\(Kano \\(State\\)\\)"):
        respond(world, within_kano(world), [LAGOS])


def test_a_limited_call_needs_an_area_to_submit_but_not_to_save_a_draft(world):
    call = within_kano(world)
    draft = respond(world, call, [], status="draft")
    assert draft.status == "draft"
    with pytest.raises(ValidationError, match="Pick at least one area"):
        da(world["applicant"]).update_response(draft.pk, {"status": "submitted"})


def test_an_open_call_takes_areas_anywhere_and_none_at_all(world):
    call = make_call(world["owner_org"])
    assert respond(world, call, [LAGOS, GWALE]).coverage_names == ["Lagos (State)", "Gwale (Ward) — Kano › Gwale"]
    other = make_call(world["owner_org"], title="Another")
    assert respond(world, other, []).status == "submitted"


def test_a_plan_call_takes_plans_not_areas(world):
    call = make_call(world["owner_org"], plans=[{"plan_id": 1, "name": "Study A"}])
    with pytest.raises(ValidationError, match="offers its own study areas"):
        respond(world, call, [KANO])


# --- the pages ---------------------------------------------------------------------------


def login(client, user):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"access_token": "t", "expires_at": timezone.now().timestamp() + 3600}
    session.save()


def _call_form(**kw):
    return {
        "title": "New call",
        "description": "Desc",
        "solicitation_type": "rfp",
        "status": "active",
        "is_public": "on",
        "questions_json": "[]",
        "evaluation_criteria_json": "[]",
        "owner_org": "owner-org",
        **kw,
    }


def test_the_create_form_keeps_the_work_inside_the_chosen_state(client, world):
    login(client, world["owner"])
    page = client.get(reverse("solicitations:create")).content.decode()
    assert "Where the work happens" in page and "sap-config-limits" in page
    resp = client.post(
        reverse("solicitations:create"), _call_form(area_mode="within", service_area_limits=json.dumps([KANO]))
    )
    assert resp.status_code == 302
    assert Solicitation.objects.get(title="New call").area_limit_names == ["Kano (State)"]


def test_choosing_within_with_no_area_is_refused_on_the_form(client, world):
    login(client, world["owner"])
    resp = client.post(reverse("solicitations:create"), _call_form(area_mode="within", service_area_limits="[]"))
    assert resp.status_code == 200
    assert "Pick at least one area the work must be inside" in resp.content.decode()
    assert not Solicitation.objects.filter(title="New call").exists()


def test_editing_a_call_to_anywhere_clears_its_limits(client, world):
    call = within_kano(world)
    login(client, world["owner"])
    resp = client.post(reverse("solicitations:edit", kwargs={"pk": call.public_id}), _call_form(area_mode="any"))
    assert resp.status_code == 302
    call.refresh_from_db()
    assert call.service_area_limits == []


def test_the_respond_page_draws_the_calls_limits_and_saves_the_picked_areas(client, world):
    call = within_kano(world)
    login(client, world["applicant"])
    page = client.get(reverse("solicitations:respond", kwargs={"pk": call.public_id})).content.decode()
    assert "Where would you do this work?" in page and "This call covers Kano (State)" in page
    assert reverse("microplans:boundary_areas") in page
    resp = client.post(
        reverse("solicitations:respond", kwargs={"pk": call.public_id}),
        {"org": "firm-org", "question_q1": "x", "service_areas": json.dumps([GWALE]), "submit": "1"},
    )
    assert resp.status_code == 302
    response = SolicitationResponse.objects.get(submitted_by=world["applicant"])
    assert response.coverage_names == ["Gwale (Ward) — Kano › Gwale"]


def test_the_respond_page_shows_an_area_outside_the_limits_as_an_error(client, world):
    call = within_kano(world)
    login(client, world["applicant"])
    resp = client.post(
        reverse("solicitations:respond", kwargs={"pk": call.public_id}),
        {"org": "firm-org", "question_q1": "x", "service_areas": json.dumps([LAGOS]), "submit": "1"},
    )
    assert resp.status_code == 200
    assert "Lagos is not inside the areas this call covers" in resp.content.decode()
    assert not SolicitationResponse.objects.filter(submitted_by=world["applicant"]).exists()


def test_the_call_page_shows_where_the_work_happens(client, world):
    call = within_kano(world)
    page = client.get(reverse("solicitations:public_detail", kwargs={"pk": call.public_id})).content.decode()
    assert "Where the work happens" in page and "The work is inside Kano (State)" in page


def test_the_owner_sees_where_a_respondent_would_work(client, world):
    response = respond(world, within_kano(world), [GWALE])
    login(client, world["owner"])
    page = client.get(reverse("solicitations:response_detail", kwargs={"pk": response.pk})).content.decode()
    assert "Where they would work" in page and "sap-config-response" in page
    listing = client.get(reverse("solicitations:responses_list", kwargs={"pk": response.solicitation.public_id}))
    assert "Gwale (Ward)" in listing.content.decode()


def test_boundary_search_and_geometry_need_only_a_login(client, world, monkeypatch):
    from connect_labs.microplans.core import admin_boundaries

    class Resolver:
        def geometry(self, a):
            return SHAPES.get(a.name)

    monkeypatch.setattr(admin_boundaries, "get_resolver", lambda: Resolver())
    url = reverse("microplans:boundary_geometry")
    body = json.dumps({"area": KANO})
    assert client.post(url, body, content_type="application/json").status_code == 302  # to the login page
    login(client, world["applicant"])
    resp = client.post(url, body, content_type="application/json")
    assert resp.status_code == 200 and resp.json()["geometry"] == SHAPES["Kano"]
