"""Phase-2 clone tests: generate_opp_from_bundle (offline, idempotent) + generate_opps_bulk."""

import pytest

from connect_labs.labs.synthetic import clone_from_prod
from connect_labs.labs.synthetic.bundle import write_bundle
from connect_labs.labs.synthetic.models import SyntheticOpportunity

pytestmark = pytest.mark.django_db


class _FakeDrive:
    def create_folder(self, name, parent_id):
        return "f1"

    def upload_file(self, folder_id, filename, content):
        pass


def _bundle(tmp_path):
    manifest_yaml = (
        "opportunity_id: 523\n"
        "opportunity_name: KMC\n"
        "random_seed: 42\n"
        "timeline: {start_date: 2026-05-04, end_date: 2026-06-01, weeks: 4,"
        " visit_cadence_per_week_per_flw: {mean: 5, stddev: 1}}\n"
        "flw_personas: [{id: a, archetype: steady,"
        " accuracy_distribution: {mean: 0.8, stddev: 0.05},"
        " completeness_distribution: {mean: 0.8, stddev: 0.05}, flag_rate: 0.1}]\n"
        "beneficiary_cohorts: [{id: primary, size: 20, progression: flat,"
        ' field_distributions: {"form.w": {distribution: normal, mean: 12.0, stddev: 2.0}}}]\n'
        "kpi_config: [{kpi: a, field_path: form.w, aggregation: mean, threshold_underperform: 1.0}]\n"
    )
    return write_bundle(
        tmp_path,
        523,
        manifest_yaml=manifest_yaml,
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "KMC"},
    )


def test_generate_makes_no_prod_calls(tmp_path, settings, monkeypatch):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    bundle = _bundle(tmp_path)
    # Any prod fetch during Phase 2 must blow up:
    monkeypatch.setattr(
        clone_from_prod, "_fetch_endpoint", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Phase 2 hit prod!"))
    )
    result = clone_from_prod.generate_opp_from_bundle(
        bundle,
        drive=_FakeDrive(),
        program_id=10000,
        program_name="KMC (Synthetic)",
        org_name="Dimagi-KMC (Synthetic)",
    )
    assert result.opportunity_id >= 10000
    assert result.app_structure_present is True
    row = SyntheticOpportunity.objects.get(opportunity_id=result.opportunity_id)
    assert row.cloned_from_opportunity_id == 523
    assert row.program_id == 10000


def test_generate_is_idempotent(tmp_path, settings, monkeypatch):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)
    r1 = clone_from_prod.generate_opp_from_bundle(
        bundle, drive=_FakeDrive(), program_id=10000, program_name="P", org_name="O"
    )
    r2 = clone_from_prod.generate_opp_from_bundle(
        bundle, drive=_FakeDrive(), program_id=10000, program_name="P", org_name="O"
    )
    assert r2.skipped is True
    assert r1.opportunity_id == r2.opportunity_id
    assert SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=523).count() == 1


