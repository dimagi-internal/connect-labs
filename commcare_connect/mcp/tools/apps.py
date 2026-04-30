"""get_opportunity_apps — fetch the Learn/Deliver CommCare app structure for an opportunity.

Wraps the production endpoint
``GET /export/opportunity/<opp_id>/app_structure/?app_type=learn|deliver|both``
(see ``commcare_connect/data_export/views.py:AppStructureView`` in
``dimagi/commcare-connect``). Each requested side returns the full HQ
application JSON (modules, forms, etc.) under ``learn_app`` / ``deliver_app``,
or ``null`` if the opportunity has no app of that type configured.

This is the structure most useful for building workflow pipeline schemas, so
agents can map form questions to their JSON paths without leaving the labs MCP.
"""

from __future__ import annotations

import logging

import httpx

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

VALID_APP_TYPES = ("learn", "deliver", "both")

# Fetching app_structure round-trips through CommCare HQ on the production side,
# which can be noticeably slower than a plain labs read. Bump the per-call
# timeout above LabsRecordAPIClient's 30s default.
APP_STRUCTURE_TIMEOUT_SECONDS = 120.0


@register(
    name="get_opportunity_apps",
    description=(
        "Fetch the Learn and/or Deliver CommCare application structure for a "
        "Connect opportunity. Wraps production "
        "GET /export/opportunity/<opp_id>/app_structure/. Returns "
        '{"learn_app": <hq app json or null>, "deliver_app": <hq app json or null>}. '
        "Use app_type='learn' or 'deliver' to fetch only one side; default is "
        "'both'. The full HQ application JSON (modules, forms, questions) is "
        "returned, which is what you need to build workflow pipeline schemas."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {
                "type": "integer",
                "description": "Connect opportunity ID. Caller must have access to this opportunity.",
            },
            "app_type": {
                "type": "string",
                "enum": list(VALID_APP_TYPES),
                "description": "Which side(s) to fetch. Defaults to 'both'.",
            },
        },
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
)
def get_opportunity_apps(user, opportunity_id: int, app_type: str = "both") -> dict:
    if app_type not in VALID_APP_TYPES:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"Invalid app_type {app_type!r}. Must be one of: {', '.join(VALID_APP_TYPES)}.",
        )

    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        url = f"{client.base_url}/export/opportunity/{opportunity_id}/app_structure/"
        try:
            resp = client.http_client.get(
                url,
                params={"app_type": app_type},
                timeout=APP_STRUCTURE_TIMEOUT_SECONDS,
            )
        except httpx.RequestError as e:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"Failed to reach Connect production: {e}",
            )

        if resp.status_code == 404:
            raise MCPToolError(
                "NOT_FOUND",
                f"Opportunity {opportunity_id} not found, has no API key, or you do not have access to it.",
            )
        if resp.status_code == 502:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                "Connect could not fetch the app structure from CommCare HQ.",
            )
        if resp.status_code >= 400:
            raise MCPToolError(
                "UPSTREAM_ERROR",
                f"Unexpected response from Connect ({resp.status_code}): {resp.text[:200]}",
            )

        return resp.json()
    finally:
        client.close()
