"""The ``weekly_runs`` ensurer: per-opp weekly chc_nutrition saved runs.

For each opportunity, this ensurer materializes the weekly review the Program
Admin Report watches:

- a ``chc_nutrition_analysis`` workflow DEFINITION (reused on re-runs),
- one COMPLETED ``workflow_run`` per completed week in ``ctx.weeks`` (skipping
  ``resource.missed_week_idxs``), each carrying a pipeline snapshot of per-FLW
  rows and the auto-flags the chc render's ``ensureAutoFlags`` would have created
  on a live run,
- optionally one ``in_progress`` run for ``ctx.current_week`` (the live
  manager-flow week), flag-free and audit/task-free.

**The per-FLW signal is sourced FROM THE MANIFEST**, not the inline archetype
roster ``program_admin_demo`` used. The mapping (see ``_persona_week_signal``):

- ``name`` (display) = ``persona.display_name`` (falls back to a title-cased id);
  ``username`` = ``persona.id``.
- approval %% (``approved_visits`` / ``total_visits``) ← ``persona.accuracy_distribution``
  for the week (a deterministic draw around its mean, with the persona's
  ``improvement_arc`` lift applied from ``intervention_week`` onward).
- SAM/MAM signal: a clean (un-anomalied) persona-week reads as an HONEST MUAC
  distribution that carries the expected SAM/MAM baseline (no flag). A persona
  an anomaly concentrates on (a MUAC field-outlier) reads as cherry-picking —
  SAM/MAM bins emptied so the row trips ``sam_low``/``mam_low``. The cohort's
  ``field_distributions`` + ``persona.field_overrides`` are what *declare* this
  in the manifest; the row's actual MUAC bins are produced by the shared
  ``archetypes.build_flw_pipeline_row`` machinery driven off that anomaly signal.
- flags on a given week ← ``anomalies`` whose ``flw_ids`` include the persona AND
  whose ``week``/``weeks`` include that week index. A field-path anomaly on the
  MUAC measurement drives a MUAC (SAM/MAM) issue; one on the gender field drives
  a gender-skew issue. The resulting row's MUAC bins / gender split then trip the
  *same* chc FLAG_CATALOG thresholds the render uses, so the seeded Flag records
  (via ``_seed_auto_flags_for_run``) and the live render agree byte-for-byte.

Everything is DETERMINISTIC: every random draw is seeded from
``manifest.random_seed`` + opp + week + flw, so re-runs are bit-stable.

Idempotency: each run is keyed on ``(opportunity_id, week_start)`` by reading the
opp's existing chc runs and matching ``period_start``. A matching run is reused;
a missing one is created. If ``resource.current_week.reset`` is set, ONLY the
current-week ``in_progress`` run is deleted and rebuilt.

This ensurer does NOT create audits or tasks — those are the ``run_audits`` /
``tasks`` ensurers (later resources), which find the runs this ensurer stamped on
``ctx.ids[f"run:{opp}:{week_start}"]``.
"""

from __future__ import annotations

import logging
import random
from typing import Any

logger = logging.getLogger(__name__)

# A non-empty placeholder token: WorkflowDataAccess requires *some* access_token,
# but for labs-only opps (id >= LABS_ONLY_OPP_ID_FLOOR) every read/write
# short-circuits to the in-process local records backend and the token is never
# sent anywhere. See the api_client `_is_labs_only` dispatch.
_LABS_ONLY_TOKEN = "labs-only"  # noqa: S105 (not a secret)

# Gender measurement field paths the chc template extracts male/female from.
# An anomaly whose ``field_path`` targets one of these drives a gender-skew
# issue (vs the default MUAC/SAM-MAM issue). Mirrors the chc template's
# male_count/female_count pipeline field paths.
_GENDER_FIELD_PATHS = (
    "form.additional_case_info.childs_gender",
    "form.child_registration.childs_gender",
    "form.subcase_0.case.update.childs_gender",
)


# ---------------------------------------------------------------------- #
# Manifest -> per-(flw, week) signal
# ---------------------------------------------------------------------- #


def _flw_seed(manifest_seed: int, opp_id: int, week_idx: int, flw_id: str) -> int:
    """Deterministic 32-bit seed for one (opp, week, flw) draw.

    Uses ``hash`` on a tuple including the manifest's ``random_seed`` so
    re-runs of the ensurer reproduce the same rows. (``hash`` is salted per
    process, but the row values are persisted on first create and reused on
    match, so cross-process stability is provided by idempotency, not the
    seed; within a process the seed is stable.)
    """
    return hash((manifest_seed, opp_id, week_idx, flw_id)) & 0xFFFFFFFF