def test_generate_opps_bulk_shared_program_and_isolation(tmp_path, settings, monkeypatch):
    """generate_opps_bulk allocates one shared program_id for all opps,
    no opp_id collides with that program_id, malformed bundles are skipped."""
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)

    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()

    # Build two valid bundles with distinct source_ids (523, 524).
    def _make_manifest(opp_id: int) -> str:
        return (
            f"opportunity_id: {opp_id}\n"
            f"opportunity_name: KMC-{opp_id}\n"
            "random_seed: 42\n"
            "timeline: {start_date: 2026-05-04, end_date: 2026-06-01, weeks: 4,"
            " visit_cadence_per_week_per_flw: {mean: 5, stddev: 1}}\n"
            "flw_personas: [{id: a, archetype: steady,"
            " accuracy_distribution: {mean: 0.8, stddev: 0.05},"
            " completeness_distribution: {mean: 0.8, stddev: 0.05}, flag_rate: 0.1}]\n"
            "beneficiary_cohorts: [{id: primary, size: 20, progression: flat,"
            ' field_distributions: {"form.w": {distribution: normal, mean: 12.0, stddev: 2.0}}}]\n'
            "kpi_config: [{kpi: a, field_path: form.w, aggregation: mean, threshold_underperform: 1.0}]\n"
        )

    from connect_labs.labs.synthetic.bundle import write_bundle

    write_bundle(
        bundle_root,
        523,
        manifest_yaml=_make_manifest(523),
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": 523, "name": "KMC-523"},
    )
    write_bundle(
        bundle_root,
        524,
        manifest_yaml=_make_manifest(524),
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": 524, "name": "KMC-524"},
    )

    # Add a malformed bundle dir (manifest.yaml has invalid YAML / bad fields).
    bad_dir = bundle_root / "999"
    bad_dir.mkdir()
    (bad_dir / "manifest.yaml").write_text("opportunity_id: not_an_integer\n  bad_indent: [\n")

    results = clone_from_prod.generate_opps_bulk(
        bundle_root,
        drive=_FakeDrive(),
        program_name="KMC (Synthetic)",
        org_name="Dimagi-KMC (Synthetic)",
    )

    # Two good bundles succeeded; the malformed one was skipped.
    assert len(results) == 2, f"expected 2 results, got {len(results)}"

    # All results share exactly ONE program_id.
    program_ids = {SyntheticOpportunity.objects.get(opportunity_id=r.opportunity_id).program_id for r in results}
    assert len(program_ids) == 1, f"expected one shared program_id, got {program_ids}"
    shared_program_id = program_ids.pop()

    # No opp_id equals the shared program_id (Fix 2 regression guard).
    opp_ids = {r.opportunity_id for r in results}
    assert shared_program_id not in opp_ids, f"program_id {shared_program_id} collides with an opp_id in {opp_ids}"

    # Both source opps are registered in the DB.
    assert SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=523).exists()
    assert SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=524).exists()


def _manifest(opp_id: int) -> str:
    return (
        f"opportunity_id: {opp_id}\n"
        f"opportunity_name: KMC-{opp_id}\n"
        "random_seed: 42\n"
        "timeline: {start_date: 2026-05-04, end_date: 2026-06-01, weeks: 4,"
        " visit_cadence_per_week_per_flw: {mean: 5, stddev: 1}}\n"
        "flw_personas: [{id: a, archetype: steady,"
        " accuracy_distribution: {mean: 0.8, stddev: 0.05},"
        " completeness_distribution: {mean: 0.8, stddev: 0.05}, flag_rate: 0.1}]\n"
        "beneficiary_cohorts: [{id: primary, size: 20, progression: flat,"
        ' field_distributions: {"form.w": {distribution: normal, mean: 12.0, stddev: 2.0}}}]\n'
        "kpi_config: [{kpi: a, field_path: form.w, aggregation: mean, threshold_underperform: 1.0}]\n"
    )


def test_generate_opps_bulk_gdrive(settings, monkeypatch):
    """Phase 2 reads bundles from a GDrive run folder (durable handoff) and registers
    all opps under one shared program. Proves the gdrive: path works end-to-end."""
    from connect_labs.labs.synthetic.bundle import GDriveBundleStore
    from connect_labs.labs.synthetic.tests.test_bundle import _FakeDrive

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)

    drive = _FakeDrive()
    run_folder = drive.create_folder("kmc-bundles", "parent")
    # Seed two bundles into the Drive run folder (as Phase 1 would have).
    store = GDriveBundleStore(drive, run_folder)
    for oid in (523, 524):
        store.write(
            oid,
            manifest_yaml=_manifest(oid),
            app_structure={"learn_app": None, "deliver_app": {"modules": []}},
            opportunity={"id": oid, "name": f"KMC-{oid}"},
        )

    results = clone_from_prod.generate_opps_bulk(
        f"gdrive:{run_folder}",
        drive=drive,
        program_name="KMC (Synthetic)",
        org_name="Dimagi-KMC (Synthetic)",
    )

    assert len(results) == 2
    program_ids = {SyntheticOpportunity.objects.get(opportunity_id=r.opportunity_id).program_id for r in results}
    assert len(program_ids) == 1
    assert SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=523).exists()
    assert SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=524).exists()


