"""Synthetic generator archetypes for non-FLW entities.

Mirrors the FLW archetype pattern (see ``commcare_connect/labs/synthetic/self_service.py``
and ``commcare_connect/labs/synthetic/program_admin_demo.py``) for the other
record types the synthetic generator emits — primarily ``AuditSession`` and
``Task``. The point of the archetype layer is the same as for FLWs: name a
narrative state once (e.g. ``completed_warned_tape_usage``) so every story we
tell with synthetic data uses the same vocabulary, the same field shapes, and
the same image pools.

Catalog overview (see source for full details):

**Audit archetypes** — each maps to a concrete (status, overall_result, image
distribution) tuple, plus a "verdict tone" that determines which of the bad-
MUAC corpus categories the fail images are drawn from:

  - ``completed_pass_clean``      — 5/5 pass, overall_result="pass"
  - ``completed_fail_tape_usage`` — 0/5 pass, overall_result="fail" (tape_usage bads)
  - ``completed_fail_framing``    — 0/5 pass, overall_result="fail" (framing bads)
  - ``completed_fail_misleading`` — 0/5 pass, overall_result="fail" (misleading/fraud bads)
  - ``completed_mixed_tape_usage``— 3/5 pass + 2 fail (tape_usage) — partial finding
  - ``in_review_partial``         — 2 reviewed + 3 pending; overall_result=None

**Task archetypes** — map to (status, official_action, close timing) so a
weekly review's coaching task can resolve as satisfactory / warned / suspended
/ still-investigating in a consistent way:

  - ``closed_satisfactory`` — closed_at = +6d, official_action="satisfactory"
  - ``closed_warned``       — closed_at = +5d, official_action="warned"
  - ``closed_suspended``    — closed_at = +2d, official_action="suspended"
  - ``investigating``       — never closed (status="investigating")

The MUAC image corpus that backs the audit archetypes lives in a shared
Google Drive folder (``LABS_SYNTHETIC_STOCK_IMAGES_FOLDER_ID``); see
``docs/synthetic-data/audit-corpus.md`` for the full catalog of image
filenames, categories, and per-image rationale. The catalog is also mirrored
into ``commcare_connect/labs/synthetic/generator/muac_reasons.json`` so it's
inspectable from code without a GDrive fetch.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# -----------------------------------------------------------------------------
# MUAC image corpus
# -----------------------------------------------------------------------------

# The shared corpus has two pools: good (clean MUAC measurements) and bad
# (categorized as tape_usage / framing / equipment / misleading / content_free).
# The image server (commcare_connect/labs/synthetic/image_server.py) serves
# blob_ids of the form ``synth-muac-{good|bad}-NNN`` from these pools.
#
# To stay loosely coupled, we read the bad-image catalog from the mirror JSON
# in the generator package — that file is the source of truth for the bad
# pool. The good pool is simply numbered 001..N (no categorization needed).

# Pool sizes for the MUAC stock corpus. The bad pool size is derived from
# muac_reasons.json (the source of truth). The good pool has no rationale
# JSON since there's nothing to explain — so we hard-code its size and bump
# this constant when new good photos are added to the GDrive stock folder.
# Verify with the ``synthetic_image_server_status`` MCP tool — its
# ``listing_files`` lists everything the service account can see.
_GOOD_POOL_SIZE = 8

_REASONS_PATH = Path(__file__).parent / "generator" / "muac_reasons.json"


def _load_muac_catalog() -> dict[str, Any]:
    """Load the bad-MUAC corpus catalog. Returns categories + per-image reasons."""
    try:
        return json.loads(_REASONS_PATH.read_text())
    except Exception:
        logger.warning("Could not read muac_reasons.json at %s", _REASONS_PATH)
        return {"_meta": {"categories": {}}}


_MUAC_CATALOG = _load_muac_catalog()


def bad_muac_filenames_for_category(category: str) -> list[str]:
    """Return the list of bad-MUAC image filenames in a given category.

    Categories are keyed in muac_reasons.json's ``_meta.categories``:
    ``tape_usage`` | ``framing`` | ``equipment`` | ``misleading`` | ``content_free``.

    Returns filenames like ``muac_bad_001.jpg`` — strip the ``.jpg`` and prefix
    with ``synth-muac-bad-`` to get a blob_id the image server can resolve.
    """
    return sorted(
        fname for fname, meta in _MUAC_CATALOG.items() if isinstance(meta, dict) and meta.get("category") == category
    )


def blob_id_for_filename(filename: str) -> str:
    """Convert a MUAC stock filename to the blob_id pattern the image server uses.

    ``muac_good_003.jpg`` → ``synth-muac-good-003``
    ``muac_bad_017.jpg`` → ``synth-muac-bad-017``
    """
    stem = filename.removesuffix(".jpg")
    # stem looks like "muac_good_003" or "muac_bad_017"
    parts = stem.split("_")
    if len(parts) != 3 or parts[0] != "muac":
        raise ValueError(f"Unexpected MUAC filename: {filename!r}")
    return f"synth-muac-{parts[1]}-{parts[2]}"


# -----------------------------------------------------------------------------
# Audit archetypes
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditImageSpec:
    """How many photos of each kind an audit archetype should attach."""

    good_count: int = 0
    bad_count: int = 0
    bad_category: str | None = None  # filtered from _MUAC_CATALOG when set
    pending_count: int = 0  # photos with no assessment yet
    # When pending_count > 0, this many of the pending photos come from the
    # good pool vs bad. Default 50/50.
    pending_good_ratio: float = 0.5


@dataclass(frozen=True)
class AuditArchetype:
    """Named audit state. Maps to AuditSession data fields the audit views
    (BulkAssessmentView + audit detail pages) read at render time."""

    name: str
    description: str
    status: str  # "in_progress" (== "in_review") | "completed"
    overall_result: str | None  # "pass" | "fail" | None
    image_spec: AuditImageSpec
    title_template: str = "MUAC audit — {flw_id}"


AUDIT_ARCHETYPES: dict[str, AuditArchetype] = {
    "completed_pass_clean": AuditArchetype(
        name="completed_pass_clean",
        description="All 5 photos reviewed and pass — clean MUAC measurements, no concerns.",
        status="completed",
        overall_result="pass",
        image_spec=AuditImageSpec(good_count=5, bad_count=0),
    ),
    "completed_fail_tape_usage": AuditArchetype(
        name="completed_fail_tape_usage",
        description="All 5 photos fail — MUAC tape misapplied (overflows reticle, not threaded, twisted).",
        status="completed",
        overall_result="fail",
        image_spec=AuditImageSpec(good_count=0, bad_count=5, bad_category="tape_usage"),
    ),
    "completed_fail_framing": AuditArchetype(
        name="completed_fail_framing",
        description="All 5 photos fail — framing makes the reading impossible to verify.",
        status="completed",
        overall_result="fail",
        image_spec=AuditImageSpec(good_count=0, bad_count=5, bad_category="framing"),
    ),
    "completed_fail_misleading": AuditArchetype(
        name="completed_fail_misleading",
        description="All 5 photos fail — photos appear fraudulent (tape not on a child's arm).",
        status="completed",
        overall_result="fail",
        image_spec=AuditImageSpec(good_count=0, bad_count=5, bad_category="misleading"),
    ),
    "completed_mixed_tape_usage": AuditArchetype(
        name="completed_mixed_tape_usage",
        description="Mixed — 3 of 5 photos pass; 2 fail (tape_usage). Partial finding, coaching warranted.",
        status="completed",
        overall_result="fail",
        image_spec=AuditImageSpec(good_count=3, bad_count=2, bad_category="tape_usage"),
    ),
    "in_review_partial": AuditArchetype(
        name="in_review_partial",
        description="Auditor has reviewed 2 of 5 photos; 3 still pending. No overall verdict yet.",
        status="in_progress",
        overall_result=None,
        image_spec=AuditImageSpec(good_count=1, bad_count=1, pending_count=3, pending_good_ratio=0.4),
    ),
}


def _all_bad_filenames() -> list[str]:
    return sorted(
        f
        for f, meta in _MUAC_CATALOG.items()
        if isinstance(meta, dict) and f.startswith("muac_bad_") and f.endswith(".jpg")
    )


def _pick_blob_ids(spec: AuditImageSpec, rng_seed: int) -> list[tuple[str, str | None]]:
    """Return a list of (blob_id, assessment_result) tuples for an audit.

    ``assessment_result`` is "pass" | "fail" | None (None == still pending).

    Deterministic given the seed: same opp + run + flw always produces the
    same image set across regenerations.

    When ``spec.bad_category`` is set, bad-pool draws prefer that category and
    top up from the other bad categories if the primary runs out (the corpus
    has only 2-3 photos per category).
    """
    import random

    rng = random.Random(rng_seed)
    out: list[tuple[str, str | None]] = []

    # Good pool — there's no JSON catalog for good photos (they don't need
    # per-image rationale). The actual GDrive folder runs muac_good_001 ..
    # muac_good_008 today. Picking from a wider range silently 404s on the
    # image server, which makes audit cards render as "Assessment image"
    # placeholders instead of the real thumbnail. Bump _GOOD_POOL_SIZE
    # whenever new good photos are added to the stock folder (see
    # docs/synthetic-data/audit-corpus.md).
    good_pool = [f"muac_good_{i:03d}.jpg" for i in range(1, _GOOD_POOL_SIZE + 1)]

    # Bad pool with category preference: primary category first, then top up
    # from the rest in deterministic order.
    if spec.bad_category:
        primary = bad_muac_filenames_for_category(spec.bad_category)
        other = [f for f in _all_bad_filenames() if f not in primary]
        bad_pool = primary + other
    else:
        bad_pool = _all_bad_filenames()

    # Sampling helper that keeps order-bias (primary category first) but
    # randomizes within each slice.
    def _take(pool: list[str], n: int) -> list[str]:
        # Take up to n in pool order, breaking ties with rng to avoid always
        # picking the first NN entries when n < len(pool).
        shuffled = list(pool)
        # Stable random shuffle within first len(primary) so cross-call seed
        # produces consistent results, but adds variety per (seed, archetype).
        rng.shuffle(shuffled)
        return shuffled[:n]

    # Assessed photos
    chosen_good = _take(good_pool, spec.good_count)
    chosen_bad = _take(bad_pool, spec.bad_count)
    out.extend((blob_id_for_filename(f), "pass") for f in chosen_good)
    out.extend((blob_id_for_filename(f), "fail") for f in chosen_bad)

    # Pending photos — drawn from both pools per pending_good_ratio
    pending_good_n = int(round(spec.pending_count * spec.pending_good_ratio))
    pending_bad_n = spec.pending_count - pending_good_n
    remaining_good = [g for g in good_pool if g not in chosen_good]
    remaining_bad = [b for b in bad_pool if b not in chosen_bad]
    pending_good = _take(remaining_good, pending_good_n)
    pending_bad = _take(remaining_bad, pending_bad_n)
    out.extend((blob_id_for_filename(f), None) for f in pending_good + pending_bad)

    rng.shuffle(out)
    return out


def build_audit_data(
    *,
    archetype_name: str,
    flw_id: str,
    monday_iso: str,
    opportunity_id: int,
    opportunity_name: str,
    workflow_run_id: int,
    visit_id_base: int,
    rng_seed: int | None = None,
) -> dict[str, Any]:
    """Build a complete AuditSession ``data`` dict for the given archetype.

    The resulting dict can be passed straight to ``labs_api.create_record``.
    Populates everything BulkAssessmentView needs to render real image
    thumbnails + per-photo pass/fail results — ``visit_images`` (the photo
    metadata) and ``visit_results`` (the per-visit aggregate + per-image
    assessment) — both keyed by stringified visit_id.

    Args:
        visit_id_base: arbitrary unique integer; this archetype expands a
            single audit into one "synthetic visit" with multiple photos
            attached. Pass distinct values per (opp, run, flw) so visit_ids
            don't collide across audits.
        rng_seed: when provided, image selection is deterministic on this
            seed. Defaults to ``visit_id_base`` so calling this twice with
            the same parameters yields the same photo set.
    """
    archetype = AUDIT_ARCHETYPES[archetype_name]
    rng_seed = rng_seed if rng_seed is not None else visit_id_base
    photos = _pick_blob_ids(archetype.image_spec, rng_seed)

    monday_dt = dt.datetime.fromisoformat(monday_iso).replace(hour=10, minute=0, tzinfo=dt.timezone.utc)
    visit_iso = monday_dt.isoformat()
    visit_id = visit_id_base

    # visit_images is read by BulkAssessmentDataView. Each entry is one photo.
    images_for_visit = []
    for blob_id, _result in photos:
        # filename is purely cosmetic in the UI; reconstruct a stable one
        # from the blob_id so logs are readable.
        pool = "good" if "-good-" in blob_id else "bad"
        suffix = blob_id.rsplit("-", 1)[-1]
        filename = f"muac_{pool}_{suffix}.jpg"
        images_for_visit.append(
            {
                "blob_id": blob_id,
                "name": filename,
                "question_id": "muac_photo",
                "username": flw_id,
                "visit_date": visit_iso,
                "entity_name": f"{flw_id} household sample",
                "related_fields": [],
            }
        )
    visit_images = {str(visit_id): images_for_visit}

    # visit_results.assessments holds per-photo pass/fail. For pending photos,
    # we omit them from assessments entirely (the UI treats no-assessment as
    # pending).
    assessments: dict[str, dict[str, Any]] = {}
    for blob_id, result in photos:
        if result is None:
            continue
        assessments[blob_id] = {
            "result": result,
            "notes": "",
            "ai_result": "",
            "ai_notes": "",
        }
    if archetype.status == "completed":
        visit_result_value = archetype.overall_result or "pass"
    else:
        visit_result_value = ""  # pending → no aggregate result yet
    visit_results = {
        str(visit_id): {
            "result": visit_result_value,
            "notes": archetype.description,
            "assessments": assessments,
        }
    }

    pass_count = sum(1 for _b, r in photos if r == "pass")
    fail_count = sum(1 for _b, r in photos if r == "fail")
    pending_count = sum(1 for _b, r in photos if r is None)

    return {
        "title": archetype.title_template.format(flw_id=flw_id),
        "tag": "synthetic_demo",
        "status": archetype.status,
        "overall_result": archetype.overall_result,
        "workflow_run_id": workflow_run_id,
        "opportunity_id": opportunity_id,
        "opportunity_name": opportunity_name,
        "description": archetype.description,
        "criteria": {
            "audit_type": "last_n_per_flw",
            "count_per_flw": len(photos),
            "start_date": monday_iso,
            "end_date": monday_iso,
        },
        "visit_ids": [visit_id],
        "visit_images": visit_images,
        "visit_results": visit_results,
        "image_count": len(photos),
        "image_results": {
            "pass": pass_count,
            "fail": fail_count,
            "pending": pending_count,
        },
        "notes": archetype.description,
        "kpi_notes": "",
        "related_fields": [],
        "created_at": visit_iso,
    }


# -----------------------------------------------------------------------------
# Task archetypes
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskArchetype:
    """Named task state. Maps to Task data fields the task edit view reads
    (status, resolution_details, events) plus an optional OCS coaching
    transcript that renders in the Tasks UI's Coaching Conversation panel.

    ``ocs_template_key`` references a template key in
    ``commcare_connect/labs/synthetic/generator/ocs_templates.py``.
    """

    name: str
    description: str
    status: str  # "investigating" | "closed"
    official_action: str | None  # "satisfactory" | "warned" | "suspended" | None
    close_delay_days: int | None  # None → never closed
    ocs_template_key: str | None = None
    resolution_note_template: str = "Closed by {actor} — {action}"


TASK_ARCHETYPES: dict[str, TaskArchetype] = {
    "closed_satisfactory": TaskArchetype(
        name="closed_satisfactory",
        description="Closed satisfactorily after coaching call — FLW improved, no further action needed.",
        status="closed",
        official_action="satisfactory",
        close_delay_days=6,
        ocs_template_key="coaching_resolved_clean",
    ),
    "closed_warned": TaskArchetype(
        name="closed_warned",
        description="Closed with formal warning — FLW remains on roster pending follow-up.",
        status="closed",
        official_action="warned",
        close_delay_days=5,
        ocs_template_key="coaching_formal_warning",
    ),
    "closed_suspended": TaskArchetype(
        name="closed_suspended",
        description="Closed by suspending the FLW — repeat failures or fraud confirmed; removed from active roster.",
        status="closed",
        official_action="suspended",
        close_delay_days=2,
        ocs_template_key="coaching_repeat_offense_suspension",
    ),
    "closed_suspended_fraud": TaskArchetype(
        name="closed_suspended_fraud",
        description=(
            "Closed by suspending the FLW for suspected photo fraud — "
            "stronger framing than a repeat-failure suspension."
        ),
        status="closed",
        official_action="suspended",
        close_delay_days=2,
        ocs_template_key="coaching_repeat_offense_fraud_suspension",
    ),
    "investigating": TaskArchetype(
        name="investigating",
        description="Coach is still investigating the photo issues raised this week. No resolution yet.",
        status="investigating",
        official_action=None,
        close_delay_days=None,
        ocs_template_key="coaching_in_progress",
    ),
}


def build_task_data(
    *,
    archetype_name: str,
    flw_id: str,
    monday_iso: str,
    opportunity_id: int,
    workflow_run_id: int,
    audit_session_id: int | None,
    title: str,
    creator_name: str,
) -> dict[str, Any]:
    """Build a complete Task ``data`` dict for the given archetype.

    Pass the result to ``labs_api.create_record(experiment='tasks', type='Task', ...)``.
    """
    archetype = TASK_ARCHETYPES[archetype_name]
    created_at = dt.datetime.fromisoformat(monday_iso).replace(hour=10, minute=15, tzinfo=dt.timezone.utc)

    events: list[dict[str, Any]] = [
        {
            "event_type": "created",
            "actor": creator_name,
            "description": f"Task created by {creator_name}",
            "timestamp": created_at.isoformat(),
        }
    ]
    resolution_details: dict[str, Any] = {}

    if archetype.status == "closed":
        assert archetype.close_delay_days is not None
        closed_at = created_at + dt.timedelta(days=archetype.close_delay_days, hours=4)
        resolution_details = {
            "official_action": archetype.official_action,
            "resolution_note": archetype.resolution_note_template.format(
                actor=creator_name, action=archetype.official_action
            ),
        }
        events.append(
            {
                "event_type": "closed",
                "actor": creator_name,
                "description": f"Closed: {archetype.official_action}",
                "timestamp": closed_at.isoformat(),
            }
        )

    # Optional synthetic OCS coaching conversation. The Tasks UI renders
    # ``data.ocs_conversation`` as a "Coaching Conversation" panel when set
    # (see commcare_connect/templates/tasks/task_create_edit.html and the
    # task-data composition in commcare_connect/tasks/views.py).
    ocs_conversation: list[dict[str, Any]] = []
    if archetype.ocs_template_key:
        from .generator.ocs_templates import render_transcript

        ocs_conversation = render_transcript(
            template_key=archetype.ocs_template_key,
            flw_name=flw_id,
            base_timestamp=created_at + dt.timedelta(hours=1),
        )

    return {
        "title": title,
        "description": archetype.description,
        "priority": "high",
        "status": archetype.status,
        "username": flw_id,
        "flw_name": flw_id,
        "user_id": None,
        "opportunity_id": opportunity_id,
        "assigned_to_type": "self",
        "assigned_to_name": creator_name,
        "audit_session_id": audit_session_id,
        "workflow_run_id": workflow_run_id,
        "resolution_details": resolution_details,
        "events": events,
        "ocs_conversation": ocs_conversation,
        "synthetic": True,
    }


# -----------------------------------------------------------------------------
# CHC Nutrition pipeline row generator
# -----------------------------------------------------------------------------
#
# The CHC Nutrition Analysis workflow template renders one row per FLW with
# total visits, MUAC distribution, SAM/MAM rates, gender split, a Flags
# column (auto-populated from the row's pipeline data via
# view.ensureAutoFlags on mount), and an Actions column with two split-
# button menus (Create Audit ▾ / Send Task ▾). The synthetic generator
# emits backdated workflow_runs with empty pipeline snapshots by default —
# which means "Open the run" from the Program Admin Report lands on a
# blank "No data available" table. To make the underlying weekly review
# meaningful, we synthesise one pipeline row per active FLW per week,
# with shape driven by the FLW's narrative state (solid /
# improver-in-flag-week / suspended). The chc_nutrition render code
# inspects the row's MUAC bins + gender split and emits sam_low/mam_low/
# gender_skew flags as appropriate.


_ALL_MUAC_BINS = (
    "muac_9_5_10_5_visits",
    "muac_10_5_11_5_visits",
    "muac_11_5_12_5_visits",
    "muac_12_5_13_5_visits",
    "muac_13_5_14_5_visits",
    "muac_14_5_15_5_visits",
    "muac_15_5_16_5_visits",
    "muac_16_5_17_5_visits",
    "muac_17_5_18_5_visits",
    "muac_18_5_19_5_visits",
    "muac_19_5_20_5_visits",
    "muac_20_5_21_5_visits",
)


def _muac_distribution(*, severity: int, rng) -> dict[str, int]:
    """Return MUAC distribution bin counts across all 12 bins.

    Severity semantics (aligned with PR #281's flag-direction flip):

      - severity 0: HONEST sampling. Realistic distribution that includes
        the expected baseline of SAM/MAM cases (SAM ≈ 3-7%, MAM ≈ 7-10%)
        you'd see in a high-malnutrition area. The row passes through
        without tripping sam_low / mam_low.
      - severity 1: thin baseline (used between flags for suspended_*
        archetypes). SAM/MAM still present, just smaller. Doesn't trip
        flags.
      - severity 2: CHERRY-PICKING suspect. Zero mass in SAM bins
        (9.5-11.5cm) and the MAM bin (11.5-12.5cm); the rest of the
        distribution still looks like a natural bell, just centered a
        touch higher (the FLW is visiting better-fed children). Trips
        both sam_low and mam_low.
      - severity 3+: extreme cherry-picking. Same SAM/MAM-empty left
        tail; mass shifted further right.

    Children measured for MUAC are typically under 5 years old, so all
    distributions stay concentrated in the 12-17cm range with a tapering
    right tail toward ~20cm. Every severity here is meant to LOOK natural
    in shape — the difference is the size of the left-tail SAM/MAM
    contribution, not the overall silhouette.
    """
    # Per-severity bin weights, indexed by bin (in the order of _ALL_MUAC_BINS).
    if severity <= 0:
        # Honest baseline: SAM=2, MAM=3 against ~28 measurements →
        # SAM ≈ 7%, MAM ≈ 11%. Comfortably above the < 1% / < 3%
        # thresholds even after downward jitter on bin 1 (worst case
        # SAM = 1, mc ≈ 27, SAM% ≈ 3.7% — still safe).
        weights = [0, 2, 3, 5, 6, 5, 3, 2, 1, 1, 0, 0]
    elif severity == 1:
        # Thinner left tail; still has some SAM/MAM presence so doesn't
        # trip flags. Used as the steady-state for suspended_* archetypes
        # in their non-flagged weeks.
        weights = [0, 1, 2, 5, 6, 5, 3, 2, 1, 1, 0, 0]
    elif severity == 2:
        # Cherry-picking — zero SAM, zero MAM, bell curve centered ~14cm.
        weights = [0, 0, 0, 3, 6, 7, 5, 3, 1, 1, 0, 0]
    else:  # severity 3+
        # Extreme cherry-picking — peak shifted to ~15cm, longer right tail.
        weights = [0, 0, 0, 1, 4, 7, 6, 4, 2, 1, 0, 0]

    # Per-bin jitter so two FLWs in the same archetype don't look identical.
    # The SAM bins (0, 1) and the MAM bin (2) are flag-threshold-sensitive
    # — a single visit in the wrong direction can flip a row across the
    # < 1% / < 3% line. Direction the jitter accordingly:
    #
    #   - severity 0/1 (intended clean): jitter UP only on SAM/MAM bins
    #     so downward noise can't accidentally cross into the flagged
    #     zone. The seed weights are already a comfortable margin above
    #     the threshold; jittering up keeps that distance.
    #   - severity 2+ (intended flagged): jitter DOWN only on SAM/MAM
    #     bins so an upward bump can't accidentally land a single
    #     measurement and un-flag the row.
    #
    # All other bins get symmetric ±1 jitter for visual variety.
    def _jit(bin_idx, w):
        if bin_idx <= 2:
            if severity >= 2:
                return max(0, w + rng.randint(-1, 0))
            return max(0, w + rng.randint(0, 1))
        return max(0, w + rng.randint(-1, 1))

    return {bin_name: _jit(i, w) for i, (bin_name, w) in enumerate(zip(_ALL_MUAC_BINS, weights))}


def _gender_counts(*, muac_count: int, kpi_issue: str | None, rng) -> tuple[int, int]:
    """Split ``muac_count`` between male + female children.

    For most FLWs we want a believably balanced split (40-60% female), which
    is what the chc_nutrition render code treats as the green KPI band.
    Random uniform splits produce too many red rows because the band only
    covers 20% of the [0, n] range.

    When ``kpi_issue == 'gender'`` we intentionally bias the split outside
    the band so the FLW's row appears red — the audit and task that get
    spawned that week then have a real KPI issue to point at.
    """
    if muac_count <= 0:
        return 0, 0
    if kpi_issue == "gender":
        # Strongly skew toward one sex (>= 80% one way). The choice of sex
        # is rng-driven so the gender-skewed FLW alternates across weeks if
        # the trajectory repeats.
        skew_pct = 0.80 + rng.random() * 0.15
        female_pct = skew_pct if rng.random() < 0.5 else (1.0 - skew_pct)
    else:
        # Compute the exact integer range that lands in the 45–55% green band
        # for this muac_count, then sample from it. Float-based sampling +
        # round() occasionally drifts to 44% for small samples (e.g.
        # 14/32 = 43.8%), which renders yellow.
        import math as _math

        lo = _math.ceil(muac_count * 0.46)
        hi = _math.floor(muac_count * 0.54)
        if lo > hi:
            female_count = muac_count // 2
        else:
            female_count = rng.randint(lo, hi)
        male_count = muac_count - female_count
        return male_count, female_count
    female_count = max(0, min(muac_count, round(muac_count * female_pct)))
    male_count = muac_count - female_count
    return male_count, female_count


def build_flw_pipeline_row(
    *,
    flw_id: str,
    archetype: str,
    flagged_this_week: bool,
    rng_seed: int,
    kpi_issue: str | None = None,
) -> dict[str, Any]:
    """Build a synthetic CHC Nutrition pipeline row for one FLW for one week.

    Field shape matches ``chc_nutrition_analysis.PIPELINE_SCHEMA`` — the
    chc_nutrition render code reads from these directly.

    ``kpi_issue`` (when set) drives which KPI looks bad on this row:
      - ``"muac"``   — high SAM/MAM concentration in the MUAC distribution
      - ``"gender"`` — gender split outside the green band (>60% one sex)
      - ``None``     — clean across all KPIs (default for ``solid`` rows)
    """
    import random

    rng = random.Random(rng_seed)

    # Severity reflects the FLW's narrative state for THIS week, restricted
    # to MUAC-distribution issues. Gender skew is handled separately via
    # ``kpi_issue == 'gender'`` so the two issue types are independent.
    if archetype in ("solid", "new_hire"):
        severity = 0
    elif archetype in ("improver_closed_satisfactory", "improver_warned", "improver_in_progress"):
        # severity=2 puts the MUAC sparkline distinctly into SAM territory
        # (SAM ~22%), which trips the chc_nutrition render's "isFailing" gate
        # so the row is visibly flagged and bulk "Mark No Issue" correctly
        # skips it. severity=1 produced SAM ~3%, below the 5% gate, and the
        # bulk-mark would include this FLW.
        severity = 2 if (flagged_this_week and kpi_issue == "muac") else 0
    elif archetype == "suspended_repeat_offense":
        severity = 2 if flagged_this_week else 1
    elif archetype == "suspended_fraudulent":
        severity = 3 if flagged_this_week else 1
    else:
        severity = 0

    distribution = _muac_distribution(severity=severity, rng=rng)
    muac_count = sum(distribution.values())

    # Midpoint of each bin (9.5-10.5 → 10.0, 10.5-11.5 → 11.0, …, 20.5-21.5 → 21.0).
    bin_midpoints = [10.0 + i for i in range(len(_ALL_MUAC_BINS))]
    if muac_count > 0:
        total = sum(midpoint * distribution[k] for midpoint, k in zip(bin_midpoints, _ALL_MUAC_BINS))
        avg_muac = round(total / muac_count, 2)
    else:
        avg_muac = 0.0

    # Visit + gender splits — believable per-FLW totals.
    total_visits = muac_count + rng.randint(2, 6)  # MUAC-eligible + non-MUAC visits
    approved_visits = total_visits if severity <= 1 else max(0, total_visits - rng.randint(2, 4))
    male_count, female_count = _gender_counts(muac_count=muac_count, kpi_issue=kpi_issue, rng=rng)

    return {
        "username": flw_id,
        "commcare_userid": f"synth-{flw_id}",
        "name": flw_id,
        "total_visits": total_visits,
        "approved_visits": approved_visits,
        "days_active": rng.randint(3, 6),
        "muac_consent_count": muac_count,
        "muac_measurements_count": muac_count,
        "muac_distribution_count": muac_count,
        "muac_distribution_mean": avg_muac,
        "avg_muac_cm": avg_muac,
        "male_count": male_count,
        "female_count": female_count,
        "children_unwell_count": distribution["muac_9_5_10_5_visits"] + distribution["muac_10_5_11_5_visits"],
        "under_malnutrition_treatment_count": distribution["muac_9_5_10_5_visits"],
        **distribution,
    }
