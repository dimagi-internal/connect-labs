import datetime as dt

import pytest

from commcare_connect.audit.data_access import AuditDataAccess
from commcare_connect.labs.synthetic.ensure.engine import EnsureContext
from commcare_connect.labs.synthetic.ensure.ensurers.opp_data import ensure_opp_data
from commcare_connect.labs.synthetic.ensure.ensurers.run_audits import ensure_run_audits
from commcare_connect.labs.synthetic.ensure.ensurers.weekly_runs import ensure_weekly_runs
from commcare_connect.labs.synthetic.ensure.env_manifest import OppDataResource, RunAuditsResource, WeeklyRunsResource
from commcare_connect.labs.synthetic.registry import invalidate_cache

OPP_ID = 10_072
SEED = 17

# One rockstar + one struggling persona; an anomaly flagging the struggling
# persona on week index 1 (a MUAC field-outlier) that is marked AUDITED via
# reviewer_visible_in: [audit]. image_config provides the MUAC corpus the audit
# photos draw from.
MANIFEST_YAML = f"""
opportunity_id: {OPP_ID}
opportunity_name: PAR Run Audits Opp
random_seed: {SEED}
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 8
    stddev: 2
flw_personas:
  - id: asha
    display_name: Asha Mensah
    archetype: rockstar
    accuracy_distribution: {{ mean: 0.93, stddev: 0.02 }}
    completeness_distribution: {{ mean: 0.95, stddev: 0.03 }}
    flag_rate: 0.02
  - id: dele
    display_name: Dele Okonkwo
    archetype: struggling
    accuracy_distribution: {{ mean: 0.62, stddev: 0.05 }}
    completeness_distribution: {{ mean: 0.70, stddev: 0.05 }}
    flag_rate: 0.30
beneficiary_cohorts:
  - id: primary
    size: 100
    field_distributions:
      "form.case.update.soliciter_muac_cm":
        distribution: normal
        mean: 13.5
        stddev: 1.8
    progression: improvement_curve
anomalies:
  - id: dele_cherry_pick_wk2
    type: field_outlier
    flw_ids: [dele]
    field_path: form.case.update.soliciter_muac_cm
    week: 1
    detection_path: muac_distribution
    reviewer_visible_in: [audit]
image_config:
  good_image_count: 10
  bad_image_count: 10
  default_bad_rate: 0.1
  flw_bad_rates:
    dele: 0.6
kpi_config:
  - kpi: accuracy
    field_path: form.case.update.soliciter_muac_cm
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
"""


def _mondays(n: int, *, start: dt.date) -> list[str]:
    return [(start + dt.timedelta(weeks=i)).isoformat() for i in range(n)]


def _setup_ctx(tmp_path):
    (tmp_path / "opp.yaml").write_text(MANIFEST_YAML)
    invalidate_cache()
    weeks = _mondays(3, start=dt.date(2026, 2, 2))  # 3 completed Mondays
    current = (dt.date(2026, 2, 2) + dt.timedelta(weeks=3)).isoformat()
    ctx = EnsureContext(env_dir=tmp_path, weeks=weeks, current_week=current)
    ensure_opp_data(OppDataResource(kind="opp_data", opportunity_id=OPP_ID, manifest="opp.yaml"), ctx)
    ensure_weekly_runs(
        WeeklyRunsResource(kind="weekly_runs", opportunity_ids=[OPP_ID], template="chc_nutrition_analysis"),
        ctx,
    )
    return ctx, weeks, current


def _audits_for_run(opp_id, run_id):
    ada = AuditDataAccess(opportunity_id=opp_id, access_token="labs-only")
    try:
        return ada.get_sessions_by_workflow_run(run_id)
    finally:
        ada.close()


@pytest.mark.django_db
def test_creates_run_linked_completable_audit_for_flagged_flw(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)

    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)

    flagged_week = weeks[1]  # anomaly week index 1
    flagged_run_id = ctx.ids[f"run:{OPP_ID}:{flagged_week}"]

    audits = _audits_for_run(OPP_ID, flagged_run_id)
    dele_audits = [a for a in audits if a.data.get("username") == "dele"]
    assert len(dele_audits) == 1, "expected exactly one run-linked audit for dele on the flagged week"
    audit = dele_audits[0]

    # Run-linked: workflow_run_id resolves to the run's labs_record_id.
    assert audit.workflow_run_id == flagged_run_id

    # Carries the real display name.
    assert audit.data.get("flw_name") == "Dele Okonkwo"

    # Has MUAC images so the bulk page renders thumbnails.
    assert audit.data.get("visit_images"), "audit must carry visit_images"
    first_visit = next(iter(audit.data["visit_images"].values()))
    assert first_visit and first_visit[0].get("blob_id"), "each visit image needs a real blob_id"

    # Completable: in_progress + every photo undecided (no result) so it CAN be
    # completed live, and only then does "Complete Image Review" enable.
    assert audit.status == "in_progress"
    assert audit.overall_result is None
    for vid, result in audit.visit_results.items():
        assert not result.get("result"), f"visit {vid} must be undecided for the audit to be completable"

    # Stashed for the rollup ensurer's drill-target selection.
    assert ctx.ids[f"audit:{flagged_run_id}:dele"] == audit.id


