"""Fund tools for the MCP server.

Uses httpx to call the Connect Labs Record API directly (no Django dependency).
Funds are stored as LabsRecord entries with type="fund" and experiment=org_id.
"""

from __future__ import annotations

import logging

import httpx
from connect_client import CONNECT_URL, HTTP_TIMEOUT, _get_connect_token

logger = logging.getLogger(__name__)

LABS_RECORD_URL = f"{CONNECT_URL.rstrip('/')}/export/labs_record/"
FUND_TYPE = "fund"


def _headers() -> dict[str, str]:
    token = _get_connect_token()
    return {"Authorization": f"Bearer {token}"}


def _serialize_record(record: dict) -> dict:
    """Extract a flat fund dict from a LabsRecord API response."""
    data = record.get("data", {})
    return {
        "id": record.get("id"),
        "experiment": record.get("experiment"),
        "type": record.get("type"),
        "organization_id": record.get("organization_id"),
        **data,
    }


async def list_funds(program_id: str) -> list[dict]:
    """List funds scoped by program_id (used for ACL)."""
    params: dict[str, str] = {
        "type": FUND_TYPE,
        "program_id": program_id,
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        return [_serialize_record(r) for r in resp.json()]


async def get_fund(fund_id: int) -> dict | None:
    """Get a single fund by ID."""
    params: dict[str, str] = {"type": FUND_TYPE}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        for r in resp.json():
            if r.get("id") == fund_id:
                return _serialize_record(r)
    return None


async def _get_raw_record(fund_id: int) -> dict | None:
    """Get the raw LabsRecord dict for a fund (needed for updates)."""
    params: dict[str, str] = {"type": FUND_TYPE}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        for r in resp.json():
            if r.get("id") == fund_id:
                return r
    return None


async def create_fund(
    program_id: str,
    name: str,
    total_budget: float | None = None,
    currency: str = "USD",
    description: str = "",
    program_ids: list | None = None,
    delivery_types: list | None = None,
    status: str = "active",
) -> dict:
    """Create a new fund. Scoped by program_id for ACL.

    The experiment field stores the funder slug (derived from name).
    """
    funder_slug = name.lower().replace(" ", "-")
    data: dict = {
        "name": name,
        "funder_slug": funder_slug,
        "status": status,
        "currency": currency,
        "allocations": [],
    }
    if total_budget is not None:
        data["total_budget"] = total_budget
    if description:
        data["description"] = description
    if program_ids:
        data["program_ids"] = program_ids
    if delivery_types:
        data["delivery_types"] = delivery_types

    payload = [
        {
            "experiment": funder_slug,
            "type": FUND_TYPE,
            "data": data,
            "program_id": int(program_id),
            "public": True,
        }
    ]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after create")
        return _serialize_record(result[0])


async def update_fund(fund_id: int, update_data: dict) -> dict:
    """Update an existing fund. Merges update_data into existing data."""
    raw = await _get_raw_record(fund_id)
    if not raw:
        raise ValueError(f"Fund {fund_id} not found")

    merged_data = dict(raw.get("data", {}))
    merged_data.update(update_data)

    payload = [
        {
            "id": fund_id,
            "experiment": raw["experiment"],
            "type": raw["type"],
            "data": merged_data,
        }
    ]
    if raw.get("organization_id"):
        payload[0]["organization_id"] = raw["organization_id"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after update")
        return _serialize_record(result[0])


async def add_fund_allocation(fund_id: int, allocation: dict) -> dict:
    """Append an allocation entry to a fund's allocations array."""
    raw = await _get_raw_record(fund_id)
    if not raw:
        raise ValueError(f"Fund {fund_id} not found")

    data = dict(raw.get("data", {}))
    allocations = list(data.get("allocations", []))
    allocations.append(allocation)
    data["allocations"] = allocations

    payload = [
        {
            "id": fund_id,
            "experiment": raw["experiment"],
            "type": raw["type"],
            "data": data,
        }
    ]
    if raw.get("organization_id"):
        payload[0]["organization_id"] = raw["organization_id"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after allocation add")
        return _serialize_record(result[0])


async def remove_fund_allocation(fund_id: int, index: int) -> dict:
    """Remove an allocation entry by index.

    Note: index-based removal is fragile under concurrent access.
    A future improvement could match by allocation fields instead.
    """
    raw = await _get_raw_record(fund_id)
    if not raw:
        raise ValueError(f"Fund {fund_id} not found")

    data = dict(raw.get("data", {}))
    allocations = list(data.get("allocations", []))
    if not (0 <= index < len(allocations)):
        raise ValueError(f"Allocation index {index} out of range (0-{len(allocations) - 1})")
    allocations.pop(index)
    data["allocations"] = allocations

    payload = [
        {
            "id": fund_id,
            "experiment": raw["experiment"],
            "type": raw["type"],
            "data": data,
        }
    ]
    if raw.get("organization_id"):
        payload[0]["organization_id"] = raw["organization_id"]

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after allocation remove")
        return _serialize_record(result[0])
