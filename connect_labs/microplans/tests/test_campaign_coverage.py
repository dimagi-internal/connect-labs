"""Reported coverage of a CommCare-run campaign against its plan, from a project's stored forms."""

import json
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from connect_labs.hqdata.access import create_project
from connect_labs.hqdata.computed import ComputedProblem, clean
from connect_labs.hqdata.models import HQForm
from connect_labs.hqdata.workflows import PageProblem, create_page, read_project_pipeline_data
from connect_labs.microplans.coverage import campaign
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db
HOST = "www.commcarehq.org"
DIST = campaign.CampaignSpec().distribution_xmlns
POP = campaign.CampaignSpec().target_xmlns
CODE = "function WorkflowUI({ pipelines }) { return <div>{(pipelines.coverage || {}).rows.length}</div>; }"


def distribution(project, n, child, cycle, csi, district, dose="oui", day=1):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=f"d{n}",
        xmlns=DIST,
        app_id="rcom",
        received_on=datetime(2026, 7, day, 10, tzinfo=timezone.utc),
        form_json={
            "form": {
                "case": {"@case_id": child},
                "var": {"passage": cycle},
                "localisation": {"csi_id": csi, "district_id": district},
                "administration_dose": {"spaq_recu": dose},
            }
        },
    )


def plan(project, n, csi, a, b, day=1):
    return HQForm.objects.create(
        project_id=project.pk,
        form_id=f"p{n}",
        xmlns=POP,
        app_id="chefcsi",
        received_on=datetime(2026, 6, day, 10, tzinfo=timezone.utc),
        form_json={
            "form": {
                "location_var": {"csi_id": csi},
                "population": {"population_3_11_mois": a, "population_12_59_mois": b},
            }
        },
    )


@pytest.fixture
def me():
    return UserFactory()


def prepared(me, project, spec=None):
    return campaign.prepare(me, project, spec or campaign.CampaignSpec())


def counts(me, project, spec=None):
    spec = spec or campaign.CampaignSpec()
    return campaign.scan(prepared(me, project, spec), spec)["counts"]


@pytest.fixture
def project(me):
    project = create_project(me, "echis-niger", hq_host=HOST, can_read_domain=lambda: True)
    rows = [
        # csi-a in district d1: cycle 1 sees 3 children and serves 2 (one refused, one seen twice); cycle 2 serves 1
        ("c1", "contact_1", "csi-a", "d1", "oui"),
        ("c1", "contact_1", "csi-a", "d1", "oui"),
        ("c2", "contact_1", "csi-a", "d1", "oui"),
        ("c3", "contact_1", "csi-a", "d1", "non"),
        ("c1", "contact_2", "csi-a", "d1", "oui"),
        # csi-b in district d2 has no plan record
        ("c9", "contact_1", "csi-b", "d2", "oui"),
    ]
    for i, row in enumerate(rows):
        distribution(project, i, *row)
    plan(project, 0, "csi-a", "10", "30")
    plan(project, 1, "csi-a", "20", "60", day=20)  # a later plan record replaces the earlier one
    return project


def test_children_are_counted_once_per_cycle_and_only_when_the_dose_was_given(me, project):
    found = counts(me, project)
    assert found[("d1", "csi-a", 1)] == {"seen": 3, "served": 2}
    assert found[("d1", "csi-a", 2)] == {"seen": 1, "served": 1}
    assert found[("d2", "csi-b", 1)] == {"seen": 1, "served": 1}


def test_a_centres_plan_is_its_latest_record_and_a_centre_with_none_is_said(me, project):
    table, meta = campaign.rows(
        prepared(me, project), campaign.CampaignSpec(), {"csi-a": "CSI A", "d1": "District One"}
    )
    first = next(r for r in table if r["csi_id"] == "csi-a" and r["cycle"] == 1)
    assert first["target"] == 80.0 and first["children_served"] == 2 and first["csi"] == "CSI A"
    assert first["district"] == "District One"
    assert [r["target"] for r in table if r["csi_id"] == "csi-b"] == [None]
    assert meta["health_centres"] == 2 and meta["health_centres_without_plan"] == 1 and meta["cycles"] == [1, 2]
    assert meta["data_through"].startswith("2026-07-01")