def _display_name_for(persona) -> str:
    """Real human name for a persona: its ``display_name`` or a title-cased id."""
    if persona.display_name:
        return persona.display_name
    token = persona.id.split("_", 1)[0]
    return token[:1].upper() + token[1:] if token else persona.id


def _anomaly_kpi_issue_for_week(manifest, flw_id: str, week_idx: int) -> str | None:
    """Return the KPI issue an anomaly concentrates on this FLW this week.

    ``"muac"`` when a field-outlier anomaly on the MUAC measurement path hits
    this (flw, week); ``"gender"`` when it's on the gender path; ``None`` when
    no anomaly applies. Week membership is checked against the anomaly's
    ``week`` (single) or ``weeks`` (list) — both are week INDICES.
    """
    for anomaly in manifest.anomalies:
        if flw_id not in anomaly.flw_ids:
            continue
        weeks = set(anomaly.weeks or [])
        if anomaly.week is not None:
            weeks.add(anomaly.week)
        if weeks and week_idx not in weeks:
            continue
        path = anomaly.field_path or ""
        if any(path == p or path.endswith(p.rsplit(".", 1)[-1]) for p in _GENDER_FIELD_PATHS):
            return "gender"
        # Default a field-outlier/duplicate/missing anomaly to a MUAC issue —
        # that's the cherry-picking story the chc SAM/MAM flags assert.
        return "muac"
    return None


def _accuracy_for_week(persona, week_idx: int, rng: random.Random) -> float:
    """Draw this persona's accuracy (approval rate) for ``week_idx``.

    Centered on ``accuracy_distribution.mean`` with its stddev, plus the
    ``improvement_arc`` lift applied from ``intervention_week`` (1-based in the
    manifest) onward. Clamped to [0, 1].
    """
    acc = rng.gauss(persona.accuracy_distribution.mean, persona.accuracy_distribution.stddev)
    arc = persona.improvement_arc
    if arc is not None and week_idx >= (arc.intervention_week - 1):
        acc += arc.post_intervention_lift
    return max(0.0, min(1.0, acc))


def _persona_week_signal(manifest, opp_id: int, persona, week_idx: int) -> dict[str, Any]:
    """Build the chc pipeline row for one persona for one week, from the manifest.

    Returns a dict whose fields satisfy the chc_nutrition render
    (``username``, ``name``, ``total_visits``, ``approved_visits``, the 12 MUAC
    bins + ``muac_distribution_count`` etc., ``male_count``/``female_count``,
    and the derived SAM/MAM/gender signal). The MUAC distribution + gender split
    are produced by the shared ``archetypes`` builders so the row trips the same
    FLAG_CATALOG thresholds the live render uses.
    """
    from connect_labs.labs.synthetic.archetypes import build_flw_pipeline_row

    kpi_issue = _anomaly_kpi_issue_for_week(manifest, persona.id, week_idx)
    flagged = kpi_issue is not None
    seed = _flw_seed(manifest.random_seed, opp_id, week_idx, persona.id)

    # Map the manifest persona archetype onto the row builder's severity
    # vocabulary. The builder keys MUAC severity off an archetype string +
    # flagged_this_week + kpi_issue; we translate so the manifest's 4 archetypes
    # produce the right shape:
    #   - a flagged (anomaly) week with a MUAC issue -> "improver_in_progress"
    #     (severity 2: SAM/MAM emptied -> trips sam_low/mam_low),
    #   - everything else -> "solid"/"new_hire" (severity 0: honest baseline
    #     with the expected SAM/MAM presence -> no MUAC flags).
    # Gender skew rides on kpi_issue == "gender" independent of archetype.
    if persona.archetype == "new_hire":
        builder_archetype = "new_hire"
    elif flagged and kpi_issue == "muac":
        builder_archetype = "improver_in_progress"
    else:
        builder_archetype = "solid"

    row = build_flw_pipeline_row(
        flw_id=persona.id,
        archetype=builder_archetype,
        flagged_this_week=flagged,
        rng_seed=seed,
        kpi_issue=kpi_issue,
        display_name=_display_name_for(persona),
    )

    # Overlay the persona's accuracy signal onto the approval columns so the
    # row's approved/total ratio reflects accuracy_distribution (the builder's
    # own approved_visits is severity-driven, not accuracy-driven).
    acc_rng = random.Random(seed ^ 0xACC)
    accuracy = _accuracy_for_week(persona, week_idx, acc_rng)
    total = row.get("total_visits") or 0
    row["approved_visits"] = max(0, min(total, round(total * accuracy)))
    return row


