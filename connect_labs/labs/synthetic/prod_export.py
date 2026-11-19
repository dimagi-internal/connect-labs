"""Read one production Connect export endpoint for an opportunity.

Used by the clone path (`clone_from_prod`) and the synthetic MCP tools, which reduce
what they read to profiles and generated data in labs. Nothing here writes real
data anywhere: the old "dump to Drive" path that copied raw exports out was removed
(#3122).
"""

from __future__ import annotations

import httpx

_V2_HEADERS = {"Accept": "application/json; version=2.0"}


def _fetch_endpoint(base_url: str, opp_id: int, key: str, access_token: str):
    """Fetch one export endpoint and return the payload.

    For list endpoints (`{results, next}` shape) walks pagination until
    exhausted and returns a flat list. For the bare opp-detail endpoint
    (no `results` key) returns the dict as-is.
    """
    tail = f"{key}/" if key else ""
    url = f"{base_url.rstrip('/')}/export/opportunity/{opp_id}/{tail}"
    headers = {"Authorization": f"Bearer {access_token}", **_V2_HEADERS}

    rows: list[dict] = []
    while url:
        resp = httpx.get(url, headers=headers, timeout=180, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        if "results" not in payload:
            return payload  # bare-dict detail endpoint
        rows.extend(payload["results"])
        url = payload.get("next")
    return rows