def computed_now(me, project, source):
    """Read a computed source the way a page does: the first read starts the background fill (which we run here), the
    next reads the kept result."""
    from django.core.cache import cache

    from connect_labs.hqdata import computed

    cache.clear()
    queued = []
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: queued.append(a)):
        with pytest.raises(computed.ComputedWarming):
            computed.read(me, project, source)
    _, _, name, params, token = queued[0]
    with patch("connect_labs.labs.integrations.commcare.cchq_tokens.cchq_token_or_none", return_value=None):
        computed.fill(me, project, name, params, token)
    return computed.read(me, project, source)


def test_a_page_reads_the_coverage_as_a_computed_source_and_a_non_member_does_not(me, project):
    source = {"computed": "campaign_coverage", "alias": "coverage", "params": {}}
    page = create_page(me, project, name="Coverage", render_code=CODE, pipeline_sources=[source])
    data = {"coverage": computed_now(me, project, source)}
    assert data["coverage"]["metadata"]["row_count"] == 3
    assert {r["csi_id"] for r in data["coverage"]["rows"]} == {"csi-a", "csi-b"}
    with pytest.raises(Exception):
        read_project_pipeline_data(UserFactory(), project, page.id)


def test_a_computed_source_with_a_bad_name_or_setting_is_refused_when_the_page_is_made(me, project):
    for bad in (
        {"computed": "no-such", "alias": "x"},
        {"computed": "campaign_coverage", "alias": "x", "params": {"goal": 3}},
        {"computed": "campaign_coverage", "alias": "x", "params": {"child_path": "not a path"}},
        {"computed": "campaign_coverage", "alias": "x", "params": {"nope": 1}},
        {"computed": "campaign_coverage", "alias": "x", "extra": 1},
    ):
        with pytest.raises(PageProblem):
            create_page(me, project, name="Bad", render_code=CODE, pipeline_sources=[bad])
    assert clean({"computed": "campaign_coverage", "alias": "c"})["params"]["goal"] == 0.95
    with pytest.raises(ComputedProblem):
        clean({"computed": "x", "alias": "c"})


def test_the_coverage_page_is_made_once_kept_current_and_opened_from_the_workflows_page(client, me, project):
    from connect_labs.hqdata.workflows import ensure_campaign_coverage_page, load_page

    first = ensure_campaign_coverage_page(me, project)
    assert ensure_campaign_coverage_page(me, project) == first  # idempotent
    definition, code = load_page(me, project, first)
    assert definition.name == "Campaign coverage" and "WorkflowUI" in code
    assert definition.pipeline_sources[0]["computed"] == "campaign_coverage"
    from connect_labs.hqdata import computed
    from connect_labs.hqdata.workflows import read_sources

    definition, _ = load_page(me, project, first)
    assert [s["computed"] for s in definition.pipeline_sources] == ["campaign_coverage", "campaign_reach"]
    assert computed_now(me, project, definition.pipeline_sources[0])["metadata"]["row_count"] == 3
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: None):
        with pytest.raises(computed.ComputedWarming):  # the figures are computed in the background the first time
            read_sources(me, project, definition, aliases={"reach"})


# --- coverage against the satellite denominators and buildings (a slow source, filled in the background) -----------


def households(project, start=100):
    """Children served in csi-a, each form recording where it was filled in (meta.location: lat lon alt accuracy)."""
    n = start
    for child, point, cycle in (
        ("k1", "13.50005 7.00005 0 5", "contact_1"),
        ("k1", "13.50005 7.00005 0 5", "contact_2"),
        ("k2", "13.50010 7.00010 0 5", "contact_1"),
        ("k3", "13.49990 6.99990 0 5", "contact_1"),
        ("k4", "13.49990 6.99990 0 200", "contact_1"),  # a poor fix (200 m) is not used to place anyone
        ("k5", "0.0 0.0 0 5", "contact_1"),  # no fix: 0,0
        ("k6", "", "contact_1"),  # no point recorded
    ):
        HQForm.objects.create(
            project_id=project.pk,
            form_id=f"h{n}",
            xmlns=DIST,
            app_id="rcom",
            username="distrib_a_01",
            received_on=datetime(2026, 7, 2 + n % 20, 10, tzinfo=timezone.utc),
            form_json={
                "form": {
                    "case": {"@case_id": child},
                    "var": {"passage": cycle},
                    "localisation": {"csi_id": "csi-a", "district_id": "d1"},
                    "administration_dose": {"spaq_recu": "oui"},
                    "meta": {"location": {"#text": point}} if point else {"location": None},
                }
            },
        )
        n += 1