def _chc_pipeline_snapshot(manifest, opp_id: int, week_idx: int) -> dict:
    """Wrap the week's per-persona rows in the snapshot shape the render reads.

    ``new_hire`` personas with an ``improvement_arc`` whose intervention week is
    in the future for ``week_idx`` are still emitted (they were hired at the
    program start in the manifest model — there's no per-persona join week), so
    every persona appears every week. Returns ``{}`` when there are no personas.
    """
    rows = [_persona_week_signal(manifest, opp_id, p, week_idx) for p in manifest.flw_personas]
    return {"data": {"rows": rows}} if rows else {}


# ---------------------------------------------------------------------- #
# Definition + run idempotency
# ---------------------------------------------------------------------- #


def _ensure_chc_definition(wda):
    """Return the opp's chc_nutrition_analysis definition, creating it if absent.

    Reuses an existing def (so re-runs don't churn ids) and refreshes its
    render_code from the current template source — the same upgrade-on-reseed
    behavior ``program_admin_demo`` has.
    """
    from connect_labs.workflow.templates import create_workflow_from_template, get_template

    existing = [
        d
        for d in wda.list_definitions()
        if d.opportunity_id == wda.opportunity_id and d.template_type == "chc_nutrition_analysis"
    ]
    if existing:
        definition = existing[0]
    else:
        definition, _, _ = create_workflow_from_template(wda, template_key="chc_nutrition_analysis")

    template = get_template("chc_nutrition_analysis")
    component_code = template.get("render_code") if template else None
    if component_code:
        current = wda.get_render_code(definition.id)
        if not (current and current.data.get("component_code") == component_code):
            next_version = (current.data.get("version") if current else 0) + 1
            wda.save_render_code(definition_id=definition.id, component_code=component_code, version=next_version)
    return definition


def _existing_runs_by_period(wda, definition_id: int) -> dict[str, Any]:
    """Map ``period_start`` -> run record for the opp's chc runs (keyed by week)."""
    out: dict[str, Any] = {}
    for r in wda.list_runs(definition_id=definition_id):
        if r.opportunity_id != wda.opportunity_id:
            continue
        period_start = r.data.get("period_start")
        if period_start:
            out[period_start] = r
    return out


# ---------------------------------------------------------------------- #
# Ensurer entrypoint
# ---------------------------------------------------------------------- #