def test_generate_opps_bulk_scope_filter(tmp_path, settings, monkeypatch):
    """only_source_ids bounds the run: bundles under the root for other sources
    are left untouched (#1166 — a shared bundle_root + fresh=True regenerated
    opps the caller never named)."""
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)

    bundle_root = tmp_path / "bundles"
    bundle_root.mkdir()
    for oid in (523, 524):
        write_bundle(
            bundle_root,
            oid,
            manifest_yaml=_manifest(oid),
            app_structure={"learn_app": None, "deliver_app": {"modules": []}},
            opportunity={"id": oid, "name": f"KMC-{oid}"},
        )

    results = clone_from_prod.generate_opps_bulk(
        bundle_root,
        drive=_FakeDrive(),
        only_source_ids=[523],
    )

    assert [r.source_opportunity_id for r in results] == [523]
    assert SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=523).exists()
    assert not SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=524).exists()


def test_generate_cohort_honors_spec_opportunity_ids(settings, monkeypatch):
    """generate_cohort passes spec.opportunity_ids as the generation scope — a
    bundle root shared with another cohort no longer means that cohort's opps
    get regenerated (#1166)."""
    from connect_labs.labs.synthetic.bundle import GDriveBundleStore
    from connect_labs.labs.synthetic.cohort import CohortSpec
    from connect_labs.labs.synthetic.tests.test_bundle import _FakeDrive as _FakeGDrive

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)

    drive = _FakeGDrive()
    run_folder = drive.create_folder("run", "parent")
    store = GDriveBundleStore(drive, run_folder)
    for oid in (523, 524):
        store.write(
            oid,
            manifest_yaml=_manifest(oid),
            app_structure={"learn_app": None, "deliver_app": {"modules": []}},
            opportunity={"id": oid, "name": f"KMC-{oid}"},
        )

    spec = CohortSpec(
        opportunity_ids=[523],
        program_id=10010,
        program_name="KMC (Synthetic)",
        org_name="Dimagi-KMC (Synthetic)",
        bundle_root=f"gdrive:{run_folder}",
    )
    _, results = clone_from_prod.generate_cohort(spec, drive=drive, fresh=True)

    assert [r.source_opportunity_id for r in results] == [523]
    assert not SyntheticOpportunity.objects.filter(cloned_from_opportunity_id=524).exists()


def test_generate_target_opportunity_id_leaves_existing_twin_untouched(tmp_path, settings, monkeypatch):
    """target_opportunity_id registers onto the named opp and never touches the
    source's existing twin — the second-twin path for a source whose twin is
    claimed by an env-owned opp (#1166)."""
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    bundle = _bundle(tmp_path)
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)

    # An existing twin for source 523, as an env-owned opp would hold it.
    SyntheticOpportunity.objects.create(
        opportunity_id=10012,
        label="Eastern Cluster",
        labs_only=True,
        enabled=True,
        gdrive_folder_id="env-folder",
        program_id=10110,
        cloned_from_opportunity_id=523,
    )

    result = clone_from_prod.generate_opp_from_bundle(
        bundle,
        drive=_FakeDrive(),
        program_id=10000,
        program_name="KMC (Synthetic)",
        org_name="Dimagi-KMC (Synthetic)",
        target_opportunity_id=10077,
    )

    assert result.skipped is False
    assert result.opportunity_id == 10077
    new_row = SyntheticOpportunity.objects.get(opportunity_id=10077)
    assert new_row.cloned_from_opportunity_id == 523
    untouched = SyntheticOpportunity.objects.get(opportunity_id=10012)
    assert untouched.label == "Eastern Cluster"
    assert untouched.gdrive_folder_id == "env-folder"
    assert untouched.program_id == 10110