def test_the_reach_reads_the_points_doses_were_recorded_at_as_aggregates(me, project):
    households(project)
    spec = campaign.CampaignSpec()
    cells = campaign.scan(prepared(me, project, spec), spec, with_points=True)["cells"]
    # the 200 m fix, the 0,0 fix and the missing point are not placed
    placed = sum(c[5] for c in cells if c[1] == 1)
    assert placed == 3  # k1, k2, k3 in cycle 1
    assert sum(c[5] for c in cells if c[1] == 2) == 1  # k1 in cycle 2


def test_test_accounts_are_left_out_of_every_count(me, project):
    HQForm.objects.create(
        project_id=project.pk,
        form_id="t1",
        xmlns=DIST,
        app_id="rcom",
        username="test_distrib_01",
        received_on=datetime(2026, 7, 1, tzinfo=timezone.utc),
        form_json={
            "form": {
                "case": {"@case_id": "tk"},
                "var": {"passage": "contact_1"},
                "localisation": {"csi_id": "csi-a", "district_id": "d1"},
                "administration_dose": {"spaq_recu": "oui"},
                "meta": {"username": "test_distrib_01"},
            }
        },
    )
    found = counts(me, project)
    assert found[("d1", "csi-a", 1)] == {"seen": 3, "served": 2}  # the fixture's own children, not the test account's


def reach(me, project, report=None, blds=None, units=()):
    """The satellite comparison over the fixture: a flat 800-children-per-square grid, three buildings at the dose
    points of csi-a, no district boundaries (so no whole-district column)."""
    import numpy as np
    import pandas as pd

    from connect_labs.labs.admin_boundaries import placement
    from connect_labs.microplans.coverage import population

    grid = population.Grid(np.full((6, 6), 800.0), 6.9, 13.6, 0.05, -0.05)
    if blds is None:
        blds = pd.DataFrame(
            {"lon": [7.00005, 7.0001, 6.9999], "lat": [13.50005, 13.5001, 13.4999], "area_m2": [50.0] * 3}
        )
    spec = campaign.CampaignSpec()
    data = prepared(me, project, spec)
    with (
        patch.object(population, "fetch_grid", lambda iso, bbox: grid),
        patch.object(placement, "load_units", lambda *a, **k: list(units)),
    ):
        return campaign.reach_rows(
            data, spec, {"csi-a": "District One"}, {"csi-a": 40.0}, report, fetch_square=lambda box: blds
        )


def test_the_reach_compares_the_satellite_estimate_and_the_buildings_per_district_and_cycle(me, project):
    households(project)
    rows, meta = reach(me, project)
    first = next(r for r in rows if r["cycle"] == 1 and r["district"] == "District One")
    assert first["coverage_unit"] is None  # no department boundaries here: not guessed
    assert first["buildings"] == 3 and first["buildings_reached"] == 3 and first["coverage_buildings"] == 1.0
    assert first["served_placed"] == 3 and first["served_total"] == 8  # of eight served, three had a usable point
    assert meta["cycles"] == [1, 2] and meta["squares_read"] >= 1
    assert "facility" not in json.dumps(meta) and "catchment" not in json.dumps(meta)  # nothing the data lacks


def test_the_satellite_estimate_is_the_department_most_doses_were_recorded_in(me, project):
    from shapely.geometry import box

    from connect_labs.labs.admin_boundaries.placement import Unit

    households(project)
    dept = Unit(key="d-a", name="Dept A", level=2, geometry=box(6.95, 13.45, 7.05, 13.55))
    far = Unit(key="d-b", name="Dept B", level=2, geometry=box(7.1, 13.3, 7.2, 13.4))
    rows, meta = reach(me, project, units=[dept, far])
    first = next(r for r in rows if r["cycle"] == 1 and r["district"] == "District One")
    assert first["departments"] == ["Dept A"] and first["departments_share_of_points"] == 1.0
    # 800 under-fives per 0.05-degree square: the department holds 4 squares' centres, 95% of them eligible.
    assert first["eligible_unit"] == pytest.approx(4 * 800 * 0.95)
    assert first["coverage_unit"] == pytest.approx(first["served_total"] / first["eligible_unit"])
    assert meta["places_outside_the_country"] == 0
    assert all(r["served_total"] for r in rows)  # a cycle a district did not run has no row


