import pytest

from commcare_connect.labs.synthetic.ensure.env_manifest import EnvManifest, EnvManifestError

GOLDEN = """
env: demo
timeline: {completed_weeks: 4, include_current_week: true}
resources:
  - {kind: opp_data, opportunity_id: 10000, manifest: m/n.yaml}
  - {kind: weekly_runs, opportunity_ids: [10000], template: chc_nutrition_analysis}
  - {kind: rollup, opportunity_ids: [10000], template: program_admin_report}
"""


def test_parses_golden():
    em = EnvManifest.from_yaml(GOLDEN)
    assert em.env == "demo"
    assert em.timeline.completed_weeks == 4
    assert [r.kind for r in em.resources] == ["opp_data", "weekly_runs", "rollup"]


def test_start_monday_pins_window():
    em = EnvManifest.from_yaml(
        "env: d\ntimeline: {completed_weeks: 4, include_current_week: true, start_monday: 2026-05-04}\n"
        "resources: [{kind: opp_data, opportunity_id: 1, manifest: m.yaml}]"
    )
    assert em.timeline.start_monday is not None
    assert em.timeline.start_monday.isoformat() == "2026-05-04"


def test_start_monday_must_be_a_monday():
    with pytest.raises(EnvManifestError, match="must be a Monday"):
        EnvManifest.from_yaml(
            "env: d\ntimeline: {completed_weeks: 4, start_monday: 2026-05-05}\n"  # Tuesday
            "resources: [{kind: opp_data, opportunity_id: 1, manifest: m.yaml}]"
        )


def test_unknown_kind_rejected():
    with pytest.raises(EnvManifestError):
        EnvManifest.from_yaml("env: d\ntimeline: {completed_weeks: 1}\nresources: [{kind: nope}]")


def test_opp_data_requires_manifest():
    with pytest.raises(EnvManifestError):
        EnvManifest.from_yaml(
            "env: d\ntimeline: {completed_weeks: 1}\nresources: [{kind: opp_data, opportunity_id: 1}]"
        )
