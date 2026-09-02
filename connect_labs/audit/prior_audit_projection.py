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
from datetime import timezone as dt_timezone

from django.conf import settings
from django.core.cache import cache
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


# The verdicts the review screen counts as "flagged as a duplicate or a fake". Folds
# the legacy split the same way tally_assessment and the Duplicate/Fake filter do -- see
# LEGACY_DUPLICATE_FAKE_RESULTS in audit/views.py. Keep the three in step.
DUPLICATE_VERDICTS = ("duplicate_fake", "duplicate", "fake")

#: A day earns a column only once it reaches this many flagged images. One or two on a
#: day is ordinary noise across an FLW's whole history; the panel is for spotting the
#: days that stand out. Images below the line are NOT discarded -- they are reported as
#: ``below_threshold`` so the headline total still reconciles with the columns shown.
DUPLICATE_DAY_THRESHOLD = 3


def _visit_date_of(raw):
    """The LOCAL date of a visit timestamp, or None.

    Mirrors the bulk-data view's own conversion (parse -> assume UTC if naive ->
    localtime) so a history column and the card it sits beside agree on which day a
    photo belongs to. Doing this in two places with two conventions would put a photo
    in one day on the card and the next day in the table, off by the UTC offset.
    """
    if not raw:
        return None
    dt = parse_datetime(str(raw))
    if dt is None:
        return None
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return timezone.localtime(dt).date()


