"""Labs image-audit results as pipeline rows (``data_source.type = "labs_audits"``).

Reads the opportunity's AuditSession LabsRecords (experiment "audit") through the
audit app's own data-access layer, as the pipeline's user -- the same token every
other source of the pipeline uses -- and returns ONE ROW PER AUDITED VISIT with the
human photo-review counts for that visit. See ``connect_labs/audit/audited_visit_rows.py``
for the rules (human results only, newest decision wins per image, pending excluded
from ``n_decided``).

A labs-only (synthetic) opportunity is served in-process by the local records
backend: ``LabsRecordAPIClient`` short-circuits on ``is_labs_only_opportunity_id``,
so nothing here special-cases it.

Each row's ``id`` is the Connect visit id as text -- exactly the ``visit_id`` the
visits cache stores -- so a semantic registry joins it with a lookup
``{on: visit_id, key: visit_id}``. The counts sit under ``form_json["audit"]``, so a
schema reads them at ``audit.n_pass`` etc. (declare them with ``transform: int`` so
SQL sees numbers).
"""

from __future__ import annotations

import logging

from django.http import HttpRequest

from connect_labs.labs.analysis.config import DataSourceConfig

logger = logging.getLogger(__name__)

#: form_json key the per-visit audit fields live under; schema paths start "audit.".
FORM_JSON_KEY = "audit"


def _get_access_token(request: HttpRequest | None, access_token: str | None) -> str:
    if access_token:
        return access_token
    if request is not None:
        token = (request.session.get("labs_oauth", {}) or {}).get("access_token", "")
        if token:
            return token
    raise ValueError(
        "labs_audits data source requires a Connect OAuth token. "
        "Ensure you are logged in via Connect OAuth before running this pipeline."
    )


def normalize_audited_visit_row(row: dict, opportunity_id: int) -> dict:
    """One ``audited_visit_rows`` row -> visit-dict shape for the raw cache."""
    return {
        "id": row["visit_id"],
        "opportunity_id": opportunity_id,
        "username": row.get("username") or "",
        "visit_date": row.get("visit_date"),
        # Not a Connect visit status: these rows are review results, and claiming
        # "approved" would let a status filter mistake them for visits.
        "status": "",
        "entity_id": "",
        "entity_name": "",
        "deliver_unit": "",
        "deliver_unit_id": None,
        "location": "",
        "flagged": False,
        "flag_reason": "",
        "reason": "",
        "form_json": {FORM_JSON_KEY: row},
        "completed_work": "",
        "status_modified_date": None,
        "review_status": "",
        "review_created_on": None,
        "justification": "",
        "date_created": None,
        "completed_work_id": None,
        "images": [],
    }


def fetch_labs_audits_as_visit_dicts(
    request: HttpRequest | None,
    data_source: DataSourceConfig,
    access_token: str | None,
    opportunity_id: int,
) -> list[dict]:
    """Every audited visit of ``opportunity_id``, as normalized visit dicts."""
    from connect_labs.audit.audited_visit_rows import build_audited_visit_rows
    from connect_labs.audit.data_access import AuditDataAccess

    if data_source.type != "labs_audits":
        raise ValueError(f"fetch_labs_audits_as_visit_dicts got a {data_source.type!r} source")
    token = _get_access_token(request, access_token)

    # Opportunity-scoped on purpose: an explicit opportunity_id keeps BaseDataAccess
    # from blending in the page's program context (see its __init__), and the API
    # authorizes the read against this opportunity for this user.
    data_access = AuditDataAccess(opportunity_id=opportunity_id, access_token=token)
    try:
        sessions = data_access.get_audit_sessions()
    finally:
        data_access.close()

    rows = build_audited_visit_rows(sessions)
    logger.info(
        "[LabsAuditsFetcher] opp %s: %s audit sessions -> %s audited visits",
        opportunity_id,
        len(sessions),
        len(rows),
    )
    return [normalize_audited_visit_row(r, opportunity_id) for r in rows]
