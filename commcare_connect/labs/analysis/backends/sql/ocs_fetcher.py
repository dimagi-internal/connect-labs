"""
OCS Sessions fetcher for the analysis pipeline.

Fetches sessions from Open Chat Studio and normalizes them to the same dict
shape as Connect CSV visits, so FieldComputation path extraction works
identically.

Session data is placed under form_json["session"] so pipeline field paths
follow the pattern "session.participant.identifier", "session.status", etc.
"""

import logging
from datetime import datetime

from django.http import HttpRequest

from commcare_connect.labs.analysis.config import DataSourceConfig

logger = logging.getLogger(__name__)


class OCSHeadlessError(Exception):
    """Raised when an ocs_sessions pipeline is run without a web request."""

    pass


def normalize_ocs_session_to_visit_dict(session: dict, index: int) -> dict:
    """
    Normalize an OCS session dict to look like a Connect visit dict.

    Session data is stored under form_json["session"] so FieldComputation
    paths like "session.participant.identifier" work via the existing dot-
    notation extractor — the same mechanism CCHQ uses with "form.*" paths.

    username is set to participant.identifier (the Connect ID / phone number)
    so grouping_key: "username" works consistently across all pipeline types.
    """
    participant = session.get("participant") or {}
    identifier = participant.get("identifier", "") or participant.get("remote_id", "")

    created_at = session.get("created_at", "")
    visit_date = None
    if created_at:
        try:
            visit_date = datetime.fromisoformat(created_at.replace("Z", "+00:00")).date().isoformat()
        except (ValueError, AttributeError):
            visit_date = created_at[:10] if len(created_at) >= 10 else None

    return {
        "id": session.get("id", index),
        "opportunity_id": 0,
        "username": identifier,
        "visit_date": visit_date,
        "status": "approved",
        "entity_id": "",
        "entity_name": "",
        "deliver_unit": "",
        "deliver_unit_id": None,
        "location": "",
        "flagged": False,
        "flag_reason": "",
        "reason": "",
        # Entire session nested under "session" key — paths start with "session."
        "form_json": {"session": session},
        "completed_work": "",
        "status_modified_date": None,
        "review_status": "",
        "review_created_on": None,
        "justification": "",
        "date_created": created_at,
        "completed_work_id": None,
        "images": [],
    }


def fetch_ocs_sessions_as_visit_dicts(
    request: HttpRequest | None,
    data_source: DataSourceConfig,
) -> list[dict]:
    """
    Fetch all OCS sessions for the configured experiment and return them as
    normalized visit dicts.

    Args:
        request: HttpRequest with ocs_oauth in session, or None for headless
            callers. Raises OCSHeadlessError when None — OCS sessions require
            an OCS OAuth token from the user's web session.
        data_source: DataSourceConfig with type="ocs_sessions" and experiment_id.

    Returns:
        List of visit-shaped dicts ready for SQL backend processing.

    Raises:
        OCSHeadlessError: If request is None.
        ValueError: If experiment_id is not configured or OCS token is missing.
    """
    if not data_source.experiment_id:
        raise ValueError("ocs_sessions data source requires experiment_id to be set in the pipeline schema.")

    from django.conf import settings

    from commcare_connect.labs.integrations.ocs.api_client import OCSAPIError, OCSDataAccess

    experiment_id = data_source.experiment_id

    # Prefer api_key (X-API-KEY) when available — it gives team-scoped access
    # regardless of which OCS account the viewer is logged into. Fall back to
    # the user's OAuth Bearer token when no api_key is configured.
    api_key = data_source.api_key or getattr(settings, "OCS_PIPELINE_API_KEY", "")

    if api_key:
        import httpx

        base_url = getattr(settings, "OCS_URL", "https://www.openchatstudio.com").rstrip("/")
        http_client = httpx.Client(headers={"X-API-KEY": api_key}, timeout=30.0)
        close_client = http_client.close
    else:
        if request is None:
            raise OCSHeadlessError(
                "Pipeline data_source.type is 'ocs_sessions' with no api_key configured. "
                "This requires an OCS OAuth token from the user's web session, but the call "
                "is running in a headless context. Either set api_key in the pipeline schema "
                "or run the preview from the web UI."
            )
        client = OCSDataAccess(request=request)
        if not client.check_token_valid():
            raise ValueError(
                "OCS OAuth not configured or expired. " "Please authorize OCS access at /labs/ocs/initiate/"
            )
        base_url = client.base_url
        http_client = client.http_client
        close_client = client.close

    logger.info(f"[OCS Fetcher] Fetching all sessions for experiment {experiment_id}...")

    # Paginate through all sessions — list_sessions only returns one page.
    all_sessions = []
    url = f"{base_url}/api/sessions/"
    params = {
        "experiment": experiment_id,
        "ordering": "created_at",
        "page_size": 100,
    }

    page = 0
    while url:
        page += 1
        try:
            response = http_client.get(url, params=params if page == 1 else None)
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            raise OCSAPIError(f"Failed to fetch OCS sessions (page {page}): {e}") from e

        if isinstance(data, dict):
            results = data.get("results", [])
            url = data.get("next")
        else:
            results = data
            url = None

        all_sessions.extend(results)
        logger.debug(f"[OCS Fetcher] Page {page}: {len(results)} sessions (total: {len(all_sessions)})")

    close_client()
    logger.info(f"[OCS Fetcher] Fetched {len(all_sessions)} sessions for experiment {experiment_id}")

    return [normalize_ocs_session_to_visit_dict(s, i) for i, s in enumerate(all_sessions)]
