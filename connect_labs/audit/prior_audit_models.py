"""Local projection of "which images has a human already judged".

WHY THIS TABLE EXISTS
``get_prior_audited_images`` answers one question -- *has this exact image been
judged in some other completed audit of this opportunity?* -- and today it
answers it by RECONSTRUCTING the opportunity's entire audit history on every
call: pull every AuditSession record from Connect's export API, each carrying
its whole ``data`` blob, parse them all, walk every verdict, build a dict, throw
it away. It runs on every ``/audit/api/<id>/bulk-data/`` load, whose own response
is single-digit kilobytes, and its cost tracks accumulated audit history rather
than anything on the page -- so it degrades on its own as audits pile up. It was
the dominant cost in the 2026-08-20 web-tier saturation (#1246, #1152).

WHY ONE ROW PER (SESSION, IMAGE) AND NOT ONE PER IMAGE
The obvious schema -- one row per image holding the winning verdict -- cannot
survive a retraction. ``ExperimentAuditUncompleteView`` reopens a completed
session (``status`` back to ``in_progress``, ``completed_at`` to ``None``), which
withdraws every verdict it contributed. Because the rule is *most recent
completed wins*, withdrawing session A's verdict must RESTORE session B's older
one for that image -- and a table holding only the winner has no memory that B
ever voted.

So this stores the raw contribution and lets the winner be a query:

    complete    -> insert this session's rows
    uncomplete  -> delete this session's rows
    read        -> DISTINCT ON (visit_id, blob_id) ... ORDER BY completed_at DESC

Every operation is idempotent and none needs to recompute anything.

WHAT THE KEY IS, AND WHAT IT IS NOT
``(visit_id, blob_id)`` identifies one photo slot on one visit. In Connect,
``BlobMeta`` is ``unique_together ("parent_id", "name")`` and ``blob_id``
defaults to ``uuid4()``, so this is stable and effectively unique -- but note
``blob_id`` is **not declared unique** there (a plain ``CharField`` with a
non-unique index), which is why the key is the pair and nothing here assumes
global blob uniqueness.

It identifies an UPLOAD, not a picture. Re-uploading the same photo produces a
new ``blob_id``. That is the correct semantics for "was this submission judged?",
and it is emphatically NOT duplicate detection -- that is the separate
``/detect_duplicates`` path in ``duplicate_detection.py``. Do not repurpose this
table for it.

THIS IS A CACHE, NOT THE SYSTEM OF RECORD
Audit sessions live in Connect and labs writes them over the API, so labs has no
transactional control over the source. A half-completed write drifts the two
apart, and drift here is silent and consequential: an auditor told "not
previously audited" about an image that was. Therefore this table must always be
rebuildable from the source (``rebuild_prior_audit_index``), verifiable against
it (``verify_prior_audit_index``), and the read path must fall back to live
computation rather than report an empty history as a clean one.
"""

from __future__ import annotations

from django.db import models

# Verdicts that count as "a human judged this". Mirrors data_access._AUDIT_VERDICTS;
# an image with no verdict, or a non-verdict value, is not a prior audit.
AUDIT_VERDICTS = ("pass", "fail", "duplicate_fake", "duplicate", "fake")