def test_generate_cohort_uses_spec_program_id(settings, monkeypatch):
    """generate_cohort registers all opps under the spec's program_id (not auto-allocated)."""
    from connect_labs.labs.synthetic.bundle import GDriveBundleStore
    from connect_labs.labs.synthetic.cohort import CohortSpec
    from connect_labs.labs.synthetic.tests.test_bundle import _FakeDrive

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    monkeypatch.setattr(clone_from_prod, "_fetch_endpoint", lambda *a, **k: None)

    drive = _FakeDrive()
    run_folder = drive.create_folder("run", "parent")
    store = GDriveBundleStore(drive, run_folder)
    for oid in (523, 524):
        store.write(
            oid,
            manifest_yaml=_manifest(oid),
            app_structure={"learn_app": None, "deliver_app": {"modules": []}},
            opportunity={"id": oid, "name": f"KMC-{oid}"},
        )

    spec = CohortSpec(
        opportunity_ids=[523, 524],
        program_id=10010,
        program_name="KMC (Synthetic)",
        org_name="Dimagi-KMC (Synthetic)",
        bundle_root=f"gdrive:{run_folder}",
    )
    out_spec, results = clone_from_prod.generate_cohort(spec, drive=drive)

    assert out_spec.program_id == 10010
    assert len(results) == 2
    program_ids = {SyntheticOpportunity.objects.get(opportunity_id=r.opportunity_id).program_id for r in results}
    assert program_ids == {10010}
    # opp ids sit above the reserved program id (no collision):
    assert all(r.opportunity_id > 10010 for r in results)


# ---------------------------------------------------------------------------
# --no-register must honour spec.opportunity_ids (#1604).
#
# It did not, and because image_config is a SINGLE config applied to every
# bundle in the run, a spec narrowed to one opportunity regenerated all of them
# through settings chosen for that one. Observed 2026-09-08 on the KMC set: a
# spec naming only opp 675 replayed all eleven bundles, and the other ten
# produced zero images each before the run was killed.
# ---------------------------------------------------------------------------


def _bundle_for(tmp_path, opp_id: int):
    manifest_yaml = (
        f"opportunity_id: {opp_id}\n"
        "opportunity_name: KMC\n"
        "random_seed: 42\n"
        "timeline: {start_date: 2026-05-04, end_date: 2026-06-01, weeks: 4,"
        " visit_cadence_per_week_per_flw: {mean: 5, stddev: 1}}\n"
        "flw_personas: [{id: a, archetype: steady,"
        " accuracy_distribution: {mean: 0.8, stddev: 0.05},"
        " completeness_distribution: {mean: 0.8, stddev: 0.05}, flag_rate: 0.1}]\n"
        "beneficiary_cohorts: [{id: primary, size: 20, progression: flat,"
        ' field_distributions: {"form.w": {distribution: normal, mean: 12.0, stddev: 2.0}}}]\n'
        "kpi_config: [{kpi: a, field_path: form.w, aggregation: mean, threshold_underperform: 1.0}]\n"
    )
    return write_bundle(
        tmp_path,
        opp_id,
        manifest_yaml=manifest_yaml,
        app_structure={"learn_app": None, "deliver_app": {"modules": []}},
        opportunity={"id": opp_id, "name": "KMC"},
    )


def test_generate_fixtures_only_replays_just_the_named_opportunities(tmp_path, settings):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    for opp_id in (523, 675, 874):
        _bundle_for(tmp_path, opp_id)

    rows = clone_from_prod.generate_fixtures_only(str(tmp_path), drive=_FakeDrive(), opportunity_ids=[675])

    assert [r["source_opportunity_id"] for r in rows] == [675]


def test_generate_fixtures_only_without_a_selection_still_replays_everything(tmp_path, settings):
    """Bare --bundles has no spec and so no selection: behaviour is unchanged."""
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    for opp_id in (523, 675, 874):
        _bundle_for(tmp_path, opp_id)

    rows = clone_from_prod.generate_fixtures_only(str(tmp_path), drive=_FakeDrive())

    assert sorted(r["source_opportunity_id"] for r in rows) == [523, 675, 874]


def test_a_requested_opportunity_with_no_bundle_is_reported_not_silently_dropped(tmp_path, settings, caplog):
    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    _bundle_for(tmp_path, 675)

    with caplog.at_level("WARNING"):
        rows = clone_from_prod.generate_fixtures_only(str(tmp_path), drive=_FakeDrive(), opportunity_ids=[675, 999])

    assert [r["source_opportunity_id"] for r in rows] == [675]
    assert "999" in caplog.text, "a requested id with no bundle must be named, not silently skipped"