def test_a_building_far_from_every_dose_is_not_reached(me, project):
    import pandas as pd

    households(project)
    far = pd.DataFrame({"lon": [7.00005, 7.004], "lat": [13.50005, 13.504], "area_m2": [50.0, 50.0]})
    rows, _ = reach(me, project, blds=far)
    first = next(r for r in rows if r["cycle"] == 1 and r["district"] == "District One")
    assert first["buildings"] == 2 and first["buildings_reached"] == 1


def test_old_pages_with_catchment_settings_still_open(me, project):
    assert campaign.CampaignSpec.from_dict({"catchment_k": 260, "facility_type": None}).max_dist_m == 30.0


def test_a_slow_source_is_filled_in_the_background_and_read_from_the_kept_result(me, project):
    from django.core.cache import cache

    from connect_labs.hqdata import computed

    cache.clear()
    source = {"computed": "campaign_reach", "alias": "reach", "params": {}}
    queued = []
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: queued.append(a)):
        with pytest.raises(computed.ComputedWarming):
            computed.read(me, project, source)
        with pytest.raises(computed.ComputedWarming):
            computed.read(me, project, source)  # a second reader joins the fill: no second task
    assert len(queued) == 1
    project_id, user_id, name, params, token = queued[0]
    done = {"rows": [{"district": "D"}], "metadata": {"cycles": [1]}}
    with patch.dict(
        computed._REGISTRY, {"campaign_reach": (computed._REGISTRY["campaign_reach"][0], lambda *a: done)}
    ):
        computed.fill(me, project, name, params, token)
    kept = computed.read(me, project, source)
    assert kept["rows"] == [{"district": "D"}] and kept["metadata"]["computed"] == "campaign_reach"


def test_a_slow_source_that_fails_says_so_once_and_then_tries_again(me, project):
    from django.core.cache import cache

    from connect_labs.hqdata import computed

    cache.clear()
    source = {"computed": "campaign_reach", "alias": "reach", "params": {}}
    queued = []
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: queued.append(a)):
        with pytest.raises(computed.ComputedWarming):
            computed.read(me, project, source)
        _, _, name, params, token = queued[0]

        def boom(*a):
            raise computed.ComputedProblem("no coordinates")

        with patch.dict(computed._REGISTRY, {"campaign_reach": (computed._REGISTRY["campaign_reach"][0], boom)}):
            computed.fill(me, project, name, params, token)
        failed = computed.read(me, project, source)
        assert "no coordinates" in failed["metadata"]["per_opp"][next(iter(failed["metadata"]["per_opp"]))]["error"]
        with pytest.raises(computed.ComputedWarming):
            computed.read(me, project, source)  # the failure was spent: a new fill is started
    assert len(queued) == 2


def test_the_page_data_answers_202_while_a_slow_source_fills(client, me, project):
    from datetime import timedelta

    from django.core.cache import cache
    from django.utils import timezone as dj_timezone

    from connect_labs.hqdata.identity import hq_host
    from connect_labs.hqdata.models import HQIdentity
    from connect_labs.hqdata.workflows import create_page
    from connect_labs.labs.models import UserCCHQToken

    cache.clear()
    UserCCHQToken.objects.create(
        user=me, access_token="hq", refresh_token="r", expires_at=dj_timezone.now() + timedelta(hours=1)
    )
    HQIdentity.objects.create(
        user=me,
        hq_host=hq_host(),
        hq_username="p@example.org",
        domains=["echis-niger"],
        refreshed_at=dj_timezone.now(),
    )
    page = create_page(
        me,
        project,
        name="Reach",
        render_code=CODE,
        pipeline_sources=[{"computed": "campaign_reach", "alias": "reach", "params": {}}],
    )
    client.force_login(me)
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: None):
        resp = client.get(f"/commcare/projects/{project.pk}/workflows/{page.id}/pipeline-data/")
    assert resp.status_code == 202 and resp.json()["status"] == "warming" and resp.json()["retry_after_ms"]