def _visit_metadata(session) -> dict:
    """``{(visit_id, blob_id): (username, visit_date)}`` from ``visit_images``.

    The verdicts live under ``visit_results`` and carry neither field, so this is the
    other half of the join. Both values are properties of the VISIT rather than the
    image -- the bulk-data view reads them off the visit's first image and applies them
    to all of it -- so the visit's first image is the fallback for an image that
    carries neither.
    """
    metadata = {}
    for visit_key, images in ((session.data or {}).get("visit_images") or {}).items():
        if not images:
            continue
        first = images[0] or {}
        visit_username = first.get("username") or ""
        visit_date = _visit_date_of(first.get("visit_date"))
        for image in images:
            blob_id = (image or {}).get("blob_id")
            if not blob_id:
                continue
            metadata[(str(visit_key), str(blob_id))] = (
                (image or {}).get("username") or visit_username,
                _visit_date_of((image or {}).get("visit_date")) or visit_date,
            )
    return metadata


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
    metadata = _visit_metadata(session)
    rows = []
    for visit_key, visit_result in ((session.data or {}).get("visit_results") or {}).items():
        for blob_id, assessment in ((visit_result or {}).get("assessments") or {}).items():
            result = (assessment or {}).get("result")
            if result not in AUDIT_VERDICTS:
                continue
            # A verdict whose image is absent from visit_images gets no attribution
            # rather than a guess -- see the username field's note on why blank has to
            # stay distinguishable from "no FLW".
            username, visit_date = metadata.get((str(visit_key), str(blob_id)), ("", None))
            rows.append(
                PriorAuditVerdict(
                    opportunity_id=opportunity_id,
                    session_id=session.id,
                    session_title=title[:255],
                    visit_id=str(visit_key)[:64],
                    blob_id=str(blob_id)[:255],
                    result=result,
                    username=(username or "")[:150],
                    visit_date=visit_date,
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
    # A full rebuild has ingested everything it was handed, so the watermark
    # moves to that batch's high mark and the next reader asks only for later.
    wm = compute_watermark(sessions)
    if wm and (state.watermark is None or wm > state.watermark):
        state.watermark = wm
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


def _winner_index(qs, wanted: set[str] | None = None) -> dict:
    """Collapse verdict rows to one winner per image, in the callers' output shape.

    Shared by read_index (whole opportunity) and prior_verdicts_for (a bounded
    set), so the two cannot drift in either the winner rule or the value shape.

    The ORDER BY is load-bearing -- see read_index for why -- and is applied by
    the caller so each can keep its own index-friendly filter.
    """
    index: dict[str, dict] = {}
    for row in qs.iterator():
        key = f"{row.visit_id}:{row.blob_id}"
        if wanted is not None and key not in wanted:
            continue
        index[key] = {
            "result": row.result,
            "session_id": row.session_id,
            "session_title": row.session_title,
            "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        }
    return index


def prior_verdicts_for(opportunity_id: int, pairs, exclude_session_id: int | None = None) -> dict:
    """Winners for JUST these ``(visit_id, blob_id)`` pairs.

    Same key, same values, same winner rule as ``read_index`` -- it is that
    function restricted to a known key set, and ``test_prior_audit_read_path``
    pins the two to identical output.

    WHY THIS EXISTS
    Both callers of the prior-audit index already hold the exact images they are
    asking about: the bulk-data view has one session's ``visit_images``, and
    audit creation has its candidate pool, which it then uses only for membership
    tests (``filter_out_prior_audited``). Neither wants an index; both want a
    lookup. ``read_index`` nevertheless loads every verdict row for the
    opportunity and builds the whole dict in Python, so cost tracked accumulated
    audit history rather than the page -- the very property this table was added
    to remove. Measured in production 2026-08-27: opportunity 2154 held **17,653
    verdict rows across 712 sessions**, materialised on every bulk-data load to
    answer roughly 25 lookups.

    WHY IT FILTERS ON visit_id AND FINISHES IN PYTHON
    Postgres can do ``(visit_id, blob_id) IN ((...), (...))`` but the ORM cannot
    express it portably, and an OR-of-tuples is pathological for the audit-creation
    caller, whose pool runs to thousands of images. Filtering on ``visit_id__in``
    uses the leading columns of ``idx_prior_audit_lookup``
    (``opportunity_id, visit_id, ...``) and fetches a SUPERSET -- other images of
    the same visits -- which ``_winner_index`` then drops. The superset is bounded
    by the visits actually asked about, never by the opportunity's history, which
    is the property that matters.
    """
    pairs = {(str(v), str(b)) for v, b in pairs}
    if not pairs:
        return {}
    wanted = {f"{v}:{b}" for v, b in pairs}
    visit_ids = {v for v, _ in pairs}

    qs = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id, visit_id__in=visit_ids)
    if exclude_session_id is not None:
        qs = qs.exclude(session_id=exclude_session_id)
    qs = qs.order_by(F("completed_at").asc(nulls_first=True), "session_id")
    return _winner_index(qs, wanted)


def has_flw_attribution(opportunity_id: int) -> bool:
    """Whether this opportunity's verdict rows carry FLW attribution at all.

    Rows written before the ``username`` column existed have none, and THE FRESHNESS
    GATE CANNOT SEE THAT: the projection is "built and current" by its own state row
    while every verdict in it is unattributable. Reading the resulting empty history as
    "this FLW has never been flagged" is exactly the silent under-report this module's
    docstring forbids, so callers are told to render "unavailable" instead of a clean
    strip. Cleared by ``rebuild_prior_audit_index``, which rewrites every row.

    An opportunity with no rows at all returns True: there is genuinely no history, and
    an empty strip is an honest answer rather than a missing one.
    """
    rows = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id)
    if not rows.exists():
        return True
    return rows.filter(username__gt="").exists()


def duplicate_history_for_flws(opportunity_id: int, usernames, exclude_session_id: int | None = None) -> dict:
    """Per FLW, how many images earlier audits flagged duplicate/fake, by visit day.

    Returns ``{username: {"days": [{"iso", "label", "count"}...], "total", "undated"}}``
    with days ascending and only days that actually have a count -- the review screen
    renders a column per entry, and a zero column is noise.

    THE WINNER RULE IS THE SAME ONE, AND THAT IS WHY THE FLW FILTER IS NOT APPLIED
    TO THE COUNTING QUERY. An image judged in five sessions must count ONCE, so this
    collapses to the most recently completed verdict exactly as ``_winner_index``
    does. But narrowing the scan by ``username`` first would break that: a row whose
    attribution is blank (written before the column existed, or from a session blob
    with no ``visit_images`` metadata) would be excluded from the scan, letting an
    OLDER attributed row win an image the blank row actually won -- an over-count, in
    the direction that accuses an FLW of duplicates they were not last judged to have.

    So the username filter only picks the VISITS, and the winner scan then runs over
    every row on those visits, attributing each winner by its own username and falling
    back to the visit's owner. Same superset-then-narrow shape as ``prior_verdicts_for``,
    and bounded by the FLW's own visits rather than the opportunity's whole history.

    ``undated`` counts flagged images whose visit timestamp could not be read, so they
    belong to no column. Surfaced rather than dropped: a total that silently disagrees
    with the sum of its columns is the failure this module's docstring is about.
    """
    usernames = {u for u in usernames if u}
    if not usernames:
        return {}

    empty = {
        u: {
            "days": [],
            "total": 0,
            "below_threshold": 0,
            "undated": 0,
            "threshold": DUPLICATE_DAY_THRESHOLD,
        }
        for u in usernames
    }

    owner_rows = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id, username__in=usernames).values_list(
        "visit_id", "username"
    )
    visit_owner: dict[str, str] = {}
    for visit_id, username in owner_rows.iterator():
        visit_owner.setdefault(visit_id, username)
    if not visit_owner:
        return empty

    qs = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id, visit_id__in=visit_owner.keys())
    if exclude_session_id is not None:
        qs = qs.exclude(session_id=exclude_session_id)
    # Ascending, so the latest completed verdict is the last write to land per image --
    # the same ordering trick _winner_index relies on.
    qs = qs.order_by(F("completed_at").asc(nulls_first=True), "session_id")

    winners: dict[tuple, PriorAuditVerdict] = {}
    for row in qs.iterator():
        winners[(row.visit_id, row.blob_id)] = row

    counts: dict[str, dict] = {u: {} for u in usernames}
    undated: dict[str, int] = {u: 0 for u in usernames}
    for row in winners.values():
        if row.result not in DUPLICATE_VERDICTS:
            continue
        owner = row.username or visit_owner.get(row.visit_id, "")
        if owner not in usernames:
            continue
        if row.visit_date is None:
            undated[owner] += 1
            continue
        counts[owner][row.visit_date] = counts[owner].get(row.visit_date, 0) + 1

    history = {}
    for username in usernames:
        by_date = counts[username]
        # Newest first, so the most recent days are the ones visible before anyone
        # scrolls -- the strip is read left to right and recency is what it is for.
        days = [
            {"iso": day.isoformat(), "label": day.strftime("%d/%m"), "count": n}
            for day, n in sorted(by_date.items(), reverse=True)
            if n >= DUPLICATE_DAY_THRESHOLD
        ]
        below = sum(n for n in by_date.values() if n < DUPLICATE_DAY_THRESHOLD)
        history[username] = {
            "days": days,
            # Every flagged image, including the ones no column shows. The panel states
            # the two excluded buckets, so this total always equals the sum of what the
            # reader can see plus what they are told about -- a headline that silently
            # disagreed with its own columns is the failure this module is about.
            "total": sum(day["count"] for day in days) + below + undated[username],
            "below_threshold": below,
            "undated": undated[username],
            "threshold": DUPLICATE_DAY_THRESHOLD,
        }
    return history


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
    return _winner_index(qs)


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