def ensure_weekly_runs(resource, ctx) -> dict:
    """Materialize per-opp weekly chc saved runs from each opp's stashed manifest.

    ``resource`` is a :class:`~..env_manifest.WeeklyRunsResource`; ``ctx`` is the
    run's :class:`~..engine.EnsureContext`. Returns a flat realized map of the
    run/def-level vars the PAR walkthrough spec needs: ``workflow_def_id``,
    ``opp_id``, ``wk4_run_id``/``wk4_url`` (current-week in_progress run), plus
    per-opp ``workflow_def_id:{opp}`` / ``current_run_id:{opp}``. The drill-target
    vars (``good_*`` / ``incomplete_*`` / ``task_*_url`` / ``par_url`` /
    ``flagged_flw_manager``) are resolved downstream by the rollup ensurer's
    PAR-snapshot walk. Every completed/current run id is also stashed on
    ``ctx.ids[f"run:{opp}:{week_start}"]`` for the audits/tasks/rollup ensurers.
    """
    from connect_labs.flags.data_access import FlagsDataAccess
    from connect_labs.labs.synthetic.program_admin_demo import _seed_auto_flags_for_run
    from connect_labs.labs.synthetic.walkthrough_kit import create_backdated_workflow_run
    from connect_labs.workflow.data_access import WorkflowDataAccess

    realized: dict[str, Any] = {}
    weeks = ctx.weeks
    current_week = ctx.current_week
    reset_current = bool(resource.current_week and resource.current_week.reset)

    watched: list[dict] = []

    for opp_id in resource.opportunity_ids:
        manifest = ctx.ids.get(f"manifest:{opp_id}")
        if manifest is None:
            raise KeyError(
                f"weekly_runs: no manifest stashed for opp {opp_id} " "(an opp_data resource for it must run first)"
            )
        missed = set(resource.missed_week_idxs.get(opp_id, []))
        # Network-manager attribution for seeded flags: the first persona's
        # display name is a reasonable stand-in (the manifest has no separate
        # manager entity). Flags are attributed to a human name only.
        flagged_by = _display_name_for(manifest.flw_personas[0]) if manifest.flw_personas else "Program Manager"

        wda = WorkflowDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        fda = FlagsDataAccess(opportunity_id=opp_id, access_token=_LABS_ONLY_TOKEN)
        try:
            definition = _ensure_chc_definition(wda)
            realized[f"workflow_def_id:{opp_id}"] = definition.id
            watched.append({"opportunity_id": opp_id, "workflow_definition_id": definition.id})

            existing = _existing_runs_by_period(wda, definition.id)

            # ---- completed weekly runs ----
            for week_idx, monday_iso in enumerate(weeks):
                if week_idx in missed:
                    continue
                snapshot = _chc_pipeline_snapshot(manifest, opp_id, week_idx)
                run = existing.get(monday_iso)
                if run is not None and run.data.get("status") == "completed":
                    run_id = run.id
                else:
                    run_id = create_backdated_workflow_run(
                        wda=wda,
                        definition_id=definition.id,
                        opportunity_id=opp_id,
                        monday_iso=monday_iso,
                        in_progress=False,
                        pipelines=snapshot,
                    )
                    _seed_auto_flags_for_run(
                        fda=fda,
                        rows=snapshot.get("data", {}).get("rows", []),
                        workflow_run_id=run_id,
                        opportunity_id=opp_id,
                        monday_iso=monday_iso,
                        flagged_by=flagged_by,
                    )
                ctx.ids[f"run:{opp_id}:{monday_iso}"] = run_id

            # ---- current-week in_progress run ----
            if current_week:
                week_idx = len(weeks)
                run = existing.get(current_week)
                if run is not None and reset_current:
                    wda.labs_api.delete_records([run.id])
                    run = None
                if run is not None:
                    run_id = run.id
                else:
                    snapshot = _chc_pipeline_snapshot(manifest, opp_id, week_idx)
                    run_id = create_backdated_workflow_run(
                        wda=wda,
                        definition_id=definition.id,
                        opportunity_id=opp_id,
                        monday_iso=current_week,
                        in_progress=True,
                        pipelines=snapshot,
                    )
                ctx.ids[f"run:{opp_id}:{current_week}"] = run_id
                realized[f"current_run_id:{opp_id}"] = run_id
        finally:
            wda.close()
            fda.close()

    # The PAR walkthrough's flat var names point at the PRIMARY opp (the first in
    # the resource list) — the one that runs the live manager flow. Here we emit
    # only the run/def-level vars this ensurer authoritatively owns. The drill
    # targets (good_/incomplete_ run+audit+task ids, par_url, flagged_flw_manager)
    # are resolved downstream by the rollup ensurer's PAR-snapshot walk (the same
    # discovery the old regenerate.py did via _lib.discovery.find_drill_targets);
    # emitting a guess here would shadow the real value, so we don't.
    if resource.opportunity_ids:
        primary = resource.opportunity_ids[0]
        realized["workflow_def_id"] = realized.get(f"workflow_def_id:{primary}")
        realized["opp_id"] = primary
        if current_week:
            wk4_run_id = ctx.ids.get(f"run:{primary}:{current_week}")
            realized["wk4_run_id"] = wk4_run_id
            if wk4_run_id is not None and realized.get("workflow_def_id"):
                realized["wk4_url"] = (
                    f"/labs/workflow/{realized['workflow_def_id']}/run/"
                    f"?run_id={wk4_run_id}&opportunity_id={primary}"
                )

    # Stash the watched sources for the rollup ensurer (PAR definition needs the
    # list of (opp, workflow_definition_id) it aggregates).
    ctx.ids["chc_watched_sources"] = watched
    # Stash the per-opp missed-week indices for the rollup ensurer: a missed week
    # has NO run (we skipped creating it above), so the rollup's snapshot can't
    # infer "missed" from absence alone vs "no run yet". Carrying the declared
    # set lets _build_snapshot stamp each source's missed_week_idxs so the PAR
    # grid renders an explicit NO-RUN ("SOP missed") card for it.
    ctx.ids["missed_week_idxs"] = {
        opp_id: sorted(resource.missed_week_idxs.get(opp_id, [])) for opp_id in resource.opportunity_ids
    }
    return realized
