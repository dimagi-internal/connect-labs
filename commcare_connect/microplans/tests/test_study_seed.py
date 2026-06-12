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
    # The shared sampling config the study UI defaults match (size-balanced two-arm draw).
    assert m.sampling["size_balance_bands"] == 3
    assert m.sampling["target_clusters"] == 24
    assert m.sampling["primary_per_psu"] == 12

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
def test_ensure_study_builds_groups_plans_and_labs_side_arms(study):
    manifest, da = study
    out = study_seed.ensure_study(da, manifest, generate=False)

    assert len(out["rounds"]) == 6
    assert len(da.list_groups()) == 6
    assert len(da.list_plans()) == 12  # 2 wards × 6 rounds

    # Each round: a study group with 2 boundary-only ward plans, freshly created.
    for r in out["rounds"]:
        assert len(r["plan_ids"]) == 2
        assert len(r["created_plans"]) == 2
        group = da.get_group(r["group_id"])
        assert group.data["kind"] == "study"
        assert group.data["sampling_config"] == manifest.sampling
        # Plans are boundary-only (not sampled) when generate=False.
        for pid in r["plan_ids"]:
            assert da.get_plan(pid).phase == "boundary"

    # Arms are stored on the GROUP (labs-side), never written onto the plans.
    r6_out = next(o for o in out["rounds"] if o["key"] == "r6")
    group = da.get_group(r6_out["group_id"])
    arms = {da.get_plan(pid).name: group.arm_for(pid) for pid in r6_out["plan_ids"]}
    assert arms == {"Attakar": study_seed.ARM_INTERVENTION, "Gura": study_seed.ARM_COMPARISON}


@pytest.mark.django_db
def test_ensure_study_is_idempotent_no_duplicates(study):
    manifest, da = study
    first = study_seed.ensure_study(da, manifest, generate=False)
    second = study_seed.ensure_study(da, manifest, generate=False)

    # Re-run reuses everything: same group ids, nothing newly created, no dupes.
    first_gids = [r["group_id"] for r in first["rounds"]]
    second_gids = [r["group_id"] for r in second["rounds"]]
    assert first_gids == second_gids
    assert all(r["created_plans"] == [] for r in second["rounds"])
    assert len(da.list_groups()) == 6
    assert len(da.list_plans()) == 12


@pytest.mark.django_db
def test_reset_round_removes_only_that_round_then_can_recreate(study):
    manifest, da = study
    study_seed.ensure_study(da, manifest, generate=False)

    r6_name = manifest.round_by_key("r6").group_name
    reset = study_seed.reset_round(da, manifest, "r6")
    assert reset["group_id"] is not None
    assert len(reset["plan_ids"]) == 2

    # R6 gone; the other five rounds untouched.
    groups = {g.data["name"] for g in da.list_groups()}
    assert r6_name not in groups
    assert len(groups) == 5
    assert len(da.list_plans()) == 10

    # The creation walkthrough re-creates just R6 afterwards.
    study_seed.ensure_study(da, manifest, generate=False, only_round="r6")
    assert len(da.list_groups()) == 6
    assert len(da.list_plans()) == 12
    assert r6_name in {g.data["name"] for g in da.list_groups()}


@pytest.mark.django_db
def test_reset_round_is_safe_when_nothing_exists(study):
    manifest, da = study
    out = study_seed.reset_round(da, manifest, "r6")  # nothing seeded yet
    assert out["group_id"] is None and out["plan_ids"] == []
