"""Phase-2 clone tests: generate_opp_from_bundle (offline, idempotent) + generate_opps_bulk."""

import pytest

from commcare_connect.labs.synthetic import clone_from_prod
from commcare_connect.labs.synthetic.bundle import write_bundle
from commcare_connect.labs.synthetic.models import SyntheticOpportunity

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

    from commcare_connect.labs.synthetic.bundle import write_bundle

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
    from commcare_connect.labs.synthetic.bundle import GDriveBundleStore
    from commcare_connect.labs.synthetic.tests.test_bundle import _FakeDrive

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


def test_generate_cohort_uses_spec_program_id(settings, monkeypatch):
    """generate_cohort registers all opps under the spec's program_id (not auto-allocated)."""
    from commcare_connect.labs.synthetic.bundle import GDriveBundleStore
    from commcare_connect.labs.synthetic.cohort import CohortSpec
    from commcare_connect.labs.synthetic.tests.test_bundle import _FakeDrive

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
