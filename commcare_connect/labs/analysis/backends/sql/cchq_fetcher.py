"""
CCHQ Form API fetcher for the analysis pipeline.

Fetches forms from CommCare HQ and normalizes them to the same dict shape
as Connect CSV visits, so FieldComputation path extraction works identically.
"""

import logging
from datetime import datetime

from django.http import HttpRequest

from commcare_connect.labs.analysis.config import DataSourceConfig
from commcare_connect.labs.integrations.commcare.api_client import CommCareDataAccess
from commcare_connect.workflow.templates.mbw_monitoring.data_fetchers import fetch_opportunity_metadata

logger = logging.getLogger(__name__)


def normalize_cchq_form_to_visit_dict(form: dict, index: int) -> dict:
    """
    Normalize a CCHQ form dict to look like a Connect visit dict.

    The key insight: FieldComputation uses paths like "form.field_name"
    which work on both Connect's form_json and CCHQ's form data because
    we put the CCHQ form data under the "form_json" key.
    """
    form_data = form.get("form", {})
    meta = form_data.get("meta", {}) if isinstance(form_data, dict) else {}
    username = meta.get("username", "") or meta.get("userID", "")

    received_on = form.get("received_on", "")
    visit_date = None
    if received_on:
        try:
            visit_date = datetime.fromisoformat(received_on.replace("Z", "+00:00")).date().isoformat()
        except (ValueError, AttributeError):
            visit_date = received_on[:10] if len(received_on) >= 10 else None

    return {
        "id": form.get("id", index),
        "opportunity_id": 0,
        "username": username,
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
        "form_json": form,  # Entire CCHQ form -- paths start with "form."
        "completed_work": "",
        "status_modified_date": None,
        "review_status": "",
        "review_created_on": None,
        "justification": "",
        "date_created": received_on,
        "completed_work_id": None,
        "images": [],
    }


def fetch_cchq_forms_as_visit_dicts(
    request: HttpRequest,
    data_source: DataSourceConfig,
    access_token: str,
    opportunity_id: int,
) -> list[dict]:
    """
    Fetch CCHQ forms and return them as normalized visit dicts.

    Args:
        request: HttpRequest with commcare_oauth in session
        data_source: DataSourceConfig with type="cchq_forms"
        access_token: Connect OAuth token (for opportunity metadata)
        opportunity_id: Opportunity ID (for metadata lookup)

    Returns:
        List of visit-shaped dicts ready for SQL backend processing
    """
    metadata = fetch_opportunity_metadata(access_token, opportunity_id)
    cc_domain = metadata.get("cc_domain")
    if not cc_domain:
        raise ValueError(f"No cc_domain found for opportunity {opportunity_id}")

    app_id = data_source.app_id
    if not app_id and data_source.app_id_source == "opportunity":
        app_id = metadata.get("cc_app_id", "")

    client = CommCareDataAccess(request, cc_domain)
    if not client.check_token_valid():
        raise ValueError(
            "CommCare OAuth not configured or expired. " "Please authorize CommCare access at /labs/commcare/initiate/"
        )

    form_name = data_source.form_name
    xmlns = None

    fetch_app_id = None

    # Strategy 1: Try gs_app_id first (for GS forms in separate supervisor app)
    if data_source.gs_app_id:
        xmlns = client.get_form_xmlns(data_source.gs_app_id, form_name)
        if xmlns:
            fetch_app_id = data_source.gs_app_id
            logger.info(f"[CCHQ Fetcher] Found xmlns via gs_app_id: {xmlns}")

    # Strategy 2: Try the main app_id
    if not xmlns and app_id:
        xmlns = client.get_form_xmlns(app_id, form_name)
        if xmlns:
            fetch_app_id = app_id
            logger.info(f"[CCHQ Fetcher] Found xmlns via app_id: {xmlns}")

    # Strategy 3: Search all apps
    if not xmlns:
        xmlns = client.discover_form_xmlns(form_name)
        if xmlns:
            logger.info(f"[CCHQ Fetcher] Discovered xmlns via search: {xmlns}")

    if not xmlns:
        logger.warning(f"[CCHQ Fetcher] Could not discover xmlns for '{form_name}', returning empty")
        return []

    forms = client.fetch_forms(xmlns=xmlns, app_id=fetch_app_id)
    logger.info(f"[CCHQ Fetcher] Fetched {len(forms)} '{form_name}' forms from {cc_domain}")

    visit_dicts = [normalize_cchq_form_to_visit_dict(form, i) for i, form in enumerate(forms)]
    return visit_dicts