@pytest.mark.django_db
def test_unflagged_flw_and_unflagged_week_get_no_audit(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)

    # Clean week (index 0): no audit for anyone.
    clean_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[0]}"]
    assert _audits_for_run(OPP_ID, clean_run_id) == []

    # Flagged week: asha (un-anomalied) gets no audit.
    flagged_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[1]}"]
    asha_audits = [a for a in _audits_for_run(OPP_ID, flagged_run_id) if a.data.get("username") == "asha"]
    assert asha_audits == []

    # Current-week in_progress run: never seeded (live manager flow owns it).
    current_run_id = ctx.ids[f"run:{OPP_ID}:{current}"]
    assert _audits_for_run(OPP_ID, current_run_id) == []


@pytest.mark.django_db
def test_rerun_is_idempotent(tmp_path):
    ctx, weeks, current = _setup_ctx(tmp_path)
    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)

    flagged_run_id = ctx.ids[f"run:{OPP_ID}:{weeks[1]}"]
    audit_id_first = ctx.ids[f"audit:{flagged_run_id}:dele"]
    count_first = len(_audits_for_run(OPP_ID, flagged_run_id))

    # Re-run on a fresh ctx (manifest + runs re-resolved to the same ids).
    ctx2, _, _ = _setup_ctx(tmp_path)
    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx2)

    count_second = len(_audits_for_run(OPP_ID, flagged_run_id))
    assert count_second == count_first, "re-run must not create duplicate audits"
    assert ctx2.ids[f"audit:{flagged_run_id}:dele"] == audit_id_first


# A manifest where the audited FLW also carries an INVESTIGATING coaching arc
# (no follow_up_outcome_week) — the audit should land the in-review MIX
# archetype (decided + pending photos), not the all-pending shape.
OPP_ID_MIX = 10_074
MANIFEST_INVESTIGATING_YAML = f"""
opportunity_id: {OPP_ID_MIX}
opportunity_name: PAR Run Audits Mix Opp
random_seed: 41
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 8
    stddev: 2
flw_personas:
  - id: asha
    display_name: Asha Mensah
    archetype: rockstar
    accuracy_distribution: {{ mean: 0.93, stddev: 0.02 }}
    completeness_distribution: {{ mean: 0.95, stddev: 0.03 }}
    flag_rate: 0.02
  - id: dele
    display_name: Dele Okonkwo
    archetype: struggling
    accuracy_distribution: {{ mean: 0.62, stddev: 0.05 }}
    completeness_distribution: {{ mean: 0.70, stddev: 0.05 }}
    flag_rate: 0.30
beneficiary_cohorts:
  - id: primary
    size: 100
    field_distributions:
      "form.case.update.soliciter_muac_cm":
        distribution: normal
        mean: 13.5
        stddev: 1.8
    progression: improvement_curve
anomalies:
  - id: dele_cherry_pick_wk2
    type: field_outlier
    flw_ids: [dele]
    field_path: form.case.update.soliciter_muac_cm
    week: 1
    detection_path: muac_distribution
    reviewer_visible_in: [audit]
coaching_arcs:
  - flw_id: dele
    week_triggered: 2
    persona: supportive_coach
    target_behavior: Incomplete household screening coverage
    transcript:
      - role: bot
        text: "Hi {{flw_name}}, your screening numbers came in low — which households did you reach?"
        ts: 2026-02-09T11:00:00Z
      - role: flw
        text: "Mostly the ones along the road; the far compounds were hard to reach."
        ts: 2026-02-09T11:06:00Z
image_config:
  good_image_count: 10
  bad_image_count: 10
  default_bad_rate: 0.1
  flw_bad_rates:
    dele: 0.6
kpi_config:
  - kpi: accuracy
    field_path: form.case.update.soliciter_muac_cm
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
"""


