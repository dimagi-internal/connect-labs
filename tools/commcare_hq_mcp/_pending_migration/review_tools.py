"""Review tools for the MCP server.

Uses httpx to call the Connect Labs Record API directly (no Django dependency).
Reviews are stored as LabsRecord entries with type="solicitation_review".
The labs_record FK (labs_record_id) points to the response record, not the solicitation.
"""

from __future__ import annotations

import logging

import httpx
from connect_client import CONNECT_URL, HTTP_TIMEOUT, _get_connect_token

logger = logging.getLogger(__name__)

LABS_RECORD_URL = f"{CONNECT_URL.rstrip('/')}/export/labs_record/"
REVIEW_TYPE = "solicitation_review"


def _headers() -> dict[str, str]:
    token = _get_connect_token()
    return {"Authorization": f"Bearer {token}"}


def _serialize_record(record: dict) -> dict:
    """Extract a flat review dict from a LabsRecord API response."""
    data = record.get("data", {})
    return {
        "id": record.get("id"),
        "experiment": record.get("experiment"),
        "type": record.get("type"),
        "labs_record_id": record.get("labs_record_id"),
        **data,
    }


async def list_reviews(response_id: int) -> list[dict]:
    """List all reviews for a response.

    Reviews are linked to responses via labs_record_id.
    """
    params: dict[str, str] = {
        "type": REVIEW_TYPE,
        "labs_record_id": str(response_id),
    }
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        return [_serialize_record(r) for r in resp.json()]


async def get_review(review_id: int) -> dict | None:
    """Get a single review by ID."""
    params: dict[str, str] = {"type": REVIEW_TYPE}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        for r in resp.json():
            if r.get("id") == review_id:
                return _serialize_record(r)
    return None


async def create_review(
    response_id: int,
    llo_entity_id: str,
    score: int | None = None,
    recommendation: str = "under_review",
    notes: str = "",
    criteria_scores: dict | None = None,
    reviewer_username: str = "",
    tags: str = "",
) -> dict:
    """Create a review for a response.

    Args:
        response_id: ID of the response being reviewed
        llo_entity_id: LLO entity ID (used as experiment for API scoping)
        score: Overall score 1-100
        recommendation: "under_review", "approved", "rejected", "needs_revision"
        notes: Reviewer notes
        criteria_scores: Dict of criterion_id -> score (1-10)
        reviewer_username: Username of the reviewer
        tags: Comma-separated tags
    """
    from datetime import datetime, timezone

    data: dict = {
        "response_id": response_id,
        "llo_entity_id": llo_entity_id,
        "recommendation": recommendation,
        "review_date": datetime.now(timezone.utc).isoformat(),
    }
    if score is not None:
        data["score"] = score
    if notes:
        data["notes"] = notes
    if criteria_scores:
        data["criteria_scores"] = criteria_scores
    if reviewer_username:
        data["reviewer_username"] = reviewer_username
    if tags:
        data["tags"] = tags

    payload = [
        {
            "experiment": llo_entity_id,
            "type": REVIEW_TYPE,
            "data": data,
            "labs_record_id": response_id,
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


async def update_review(review_id: int, update_data: dict) -> dict:
    """Update an existing review. Merges update_data into existing data."""
    # Fetch current raw record
    params: dict[str, str] = {"type": REVIEW_TYPE}
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        resp = await client.get(LABS_RECORD_URL, params=params, headers=_headers())
        resp.raise_for_status()
        raw = None
        for r in resp.json():
            if r.get("id") == review_id:
                raw = r
                break
        if not raw:
            raise ValueError(f"Review {review_id} not found")

        merged_data = dict(raw.get("data", {}))
        merged_data.update(update_data)

        payload = [
            {
                "id": review_id,
                "experiment": raw["experiment"],
                "type": raw["type"],
                "data": merged_data,
                "labs_record_id": raw.get("labs_record_id"),
                "public": True,
            }
        ]

        resp = await client.post(LABS_RECORD_URL, json=payload, headers=_headers())
        resp.raise_for_status()
        result = resp.json()
        if not result:
            raise ValueError("API returned empty response after update")
        return _serialize_record(result[0])
