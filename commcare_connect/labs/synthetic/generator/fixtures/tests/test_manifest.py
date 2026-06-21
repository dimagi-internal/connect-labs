import datetime as dt

import pytest
from pydantic import ValidationError

from commcare_connect.labs.synthetic.generator.fixtures.manifest import (
    BeneficiaryCohort,
    CategoricalDistribution,
    CorrelationSpec,
    LongitudinalSpec,
    Manifest,
    ManifestValidationError,
    MeanStddev,
    NormalDistribution,
    TemporalProfile,
    TrajectoryParams,
)

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

_YAML_WITH_TEMPORAL = (
    VALID_MANIFEST_YAML
    + """
temporal:
  day_of_week: [1, 1, 1, 1, 1, 0.2, 0.1]
  hour_of_day: [0,0,0,0,0,0,0,1,2,3,4,5,6,5,4,3,2,1,0,0,0,0,0,0]
flag_reason_distribution:
  "GPS outside service area": 1.0
"""
)


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


def _normal(mean: float, stddev: float) -> NormalDistribution:
    return NormalDistribution(mean=mean, stddev=stddev)


def test_longitudinal_synthetic_carries_per_field_trajectory_params():
    spec = LongitudinalSpec(
        mode="synthetic",
        fields={
            "form.weight": TrajectoryParams(
                model="trajectory",
                intercept=MeanStddev(mean=1200, stddev=300),
                slope=MeanStddev(mean=25, stddev=5),
                residual_std=15.0,
                x_axis="day",
            )
        },
    )
    assert spec.mode == "synthetic"
    assert spec.fields["form.weight"].slope.mean == 25
    assert spec.fields["form.weight"].autocorr == 0.0  # default for a non-AR field


def test_longitudinal_mirror_requires_a_non_empty_transplant_pool():
    with pytest.raises(ValidationError):
        LongitudinalSpec(mode="mirror", transplant_pool=[])


def test_trajectory_autocorr_must_be_in_unit_interval():
    with pytest.raises(ValidationError):
        TrajectoryParams(
            model="autoregressive",
            intercept=MeanStddev(mean=0, stddev=1),
            slope=MeanStddev(mean=0, stddev=0),
            residual_std=1.0,
            autocorr=1.5,
        )


def test_cohort_carries_longitudinal_mirror_pool_and_defaults_to_none():
    plain = BeneficiaryCohort(
        id="primary",
        size=10,
        field_distributions={"form.weight": _normal(1200, 300)},
        progression="flat",
    )
    assert plain.longitudinal is None  # backward compatible — existing cohorts unaffected

    mirror = BeneficiaryCohort(
        id="primary",
        size=10,
        field_distributions={"form.weight": _normal(1200, 300)},
        progression="improvement_curve",
        longitudinal=LongitudinalSpec(
            mode="mirror",
            transplant_pool=[
                {
                    "owner": "flw_001",
                    "start_date": "2026-01-01",
                    "visits": [{"day": 0, "values": {"form.weight": 1200.0}}],
                }
            ],
        ),
    )
    assert mirror.longitudinal.mode == "mirror"
    assert mirror.longitudinal.transplant_pool[0]["owner"] == "flw_001"


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


_MINIMAL_YAML_HEADER = """
opportunity_id: 1
opportunity_name: Test
random_seed: 42
timeline:
  start_date: '2026-01-01'
  end_date: '2026-01-29'
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 10
    stddev: 2
flw_personas:
  - id: a
    archetype: steady
    accuracy_distribution: {mean: 0.8, stddev: 0.1}
    completeness_distribution: {mean: 0.9, stddev: 0.05}
    flag_rate: 0.05
beneficiary_cohorts:
  - {id: c1, size: 10, field_distributions: {}, progression: flat}
kpi_config:
  - {kpi: acc, field_path: form.x, aggregation: validated_rate, threshold_underperform: 0.7}
"""


def test_manifest_with_tasks():
    yaml_str = (
        _MINIMAL_YAML_HEADER
        + """
tasks:
  - flw_id: a
    title: Follow up on flagged visits
    priority: high
    status: completed
    created_week: 2
"""
    )
    m = Manifest.from_yaml(yaml_str)
    assert len(m.tasks) == 1
    assert m.tasks[0].flw_id == "a"
    assert m.tasks[0].priority == "high"
    assert m.tasks[0].created_week == 2


def test_manifest_tasks_default_empty():
    m = Manifest.from_yaml(_MINIMAL_YAML_HEADER)
    assert m.tasks == []


def test_manifest_with_image_config():
    yaml_str = (
        _MINIMAL_YAML_HEADER
        + """
image_config:
  question_path: form.muac_group.muac_photo
  stock_image_count: 15
  probability: 0.85
"""
    )
    m = Manifest.from_yaml(yaml_str)
    assert m.image_config is not None
    assert m.image_config.probability == 0.85
    assert m.image_config.stock_image_count == 15


def test_manifest_task_validates_flw_ref():
    yaml_str = (
        _MINIMAL_YAML_HEADER
        + """
tasks:
  - flw_id: unknown_flw
    title: Should fail
    priority: high
    status: pending
    created_week: 1
"""
    )
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(yaml_str)


def test_categorical_distribution_normalizes_and_validates():
    d = CategoricalDistribution(distribution="categorical", values={"male": 0.7, "female": 0.3})
    assert d.values["male"] == 0.7
    assert d.null_rate == 0.0


def test_categorical_rejects_negative_rate():
    with pytest.raises(Exception):
        CategoricalDistribution(distribution="categorical", values={"x": -0.1})


def test_null_rate_on_normal_distribution():
    d = NormalDistribution(mean=1.0, stddev=0.5, null_rate=0.2)
    assert d.null_rate == 0.2


def test_correlation_spec_dimension_check():
    ok = CorrelationSpec(fields=["a", "b"], matrix=[[1.0, 0.5], [0.5, 1.0]])
    assert ok.method == "spearman"
    with pytest.raises(ValidationError):
        CorrelationSpec(fields=["a", "b"], matrix=[[1.0, 0.5]])  # not 2x2


def test_temporal_profile_lengths():
    TemporalProfile(day_of_week=[1] * 7, hour_of_day=[1] * 24)
    with pytest.raises(ValidationError):
        TemporalProfile(day_of_week=[1] * 6, hour_of_day=[1] * 24)


def test_weekly_volume_multipliers_optional_and_validated():
    base = Manifest.from_yaml(_YAML_WITH_TEMPORAL)
    assert base.temporal is not None
    assert len(base.temporal.day_of_week) == 7
    assert base.flag_reason_distribution["GPS outside service area"] == 1.0
