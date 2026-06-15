"""Idempotent study-design seeder (``microplans.study_seed``).

Drives the REAL shared manifest (``verified-monitoring/demo_config.json`` + the Kaura
wards geojson) against the labs DB, with ``generate=False`` so no Overture building
fetch is needed — the structural contract (groups by name, ward plans by boundary_id,
labs-side arms, idempotency, reset) is what these assert. The sampling pass itself is
the shared ``sample_group_plans`` path, covered by the sampling tests.
"""

from __future__ import annotations

import pytest

from commcare_connect.microplans import study_seed


def test_load_manifest_from_real_shared_config():
    m = study_seed.load_manifest()
    assert m.opportunity_id == 10008
    assert m.program_id == 10008
    assert len(m.rounds) == 6
    # The study leaves `sampling` empty and INHERITS the canonical defaults (the single
    # source of truth) — the same draw the plan-creation UI uses. Assert it resolves to
    # SAMPLING_DEFAULTS via FrameConfig, so the synthetic plans and the UI stay in sync.
    from commcare_connect.microplans.sampling.defaults import SAMPLING_DEFAULTS
    from commcare_connect.microplans.sampling.frame import FrameConfig

    assert m.sampling == {}
    fc = FrameConfig.from_payload(m.sampling)
    assert fc.size_balance_bands == SAMPLING_DEFAULTS["size_balance_bands"] == 3
    assert fc.target_clusters == SAMPLING_DEFAULTS["target_clusters"]
    assert fc.primary_per_psu == SAMPLING_DEFAULTS["primary_per_psu"]
    assert fc.alternates_per_psu == SAMPLING_DEFAULTS["alternates_per_psu"]

    r6 = m.round_by_key("r6")
    assert r6.live_demo is True
    assert r6.label == "R6 — Attakar × Gura"
    intervention, comparison = r6.wards
    assert (intervention.name, intervention.arm) == ("Attakar", study_seed.ARM_INTERVENTION)
    assert (comparison.name, comparison.arm) == ("Gura", study_seed.ARM_COMPARISON)
    # Wards carry real admin-boundary identity + geometry (grounding, not placeholders).
    for w in r6.wards:
        assert w.boundary_id and w.geometry.get("type")
    # Only the live-demo round is flagged.
    assert [r.live_demo for r in m.rounds] == [False, False, False, False, False, True]


@pytest.fixture
def study(db):
    """The manifest + a labs-only-backed data access, opp ensured via the seeder."""
    manifest = study_seed.load_manifest()
    study_seed.ensure_synthetic_program(manifest)
    return manifest, study_seed.data_access_for(manifest)


@pytest.mark.django_db
def test_ensure_synthetic_program_is_idempotent_and_labs_only():
    from commcare_connect.labs.synthetic.models import SyntheticOpportunity

    manifest = study_seed.load_manifest()
    study_seed.ensure_synthetic_program(manifest)
    study_seed.ensure_synthetic_program(manifest)  # second call must not duplicate/raise
    rows = SyntheticOpportunity.objects.filter(opportunity_id=10008)
    assert rows.count() == 1
    assert rows.first().labs_only and rows.first().enabled


@pytest.mark.django_db
def test_ensure_study_builds_one_two_arm_plan_per_round(study):
    manifest, da = study
    out = study_seed.ensure_study(da, manifest, generate=False)

    assert len(out["rounds"]) == 6
    assert len(da.list_groups()) == 0  # single-plan model: no study groups
    assert len(da.list_plans()) == 6  # ONE two-arm plan per round

    # Each round: one boundary-only two-arm plan, freshly created, both wards as
    # arm-tagged input_areas.
    for r in out["rounds"]:
        assert r["plan_id"] is not None
        assert r["created_plans"] == [r["plan_id"]]
        plan = da.get_plan(r["plan_id"])
        assert plan.phase == "boundary"  # not sampled when generate=False
        ias = plan.data["input_areas"]
        assert len(ias) == 2
        assert {a["arm"] for a in ias} == {study_seed.ARM_INTERVENTION, study_seed.ARM_COMPARISON}

    # Arms live on the plan's input_areas (the two-arm single-plan shape), not a group.
    r6 = next(o for o in out["rounds"] if o["key"] == "r6")
    arms = {a["name"]: a["arm"] for a in da.get_plan(r6["plan_id"]).data["input_areas"]}
    assert arms == {"Attakar": study_seed.ARM_INTERVENTION, "Gura": study_seed.ARM_COMPARISON}


@pytest.mark.django_db
def test_ensure_study_is_idempotent_no_duplicates(study):
    manifest, da = study
    first = study_seed.ensure_study(da, manifest, generate=False)
    second = study_seed.ensure_study(da, manifest, generate=False)

    # Re-run reuses the same plans, creates nothing new, no dupes (and no groups).
    first_pids = [r["plan_id"] for r in first["rounds"]]
    second_pids = [r["plan_id"] for r in second["rounds"]]
    assert first_pids == second_pids
    assert all(r["created_plans"] == [] for r in second["rounds"])
    assert len(da.list_groups()) == 0
    assert len(da.list_plans()) == 6


@pytest.mark.django_db
def test_reset_round_removes_only_that_round_then_can_recreate(study):
    manifest, da = study
    out = study_seed.ensure_study(da, manifest, generate=False)
    r6_plan_id = next(o for o in out["rounds"] if o["key"] == "r6")["plan_id"]

    reset = study_seed.reset_round(da, manifest, "r6")
    assert reset["plan_id"] == r6_plan_id
    assert r6_plan_id in reset["plan_ids"]

    # R6's plan gone; the other five rounds untouched.
    assert r6_plan_id not in {p.id for p in da.list_plans()}
    assert len(da.list_plans()) == 5

    # The creation walkthrough re-creates just R6 afterwards.
    study_seed.ensure_study(da, manifest, generate=False, only_round="r6")
    assert len(da.list_plans()) == 6


@pytest.mark.django_db
def test_reset_round_is_safe_when_nothing_exists(study):
    manifest, da = study
    out = study_seed.reset_round(da, manifest, "r6")  # nothing seeded yet
    assert out["group_id"] is None and out["plan_ids"] == []
