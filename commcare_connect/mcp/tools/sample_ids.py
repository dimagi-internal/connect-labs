"""Sample IDs tool — returns real org/program/fund/solicitation IDs for testing.

Migrated from tools/commcare_hq_mcp/_pending_migration/sample_ids_tools.py.
Preserves the original response shape.

Implementation notes:
- Programs are fetched from /export/opp_org_program_list/ (not covered by
  LabsRecordAPIClient), so we use client.http_client directly for that call.
- Solicitations and funds use client.get_records(type=...) with an optional
  program_id scope derived from the first program found.
- All errors are caught and logged individually (same graceful-degradation
  strategy as the original): a failure in one category doesn't prevent the
  others from being returned.
"""

from __future__ import annotations

import logging

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register  # noqa: F401

logger = logging.getLogger(__name__)

MAX_PER_CATEGORY = 5


@register(
    name="get_sample_ids",
    description=(
        "Return a small set of real IDs from the current environment "
        "(programs, solicitations, funds) so agents can construct valid "
        "URLs for testing without manual API digging. Intentionally "
        "unscoped — pulls from whatever the caller has access to."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def get_sample_ids(user):
    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        programs: list[dict] = []
        solicitations: list[dict] = []
        funds: list[dict] = []
        first_program_id: int | None = None

        # 1. Fetch programs from /export/opp_org_program_list/
        # This endpoint is not covered by LabsRecordAPIClient.get_records(),
        # so we use client.http_client (the underlying httpx.Client) directly.
        try:
            org_data_url = f"{client.base_url}/export/opp_org_program_list/"
            resp = client.http_client.get(org_data_url)
            resp.raise_for_status()
            org_data = resp.json()
            for prog in (org_data.get("programs") or [])[:MAX_PER_CATEGORY]:
                prog_id = prog.get("id")
                prog_name = prog.get("name") or prog.get("slug") or str(prog_id)
                programs.append({"id": prog_id, "name": prog_name})
                if first_program_id is None and prog_id is not None:
                    first_program_id = prog_id
        except Exception as e:
            logger.warning(f"Failed to fetch programs: {e}")

        # 2. Fetch solicitations (scoped by first program if available)
        try:
            sol_records = client.get_records(
                type="solicitation",
                program_id=first_program_id,
            )
            for rec in sol_records[:MAX_PER_CATEGORY]:
                rec_id = rec.id
                data = rec.data or {}
                title = data.get("title") or data.get("name") or f"Solicitation {rec_id}"
                solicitations.append({"id": rec_id, "name": title})
        except Exception as e:
            logger.warning(f"Failed to fetch solicitations: {e}")

        # 3. Fetch funds (scoped by first program if available)
        try:
            fund_records = client.get_records(
                type="fund",
                program_id=first_program_id,
            )
            for rec in fund_records[:MAX_PER_CATEGORY]:
                rec_id = rec.id
                data = rec.data or {}
                name = data.get("name") or data.get("funder_slug") or f"Fund {rec_id}"
                funds.append({"id": rec_id, "name": name})
        except Exception as e:
            logger.warning(f"Failed to fetch funds: {e}")

        return {
            "funds": funds,
            "solicitations": solicitations,
            "programs": programs,
        }
    finally:
        client.close()
