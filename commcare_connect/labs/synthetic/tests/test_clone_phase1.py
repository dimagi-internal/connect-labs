"""Phase-1 clone tests: profile_opp_to_bundle + profile_opps_bulk."""

from unittest.mock import patch

from commcare_connect.labs.synthetic import clone_from_prod
from commcare_connect.labs.synthetic.bundle import GDriveBundleStore, make_bundle_store, read_bundle
from commcare_connect.labs.synthetic.tests.test_bundle import _FakeDrive


def test_profile_opp_to_bundle_writes_bundle(tmp_path):
    # Spread visits across two weeks so the profiler can compute a valid
    # start_date < end_date timeline (manifest validation rejects same-day spans).
    visits = [{"username": "a", "visit_date": "2026-05-04", "form_json": {"form": {"w": 1.0}}}] * 4 + [
        {"username": "b", "visit_date": "2026-05-11", "form_json": {"form": {"w": 1.5}}}
    ] * 4
    fake = {
        "": {"id": 523, "name": "KMC NAMA"},
        "user_visits": visits,
        "user_data": [],
        "app_structure": {"learn_app": None, "deliver_app": {"modules": []}},
    }

    def fake_fetch(base_url, opp_id, key, token):
        return fake[key]

    store = make_bundle_store(str(tmp_path))
    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        handle = clone_from_prod.profile_opp_to_bundle(523, base_url="https://x", oauth_token="t", store=store)

    loaded = read_bundle(handle)
    assert loaded.source_opp_id == 523
    assert "opportunity_id" in loaded.manifest_yaml
    assert loaded.app_structure["deliver_app"] == {"modules": []}


def test_profile_opp_to_bundle_raises_on_empty_visits(tmp_path):
    import pytest

    fake = {
        "": {"id": 1, "name": "Test Opp"},
        "user_visits": [],
        "user_data": [],
        "app_structure": {},
    }

    def fake_fetch(base_url, opp_id, key, token):
        return fake[key]

    store = make_bundle_store(str(tmp_path))
    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        with pytest.raises(ValueError):
            clone_from_prod.profile_opp_to_bundle(1, base_url="https://x", oauth_token="t", store=store)


def _bulk_fetch(opp_id, key):
    # Spread visits over two weeks so profiler produces a valid timeline span.
    visits = [{"username": "a", "visit_date": "2026-05-04", "form_json": {}}] * 4 + [
        {"username": "b", "visit_date": "2026-05-11", "form_json": {}}
    ] * 4
    return {
        "": {"id": opp_id, "name": f"Opp {opp_id}"},
        "user_visits": visits,
        "user_data": [],
        "app_structure": {},
    }[key]


def test_profile_opps_bulk_isolates_failures(tmp_path):
    def fake_fetch(base_url, opp_id, key, token):
        if opp_id == 999:
            raise RuntimeError("simulated failure")
        return _bulk_fetch(opp_id, key)

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        resolved, handles = clone_from_prod.profile_opps_bulk(
            [100, 999, 200],
            base_url="https://x",
            oauth_token="t",
            bundle_root=str(tmp_path),
        )

    # opp 999 fails but 100 and 200 succeed
    assert resolved == str(tmp_path)
    assert len(handles) == 2
    ids = {read_bundle(h).source_opp_id for h in handles}
    assert ids == {100, 200}


def test_profile_opps_bulk_gdrive(tmp_path):
    """gdrive: bundle_root profiles into one shared Drive run folder; the resolved
    root comes back as gdrive:<run_folder_id> for Phase 2 to read."""
    drive = _FakeDrive()
    run_folder = drive.create_folder("run", "parent")

    def fake_fetch(base_url, opp_id, key, token):
        return _bulk_fetch(opp_id, key)

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        resolved, handles = clone_from_prod.profile_opps_bulk(
            [100, 200],
            base_url="https://x",
            oauth_token="t",
            bundle_root=f"gdrive:{run_folder}",
            drive=drive,
        )

    assert resolved == f"gdrive:{run_folder}"
    assert len(handles) == 2
    # The persisted bundles are readable back from Drive:
    store = GDriveBundleStore(drive, run_folder)
    assert {store.read(h).source_opp_id for h in store.list_handles()} == {100, 200}


def test_profile_cohort_records_resolved_bundle_root():
    """profile_cohort profiles the spec's opps into its bundle_root and records the
    resolved bundle_root back on the spec, so the same spec drives Phase 2."""
    from commcare_connect.labs.synthetic.cohort import CohortSpec

    drive = _FakeDrive()
    run_folder = drive.create_folder("run", "parent")
    spec = CohortSpec(
        opportunity_ids=[100, 200],
        program_name="KMC (Synthetic)",
        org_name="O",
        bundle_root=f"gdrive:{run_folder}",
    )

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=lambda b, o, k, t: _bulk_fetch(o, k)):
        out = clone_from_prod.profile_cohort(spec, base_url="https://x", oauth_token="t", drive=drive)

    assert out is spec  # mutated in place
    assert out.bundle_root == f"gdrive:{run_folder}"
    store = GDriveBundleStore(drive, run_folder)
    assert {store.read(h).source_opp_id for h in store.list_handles()} == {100, 200}


def test_generate_fixtures_only_writes_gdrive_no_db(settings):
    """generate_fixtures_only uploads fixtures to GDrive and returns a folder map,
    with NO database write (no SyntheticOpportunity row, no django_db marker)."""
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    drive = _FakeDrive()
    run_folder = drive.create_folder("run", "parent")
    store = GDriveBundleStore(drive, run_folder)
    manifest = (
        "opportunity_id: 523\nopportunity_name: KMC\nrandom_seed: 42\n"
        "timeline: {start_date: 2026-05-04, end_date: 2026-06-01, weeks: 4,"
        " visit_cadence_per_week_per_flw: {mean: 5, stddev: 1}}\n"
        "flw_personas: [{id: a, archetype: steady,"
        " accuracy_distribution: {mean: 0.8, stddev: 0.05},"
        " completeness_distribution: {mean: 0.8, stddev: 0.05}, flag_rate: 0.1}]\n"
        "beneficiary_cohorts: [{id: primary, size: 20, progression: flat,"
        ' field_distributions: {"form.w": {distribution: normal, mean: 12.0, stddev: 2.0}}}]\n'
        "kpi_config: [{kpi: a, field_path: form.w, aggregation: mean, threshold_underperform: 1.0}]\n"
    )
    store.write(
        523,
        manifest_yaml=manifest,
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "KMC"},
    )

    rows = clone_from_prod.generate_fixtures_only(f"gdrive:{run_folder}", drive=drive)

    assert len(rows) == 1
    assert rows[0]["source_opportunity_id"] == 523
    assert rows[0]["gdrive_folder_id"]
    assert rows[0]["visit_count"] > 0