class PriorAuditVerdict(models.Model):
    """One completed session's verdict on one image.

    Not "the" verdict for that image -- several sessions may have judged it, and
    which one wins is decided on read by ``completed_at``.
    """

    # Scope for every read; the index is always asked for one opportunity.
    opportunity_id = models.IntegerField(db_index=True)

    # The contributing session. Deleting by this alone is how a retraction works.
    session_id = models.IntegerField(db_index=True)
    session_title = models.CharField(max_length=255, blank=True, default="")

    # visit_id is stored as text because it is a JSON object key upstream
    # (``data["visit_results"]``), and coercing it to an int here would invent a
    # normalisation the source does not have.
    visit_id = models.CharField(max_length=64)
    blob_id = models.CharField(max_length=255)

    result = models.CharField(max_length=32)

    # WHO and WHEN, denormalised from the same session blob the verdicts come from.
    # The review screen shows an FLW's duplicate/fake history by the day the photo was
    # taken, and neither field is derivable from a verdict row: ``visit_results`` holds
    # the verdicts, ``visit_images`` holds the FLW and the visit timestamp, and
    # rows_for_session joins the two halves of the one session it is already reading.
    #
    # Both are blank/null on every row written before this column existed, and on any
    # row whose session blob lacked the metadata. A blank username is NOT "no FLW" --
    # it is "not attributed" -- which is why duplicate_history_for_flws reports whether
    # any such row exists instead of quietly leaving them out of a total it presents as
    # complete. Backfill with ``rebuild_prior_audit_index``.
    username = models.CharField(max_length=150, blank=True, default="")

    # The visit's LOCAL date -- the day the photo was taken, which is what the history
    # columns mean to a supervisor. Deliberately NOT completed_at: a daily audit run on
    # the 22nd covers the 21st's visits, and completed_at also moves when a completed
    # audit is edited (#1286), so it tracks reviewer activity rather than FLW behaviour.
    visit_date = models.DateField(null=True, blank=True)

    # Nullable because a session can be marked completed without a timestamp.
    # Such a row loses every comparison in the winner query, which matches
    # build_prior_audit_index: a null completed_at never displaces a dated one.
    completed_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        constraints = [
            # One session gets one verdict per image. Makes the rebuild an upsert
            # and makes a double-insert impossible rather than merely unlikely.
            models.UniqueConstraint(
                fields=["session_id", "visit_id", "blob_id"],
                name="uniq_prior_audit_session_visit_blob",
            )
        ]
        indexes = [
            # The read path: one opportunity, then the winner per image.
            models.Index(
                fields=["opportunity_id", "visit_id", "blob_id", "-completed_at"],
                name="idx_prior_audit_lookup",
            ),
            # The per-FLW duplicate-history read: one opportunity, one FLW, all time.
            # Without this that read degrades to a scan of the opportunity's whole
            # history -- 17,653 rows on opp 2154 -- which is the cost this table exists
            # to remove.
            models.Index(
                fields=["opportunity_id", "username"],
                name="idx_prior_audit_flw",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.visit_id}:{self.blob_id} = {self.result} (session {self.session_id})"


class PriorAuditProjectionState(models.Model):
    """Records that an opportunity's projection has actually been built.

    Without this the read path cannot tell the two meanings of an empty result
    apart: "this opportunity genuinely has no prior verdicts" and "nobody has
    ever built this opportunity". They look identical in the rows table and
    differ completely in consequence -- the second one tells an auditor an image
    has never been judged when it has. So the projection is trusted ONLY for an
    opportunity with a row here, and every other opportunity silently falls back
    to the live computation.

    ``built_by`` is recorded because scope follows the identity that built it:
    the source is Connect's export API, which returns what that user's org
    membership can see. Pulse learned this the hard way -- an ingest run under a
    narrower account understated every headline figure ~5x and nothing errored
    (see pulse/client.get_poller_user). Here a narrower identity would produce
    MISSING prior verdicts, which is the dangerous direction, so which identity
    built a projection has to be answerable after the fact.
    """

    opportunity_id = models.IntegerField(unique=True)
    built_at = models.DateTimeField(auto_now=True)
    built_by = models.CharField(max_length=150, blank=True, default="")

    # Both counts are for drift diagnosis: a reconciliation run that finds a
    # different session count than the build saw is the cheapest possible signal
    # that the identity's scope changed underneath the projection.
    source_sessions = models.IntegerField(default=0)
    rows = models.IntegerField(default=0)

    # Highest completed_at this projection has ingested. The next read asks the
    # export API only for sessions later than this, which is both the staleness
    # CHECK and the incremental update -- the records that come back are exactly
    # the ones that changed.
    #
    # Only usable because completed_at now MOVES when a completed session is
    # edited (#1286). While it was frozen at completion there was no timestamp
    # that tracked a verdict change, which is why this started as a blunt TTL.
    watermark = models.DateTimeField(null=True, blank=True)

    def __str__(self) -> str:
        return f"opp {self.opportunity_id}: {self.rows} rows from {self.source_sessions} sessions"
