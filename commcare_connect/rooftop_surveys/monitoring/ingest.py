"""Pull FLW visit data from Connect's export API for monitoring.

The labs export API exposes `/export/opportunity/<id>/user_visits/`, whose rows
carry the top-level visit fields (username, visit_date, status, location, ...)
plus `form_json` — the submitted rooftop survey. We flatten form_json so its
answers (distance, believed-reached reason, inhabited flag, fallback, ...) sit
alongside the top-level fields, then `normalize_visits` maps them to the
canonical monitoring schema via an opp-specific field_map.

The form's exact field paths depend on the deployed rooftop survey, so the
field_map is configurable (defaults in normalize.DEFAULT_FIELD_MAP). The HTTP
fetch needs a live opp with submissions to validate end-to-end; `flatten_visits`
is pure and unit-tested.
"""

from __future__ import annotations

import logging

import httpx
import pandas as pd
from django.conf import settings

from commcare_connect.rooftop_surveys.monitoring.normalize import normalize_visits

logger = logging.getLogger(__name__)


def flatten_visits(rows: list[dict], form_prefix: str = "form.") -> pd.DataFrame:
    """Top-level visit fields + flattened `form_json` (prefixed) as one DataFrame."""
    if not rows:
        return pd.DataFrame()
    top = pd.DataFrame(rows)
    forms = [r.get("form_json") or {} for r in rows]
    form_df = pd.json_normalize(forms).add_prefix(form_prefix)
    top = top.drop(columns=["form_json"], errors="ignore").reset_index(drop=True)
    form_df = form_df.reset_index(drop=True)
    return pd.concat([top, form_df], axis=1)


def fetch_user_visits(
    opp_id: int, access_token: str, base_url: str | None = None, timeout: float = 60.0
) -> list[dict]:
    """Fetch all user-visit rows for an opp from the export API (follows pagination)."""
    base_url = base_url or settings.CONNECT_PRODUCTION_URL
    url = f"{base_url}/export/opportunity/{opp_id}/user_visits/"
    headers = {"Authorization": f"Bearer {access_token}"}
    rows: list[dict] = []
    with httpx.Client(timeout=timeout) as client:
        while url:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                rows.extend(data)
                break
            rows.extend(data.get("results", []))
            url = data.get("next")
    logger.info("rooftop ingest: fetched %d user visits for opp %s", len(rows), opp_id)
    return rows


def load_canonical(
    opp_id: int, access_token: str, field_map: dict | None = None, base_url: str | None = None
) -> pd.DataFrame:
    """Fetch → flatten → normalize to the canonical monitoring schema."""
    rows = fetch_user_visits(opp_id, access_token, base_url=base_url)
    return normalize_visits(flatten_visits(rows), field_map)
