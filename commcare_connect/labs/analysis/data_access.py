"""
Data access utilities for analysis framework.

Provides utility functions for fetching data from Connect API.
"""

import logging

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from commcare_connect.labs.analysis.utils import DJANGO_CACHE_TTL

logger = logging.getLogger(__name__)


def fetch_flw_names(
    access_token: str,
    opportunity_id: int,
    use_cache: bool = True,
    last_active_out: dict | None = None,
) -> dict[str, str]:
    """
    Fetch FLW display names for an opportunity from Connect API.

    This is the low-level function that can be used by both views (via request)
    and Celery tasks (via raw parameters).

    Args:
        access_token: OAuth Bearer token for Connect API
        opportunity_id: Opportunity ID to fetch FLW names for
        use_cache: Whether to use Django cache (default True)
        last_active_out: Optional dict to populate with {username: last_active_str}.
            When provided, last_active data is written directly into this dict,
            avoiding reliance on Django cache for in-process data sharing.

    Returns:
        Dictionary mapping username to display name.
        Falls back to username if display name is empty.
        Example: {"e5e685ae3f024fb6848d0d87138d526f": "John Doe"}

    Raises:
        RuntimeError: If API call fails or times out
    """
    # Try cache first
    if use_cache:
        cache_key = f"flw_names_{opportunity_id}"
        try:
            cached = cache.get(cache_key)
            if cached is not None:
                # If last_active was requested, only use cache if la data is also cached
                if last_active_out is not None:
                    la_cached = cache.get(f"flw_last_active_{opportunity_id}")
                    if la_cached is not None:
                        last_active_out.update(la_cached)
                    else:
                        cached = None  # Force fresh fetch to populate last_active
                if cached is not None:
                    logger.debug(f"FLW names loaded from cache for opp {opportunity_id}")
                    return cached
        except Exception as e:
            logger.warning(f"Cache get failed for {cache_key}: {e}")

    # Fetch from API (v2 paginated JSON)
    from commcare_connect.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError

    endpoint = f"/export/opportunity/{opportunity_id}/user_data/"
    logger.info(f"Fetching FLW names from {endpoint}")

    try:
        with ExportAPIClient(
            base_url=settings.CONNECT_PRODUCTION_URL,
            access_token=access_token,
            timeout=30.0,
        ) as client:
            records = client.fetch_all(endpoint)
    except ExportAPIError as e:
        logger.error(f"Failed to fetch FLW names for opportunity {opportunity_id}: {e}")
        raise RuntimeError(f"Connect export API error while fetching FLW names: {e}") from e

    logger.info(f"Fetched {len(records)} FLWs from Connect")

    # Build mapping: username -> name (fallback to username if name is empty)
    flw_names: dict[str, str] = {}
    flw_last_active: dict[str, str] = {}
    for row in records:
        username = row.get("username")
        if not username:
            continue
        name = row.get("name")
        flw_names[username] = name if name else username
        last_active = row.get("last_active")
        if last_active:
            flw_last_active[username] = str(last_active)

    # Populate caller's dict directly (avoids cache dependency)
    if last_active_out is not None:
        last_active_out.update(flw_last_active)

    # Cache the results
    if use_cache:
        try:
            cache.set(cache_key, flw_names, DJANGO_CACHE_TTL)
            cache.set(f"flw_last_active_{opportunity_id}", flw_last_active, DJANGO_CACHE_TTL)
            logger.debug(f"FLW names cached for opp {opportunity_id}")
        except Exception as e:
            logger.warning(f"Cache set failed for {cache_key}: {e}")

    return flw_names


def get_flw_names_for_opportunity(request: HttpRequest) -> dict[str, str]:
    """
    Get FLW display names for the opportunity in request context.

    Convenience wrapper around fetch_flw_names() that extracts credentials
    from the request session.

    Args:
        request: HttpRequest with labs_oauth and labs_context in session

    Returns:
        Dictionary mapping username to display name
        Example: {"e5e685ae3f024fb6848d0d87138d526f": "John Doe"}

    Raises:
        ValueError: If no OAuth token or opportunity context found
    """
    access_token = request.session.get("labs_oauth", {}).get("access_token")
    labs_context = getattr(request, "labs_context", {})
    opportunity_id = labs_context.get("opportunity_id")

    if not access_token:
        raise ValueError("No labs OAuth token found in session")

    if not opportunity_id:
        raise ValueError("No opportunity selected in labs context")

    return fetch_flw_names(access_token, opportunity_id)