#: Backstop only. The watermark below is the primary freshness mechanism; this
#: bounds the one thing a watermark structurally CANNOT see.
#:
#: A watermark asks "what completed_at is later than mine?", so it finds additions
#: and edits. It cannot find a REMOVAL: a session that was reopened has its
#: completed_at cleared, so it simply stops appearing rather than showing up as
#: changed. Retractions are handled by record_session at the write chokepoint --
#: but that is deliberately best-effort and swallows its errors, so a failed one
#: leaves a verdict standing that should have been withdrawn.
#:
#: A daily full rebuild costs one extra fetch per opportunity per day and closes
#: that hole. It is long precisely because it is no longer doing the routine
#: work: before the watermark this was 15 minutes and every reader past it paid
#: for a full rebuild whether anything had changed or not.
STALE_AFTER = timedelta(hours=24)


def get_state(opportunity_id: int):
    return PriorAuditProjectionState.objects.filter(opportunity_id=opportunity_id).first()


def compute_watermark(sessions):
    """Highest completed_at across these sessions, or None."""
    stamps = [_as_dt(getattr(s, "completed_at", None)) for s in sessions]
    stamps = [t for t in stamps if t is not None]
    return max(stamps) if stamps else None


@transaction.atomic
def merge_changed(opportunity_id: int, changed_sessions, built_by: str = "") -> dict:
    """Fold in only the sessions that changed since the last watermark.

    Deliberately NOT rebuild_opportunity: that refreshes every session it is
    handed, and the whole point here is to touch only what moved. Same
    per-session semantics though -- a session seen and still completed has its
    rows refreshed, one seen and no longer completed loses them.

    The watermark only advances; a batch that somehow arrives with an older max
    must not rewind it and cause the same records to be re-fetched forever.
    """
    state = PriorAuditProjectionState.objects.select_for_update().get(opportunity_id=opportunity_id)
    written = 0
    for session in changed_sessions:
        PriorAuditVerdict.objects.filter(session_id=session.id).delete()
        rows = rows_for_session(session)
        if rows:
            PriorAuditVerdict.objects.bulk_create(rows, batch_size=1000)
            written += len(rows)

    new_wm = compute_watermark(changed_sessions)
    if new_wm and (state.watermark is None or new_wm > state.watermark):
        state.watermark = new_wm
    state.rows = PriorAuditVerdict.objects.filter(opportunity_id=opportunity_id).count()
    if built_by:
        state.built_by = built_by[:150]
    state.save()
    return {
        "opportunity_id": opportunity_id,
        "sessions_merged": len(changed_sessions),
        "rows_written": written,
        "rows_total": state.rows,
        "watermark": state.watermark,
    }


