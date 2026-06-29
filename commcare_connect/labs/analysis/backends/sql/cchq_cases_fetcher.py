"""
CCHQ Case API v2 fetcher for the analysis pipeline.

Fetches cases from CommCare HQ (e.g. work-area cases) and normalizes them to
the same dict shape as Connect CSV visits, so FieldComputation path extraction
works identically.

Each case is placed under form_json["case"] so pipeline field paths follow the
pattern "case.properties.<prop>" / "case.owner_id" — the same dot-notation
mechanism cchq_forms uses for "form.*" paths.

Auth: cases require the CommCare HQ OAuth token from the user's web session
(like cchq_forms), NOT the Connect OAuth token. The Connect ``access_token``
is used only to resolve the opportunity's HQ domain via opportunity metadata.
"""

import logging

from django.http import HttpRequest

from commcare_connect.labs.analysis.config import DataSourceConfig
from commcare_connect.labs.analysis.data_access import fetch_opportunity_metadata
from commcare_connect.labs.integrations.commcare.api_client import CommCareDataAccess

logger = logging.getLogger(__name__)


def normalize_cchq_case_to_visit_dict(case: dict, opportunity_id: int, index: int) -> dict:
    """
    Normalize a CommCare Case API v2 case dict to visit-dict shape.

    The whole case is nested under form_json["case"] so FieldComputation paths
    like "case.properties.expected_visit_count" and "case.owner_id" resolve via
    the existing dot-notation extractor.

    Case API v2 returns each case roughly as::

        {case_id, case_name, case_type, owner_id, date_opened,
         date_modified, closed, properties: {...}, indices: {...}}
    """
    case_id = case.get("case_id", index)

    raw_date = case.get("date_modified") or case.get("date_opened") or ""
    visit_date = str(raw_date)[:10] or None

    return {
        "id": case_id,
        "opportunity_id": opportunity_id,
        "username": "",  # WA owner is an HQ owner_id, not a Connect username; mapped report-side
        "visit_date": visit_date,
        "status": "approved",
        "entity_id": str(case_id),
        "entity_name": case.get("case_name", ""),
        "deliver_unit": "",
        "deliver_unit_id": None,
        "location": "",
        "flagged": False,
        "flag_reason": "",
        "reason": "",
        "form_json": {"case": case},  # paths start with "case."
        "completed_work": "",
        "status_modified_date": None,
        "review_status": "",
        "review_created_on": None,
        "justification": "",
        "date_created": case.get("date_opened", ""),
        "completed_work_id": None,
        "images": [],
    }


def fetch_cchq_cases_as_visit_dicts(
    request: HttpRequest | None,
    data_source: DataSourceConfig,
    access_token: str,
    opportunity_id: int,
) -> list[dict]:
    """
    Fetch CCHQ cases of ``data_source.case_type`` for the opportunity's HQ domain
    and return them as normalized visit dicts.

    Args:
        request: HttpRequest with commcare_oauth in session. Required — the Case
            API needs a CommCare HQ OAuth token that only exists on the web
            session. Headless callers (MCP/scripts) cannot authenticate to CCHQ.
        data_source: DataSourceConfig with type="cchq_cases" and case_type set.
        access_token: Connect OAuth token (used only for opportunity metadata).
        opportunity_id: Opportunity ID (for cc_domain lookup).

    Returns:
        List of visit-shaped dicts ready for SQL backend processing.

    Raises:
        CCHQHeadlessError: If ``request`` is ``None``.
        ValueError: If case_type is unset, cc_domain is unresolved, or the
            CommCare OAuth token is missing/expired.
        CCHQAuthError: If CCHQ rejects the access probe for the domain.
    """
    if not data_source.case_type:
        raise ValueError("cchq_cases data source requires case_type to be set in the pipeline schema.")

    if request is None:
        from commcare_connect.labs.integrations.commcare.api_client import CCHQHeadlessError

        raise CCHQHeadlessError(
            "Pipeline data_source.type is 'cchq_cases', which requires a "
            "CommCare HQ OAuth token from the user's web session. This call "
            "is running in a headless context (no request) so no token is "
            "available. Run the preview from the web UI."
        )

    metadata = fetch_opportunity_metadata(access_token, opportunity_id)
    cc_domain = metadata.get("cc_domain")
    if not cc_domain:
        raise ValueError(f"No cc_domain found for opportunity {opportunity_id}")

    client = CommCareDataAccess(request, cc_domain)
    if not client.check_token_valid():
        raise ValueError(
            "CommCare OAuth not configured or expired. " "Please authorize CommCare access at /labs/commcare/initiate/"
        )

    # Verify the token actually works for this domain before the (slow) fetch,
    # so the caller sees "Authorize CommCare HQ" instead of "0 cases found".
    if not client.verify_hq_access():
        from commcare_connect.labs.integrations.commcare.api_client import CCHQAuthError

        raise CCHQAuthError(
            f"CommCare HQ rejected the access probe for domain {cc_domain!r}. "
            f"User needs to re-authorize CommCare access at /labs/commcare/initiate/.",
            domain=cc_domain,
        )

    case_type = data_source.case_type
    cases = client.fetch_cases(case_type=case_type)
    logger.info(f"[CCHQ Cases Fetcher] Fetched {len(cases)} '{case_type}' cases from {cc_domain}")

    return [normalize_cchq_case_to_visit_dict(case, opportunity_id, i) for i, case in enumerate(cases)]
