"""Build, store and read the prior-audit projection.

The projection mirrors what ``build_prior_audit_index`` computes live. Every
rule it applies is deliberately duplicated from there rather than approximated,
because the two must agree exactly or the switch-over is unsafe:

* only ``status == "completed"`` sessions contribute;
* only values in ``AUDIT_VERDICTS`` count -- pending/blank is not a prior audit;
* on a clash, the most recently completed session wins;
* a ``None`` completed_at never displaces a dated verdict.

``verify_opportunity`` exists to PROVE that agreement against real data before
anything reads from the table. Until it passes on production, this module is
write-and-compare only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F
from django.utils.dateparse import parse_datetime

from connect_labs.audit.prior_audit_models import AUDIT_VERDICTS, PriorAuditProjectionState, PriorAuditVerdict

logger = logging.getLogger(__name__)


def _as_dt(value):
    """Session ``completed_at`` arrives as a datetime or an ISO string."""
    if value is None or hasattr(value, "year"):
        return value
    return parse_datetime(str(value))


def rows_for_session(session) -> list[PriorAuditVerdict]:
    """Every verdict one session contributes. Empty unless it is completed.

    Mirrors build_prior_audit_index's per-session half exactly. It does NOT
    resolve clashes between sessions -- that is the read query's job, and doing
    it here is what would make a retraction unrecoverable.
    """
    if getattr(session, "status", None) != "completed":
        return []
    completed_at = _as_dt(getattr(session, "completed_at", None))
    title = (session.data or {}).get("title", "") or ""
    opportunity_id = session.opportunity_id
    rows = []
    for visit_key, visit_result in ((session.data or {}).get("visit_results") or {}).items():
        for blob_id, assessment in ((visit_result or {}).get("assessments") or {}).items():
            result = (assessment or {}).get("result")
            if result not in AUDIT_VERDICTS:
                continue
            rows.append(
                PriorAuditVerdict(
                    opportunity_id=opportunity_id,
                    session_id=session.id,
                    session_title=title[:255],
                    visit_id=str(visit_key)[:64],
                    blob_id=str(blob_id)[:255],
                    result=result,
                    completed_at=completed_at,
                )
            )
    return rows


@transaction.atomic
def replace_session(session) -> int:
    """Make the table match this session's current contribution.

    Delete-then-insert rather than upsert: a session that was reopened, or that
    had a verdict cleared, contributes FEWER rows than before, and an upsert
    would leave the stale ones behind as phantom prior audits. Deleting by
    session_id is also exactly what a retraction needs, so completion and
    uncompletion are the same code path with different inputs.
    """
    PriorAuditVerdict.objects.filter(session_id=session.id).delete()
    rows = rows_for_session(session)
    if rows:
        PriorAuditVerdict.objects.bulk_create(rows, batch_size=1000)
    return len(rows)


@transaction.atomic
def rebuild_opportunity(opportunity_id: int, sessions, built_by: str = "") -> dict:
    """Rebuild one opportunity's projection from the full session list.

    Scoped to the opportunity, never a global truncate: the caller fetched one
    opportunity's sessions, so wiping anything else would delete rows this call
    has no evidence about.
    """
    deleted, _ = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id).delete()
    rows: list[PriorAuditVerdict] = []
    contributing = 0
    for session in sessions:
        session_rows = rows_for_session(session)
        if session_rows:
            contributing += 1
            rows.extend(session_rows)
    if rows:
        PriorAuditVerdict.objects.bulk_create(rows, batch_size=1000)
    # Stamped in the SAME transaction as the rows. A build that wrote rows but
    # not the state row would keep falling back forever (merely wasteful); one
    # that stamped state without the rows would report an empty history as a
    # clean one, which is the failure this whole design exists to avoid.
    PriorAuditProjectionState.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults={
            "built_by": (built_by or "")[:150],
            "source_sessions": len(sessions),
            "rows": len(rows),
        },
    )
    return {
        "opportunity_id": opportunity_id,
        "sessions_seen": len(sessions),
        "sessions_contributing": contributing,
        "rows_deleted": deleted,
        "rows_written": len(rows),
    }


def read_index(opportunity_id: int, exclude_session_id: int | None = None) -> dict:
    """The projection's answer, in build_prior_audit_index's exact output shape.

    Same key (``"<visit_id>:<blob_id>"``) and same value keys, so a caller can be
    switched between the two without noticing -- which is the point: the diff in
    ``verify_opportunity`` is only meaningful if the shapes are identical.

    Ordered ASCENDING by the same precedence the live builder sorts on
    (``data_access.PRIOR_AUDIT_ORDER``: dated beats undated, then completed_at,
    then session_id) and then plainly overwritten, so the last row wins.

    The ORDER BY is load-bearing, not cosmetic. Without it Postgres returns rows
    in whatever order it likes, so two images whose verdicts tie on completed_at
    -- or which both carry none -- would resolve differently between runs, and
    ``verify_opportunity`` would report a disagreement that appears and vanishes.
    ``F(...).asc(nulls_first=True)`` reproduces the "undated loses" half in SQL.
    """
    qs = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id)
    if exclude_session_id is not None:
        qs = qs.exclude(session_id=exclude_session_id)
    qs = qs.order_by(F("completed_at").asc(nulls_first=True), "session_id")

    index: dict[str, dict] = {}
    for row in qs.iterator():
        index[f"{row.visit_id}:{row.blob_id}"] = {
            "result": row.result,
            "session_id": row.session_id,
            "session_title": row.session_title,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    return index


@dataclass
class VerifyResult:
    opportunity_id: int
    live_keys: int
    projected_keys: int
    missing: list  # in live, absent from projection
    extra: list  # in projection, absent from live
    mismatched: list  # same key, different verdict/session

    @property
    def agrees(self) -> bool:
        return not (self.missing or self.extra or self.mismatched)

    def summary(self) -> str:
        verdict = "AGREES" if self.agrees else "DISAGREES"
        return (
            f"opp {self.opportunity_id}: {verdict} — live={self.live_keys} projected={self.projected_keys} "
            f"missing={len(self.missing)} extra={len(self.extra)} mismatched={len(self.mismatched)}"
        )


def verify_opportunity(opportunity_id: int, live_index: dict, exclude_session_id: int | None = None) -> VerifyResult:
    """Diff the projection against the live computation for one opportunity.

    Compares the fields a caller ACTS on -- the verdict and which session it came
    from. ``completed_at`` is deliberately excluded from the comparison: the live
    builder stringifies whatever the blob held, the projection round-trips it
    through a DateTimeField, and a formatting difference is not a disagreement
    about what was audited.
    """
    projected = read_index(opportunity_id, exclude_session_id=exclude_session_id)
    live_keys, proj_keys = set(live_index), set(projected)

    def _cmp(entry):
        return (entry.get("result"), entry.get("session_id"))

    mismatched = [
        {"key": k, "live": _cmp(live_index[k]), "projected": _cmp(projected[k])}
        for k in live_keys & proj_keys
        if _cmp(live_index[k]) != _cmp(projected[k])
    ]
    return VerifyResult(
        opportunity_id=opportunity_id,
        live_keys=len(live_keys),
        projected_keys=len(proj_keys),
        missing=sorted(live_keys - proj_keys),
        extra=sorted(proj_keys - live_keys),
        mismatched=mismatched,
    )


def is_built(opportunity_id: int) -> bool:
    """Has this opportunity's projection ever been built?

    The read path's entire safety rests on this. An opportunity with no state
    row falls back to the live computation, however many rows happen to be in
    the table for it -- dual-written rows from a completion accumulate before
    the backfill runs, and a partial set is exactly the thing that must never be
    served as a complete history.
    """
    return PriorAuditProjectionState.objects.filter(opportunity_id=opportunity_id).exists()


def record_session(session) -> int | None:
    """Keep the projection in step when a session is completed or reopened.

    Called from the request that just wrote the session, so it needs no fetch
    and no credential -- it writes the object already in hand. That matters:
    anything that had to re-read from Connect would inherit the caller's scope
    and could silently write a narrower truth.

    Best-effort, following _maybe_complete_workflow_run: the session write has
    already succeeded and is what the user asked for, so a projection failure is
    logged and swallowed rather than turned into a 500 on a completed audit. The
    cost of swallowing is bounded -- reconciliation finds the drift, and until it
    does the projection is merely stale, in the direction of showing a verdict
    that was retracted or missing one that was added.

    Returns the row count written, or None if it failed.
    """
    try:
        return replace_session(session)
    except Exception:
        logger.exception(
            "prior-audit projection: failed to record session %s (opp %s); "
            "projection is now stale for this opportunity until reconciliation",
            getattr(session, "id", "?"),
            getattr(session, "opportunity_id", "?"),
        )
        return None
