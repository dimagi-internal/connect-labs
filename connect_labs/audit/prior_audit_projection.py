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
from datetime import timedelta

from django.db import transaction
from django.db.models import F
from django.utils import timezone
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
def rebuild_opportunity(opportunity_id: int, sessions, built_by: str = "", prune_unseen: bool = False) -> dict:
    """MERGE one identity's view of an opportunity into the projection.

    Merge, not replace, and that is the whole safety model. The source is
    Connect's export API, which returns only what the CALLING identity's org
    membership can see -- so a blanket delete followed by "insert what I could
    see" lets any narrow-permission caller destroy verdicts it was never able to
    read. That is silent, permanent, and produces exactly the failure this
    projection exists to prevent: an auditor told an image was never judged when
    it was.

    So rows are only ever removed for a session this call actually SAW:

      * a session seen and still completed -> its rows are refreshed;
      * a session seen and no longer completed (reopened) -> its rows go, which
        is a real retraction and the point of storing per-session rows;
      * a session NOT seen -> left completely alone, because "invisible to me"
        and "does not exist" are indistinguishable from here.

    The consequence is the property that matters: the projection is MONOTONIC
    under permission variation. Identities with different scopes contribute
    different subsets and the table converges to their union; no permission
    level can shrink it. A narrower caller can fail to add, never subtract.

    ``prune_unseen`` opts out of that guarantee and must only be used by a caller
    known to see everything -- it deletes rows for sessions absent from this
    view, which is the only way to retire a session deleted upstream, and also
    the one operation a narrow identity could use to lose data.
    """
    seen_ids = {s.id for s in sessions}
    rows: list[PriorAuditVerdict] = []
    contributing = 0
    for session in sessions:
        session_rows = rows_for_session(session)
        if session_rows:
            contributing += 1
            rows.extend(session_rows)

    # Scoped to the sessions in hand. Retractions are covered because a reopened
    # session IS in seen_ids and simply contributes no rows back.
    deleted, _ = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id, session_id__in=seen_ids).delete()

    pruned = 0
    if prune_unseen:
        pruned, _ = (
            PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id).exclude(session_id__in=seen_ids).delete()
        )

    if rows:
        PriorAuditVerdict.objects.bulk_create(rows, batch_size=1000)

    # Stamped in the SAME transaction as the rows. A build that wrote rows but
    # not the state row would keep falling back forever (merely wasteful); one
    # that stamped state without the rows would report an empty history as a
    # clean one, which is the failure this whole design exists to avoid.
    #
    # source_sessions/rows describe THIS identity's view, and are only ever
    # raised, never lowered -- a narrower run must not make the projection look
    # like it was built from less than it actually holds.
    state, _created = PriorAuditProjectionState.objects.get_or_create(opportunity_id=opportunity_id)
    total_rows = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id).count()
    if len(sessions) >= state.source_sessions:
        state.built_by = (built_by or "")[:150]
        state.source_sessions = len(sessions)
    state.rows = total_rows
    state.save()

    return {
        "opportunity_id": opportunity_id,
        "sessions_seen": len(sessions),
        "sessions_contributing": contributing,
        "rows_refreshed": deleted,
        "rows_written": len(rows),
        "rows_pruned": pruned,
        "rows_total": total_rows,
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
    missing: list  # in live, absent from projection -- the projection is behind
    extra: list  # in projection FROM A SEEN SESSION, absent from live -- stale
    mismatched: list  # same key, different verdict/session
    beyond_scope: int = 0  # projection rows from sessions this identity cannot see

    @property
    def agrees(self) -> bool:
        return not (self.missing or self.extra or self.mismatched)

    def summary(self) -> str:
        verdict = "AGREES" if self.agrees else "DISAGREES"
        tail = f" beyond-scope={self.beyond_scope}" if self.beyond_scope else ""
        return (
            f"opp {self.opportunity_id}: {verdict} — live={self.live_keys} projected={self.projected_keys} "
            f"missing={len(self.missing)} extra={len(self.extra)} mismatched={len(self.mismatched)}{tail}"
        )


def verify_opportunity(
    opportunity_id: int,
    live_index: dict,
    exclude_session_id: int | None = None,
    visible_session_ids: set | None = None,
) -> VerifyResult:
    """Diff the projection against one identity's live computation.

    Compares the fields a caller ACTS on -- the verdict and which session it came
    from. ``completed_at`` is deliberately excluded: the live builder stringifies
    whatever the blob held, the projection round-trips it through a DateTimeField,
    and a formatting difference is not a disagreement about what was audited.

    ``visible_session_ids`` is what makes this honest under merge semantics. The
    projection is a union across every identity that has ever built it, so it
    legitimately holds rows from sessions the CURRENT identity cannot see. Judged
    naively those look identical to stale rows, and a verifier that called them
    drift would fire forever on any narrow identity -- which trains people to
    ignore it, and an ignored verifier is the same as none.

    Passed a visible set, rows from outside it are counted as ``beyond_scope``
    and excluded from the verdict. Omit it and every projected row is judged,
    which is only correct for an identity known to see everything.
    """
    projected = read_index(opportunity_id, exclude_session_id=exclude_session_id)

    beyond_scope = 0
    if visible_session_ids is not None:
        in_scope = {}
        for key, entry in projected.items():
            if entry.get("session_id") in visible_session_ids:
                in_scope[key] = entry
            else:
                beyond_scope += 1
        projected = in_scope

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
        beyond_scope=beyond_scope,
    )


