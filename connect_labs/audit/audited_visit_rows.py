"""One row per audited VISIT, from an opportunity's image-audit sessions.

This is what the ``labs_audits`` pipeline data source reads
(``labs/analysis/backends/sql/labs_audits_fetcher.py``). It exists so a semantic
registry can LEFT-JOIN Labs' own photo-review results onto its visit rows with a
``lookups`` entry ``on: visit_id`` and grade a per-worker photo approval rate.

THE RULES (keep them in step with the docs in WORKFLOW_REFERENCE.md and
user_docs/semantic-layer.md):

* HUMAN results only. An image's verdict is its assessment's ``result``, sorted by
  ``models.human_verdict`` into pass / fail / duplicate_fake / pending -- the same
  classification the session stats and the Photo Audit Report use. ``ai_result`` is
  never read: an AI reviewer's opinion is not a decision.
* An image is one ``(visit_id, blob_id)`` -- one photo slot on one visit, the key
  ``PriorAuditVerdict`` uses. Only images that HAVE an assessment entry in some
  session count, as in ``get_assessment_stats``; an image a session merely lists in
  ``visit_images`` with no assessment is not counted at all.
* Every session counts, whatever its status: a human decision taken in an
  in-progress session still counts; an image nobody has decided is ``pending``.
* An image audited in more than one session: the most recent session's human
  DECISION wins. Sessions are ordered by ``data_access.PRIOR_AUDIT_ORDER`` -- a
  completed session (with ``completed_at``) outranks one without, then later
  ``completed_at`` wins, then the higher session id (records carry no modified
  date; ids grow with creation). A session in which the image is still pending
  never overrides an earlier decision on it: "pending" is the absence of a
  decision, not a newer one.
* One row per visit with at least one assessed image. ``n_decided`` is
  pass + fail + duplicate_fake; ``n_pending`` is excluded from it. The approval
  rate is therefore ``n_pass / n_decided`` (duplicate/fake counts against it), the
  Photo Audit Report's definition.
* ``username`` is the worker who MADE THE VISIT (who took the photo): the
  ``username`` the session stored on the visit's images in ``visit_images``, which
  is the Connect visit's own username copied at session creation
  (``analysis_config.extract_images_with_question_ids``). Not the session's
  ``flw_username`` (the first visit's worker -- wrong for a combined session) and
  not the record's ``username`` (the AUDITOR who created the session).
* ``xform_id`` is the CommCare form (xform) id of the visit, the id a CommCare-form
  pipeline carries as ``form.meta.instanceID``: it lets a report line these results up
  with its own form rows. It is read from the ``hq_xform_id`` a session stores on a
  visit's images when it was created with the CommCare HQ photo source, and is ""
  otherwise; ``labs_audits_fetcher`` fills a blank one from the visit cache.
* ``visit_date`` is the local date of the visit's timestamp from the same place
  (``prior_audit_projection._visit_metadata``), or None.
"""

from __future__ import annotations

from collections.abc import Iterable

from connect_labs.audit.models import human_verdict

#: The per-visit fields a row carries; a pipeline reads them at ``audit.<name>``.
ROW_FIELDS = (
    "visit_id",
    "xform_id",
    "username",
    "visit_date",
    "n_images",
    "n_pass",
    "n_fail",
    "n_duplicate_fake",
    "n_pending",
    "n_decided",
    "question_ids",
    "session_ids",
)


def build_audited_visit_rows(sessions: Iterable) -> list[dict]:
    """``sessions`` (AuditSessionRecord-like) -> one dict per audited visit.

    Pure: no I/O. Rows are ordered by visit id (numerically where the ids are
    numbers) so the output is stable.
    """
    from connect_labs.audit.data_access import PRIOR_AUDIT_ORDER
    from connect_labs.audit.prior_audit_projection import _visit_metadata

    # (visit_id, blob_id) -> (verdict, question_id, session_id)
    winner: dict[tuple[str, str], tuple[str, str, int]] = {}
    # visit_id -> (username, visit_date), from the most recent session that knows it
    visit_meta: dict[str, tuple[str, object]] = {}
    sessions_by_visit: dict[str, set] = {}
    xform_by_visit: dict[str, str] = {}

    for session in sorted(sessions, key=PRIOR_AUDIT_ORDER):
        data = getattr(session, "data", None) or {}
        metadata = _visit_metadata(session)
        for (visit_key, _blob), (username, visit_date) in metadata.items():
            if username or visit_date:
                prev_user, prev_date = visit_meta.get(visit_key, ("", None))
                visit_meta[visit_key] = (username or prev_user, visit_date or prev_date)
        for visit_key, images in (data.get("visit_images") or {}).items():
            for image in images or []:
                xform_id = (image or {}).get("hq_xform_id")
                if xform_id:
                    xform_by_visit[str(visit_key)] = str(xform_id)
                    break
        question_of = _question_ids(data)
        for visit_key, visit_result in (data.get("visit_results") or {}).items():
            visit_key = str(visit_key)
            for blob_id, assessment in ((visit_result or {}).get("assessments") or {}).items():
                key = (visit_key, str(blob_id))
                verdict = human_verdict(assessment or {})
                question_id = (assessment or {}).get("question_id") or question_of.get(key) or ""
                sessions_by_visit.setdefault(visit_key, set()).add(session.id)
                if verdict != "pending" or key not in winner:
                    # Ascending order: a later decision overwrites; pending only fills a gap.
                    winner[key] = (verdict, question_id, session.id)

    per_visit: dict[str, dict] = {}
    for (visit_key, _blob), (verdict, question_id, _sid) in winner.items():
        row = per_visit.get(visit_key)
        if row is None:
            username, visit_date = visit_meta.get(visit_key, ("", None))
            row = per_visit[visit_key] = {
                "visit_id": visit_key,
                "xform_id": xform_by_visit.get(visit_key, ""),
                "username": username,
                "visit_date": visit_date.isoformat() if visit_date else None,
                "n_images": 0,
                "n_pass": 0,
                "n_fail": 0,
                "n_duplicate_fake": 0,
                "n_pending": 0,
                "_questions": set(),
            }
        row["n_images"] += 1
        row[f"n_{verdict}"] += 1
        if question_id:
            row["_questions"].add(str(question_id))

    rows = []
    for visit_key in sorted(per_visit, key=_visit_sort_key):
        row = per_visit[visit_key]
        row["n_decided"] = row["n_pass"] + row["n_fail"] + row["n_duplicate_fake"]
        row["question_ids"] = ",".join(sorted(row.pop("_questions")))
        row["session_ids"] = ",".join(str(s) for s in sorted(sessions_by_visit.get(visit_key, ())))
        rows.append({name: row[name] for name in ROW_FIELDS})
    return rows


def _question_ids(data: dict) -> dict[tuple[str, str], str]:
    """``(visit_id, blob_id) -> question_id`` from ``visit_images``, for an
    assessment saved without one."""
    out = {}
    for visit_key, images in (data.get("visit_images") or {}).items():
        for image in images or []:
            image = image or {}
            if image.get("blob_id") and image.get("question_id"):
                out[(str(visit_key), str(image["blob_id"]))] = str(image["question_id"])
    return out


def _visit_sort_key(visit_key: str):
    return (0, int(visit_key), "") if visit_key.isdigit() else (1, 0, visit_key)
