"""Solicitation tools for the MCP server.

Uses httpx to call the Connect Labs Record API directly (no Django dependency).
Solicitations are stored as LabsRecord entries with type="solicitation".
Responses are stored with type="solicitation_response".
"""

from __future__ import annotations

import logging

import httpx
from connect_client import CONNECT_URL, HTTP_TIMEOUT, _get_connect_token

logger = logging.getLogger(__name__)

LABS_RECORD_URL = f"{CONNECT_URL.rstrip('/')}/export/labs_record/"


def _headers() -> dict[str, str]:
    token = _get_connect_token()
    return {"Authorization": f"Bearer {token}"}


def _serialize_record(record: dict) -> dict:
    """Extract a flat solicitation dict from a LabsRecord API response."""
    data = record.get("data", {})
    return {
        "id": record.get("id"),
        "experiment": record.get("experiment"),
        "type": record.get("type"),
        "program_id": record.get("program_id"),
        "labs_record_id": record.get("labs_record_id"),
        **data,
    }


async def list_solicitations(
    program_id: str | None = None,
    organization_id: str | None = None,
    status: str | None = None,
    solicitation_type: str | None = None,
) -> list[dict]:
    """List solicitations from the Labs Record API."""
    params: dict[str, str] = {"type": "solicitation"}
    experiment = program_id or organization_id
    if experiment:
        params["experiment"] = experiment
    if status:
        params["data__status"] = status
    if solicitation_type:
        params["data__solicitation_type"] = solicitation_type

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        return [_serialize_record(r) for r in resp.json()]


async def get_solicitation(solicitation_id: int) -> dict | None:
    """Get a single solicitation by ID."""
    params = {"type": "solicitation"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        for r in resp.json():
            if r.get("id") == solicitation_id:
                return _serialize_record(r)
    return None


async def create_solicitation(
    program_id: str | None = None,
    organization_id: str | None = None,
    data: dict | None = None,
) -> dict:
    """Create a new solicitation via the Labs Record API.

    Requires either program_id or organization_id for scoping.
    """
    if not data:
        raise ValueError("data is required")
    experiment = program_id or organization_id
    if not experiment:
        raise ValueError("Either program_id or organization_id is required")

    is_public = data.get("is_public", False)
    record: dict = {
        "experiment": experiment,
        "type": "solicitation",
        "data": data,
        "public": is_public,
    }
    if program_id:
        record["program_id"] = int(program_id)
    payload = [record]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after create")
        return _serialize_record(result[0])


async def update_solicitation(solicitation_id: int, update_data: dict) -> dict:
    """Update an existing solicitation via the Labs Record API.

    Fetches the current record first to preserve metadata, then merges
    update_data into the existing data dict.
    """
    # Fetch current record
    current = await get_solicitation(solicitation_id)
    if not current:
        raise ValueError(f"Solicitation {solicitation_id} not found")

    # Re-fetch raw record to get experiment/type/program_id
    params = {"type": "solicitation"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        raw_record = None
        for r in resp.json():
            if r.get("id") == solicitation_id:
                raw_record = r
                break
        if not raw_record:
            raise ValueError(f"Solicitation {solicitation_id} not found")

        # Merge update_data into existing data
        merged_data = dict(raw_record.get("data", {}))
        merged_data.update(update_data)

        payload = [
            {
                "id": solicitation_id,
                "experiment": raw_record["experiment"],
                "type": raw_record["type"],
                "data": merged_data,
            }
        ]
        if raw_record.get("program_id"):
            payload[0]["program_id"] = raw_record["program_id"]

        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after update")
        return _serialize_record(result[0])


async def list_responses(solicitation_id: int) -> list[dict]:
    """List responses for a solicitation."""
    params = {
        "type": "solicitation_response",
        "labs_record_id": str(solicitation_id),
    }

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        return [_serialize_record(r) for r in resp.json()]


async def get_response(response_id: int) -> dict | None:
    """Get a single response by ID."""
    params = {"type": "solicitation_response"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        for r in resp.json():
            if r.get("id") == response_id:
                return _serialize_record(r)
    return None


async def _get_raw_response(response_id: int) -> dict | None:
    """Get the raw LabsRecord dict for a response (needed for updates)."""
    params = {"type": "solicitation_response"}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        for r in resp.json():
            if r.get("id") == response_id:
                return r
    return None


async def award_response(
    response_id: int,
    reward_budget: int,
    org_id: str,
    fund_id: int | None = None,
) -> dict:
    """Award a response: mark as awarded and optionally allocate from a fund.

    The fund_id is an explicit argument (not looked up from the solicitation)
    so callers have full control. This differs from the Django-side
    SolicitationsDataAccess.award_response which derives fund_id from the
    solicitation's fund_id field.

    Flow:
    1. GET response to get current data
    2. POST update with status=awarded, reward_budget, org_id
    3. If fund_id: GET solicitation for title, then add fund allocation
    4. Return updated response
    """
    from fund_tools import add_fund_allocation

    # 1. Fetch current response
    raw = await _get_raw_response(response_id)
    if not raw:
        raise ValueError(f"Response {response_id} not found")

    # 2. Update response status
    current_data = dict(raw.get("data", {}))
    current_data["status"] = "awarded"
    current_data["reward_budget"] = reward_budget
    current_data["org_id"] = org_id

    payload = [
        {
            "id": response_id,
            "experiment": raw["experiment"],
            "type": raw["type"],
            "data": current_data,
            "public": True,
        }
    ]
    if raw.get("labs_record_id"):
        payload[0]["labs_record_id"] = raw["labs_record_id"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after award update")
        updated_response = _serialize_record(result[0])

    # 3. Auto-allocate from fund if fund_id provided
    if fund_id:
        solicitation_id = current_data.get("solicitation_id")
        solicitation_title = ""
        org_name = current_data.get("llo_entity_name", "")

        # Try to get solicitation title for the allocation notes
        if solicitation_id:
            sol = await get_solicitation(int(solicitation_id))
            if sol:
                solicitation_title = sol.get("title", "")

        await add_fund_allocation(
            fund_id=fund_id,
            allocation={
                "amount": reward_budget,
                "type": "award",
                "solicitation_id": solicitation_id,
                "response_id": response_id,
                "org_id": org_id,
                "org_name": org_name,
                "notes": f"Award from {solicitation_title}" if solicitation_title else "Award",
            },
        )

    return updated_response
