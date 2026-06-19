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