def is_stale(state) -> bool:
    """Past the full-rebuild floor. See STALE_AFTER for why one still exists."""
    return (timezone.now() - state.built_at) >= STALE_AFTER


#: How long a stale projection may keep being served while its refresh happens
#: off the request path, before a reader gives up waiting and rebuilds inline.
#:
#: This exists because the async refresh can fail in ways that are SILENT from
#: the read path: the dispatching user has no stored Connect token, the broker is
#: down, the worker is saturated, the task raises. Without a deadline every
#: subsequent request would see "stale, but async is enabled", dispatch another
#: doomed task, and serve the projection anyway -- so a broken refresh would
#: quietly disable STALE_AFTER itself, which is the retraction backstop. The
#: failure would present as a verdict standing that should have been withdrawn,
#: with nothing in the logs saying the floor had stopped firing.
#:
#: 2x rather than a tuned number: it is a fallback, not a schedule. One missed
#: refresh cycle is tolerated, two means the mechanism is not working and the
#: reader should pay the cost that the async path was supposed to save.
REFRESH_DEADLINE = 2 * STALE_AFTER


def is_past_refresh_deadline(state) -> bool:
    """Too old to keep serving while a background refresh is trusted to happen."""
    return (timezone.now() - state.built_at) >= REFRESH_DEADLINE


def async_stale_refresh_enabled() -> bool:
    """Is the stale full-rebuild allowed to move off the request path?

    Defaults to False, which is EXACTLY today's behaviour: a stale projection is
    rebuilt synchronously inside the request that found it stale. The flag is
    the switch for a production-behaviour change whose value cannot be judged
    from a test suite -- see the module note on the read path in data_access.
    """
    return bool(getattr(settings, "PRIOR_AUDIT_ASYNC_STALE_REFRESH", False))


#: One in-flight refresh per opportunity. The lock lives in the cache rather than
#: the DB because losing it is harmless: the deadline above still bounds the
#: worst case, and django_redis is configured with IGNORE_EXCEPTIONS, so a cache
#: outage makes `add` falsy and we simply stop dispatching rather than erroring.
_REFRESH_LOCK_KEY = "prior-audit-refresh:{opportunity_id}"
_REFRESH_LOCK_TTL = int(STALE_AFTER.total_seconds() // 4)  # 6h


def schedule_stale_refresh(opportunity_id: int, username: str) -> bool:
    """Ask a worker to do the full rebuild. True if this call dispatched one.

    Single-flight, and deliberately on the WORKER side of the boundary. The
    obvious place for a lock is the request -- but the expensive step is the
    outbound fetch of every completed session, so an in-request lock leaves the
    losers either blocking on it (the #1152 pile-up this whole issue is about)
    or duplicating the work anyway. Out here the losers simply serve the
    projection they already have and return, which is the answer they wanted.

    Scope is unchanged from the synchronous path: the rebuild runs under the
    identity of the user whose request found the projection stale, using the
    token already stored for them. It is not a standing credential grant -- there
    is no schedule and no service account, and nothing runs when nobody is
    reading. That distinction is why this does not re-open the decision recorded
    in CELERY_BEAT_SCHEDULE against a scheduled prior-audit job.
    """
    if not username:
        # Nothing to run as. Refusing is correct: guessing an identity is how
        # Pulse understated every figure 5x (see pulse.client.get_poller_user).
        return False
    if not cache.add(_REFRESH_LOCK_KEY.format(opportunity_id=opportunity_id), username, _REFRESH_LOCK_TTL):
        return False
    from connect_labs.audit.tasks import refresh_prior_audit_projection

    try:
        refresh_prior_audit_projection.delay(opportunity_id, username)
    except Exception:
        # A broker that will not accept the task must not turn a working page
        # into a 500 -- the caller still holds a correct answer either way.
        # Release the lock so the next reader can retry rather than waiting out
        # the TTL with no refresh queued.
        logger.exception(
            "prior-audit projection: could not queue refresh for opp %s; "
            "serving the existing projection until the refresh deadline",
            opportunity_id,
        )
        cache.delete(_REFRESH_LOCK_KEY.format(opportunity_id=opportunity_id))
        return False
    return True


def clear_refresh_lock(opportunity_id: int) -> None:
    """Let a new refresh be dispatched as soon as the last one finished."""
    cache.delete(_REFRESH_LOCK_KEY.format(opportunity_id=opportunity_id))


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