def _setup_ctx_investigating(tmp_path):
    (tmp_path / "opp.yaml").write_text(MANIFEST_INVESTIGATING_YAML)
    invalidate_cache()
    weeks = _mondays(3, start=dt.date(2026, 2, 2))
    current = (dt.date(2026, 2, 2) + dt.timedelta(weeks=3)).isoformat()
    ctx = EnsureContext(env_dir=tmp_path, weeks=weeks, current_week=current)
    ensure_opp_data(OppDataResource(kind="opp_data", opportunity_id=OPP_ID_MIX, manifest="opp.yaml"), ctx)
    ensure_weekly_runs(
        WeeklyRunsResource(kind="weekly_runs", opportunity_ids=[OPP_ID_MIX], template="chc_nutrition_analysis"),
        ctx,
    )
    return ctx, weeks, current


@pytest.mark.django_db
def test_investigating_arc_flw_gets_in_review_mix_audit(tmp_path):
    """A flagged FLW whose coaching arc is still OPEN gets the in-review MIX
    audit (some photos decided, some pending) — the scene-13 drill audit, not
    the all-pending ``pending_all_clean`` shape."""
    ctx, weeks, _ = _setup_ctx_investigating(tmp_path)

    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)

    flagged_run_id = ctx.ids[f"run:{OPP_ID_MIX}:{weeks[1]}"]
    audits = _audits_for_run(OPP_ID_MIX, flagged_run_id)
    dele_audits = [a for a in audits if a.data.get("username") == "dele"]
    assert len(dele_audits) == 1
    audit = dele_audits[0]

    # Still in review (not completed), but a GENUINE decided/undecided mix.
    assert audit.status == "in_progress"
    assert audit.overall_result is None
    img = audit.data.get("image_results") or {}
    decided = (img.get("pass") or 0) + (img.get("fail") or 0)
    pending = img.get("pending") or 0
    assert decided > 0, "in-review MIX must have at least one decided photo"
    assert pending > 0, "in-review MIX must have at least one pending photo"
    # The mix carries a real fail (a finding), not just passes.
    assert (img.get("fail") or 0) >= 1


@pytest.mark.django_db
def test_reconciles_stale_audit_on_reuse_to_in_review_mix(tmp_path):
    """A pre-existing seeded audit in the WRONG shape (all-pending) is rebuilt
    in place to the arc's current shape (in-review MIX) on reuse — same id,
    status reconciled. This is the stale-reuse bug: create-path picks the
    archetype, but a re-ensure must upgrade a record whose shape no longer
    matches the arc."""
    from commcare_connect.labs.synthetic.walkthrough_kit import generate_audit_from_archetype

    ctx, weeks, _ = _setup_ctx_investigating(tmp_path)
    flagged_run_id = ctx.ids[f"run:{OPP_ID_MIX}:{weeks[1]}"]

    # Seed a STALE audit for dele in the all-pending shape (the wrong archetype
    # for an investigating arc — it should be the in-review MIX).
    ada = AuditDataAccess(opportunity_id=OPP_ID_MIX, access_token="labs-only")
    try:
        stale_id = generate_audit_from_archetype(
            ada=ada,
            opportunity_id=OPP_ID_MIX,
            opportunity_name="PAR Run Audits Mix Opp",
            workflow_run_id=flagged_run_id,
            flw_id="dele",
            monday_iso=weeks[1],
            audit_archetype="pending_all_clean",
            visit_id=9_100_000,
            flw_name="Dele Okonkwo",
        )
    finally:
        ada.close()

    stale = _audits_for_run(OPP_ID_MIX, flagged_run_id)[0]
    stale_img = stale.data.get("image_results") or {}
    assert (stale_img.get("pass") or 0) + (stale_img.get("fail") or 0) == 0, "stale audit starts all-pending"

    # Re-ensure: the stale audit must be reconciled in place to the MIX shape.
    ensure_run_audits(RunAuditsResource(kind="run_audits"), ctx)

    after = _audits_for_run(OPP_ID_MIX, flagged_run_id)
    dele_after = [a for a in after if a.data.get("username") == "dele"]
    assert len(dele_after) == 1, "reconcile must NOT mint a duplicate audit"
    rebuilt = dele_after[0]
    assert rebuilt.id == stale_id, "reconcile keeps the same record id"
    img = rebuilt.data.get("image_results") or {}
    assert (img.get("pass") or 0) + (img.get("fail") or 0) > 0, "reconciled audit now shows decided photos"
    assert (img.get("pending") or 0) > 0, "reconciled audit still has pending photos (the mix)"
    assert ctx.ids[f"audit:{flagged_run_id}:dele"] == stale_id
