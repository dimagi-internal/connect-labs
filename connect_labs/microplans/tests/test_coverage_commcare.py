"""The coverage page's CommCare source: a Connect person who has also connected CommCare reads a project's forms.

The households come from the project's stored forms through the one function every project pipeline runs through, as
the signed-in member, and reduce to exactly the shapes the Connect-visit and uploaded-file sources give the check.
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
import pytest
from django.urls import reverse
from django.utils import timezone as dj_timezone

from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.identity import hq_host
from connect_labs.hqdata.models import HQForm, HQIdentity
from connect_labs.labs.models import UserCCHQToken
from connect_labs.microplans.coverage import check
from connect_labs.microplans.coverage import commcare as source
from connect_labs.microplans.coverage import page
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"


def buildings_at(lon, lat, radius_m):
    return pd.DataFrame({"lon": [7.0, 7.0001], "lat": [13.5, 13.5], "area_m2": [50.0, 50.0]})


def spec(**over):
    base = {"facilities": [{"id": "F1", "lon": 7.0, "lat": 13.5}], "catchment_k": 10}
    return check.CheckSpec.from_dict({**base, **over})


def smc_form(form_id, household, cycle, *, csi="F1", point=None, child=None, served="oui", xmlns="http://x/cps"):
    """A distribution form the way the eCHIS Niger SMC app records it (the paths the check's defaults read)."""
    body = {
        "var_menage": {"case_id_menage": household},
        "var": {"passage": cycle},
        "localisation": {"csi_id": csi},
        "meta": {"username": "agent1"},
        "case": {"@case_id": child},
        "administration_dose": {"spaq_recu": served},
    }
    if point:
        body["administration_dose"]["geolocalisation"] = point
    return HQForm.objects.create(
        project_id=form_id[0],
        form_id=form_id[1],
        xmlns=xmlns,
        app_id="app1",
        username="agent1",
        received_on=datetime(2026, 11, 1, 10, tzinfo=timezone.utc),
        form_json={"id": form_id[1], "received_on": "2026-11-01T10:00:00Z", "form": body},
    )


@pytest.fixture
def person(client):
    """A Connect person (an email) who has ALSO connected CommCare, and belongs to a project."""
    user = UserFactory()
    client.force_login(user)
    UserCCHQToken.objects.create(
        user=user, access_token="hq", refresh_token="r", expires_at=dj_timezone.now() + timedelta(hours=1)
    )
    HQIdentity.objects.create(
        user=user,
        hq_host=hq_host(),
        hq_username="p@example.org",
        domains=["niger-smc"],
        refreshed_at=dj_timezone.now(),
    )
    return user


@pytest.fixture
def project(person):
    project = create_project(person, "niger-smc", hq_host=HOST, can_read_domain=lambda: True)
    forms = [
        # h1: a point on its first visit, two children served in cycle 1, one in cycle 2 (later forms: no point)
        ("h1", "contact_1", "13.50005 7.00005 0.0 5.0", "c1"),
        ("h1", "contact_1", None, "c2"),
        ("h1", "contact_2", None, "c1"),
        # h2: visited, the dose was refused (served 'non'), so nobody counts as served
        ("h2", "contact_1", "13.50010 7.00010 0.0 5.0", "c3"),
    ]
    for i, (hh, cyc, point, child) in enumerate(forms):
        smc_form((project.pk, f"f{i}"), hh, cyc, point=point, child=child, served="non" if hh == "h2" else "oui")
    return project


def test_the_forms_reduce_to_the_rows_the_check_takes_and_the_served_it_counts(person, project):
    rows, served = source.read(person, project, spec())
    by = {r[1]: r for r in rows}
    assert by["h1"][0] == "F1" and by["h1"][2] == "13.50005 7.00005 0.0 5.0" and by["h1"][3] == "contact_1,contact_2"
    assert by["h2"][2] == "13.50010 7.00010 0.0 5.0"
    assert served == {"h1": {1: 2, 2: 1}}  # distinct children given the dose, per household and cycle; h2 refused


def test_a_household_with_no_point_still_counts_and_is_unplaced(person, project):
    smc_form((project.pk, "nopoint"), "h3", "contact_1", point=None, child="c9")
    rows, _ = source.read(person, project, spec())
    assert {r[1]: r[2] for r in rows}["h3"] is None


def test_the_app_and_form_narrowing_leaves_the_other_forms_out(person, project):
    smc_form((project.pk, "other"), "h9", "contact_1", point="13.5 7.0 0 5", child="c1", xmlns="http://x/other")
    every, _ = source.read(person, project, spec())
    narrowed, _ = source.read(person, project, spec(), xmlns="http://x/cps")
    assert "h9" in {r[1] for r in every} and "h9" not in {r[1] for r in narrowed}


def test_a_person_who_is_not_in_the_project_reads_nothing(project):
    from connect_labs.hqdata.access import ProjectAccessDenied

    with pytest.raises(ProjectAccessDenied):
        source.read(UserFactory(), project, spec())


def test_the_same_check_runs_over_these_rows_as_over_connect_visits(person, project):
    rows, _ = source.read(person, project, spec())
    result = check.run_check(rows, spec(), buildings_at)
    assert result["areas"][0]["group"] == "F1" and result["areas"][0]["buildings"] == 2


# --- the page ------------------------------------------------------------------------------------------------


def post(client, body):
    return client.post(
        reverse("microplans:buildings_reached_commcare"), data=json.dumps(body), content_type="application/json"
    )


def stub_buildings():
    return patch.object(
        page.check,
        "default_fetch",
        lambda f: lambda lon, lat, r: pd.DataFrame(
            {"lon": [7.0, 7.0001], "lat": [13.5, 13.5], "area_m2": [50.0, 50.0]}
        ),
    )


def test_a_connect_person_with_commcare_connected_runs_a_project_and_gets_counts_only(client, person, project):
    body = {"project_id": project.pk, "facilities": "F1,7.0,13.5,D1", "catchment_k": 10, "lens": "buildings"}
    with stub_buildings():
        resp = post(client, body)
    assert resp.status_code == 200
    out = resp.json()
    assert out["commcare"] == {"project": "niger-smc", "households": 2}
    assert out["total"]["buildings"] == 2 and out["areas"][0]["district"] == "D1"
    text = resp.content.decode()
    assert "h1" not in text.replace('"h1"', "") and "13.50005" not in text and "c1" not in text


def test_a_person_with_commcare_not_connected_is_sent_to_connect_it(client):
    user = UserFactory()
    client.force_login(user)
    resp = post(client, {"project_id": 1, "facilities": "F1,7.0,13.5"})
    assert resp.status_code == 403
    assert (
        resp.json()["error"] == "commcare_not_connected" and resp.json()["connect_url"] == "/labs/commcare/initiate/"
    )


def test_a_project_that_is_not_theirs_is_refused_the_same_way_as_one_that_does_not_exist(client, person, project):
    other = create_project(UserFactory(), "secret", hq_host=HOST, can_read_domain=lambda: True)
    body = {"facilities": "F1,7.0,13.5"}
    for project_id in (other.pk, 999999, "x"):
        resp = post(client, {**body, "project_id": project_id})
        assert resp.status_code == 400 and "your CommCare projects" in resp.json()["message"]


def test_the_page_offers_the_persons_projects_or_the_way_to_connect(client, person, project):
    html = client.get(reverse("microplans:buildings_reached")).content.decode()
    assert 'value="commcare"' in html and f'<option value="{project.pk}">niger-smc</option>' in html
    person.cchq_token.delete()
    html = client.get(reverse("microplans:buildings_reached")).content.decode()
    assert (
        "/labs/commcare/initiate/" in html
        and "<option" not in html.split('id="br-commcare-wrap"')[1].split("</div>")[0]
    )


def test_the_population_lens_runs_over_a_project_too(client, person, project):
    import numpy as np

    from connect_labs.microplans.coverage import population

    grid = population.Grid(np.full((6, 6), 800.0), 6.9, 13.6, 0.05, -0.05)
    body = {"project_id": project.pk, "facilities": "F1,7.0,13.5,D1", "iso": "ner", "lens": "population"}
    with patch.object(page.population, "fetch_grid", lambda iso, bbox: grid):
        resp = post(client, body)
    assert resp.status_code == 200
    out = resp.json()
    assert out["iso"] == "NER" and out["cycles"] == [1, 2] and out["districts"][0]["served"] == {"1": 2, "2": 1}
    assert out["commcare"]["project"] == "niger-smc"


def test_the_tool_reads_a_project_as_a_member_and_refuses_anyone_else(person, project):
    import connect_labs.mcp.tools  # noqa: F401 -- registers the tools
    from connect_labs.mcp.tool_registry import MCPToolError, get_tool

    settings = {"facilities": [{"id": "F1", "lon": 7.0, "lat": 13.5}], "catchment_k": 10}
    handler = get_tool("microplans_coverage_check").handler
    with patch.object(
        check,
        "default_fetch",
        lambda f: lambda lon, lat, r: pd.DataFrame({"lon": [7.0], "lat": [13.5], "area_m2": [50.0]}),
    ):
        out = handler(user=person, settings=settings, commcare_project_id=project.pk)
        assert out["areas"][0]["group"] == "F1" and out["total"]["buildings"] == 1
        with pytest.raises(MCPToolError) as caught:
            handler(user=UserFactory(), settings=settings, commcare_project_id=project.pk)
    assert caught.value.code == "NOT_FOUND"


# --- the facilities come from CommCare's locations ------------------------------------------------------------


def hq_page(objects, next_path=None):
    class Resp:
        status_code = 200

        def json(self):
            return {"meta": {"next": next_path}, "objects": objects}

    return Resp()


TYPES = [
    {"id": 1, "name": "District", "code": "district"},
    {"id": 2, "name": "CSI", "code": "csi"},
]


def locations_get(url, headers=None, timeout=None):
    if "/location_type/" in url:
        return hq_page(TYPES)
    base = "https://www.commcarehq.org/a/niger-smc/api/v0.5/location_type/"
    place = "https://www.commcarehq.org/a/niger-smc/api/v0.5/location/"
    if "offset=1" in url:
        return hq_page(
            [
                {
                    "id": 12,
                    "location_id": "csi-b",
                    "name": "CSI B",
                    "latitude": None,
                    "longitude": None,
                    "parent": f"{place}10/",
                    "location_type": f"{base}2/",
                }
            ]
        )
    return hq_page(
        [
            {
                "id": 10,
                "location_id": "dist-1",
                "name": "Maradi, Ville",
                "latitude": 13.5,
                "longitude": 7.1,
                "parent": None,
                "location_type": f"{base}1/",
            },
            {
                "id": 11,
                "location_id": "csi-a",
                "name": "CSI A",
                "latitude": "13.51",
                "longitude": "7.09",
                "parent": f"{place}10/",
                "location_type": f"{base}2/",
            },
        ],
        next_path="?limit=1000&offset=1",
    )


def test_the_facilities_are_the_projects_locations_of_the_facility_type_with_their_district(person, project):
    with patch("connect_labs.hqdata.locations.httpx.get", locations_get):
        found = source.facility_list("tok", "niger-smc")
    assert found["type_id"] == 2  # the type that looks like the health facility
    assert found["facilities"] == [{"id": "csi-a", "lon": 7.09, "lat": 13.51, "district": "Maradi, Ville"}]
    assert found["without_coordinates"] == 1  # csi-b has none in CommCare: said, not drawn
    assert {t["name"]: (t["count"], t["with_coordinates"]) for t in found["types"]} == {
        "District": (1, 1),
        "CSI": (2, 1),
    }


def test_the_page_fills_the_facility_list_from_commcare_and_the_run_uses_it(client, person, project):
    with patch("connect_labs.hqdata.locations.httpx.get", locations_get):
        resp = client.post(
            reverse("microplans:buildings_reached_facilities"),
            data=json.dumps({"project_id": project.pk}),
            content_type="application/json",
        )
    assert resp.status_code == 200
    out = resp.json()
    assert (
        out["text"] == "csi-a,7.09,13.51,Maradi  Ville" and out["facilities"] == 1 and out["without_coordinates"] == 1
    )


def test_a_person_without_commcare_or_the_project_gets_no_locations(client, person, project):
    other = create_project(UserFactory(), "secret", hq_host=HOST, can_read_domain=lambda: True)
    resp = client.post(
        reverse("microplans:buildings_reached_facilities"),
        data=json.dumps({"project_id": other.pk}),
        content_type="application/json",
    )
    assert resp.status_code == 400 and "your CommCare projects" in resp.json()["message"]


def test_commcare_refusing_the_locations_is_said_in_words(client, person, project):
    def refused(url, headers=None, timeout=None):
        return type("R", (), {"status_code": 403, "json": lambda self: {}})()

    with patch("connect_labs.hqdata.locations.httpx.get", refused):
        resp = client.post(
            reverse("microplans:buildings_reached_facilities"),
            data=json.dumps({"project_id": project.pk}),
            content_type="application/json",
        )
    assert resp.status_code == 400 and "permission to view locations" in resp.json()["message"]


def test_the_tool_needs_no_facility_list_for_a_project(person, project):
    import connect_labs.mcp.tools  # noqa: F401 -- registers the tools
    from connect_labs.mcp.tool_registry import get_tool

    handler = get_tool("microplans_coverage_check").handler
    with (
        patch("connect_labs.hqdata.locations.httpx.get", locations_get),
        patch.object(check, "default_fetch", lambda f: buildings_at),
    ):
        out = handler(
            user=person,
            settings={"catchment_k": 10, "group_path": "form.localisation.csi_id"},
            commcare_project_id=project.pk,
        )
    assert [a["group"] for a in out["areas"]] == ["csi-a"]
