import datetime as dt

import pytest

from commcare_connect.labs.synthetic.generator.manifest import Manifest, ManifestValidationError

VALID_MANIFEST_YAML = """
opportunity_id: 1237
opportunity_name: Demo
random_seed: 42
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
    accuracy_distribution: { mean: 0.92, stddev: 0.04 }
    completeness_distribution: { mean: 0.95, stddev: 0.03 }
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
anomalies: []
kpi_config:
  - kpi: accuracy
    field_path: form.weight_kg
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
coaching_arcs: []
"""


def test_manifest_parses_valid_yaml():
    m = Manifest.from_yaml(VALID_MANIFEST_YAML)
    assert m.opportunity_id == 1237
    assert m.random_seed == 42
    assert m.timeline.start_date == dt.date(2026, 2, 1)
    assert m.timeline.weeks == 4
    assert m.flw_personas[0].id == "asha"
    assert m.flw_personas[0].archetype == "rockstar"
    assert m.beneficiary_cohorts[0].size == 100
    assert m.kpi_config[0].kpi == "accuracy"


def test_manifest_rejects_unknown_archetype():
    bad = VALID_MANIFEST_YAML.replace("archetype: rockstar", "archetype: wizard")
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)


def test_manifest_rejects_negative_seed():
    bad = VALID_MANIFEST_YAML.replace("random_seed: 42", "random_seed: -1")
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)


def test_manifest_rejects_end_before_start():
    bad = VALID_MANIFEST_YAML.replace("end_date: 2026-02-28", "end_date: 2026-01-01")
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)


def test_manifest_rejects_coaching_arc_with_unknown_flw_id():
    base = "\n".join(line for line in VALID_MANIFEST_YAML.splitlines() if line.strip() != "coaching_arcs: []")
    bad = (
        base
        + """
coaching_arcs:
  - flw_id: not_a_real_persona
    week_triggered: 2
    persona: supportive_coach
    target_behavior: improve accuracy
    transcript:
      - { role: bot, text: hi, ts: 2026-02-15T09:00:00 }
"""
    )
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)
