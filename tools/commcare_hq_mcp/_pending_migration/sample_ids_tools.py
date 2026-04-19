"""Sample IDs tool for the MCP server.

Returns a small set of real fund, solicitation, and program IDs from the
current environment so agents can construct valid localhost URLs for testing
without manual curl commands.

Uses httpx to call the Connect production API (no Django dependency).
"""

from __future__ import annotations

import logging

import httpx
from connect_client import CONNECT_URL, HTTP_TIMEOUT, _get_connect_token

logger = logging.getLogger(__name__)

LABS_RECORD_URL = f"{CONNECT_URL.rstrip('/')}/export/labs_record/"
ORG_DATA_URL = f"{CONNECT_URL.rstrip('/')}/export/opp_org_program_list/"

MAX_PER_CATEGORY = 5


def _headers() -> dict[str, str]:
    token = _get_connect_token()
    return {"Authorization": f"Bearer {token}"}


async def get_sample_ids() -> dict:
    """Fetch a small set of real IDs for funds, solicitations, and programs.

    Returns a dict with three keys, each containing a list of
    {"id": ..., "name": ...} entries (up to MAX_PER_CATEGORY each).

    Strategy:
    - Programs: fetched from /export/opp_org_program_list/ (the user's own programs)
    - Solicitations: fetched from /export/labs_record/?type=solicitation using
      the user's first program_id for scoping
    - Funds: fetched from /export/labs_record/?type=fund using the same program_id
    """
    headers = _headers()

    programs: list[dict] = []
    solicitations: list[dict] = []
    funds: list[dict] = []
    first_program_id: str | None = None

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        # 1. Fetch programs from the user's org data
        try:
            resp = await client.get(ORG_DATA_URL, headers=headers)
            resp.raise_for_status()
            org_data = resp.json()

            for prog in (org_data.get("programs") or [])[:MAX_PER_CATEGORY]:
                prog_id = prog.get("id")
                prog_name = prog.get("name") or prog.get("slug") or str(prog_id)
                programs.append({"id": prog_id, "name": prog_name})
                if first_program_id is None and prog_id is not None:
                    first_program_id = str(prog_id)
        except Exception as e:
            logger.warning(f"Failed to fetch programs: {e}")

        # 2. Fetch solicitations (scoped by first program, or public)
        try:
            sol_params: dict[str, str] = {"type": "solicitation"}
            if first_program_id:
                sol_params["program_id"] = first_program_id
            resp = await client.get(LABS_RECORD_URL, params=sol_params, headers=headers)
            resp.raise_for_status()
            for rec in resp.json()[:MAX_PER_CATEGORY]:
                rec_id = rec.get("id")
                data = rec.get("data", {})
                title = data.get("title") or data.get("name") or f"Solicitation {rec_id}"
                solicitations.append({"id": rec_id, "name": title})
        except Exception as e:
            logger.warning(f"Failed to fetch solicitations: {e}")

        # 3. Fetch funds (scoped by first program, or public)
        try:
            fund_params: dict[str, str] = {"type": "fund"}
            if first_program_id:
                fund_params["program_id"] = first_program_id
            resp = await client.get(LABS_RECORD_URL, params=fund_params, headers=headers)
            resp.raise_for_status()
            for rec in resp.json()[:MAX_PER_CATEGORY]:
                rec_id = rec.get("id")
                data = rec.get("data", {})
                name = data.get("name") or data.get("funder_slug") or f"Fund {rec_id}"
                funds.append({"id": rec_id, "name": name})
        except Exception as e:
            logger.warning(f"Failed to fetch funds: {e}")

    return {
        "funds": funds,
        "solicitations": solicitations,
        "programs": programs,
    }
