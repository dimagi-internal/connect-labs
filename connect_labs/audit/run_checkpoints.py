"""Checkpoint identity + completeness for audit sessions created by a workflow run.

Two layers ask "has this work already been done for this run?" and they used
to answer it independently, both wrongly:

- ``run_audit_creation`` (``connect_labs.audit.tasks``) asked "does ANY session
  exist for this workflow_run_id?" before creating sessions. That is blind to
  which audit the caller asked for: a run that legitimately creates more than
  one audit (the dual-track template's Track A + Track B, or a second
  "Create Audit" click on a long-lived Muac Picture Audit run) had every call
  after the first silently swallowed -- no sessions created, ``success: True``,
  and the FIRST call's sessions reported back as if they were this call's.
- ``weekly_dual_track_audit_create`` (the batch handler) asked "does a session
  exist for this (opportunity, tag)?" and skipped the whole call if so. That
  is blind to whether the existing sessions were ever FINISHED: a call killed
  during AI review (the dominant real failure mode -- review is where the
  hours go) has sessions, so a resume marked it done and its unreviewed images
  were never revisited.

This module is the single answer both layers now use.

**Identity** (:func:`call_key` / :func:`session_key`) is
``(opportunity_id, tag, start_date, end_date)`` -- what actually distinguishes
one audit-creation call from another within a run. Tag alone is not enough (a
creator run reused across days would collide); the window alone is not enough
(two tracks share a window).

**Completeness** (:func:`session_is_complete`) is read from the session's OWN
persisted flags, the same ones each stage checks to skip itself on re-entry
(``ai_review_complete``, ``visit_cluster_dup_detection_complete``,
``dup_detection_complete``). Eligibility is inferred from the session too, so a
session that never had an AI reviewer isn't held open forever waiting for a
flag nothing will ever write.

The two layers compose deliberately:

- The batch handler skips a call only when it is COMPLETE (see
  :func:`completed_call_keys`) -- no work left to do there.
- Anything not complete falls through to ``run_audit_creation``, which
  recognises its own sessions by :func:`call_key`, skips only re-CREATION, and
  lets the review/detection stages re-enter and finish what's outstanding.
"""

from __future__ import annotations


def _window(criteria) -> tuple:
    """``(start_date, end_date)`` from a criteria dict, normalised to str|None.

    Non-date-range audits (``last_n_per_flw`` and friends) carry no window;
    they key on ``(None, None)``, which still separates them by tag and
    opportunity -- strictly better than the tag-blind behaviour this replaces.
    """
    c = criteria or {}
    start = c.get("start_date")
    end = c.get("end_date")
    return (str(start) if start else None, str(end) if end else None)


def call_key(opportunity_id, criteria) -> tuple:
    """Checkpoint identity for one audit-creation call, from its inputs."""
    return (opportunity_id, (criteria or {}).get("tag") or "", *_window(criteria))


def session_key(session) -> tuple:
    """Checkpoint identity for an existing session, from what it persisted.

    Reads ``data["opportunity_id"]`` (what the audit is ABOUT, which is what
    the call named) rather than the record's storage scope -- see
    ``AuditSessionRecord.opportunity_id`` for why those can differ.
    """
    data = getattr(session, "data", None) or {}
    return (data.get("opportunity_id"), data.get("tag") or "", *_window(data.get("criteria")))


def session_is_complete(session, *, require_dup_detection: bool = False) -> bool:
    """Did every post-creation stage this session was ELIGIBLE for finish?

    Each flag is written only when its stage ran to completion uncancelled
    (see ``_run_ai_review_on_sessions`` / ``_run_duplicate_detection_on_sessions``
    / ``run_visit_cluster_duplicate_detection``), so an unset flag on an
    eligible session means "still outstanding", which is exactly what a resume
    needs to know.

    Eligibility is read from the session itself:

    - ``has_ai_reviewer`` -- persisted at creation; false for a track with no
      classifiers, which must not be held open for an AI review that will
      never run.
    - ``visit_clusters`` -- non-empty only when clustering produced groupings
      for this session, which is what visit-cluster duplicate detection
      consumes.

    ``require_dup_detection`` covers the one stage whose eligibility is NOT
    recoverable from the session: image-hash duplicate detection is gated on
    ``criteria["detect_duplicates"]``, which ``create_audit_session`` does not
    persist. Callers that know they requested it pass True.
    """
    data = getattr(session, "data", None) or {}
    if data.get("has_ai_reviewer") and not data.get("ai_review_complete"):
        return False
    if data.get("visit_clusters") and not data.get("visit_cluster_dup_detection_complete"):
        return False
    if require_dup_detection and not data.get("dup_detection_complete"):
        return False
    return True


def sessions_by_call(sessions) -> dict[tuple, list]:
    """Group sessions by :func:`session_key`."""
    grouped: dict[tuple, list] = {}
    for session in sessions:
        grouped.setdefault(session_key(session), []).append(session)
    return grouped


def completed_call_keys(sessions, *, require_dup_detection: bool = False) -> set[tuple]:
    """Call keys with at least one session, ALL of them complete.

    A call whose sessions exist but are still mid-review is deliberately NOT
    in this set: it has outstanding work, so a resume must re-enter it rather
    than declare it done.
    """
    return {
        key
        for key, group in sessions_by_call(sessions).items()
        if all(session_is_complete(s, require_dup_detection=require_dup_detection) for s in group)
    }