#: How stale a projection may be before the next reader rebuilds it.
#:
#: Not a correctness mechanism -- ``record_session`` keeps the table in step as
#: sessions complete and reopen. This bounds how long a MISSED dual-write can go
#: unnoticed, and a missed one is expected: record_session deliberately swallows
#: its errors so a failed cache write cannot 500 an audit already completed.
#:
#: Fifteen minutes costs at most one live computation per opportunity per quarter
#: hour -- which is what the code did on EVERY request before any of this existed
#: -- and it removes the need for a scheduled reconciler running under somebody's
#: stored credential.
STALE_AFTER = timedelta(minutes=15)


def is_fresh(opportunity_id: int) -> bool:
    """Is this opportunity's projection built AND recent enough to serve?

    "Built" alone is not enough. Rows accumulate from completion dual-writes
    before anything has built the opportunity, and a partial set served as a
    complete history under-reports prior audits -- the failure this projection
    exists to prevent. Only a state row licenses reading, and only a recent one
    licenses reading without a refresh.
    """
    state = PriorAuditProjectionState.objects.filter(opportunity_id=opportunity_id).first()
    return bool(state) and (timezone.now() - state.built_at) < STALE_AFTER


def is_built(opportunity_id: int) -> bool:
    """Has this opportunity ever been built? (Ignores staleness.)"""
    return PriorAuditProjectionState.objects.filter(opportunity_id=opportunity_id).exists()


def populate(opportunity_id: int, sessions, built_by: str = "") -> None:
    """Store an index a reader has just computed. Never breaks the read.

    The caller fetched these sessions with its OWN credentials and already proved
    access to the opportunity to get this far, so there is no credential to
    borrow and no scope to inherit -- the write is authorised by the same request
    that needed the answer.

    Best-effort: the reader holds a correct answer either way, and a cache write
    must not turn a working page into a 500.
    """
    try:
        rebuild_opportunity(opportunity_id, sessions, built_by=built_by)
    except Exception:
        logger.exception(
            "prior-audit projection: failed to populate opp %s just-in-time; "
            "reads stay correct via live computation",
            opportunity_id,
        )


def record_session(session) -> int | None:
    """Keep the projection in step when a session is completed or reopened.

    Called from the request that just wrote the session, so it needs no fetch
    and no credential -- it writes the object already in hand. That is what
    makes this path permission-independent: anything that had to re-read from
    Connect would inherit the caller's scope and could persist a narrower truth
    than the one just saved.

    Best-effort, following _maybe_complete_workflow_run: the session write has
    already succeeded and is what the user asked for, so a projection failure is
    logged and swallowed rather than turned into a 500 on a completed audit. The
    cost of swallowing is bounded -- reconciliation finds the drift, and until it
    does the projection is merely stale.

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
