"""Synthetic generator archetypes for non-FLW entities.

Mirrors the FLW archetype pattern (see ``commcare_connect/labs/synthetic/self_service.py``
and ``commcare_connect/mcp/tools/program_admin_demo_v2.py``) for the other
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
from dataclasses import dataclass, field
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
        fname
        for fname, meta in _MUAC_CATALOG.items()
        if isinstance(meta, dict) and meta.get("category") == category
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

    monday_dt = dt.datetime.fromisoformat(monday_iso).replace(
        hour=10, minute=0, tzinfo=dt.timezone.utc
    )
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
    (status, resolution_details, events)."""

    name: str
    description: str
    status: str  # "investigating" | "closed"
    official_action: str | None  # "satisfactory" | "warned" | "suspended" | None
    close_delay_days: int | None  # None → never closed
    resolution_note_template: str = "Closed by {actor} — {action}"


TASK_ARCHETYPES: dict[str, TaskArchetype] = {
    "closed_satisfactory": TaskArchetype(
        name="closed_satisfactory",
        description="Closed satisfactorily after coaching call — FLW improved, no further action needed.",
        status="closed",
        official_action="satisfactory",
        close_delay_days=6,
    ),
    "closed_warned": TaskArchetype(
        name="closed_warned",
        description="Closed with formal warning — FLW remains on roster pending follow-up.",
        status="closed",
        official_action="warned",
        close_delay_days=5,
    ),
    "closed_suspended": TaskArchetype(
        name="closed_suspended",
        description="Closed by suspending the FLW — repeat failures or fraud confirmed; removed from active roster.",
        status="closed",
        official_action="suspended",
        close_delay_days=2,
    ),
    "investigating": TaskArchetype(
        name="investigating",
        description="Coach is still investigating the photo issues raised this week. No resolution yet.",
        status="investigating",
        official_action=None,
        close_delay_days=None,
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
    created_at = dt.datetime.fromisoformat(monday_iso).replace(
        hour=10, minute=15, tzinfo=dt.timezone.utc
    )

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
    }