def test_new_data_shows_the_last_result_marked_stale_while_the_new_one_is_computed(me, project):
    from django.core.cache import cache

    from connect_labs.hqdata import computed

    cache.clear()
    source = {"computed": "campaign_reach", "alias": "reach", "params": {}}
    queued = []
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: queued.append(a)):
        with pytest.raises(computed.ComputedWarming):
            computed.read(me, project, source)
        done = {"rows": [{"district": "D"}], "metadata": {"cycles": [1]}}
        _, _, name, params, token = queued[0]
        with patch.dict(
            computed._REGISTRY, {"campaign_reach": (computed._REGISTRY["campaign_reach"][0], lambda *a: done)}
        ):
            computed.fill(me, project, name, params, token)
        project.last_synced_at = datetime(2026, 11, 19, tzinfo=timezone.utc)  # a sync, a day later, brought new data
        project.save()
        shown = computed.read(me, project, source)
    assert shown["rows"] == [{"district": "D"}] and shown["metadata"]["stale"] is True
    assert len(queued) == 2  # and the refresh was started


def test_a_sync_that_never_ends_does_not_restart_the_computation_every_batch(project):
    from connect_labs.hqdata import computed

    project.last_synced_at = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    first = computed.result_key(project, "campaign_reach", {})
    project.last_synced_at = datetime(2026, 11, 19, 10, 40, tzinfo=timezone.utc)  # the next batch, 40 minutes on
    assert computed.result_key(project, "campaign_reach", {}) == first
    project.last_synced_at = datetime(2026, 11, 19, 17, 0, tzinfo=timezone.utc)  # a later block of hours
    assert computed.result_key(project, "campaign_reach", {}) != first


def test_every_slow_source_is_started_at_once_and_the_wait_says_what_each_is_doing(me, project):
    from django.core.cache import cache

    from connect_labs.hqdata import computed
    from connect_labs.hqdata.workflows import ensure_campaign_coverage_page, load_page, read_sources

    cache.clear()
    page = ensure_campaign_coverage_page(me, project)
    definition, _ = load_page(me, project, page)
    queued = []
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: queued.append(a)):
        with pytest.raises(computed.ComputedWarming) as waiting:
            read_sources(me, project, definition)
    assert [q[2] for q in queued] == ["campaign_coverage", "campaign_reach"]  # both fills queued by one read
    assert "Coverage figures" in waiting.value.message and "Satellite check" in waiting.value.message


def test_a_fill_says_how_far_along_it_is(me, project):
    """The reach reports its stages and how many squares of buildings it has read."""
    households(project)
    said = []
    reach(me, project, lambda message, **progress: said.append((message, progress)))
    messages = [m for m, _ in said]
    assert any("Buildings in square 1 of" in m for m in messages)
    assert any(p.get("total_stages") == 4 for _, p in said)


def test_names_come_from_the_forms(me, project):
    form = HQForm.objects.filter(project_id=project.pk, xmlns=DIST).first()
    form.form_json["form"]["localisation"].update(csi_name="CSI A", district_name="DS One")
    form.save()
    spec = campaign.CampaignSpec()
    named = campaign.scan(prepared(me, project, spec), spec)["names"]
    assert named["csi-a"] == "CSI A" and named["d1"] == "DS One"


def test_the_campaign_reads_through_two_pipelines_saved_in_the_project(me, project):
    from connect_labs.hqdata.pipelines import list_pipelines

    prepared(me, project)
    prepared(me, project)  # kept, not saved again
    assert sorted(p.name for p in list_pipelines(me, project)) == [
        campaign.DISTRIBUTION_PIPELINE,
        campaign.PLAN_PIPELINE,
    ]
    # Another campaign in the project (other forms or paths) gets its own pipeline rather than taking turns with one.
    prepared(me, project, campaign.CampaignSpec(child_path="form.child_id"))
    names = sorted(p.name for p in list_pipelines(me, project))
    assert len(names) == 3 and campaign.DISTRIBUTION_PIPELINE in names and campaign.PLAN_PIPELINE in names


