import pytest

from connect_labs.labs.synthetic.ensure.engine import EnsureContext
from connect_labs.labs.synthetic.ensure.ensurers.opp_data import ensure_opp_data
from connect_labs.labs.synthetic.ensure.env_manifest import OppDataResource
from connect_labs.labs.synthetic.generator.fixtures.manifest import Manifest
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.registry import get_synthetic_opp, invalidate_cache

OPP_ID = 10_042

MANIFEST_YAML = f"""
opportunity_id: {OPP_ID}
opportunity_name: PAR Demo Opp
random_seed: 7
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 8
    stddev: 2
flw_personas:
  - id: asha
    display_name: Asha M.
    archetype: rockstar
    accuracy_distribution: {{ mean: 0.92, stddev: 0.04 }}
    completeness_distribution: {{ mean: 0.95, stddev: 0.03 }}
    flag_rate: 0.02
beneficiary_cohorts:
  - id: primary
    size: 100
    field_distributions:
      "form.weight_kg":
        distribution: normal
        mean: 12.4
        stddev: 2.1
    progression: improvement_curve
kpi_config:
  - kpi: accuracy
    field_path: form.weight_kg
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
"""


def _write_manifest(tmp_path):
    path = tmp_path / "opp.yaml"
    path.write_text(MANIFEST_YAML)
    return path


@pytest.mark.django_db
def test_ensure_opp_data_registers_opp_and_stashes_manifest(tmp_path):
    _write_manifest(tmp_path)
    invalidate_cache()

    resource = OppDataResource(kind="opp_data", opportunity_id=OPP_ID, manifest="opp.yaml")
    ctx = EnsureContext(env_dir=tmp_path)

    result = ensure_opp_data(resource, ctx)

    # Opp is registered, enabled, and visible via the registry.
    opp = get_synthetic_opp(OPP_ID)
    assert opp is not None
    assert opp.enabled is True
    assert opp.labs_only is True
    assert opp.label == "PAR Demo Opp"

    # Manifest is stashed on the context for downstream ensurers.
    stashed = ctx.ids[f"manifest:{OPP_ID}"]
    assert isinstance(stashed, Manifest)
    assert stashed.opportunity_id == OPP_ID

    # Readiness marker returned for the realized map.
    assert result == {f"opp_{OPP_ID}_ready": True}


@pytest.mark.django_db
def test_ensure_opp_data_is_idempotent(tmp_path):
    _write_manifest(tmp_path)
    invalidate_cache()

    resource = OppDataResource(kind="opp_data", opportunity_id=OPP_ID, manifest="opp.yaml")

    ensure_opp_data(resource, EnsureContext(env_dir=tmp_path))
    count_after_first = SyntheticOpportunity.objects.filter(opportunity_id=OPP_ID).count()

    ensure_opp_data(resource, EnsureContext(env_dir=tmp_path))
    count_after_second = SyntheticOpportunity.objects.filter(opportunity_id=OPP_ID).count()

    assert count_after_first == 1
    assert count_after_second == 1
