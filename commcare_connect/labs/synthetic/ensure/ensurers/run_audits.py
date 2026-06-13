"""The ``run_audits`` ensurer: run-linked, COMPLETABLE audits from anomalies.

For every opp whose manifest was stashed by ``opp_data`` (``ctx.ids["manifest:*"]``),
this ensurer walks the manifest's ``anomalies`` and, for each anomaly the manifest
marks as *audited* (``"audit"`` in ``reviewer_visible_in``), ensures a run-linked
``AuditSession`` for each flagged FLW on each week the anomaly concentrates on.

**Why "completable" is the whole point.** The PAR walkthrough's scene clicks
"Complete Image Review" and waits for the redirect to ``/labs/workflow/``. That
redirect only fires when the audit is:

1. **run-linked** — ``AuditSessionRecord.workflow_run_id == labs_record_id == run_id``
   (the bulk-assessment page reads this to know which run to return to), and
2. **decidable** — every photo is still PENDING (no result), so the page offers a
   live pass/fail decision per photo and only enables "Complete Image Review" once
   all are decided.

Both are guaranteed by porting the shared kit directly:

- run-linked: ``generate_audit_from_archetype`` passes ``labs_record_id=run_id``
  to ``create_record`` (so ``workflow_run_id`` resolves to the run), and
- decidable: the ``pending_all_clean`` audit archetype lands status ``in_progress``
  with all 5 photos from the good MUAC corpus UNREVIEWED (no result yet) —
  the same archetype ``program_admin_demo``'s manager-flow seeds for the live
  "Complete Image Review" demo (see ``manager_flow_views.py``). The photos carry
  real MUAC blob_ids so the bulk page renders thumbnails.

**Source of WHICH flws / WHICH weeks.** Both come from the manifest, NOT an inline
roster:

- an anomaly's ``flw_ids`` are the flagged workers,
- an anomaly's ``week`` (single) + ``weeks`` (list) are the week INDICES it hits,
- a persona's ``display_name`` is stamped as the audit's real ``flw_name``.

The current-week ``in_progress`` run is skipped: that's the live manager-flow week,
whose audits the walkthrough recorder creates on camera.

**Idempotency** is keyed on ``(workflow_run_id, flw_id)`` via
``AuditDataAccess.get_sessions_by_workflow_run``: a matching audit is reused, a
missing one is created. Re-runs are therefore count-stable.

**Realized vars.** This ensurer does NOT emit the PAR ``good_*`` / ``incomplete_*``
audit drill-target vars. Selecting which (opp, run, flw) audit is the "good"
(resolved-cluster) vs "incomplete" (in-review-cluster) drill target is a cross-opp
decision the rollup ensurer (Task 8) makes from the full PAR snapshot — the same
deferral ``weekly_runs`` uses for run-level drill targets. Instead, this ensurer
stashes ``ctx.ids[f"audit:{run_id}:{flw_id}"] = audit_id`` for every audit it
ensures, so the rollup can pick the named targets from a complete map.

It reuses the shared synthetic kit's ``generate_audit_from_archetype`` (which calls
``archetypes.build_audit_data``) directly — no dependency on PAR-specific helpers in
``program_admin_demo.py``. (``_display_name_for`` is re-implemented here, matching
``weekly_runs``, rather than importing the demo module.)
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Mirrors weekly_runs: labs-only opps short-circuit to the in-process backend, so
# the token is never sent anywhere.
_LABS_ONLY_TOKEN = "labs-only"  # noqa: S105 (not a secret)

# The completable audit archetype: status in_progress, all 5 photos from the good
# MUAC corpus, every one PENDING (no result) so the reviewer can decide each live
# and "Complete Image Review" finalizes once all are decided. Same archetype the
# manager-flow demo uses (manager_flow_views.py).
_COMPLETABLE_AUDIT_ARCHETYPE = "pending_all_clean"

# Marker in an anomaly's ``reviewer_visible_in`` that means "this flag gets an
# audit". Anomalies without it still drive pipeline-row flags (weekly_runs) but no
# audit record.
_AUDIT_VISIBILITY = "audit"

# Seeded visit-id bases live in the 9_000_000..9_499_999 sub-range of the synthetic
# visit-id namespace, disjoint from live manager-flow audits (LIVE_VISIT_ID_FLOOR =
# 9_500_000). Bases are derived deterministically per (opp, run, flw) so re-creates
# (if an audit were ever rebuilt) reproduce the same visit ids.
_SEEDED_VISIT_ID_FLOOR = 9_000_000
_SEEDED_VISIT_ID_SPAN = 500_000


def _display_name_for(persona) -> str:
    """Real human name for a persona: its ``display_name`` or a title-cased id.

    Mirrors ``weekly_runs._display_name_for`` so audits and runs agree on names.
    """
    if persona.display_name:
        return persona.display_name
    token = persona.id.split("_", 1)[0]
    return token[:1].upper() + token[1:] if token else persona.id


def _seeded_visit_id_base(manifest_seed: int, run_id: int, flw_id: str) -> int:
    """Deterministic, collision-clear visit-id base for one (run, flw) audit."""
    return _SEEDED_VISIT_ID_FLOOR + (hash((manifest_seed, run_id, flw_id)) % _SEEDED_VISIT_ID_SPAN)


def _audited_flw_weeks(manifest) -> dict[str, set[int]]:
    """Map ``flw_id`` -> set of week INDICES that anomaly carries an audit.

    Only anomalies whose ``reviewer_visible_in`` contains ``"audit"`` count; an
    anomaly's audited weeks are its ``week`` (single) plus ``weeks`` (list).
    """
    out: dict[str, set[int]] = {}
    for anomaly in manifest.anomalies:
        if _AUDIT_VISIBILITY not in (anomaly.reviewer_visible_in or []):
            continue
        weeks: set[int] = set(anomaly.weeks or [])
        if anomaly.week is not None:
            weeks.add(anomaly.week)
        if not weeks:
            continue
        for flw_id in anomaly.flw_ids:
            out.setdefault(flw_id, set()).update(weeks)
    return out


def ensure_run_audits(resource, ctx) -> dict:
    """Ensure run-linked completable audits for every audited (flw, week) anomaly.

    ``resource`` is a :class:`~..env_manifest.RunAuditsResource`; ``ctx`` is the
    run's :class:`~..engine.EnsureContext`. For each stashed ``manifest:<opp>``,
    for each anomaly marked ``reviewer_visible_in: [audit]``, ensures one
    ``AuditSession`` per flagged FLW per audited week (skipping the current-week
    in_progress run). Stashes ``ctx.ids[f"audit:{run_id}:{flw_id}"] = audit_id``
    for the rollup ensurer to select PAR drill targets from. Returns an empty
    realized map — the ``good_*`` / ``incomplete_*`` audit vars are deferred to
    the rollup ensurer's cross-opp PAR-snapshot walk.
    """
    from commcare_connect.audit.data_access import AuditDataAccess
    from commcare_connect.labs.synthetic.walkthrough_kit import generate_audit_from_archetype

    weeks = ctx.weeks
    current_week = ctx.current_week

    manifest_keys = sorted(k for k in ctx.ids if isinstance(k, str) and k.startswith("manifest:"))

    audits_ensured = 0
    audits_reused = 0

    for key in manifest_keys:
        manifest = ctx.ids[key]
        opp_id = manifest.opportunity_id
        persona_by_id = {p.id: p for p in manifest.flw_personas}
        audited = _audited_flw_weeks(manifest)
        if not audited:
            continue

        ada = AuditDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            for flw_id, week_idxs in audited.items():
                persona = persona_by_id.get(flw_id)
                if persona is None:
                    # The manifest validator already rejects anomalies that
                    # reference unknown flw_ids, so this is defensive only.
                    continue
                flw_name = _display_name_for(persona)
                for week_idx in sorted(week_idxs):
                    if week_idx < 0 or week_idx >= len(weeks):
                        # Anomaly week index outside the resolved completed window
                        # (e.g. it targets only the live current week). No
                        # completed run exists to link an audit to.
                        continue
                    monday_iso = weeks[week_idx]
                    if monday_iso == current_week:
                        # The live manager-flow week: its audits are created on
                        # camera by the recorder, not seeded here.
                        continue
                    run_id = ctx.ids.get(f"run:{opp_id}:{monday_iso}")
                    if run_id is None:
                        raise KeyError(
                            f"run_audits: no run stashed for opp {opp_id} week {monday_iso} "
                            "(weekly_runs must run before run_audits)"
                        )

                    existing = [
                        s for s in ada.get_sessions_by_workflow_run(run_id) if s.data.get("username") == flw_id
                    ]
                    if existing:
                        audit_id = existing[0].id
                        audits_reused += 1
                    else:
                        audit_id = generate_audit_from_archetype(
                            ada=ada,
                            opportunity_id=opp_id,
                            opportunity_name=manifest.opportunity_name,
                            workflow_run_id=run_id,
                            flw_id=flw_id,
                            monday_iso=monday_iso,
                            audit_archetype=_COMPLETABLE_AUDIT_ARCHETYPE,
                            visit_id=_seeded_visit_id_base(manifest.random_seed, run_id, flw_id),
                            flw_name=flw_name,
                        )
                        audits_ensured += 1

                    ctx.ids[f"audit:{run_id}:{flw_id}"] = audit_id
        finally:
            ada.close()

    logger.info(
        "run_audits: ensured %d new + reused %d existing run-linked audits",
        audits_ensured,
        audits_reused,
    )

    # PAR good_*/incomplete_* audit drill targets are selected by the rollup
    # ensurer from ctx.ids["audit:*"]; nothing authoritative to emit here.
    return {}