def test_the_records_are_read_once_then_only_what_changed(me, project):
    from connect_labs.hqdata.pipeline_feed import StoreFeed

    real = StoreFeed.catch_up
    reads = []

    def counted(feed, *args, **kwargs):
        reads.append((feed.data_source.xmlns, real(feed, *args, **kwargs)))
        return reads[-1][1]

    with patch.object(StoreFeed, "catch_up", autospec=True, side_effect=counted):
        prepared(me, project)
        prepared(me, project)
        distribution(project, 99, "c7", "contact_2", "csi-a", "d1")  # a new record synced later
        found = counts(me, project)
    assert [n for x, n in reads if x == DIST] == [6, 0, 1]
    assert found[("d1", "csi-a", 2)] == {"seen": 2, "served": 2}


def test_a_long_first_build_stops_with_its_progress_saved_and_carries_on(me, project, monkeypatch):
    import time

    from connect_labs.labs.analysis.backends.sql import incremental

    monkeypatch.setattr(incremental, "VISIT_CHUNK", 2)
    spec = campaign.CampaignSpec()
    with pytest.raises(incremental.MaintenanceContinues):
        campaign.prepare(me, project, spec, deadline=time.monotonic() - 1)
    assert counts(me, project, spec)[("d1", "csi-a", 1)] == {"seen": 3, "served": 2}  # the next call finished it


def test_the_schedule_keeps_a_projects_pages_computed_so_they_are_ready_when_opened(me, project):
    from django.core.cache import cache

    from connect_labs.hqdata import computed
    from connect_labs.hqdata.models import HQProject
    from connect_labs.hqdata.tasks import refresh_computed_sources
    from connect_labs.hqdata.workflows import ensure_campaign_coverage_page

    cache.clear()
    ensure_campaign_coverage_page(me, project)
    HQProject.objects.filter(pk=project.pk).update(storage_mode=HQProject.STORAGE_PERSISTENT, sync_owner=me)
    queued = []
    with patch("connect_labs.hqdata.tasks.fill_computed_source.delay", lambda *a: queued.append(a)):
        assert refresh_computed_sources() == {"started": 2, "skipped": 0}
        assert refresh_computed_sources()["started"] == 0  # both under way: nothing started twice
    assert sorted(q[2] for q in queued) == ["campaign_coverage", "campaign_reach"]
    _, _, name, params, token = next(q for q in queued if q[2] == "campaign_coverage")
    with patch("connect_labs.labs.integrations.commcare.cchq_tokens.cchq_token_or_none", return_value=None):
        computed.fill(me, project, name, params, token)
    source = {"computed": "campaign_coverage", "alias": "coverage", "params": {}}
    assert computed.read(me, project, source)["metadata"]["row_count"] == 3  # ready without anyone waiting


def test_the_buildings_stop_at_their_deadline_and_the_next_run_carries_on(me, project):
    import time

    import numpy as np
    import pandas as pd

    from connect_labs.microplans.coverage import population, squares

    grid = population.Grid(np.full((6, 6), 800.0), 6.9, 13.6, 0.05, -0.05)
    points = pd.DataFrame([{"district": "D", "lon": 7.0, "lat": 13.5, "cycles": {1}}])
    with pytest.raises(squares.SquaresContinue):
        squares.buildings_reached(points, grid, lambda box: pd.DataFrame(), deadline=time.monotonic() - 1)
    done = squares.buildings_reached(points, grid, lambda box: pd.DataFrame({"lon": [7.0], "lat": [13.5]}))
    assert done["districts"]["D"][1] == {"buildings": 1, "reached": 1, "squares": 1, "share": 1.0}


def test_a_new_version_of_a_source_never_shows_a_result_kept_from_the_old_one(project):
    from connect_labs.hqdata import computed

    before = computed.result_key(project, "campaign_reach", {})
    with patch.dict(computed._VERSIONS, {"campaign_reach": 99}):
        assert computed.result_key(project, "campaign_reach", {}) != before
        assert computed.latest_key(project, "campaign_reach", {}) != computed.latest_key(project, "x", {})
