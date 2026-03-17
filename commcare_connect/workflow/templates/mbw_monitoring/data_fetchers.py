"""
Data fetching utilities for MBW Monitoring Dashboard.

Handles fetching opportunity metadata, visit cases, and mother cases
from Connect API and CommCare HQ.
"""

import logging
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest

from commcare_connect.labs.analysis.data_access import get_flw_names_for_opportunity
from commcare_connect.labs.integrations.commcare.api_client import CommCareDataAccess

logger = logging.getLogger(__name__)

METADATA_CACHE_TTL = 3600  # 1 hour
CASES_CACHE_TTL = 3600  # 1 hour (production HQ case cache TTL)

# Long TTL for dev fixture cache (24 hours)
DEV_FIXTURE_CACHE_TTL = 86400


def _is_dev_fixture_enabled() -> bool:
    """Check if MBW_DEV_FIXTURE mode is active."""
    return getattr(settings, "MBW_DEV_FIXTURE", False)


def _get_cache_config() -> dict:
    """Return cache settings based on MBW_DEV_FIXTURE mode."""
    if _is_dev_fixture_enabled():
        return {
            "cases_ttl": DEV_FIXTURE_CACHE_TTL,  # 86400 (24 hr) — long-lived, tolerance decides freshness
            "cache_tolerance_pct": 85,
            "cache_tolerance_minutes": 90,
        }
    return {
        "cases_ttl": CASES_CACHE_TTL,  # 3600 (1 hr)
        "cache_tolerance_pct": 98,
        "cache_tolerance_minutes": 30,
    }


def _validate_hq_cache(cached_data: dict, requested_count: int, config: dict) -> bool:
    """
    Validate cached HQ case data using tolerance rules.

    Args:
        cached_data: Dict with 'cases', 'cached_count', 'cached_at'
        requested_count: Number of case IDs being requested now
        config: Cache config from _get_cache_config()

    Returns:
        True if cached data is acceptable
    """
    cached_count = cached_data.get("cached_count", 0)
    tolerance_pct = config["cache_tolerance_pct"]
    tolerance_minutes = config["cache_tolerance_minutes"]

    # Cache has at least as many cases as requested — valid
    if cached_count >= requested_count:
        logger.debug(f"[HQ Cache] Valid: cached={cached_count} >= requested={requested_count}")
        return True

    # Check percentage tolerance
    if requested_count > 0:
        actual_pct = (cached_count / requested_count) * 100
        if actual_pct >= tolerance_pct:
            logger.info(
                f"[HQ Cache] ACCEPTED (pct): cached={cached_count}, requested={requested_count}, "
                f"actual={actual_pct:.1f}% >= tolerance={tolerance_pct}%"
            )
            return True

    # Check time-based tolerance
    cached_at_str = cached_data.get("cached_at")
    if cached_at_str:
        try:
            cached_at = datetime.fromisoformat(cached_at_str)
            age_minutes = (datetime.now(timezone.utc) - cached_at).total_seconds() / 60
            if age_minutes <= tolerance_minutes:
                logger.info(
                    f"[HQ Cache] ACCEPTED (time): cached={cached_count}, requested={requested_count}, "
                    f"age={age_minutes:.1f}min <= tolerance={tolerance_minutes}min"
                )
                return True
        except Exception as e:
            logger.warning(f"[HQ Cache] Failed to parse cached_at: {e}")

    logger.info(
        f"[HQ Cache] MISS: cached={cached_count}, requested={requested_count}, "
        f"tolerance_pct={tolerance_pct}%, tolerance_min={tolerance_minutes}"
    )
    return False


def fetch_opportunity_metadata(access_token: str, opportunity_id: int) -> dict:
    """
    Fetch opportunity metadata from Connect API to extract cc_domain.

    Args:
        access_token: Connect OAuth token
        opportunity_id: Opportunity ID

    Returns:
        Dict with opportunity metadata including cc_domain

    Raises:
        ValueError: If metadata cannot be fetched or cc_domain not found
    """
    cache_key = f"mbw_opp_metadata:{opportunity_id}"
    cached = cache.get(cache_key)
    if cached:
        logger.debug(f"Opportunity metadata cache hit for {opportunity_id}")
        return cached

    url = f"{settings.CONNECT_PRODUCTION_URL}/export/opportunity/{opportunity_id}/"
    headers = {"Authorization": f"Bearer {access_token}"}

    logger.info(f"Fetching opportunity metadata from {url}")

    try:
        response = httpx.get(url, headers=headers, timeout=30.0)
        response.raise_for_status()
    except httpx.HTTPStatusError as e:
        logger.error(f"Failed to fetch opportunity metadata: {e}")
        raise ValueError(f"Failed to fetch opportunity metadata: {e.response.status_code}") from e
    except httpx.TimeoutException as e:
        logger.error(f"Timeout fetching opportunity metadata: {e}")
        raise ValueError("Timeout fetching opportunity metadata") from e

    data = response.json()

    # Extract cc_domain from deliver_app or learn_app
    cc_domain = None
    deliver_app = data.get("deliver_app") or {}
    learn_app = data.get("learn_app") or {}

    cc_domain = deliver_app.get("cc_domain") or learn_app.get("cc_domain")

    if not cc_domain:
        logger.error(
            f"No cc_domain in opportunity {opportunity_id} metadata. "
            f"deliver_app keys: {list(deliver_app.keys())}, learn_app keys: {list(learn_app.keys())}"
        )
        raise ValueError(f"Opportunity {opportunity_id} is missing CommCare domain configuration.")

    cc_app_id = deliver_app.get("cc_app_id") or learn_app.get("cc_app_id")

    result = {
        "cc_domain": cc_domain,
        "cc_app_id": cc_app_id,
        "opportunity_name": data.get("name", ""),
        "opportunity_id": opportunity_id,
        "raw": data,
    }

    cache.set(cache_key, result, METADATA_CACHE_TTL)
    logger.info(f"Fetched opportunity metadata: cc_domain={cc_domain}")

    return result


def fetch_visit_cases_by_ids(
    request: HttpRequest,
    cc_domain: str,
    case_ids: list[str],
    bust_cache: bool = False,
    opportunity_id: int | str = "",
) -> list[dict]:
    """
    Fetch visit cases from CommCare HQ by case IDs.

    Caches results with tolerance-based validation. In dev mode (MBW_DEV_FIXTURE=1)
    uses a 24hr Redis TTL with relaxed tolerance (85%/90min). In prod uses a 1hr
    Redis TTL with strict tolerance (98%/30min). Use bust_cache=True to force refresh.

    Args:
        request: HttpRequest with commcare_oauth in session
        cc_domain: CommCare domain
        case_ids: List of case IDs to fetch
        bust_cache: If True, ignore cached data and re-fetch

    Returns:
        List of case dicts from CommCare HQ
    """
    if not case_ids:
        return []

    # Deduplicate
    unique_ids = list(set(case_ids))
    config = _get_cache_config()
    cache_key = f"mbw_visit_cases:{opportunity_id}:{cc_domain}"

    # Check cache with tolerance validation
    if not bust_cache:
        cached = cache.get(cache_key)
        if cached is not None and _validate_hq_cache(cached, len(unique_ids), config):
            logger.info(
                f"[HQ Cache] Visit cases HIT: {cached['cached_count']} cases "
                f"(requested={len(unique_ids)}, key={cache_key})"
            )
            return cached["cases"]

    logger.info(f"Fetching {len(unique_ids)} visit cases from CommCare HQ ({cc_domain})")

    client = CommCareDataAccess(request, cc_domain)

    if not client.check_token_valid():
        raise ValueError(
            "CommCare OAuth not configured or expired. " "Please authorize CommCare access at /labs/commcare/initiate/"
        )

    all_cases = client.fetch_cases_by_ids(unique_ids)
    logger.info(f"Fetched {len(all_cases)} visit cases from CommCare HQ")

    # Store in cache with metadata
    cache_data = {
        "cases": all_cases,
        "cached_count": len(all_cases),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    cache.set(cache_key, cache_data, config["cases_ttl"])
    logger.info(f"[HQ Cache] Cached {len(all_cases)} visit cases " f"(ttl={config['cases_ttl']}s, key={cache_key})")

    return all_cases


def fetch_mother_cases_by_ids(
    request: HttpRequest,
    cc_domain: str,
    mother_case_ids: list[str],
    bust_cache: bool = False,
    opportunity_id: int | str = "",
) -> list[dict]:
    """
    Fetch mother cases from CommCare HQ by case IDs.

    Caches results with tolerance-based validation (same rules as visit cases).
    Use bust_cache=True to force a refresh.

    Args:
        request: HttpRequest with commcare_oauth in session
        cc_domain: CommCare domain
        mother_case_ids: List of mother case IDs to fetch
        bust_cache: If True, ignore cached data and re-fetch

    Returns:
        List of mother case dicts from CommCare HQ
    """
    if not mother_case_ids:
        return []

    unique_ids = list(set(mother_case_ids))
    config = _get_cache_config()
    cache_key = f"mbw_mother_cases:{opportunity_id}:{cc_domain}"

    # Check cache with tolerance validation
    if not bust_cache:
        cached = cache.get(cache_key)
        if cached is not None and _validate_hq_cache(cached, len(unique_ids), config):
            logger.info(
                f"[HQ Cache] Mother cases HIT: {cached['cached_count']} cases "
                f"(requested={len(unique_ids)}, key={cache_key})"
            )
            return cached["cases"]

    logger.info(f"Fetching {len(unique_ids)} mother cases from CommCare HQ ({cc_domain})")

    client = CommCareDataAccess(request, cc_domain)

    if not client.check_token_valid():
        raise ValueError(
            "CommCare OAuth not configured or expired. " "Please authorize CommCare access at /labs/commcare/initiate/"
        )

    all_cases = client.fetch_cases_by_ids(unique_ids)
    logger.info(f"Fetched {len(all_cases)} mother cases from CommCare HQ")

    # Store in cache with metadata
    cache_data = {
        "cases": all_cases,
        "cached_count": len(all_cases),
        "cached_at": datetime.now(timezone.utc).isoformat(),
    }
    cache.set(cache_key, cache_data, config["cases_ttl"])
    logger.info(f"[HQ Cache] Cached {len(all_cases)} mother cases " f"(ttl={config['cases_ttl']}s, key={cache_key})")

    return all_cases


def extract_case_ids_from_visits(visit_rows: list) -> list[str]:
    """
    Extract unique case IDs from pipeline visit rows.

    Args:
        visit_rows: List of VisitRow objects from pipeline

    Returns:
        List of unique case IDs
    """
    case_ids = set()
    for row in visit_rows:
        case_id = row.computed.get("case_id")
        if case_id:
            case_ids.add(case_id)
    return list(case_ids)


def extract_mother_case_ids_from_cases(visit_cases: list[dict]) -> list[str]:
    """
    Extract unique mother case IDs from visit cases.

    Args:
        visit_cases: List of case dicts from CommCare HQ

    Returns:
        List of unique mother case IDs
    """
    mother_ids = set()
    for case in visit_cases:
        props = case.get("properties", {})
        mother_id = props.get("mother_case_id")
        if mother_id:
            mother_ids.add(mother_id)
    return list(mother_ids)


def get_active_connect_usernames(request: HttpRequest) -> tuple[set[str], dict[str, str]]:
    """
    Get active Connect usernames and display name mapping.

    Args:
        request: HttpRequest with labs_oauth and labs_context

    Returns:
        Tuple of (set of active usernames, dict mapping username to display name)
    """
    flw_names = get_flw_names_for_opportunity(request)
    return set(flw_names.keys()), flw_names


def group_visit_cases_by_flw(
    visit_cases: list[dict],
    visit_rows: list,
    active_usernames: set[str],
) -> dict[str, list[dict]]:
    """
    Group visit cases by FLW username, filtering to active Connect users.

    Links visit cases back to FLW via the pipeline visit rows (which have username).
    """
    # Build case_id → username mapping from pipeline visit rows
    case_to_username = {}
    for row in visit_rows:
        case_id = row.computed.get("case_id")
        if case_id and row.username:
            case_to_username[case_id] = row.username

    pipeline_ids = list(case_to_username.keys())[:3]
    hq_ids = [c.get("case_id") for c in visit_cases[:3]]
    logger.info(
        "[MBW Follow-Up] case_to_username size=%d, visit_cases size=%d, "
        "sample pipeline case_ids=%s, sample HQ case_ids=%s",
        len(case_to_username),
        len(visit_cases),
        pipeline_ids,
        hq_ids,
    )

    # Group cases by username
    matched = 0
    active_matched = 0
    by_flw = defaultdict(list)
    for case in visit_cases:
        case_id = case.get("case_id")
        username = case_to_username.get(case_id)
        if username:
            matched += 1
            if username in active_usernames:
                active_matched += 1
                by_flw[username].append(case)

    logger.info(
        "[MBW Follow-Up] matched=%d, active_matched=%d, unique_flws=%d, " "active_usernames size=%d",
        matched,
        active_matched,
        len(by_flw),
        len(active_usernames),
    )

    return dict(by_flw)


def count_mother_cases_by_flw(
    mother_cases: list[dict],
    active_usernames: set[str],
) -> dict[str, int]:
    """
    Count mother cases per FLW using user_connect_id property.

    Args:
        mother_cases: List of mother case dicts from CommCare HQ
        active_usernames: Set of active Connect usernames

    Returns:
        Dict mapping username to mother case count
    """
    counts = defaultdict(int)
    for case in mother_cases:
        props = case.get("properties", {})
        user_connect_id = props.get("user_connect_id")
        if user_connect_id and user_connect_id in active_usernames:
            counts[user_connect_id] += 1
    return dict(counts)


def bust_mbw_hq_cache() -> int:
    """
    Clear all MBW HQ case caches.

    Returns:
        Number of cache keys cleared
    """
    cleared = 0
    try:
        if hasattr(cache, "delete_pattern"):
            cleared += cache.delete_pattern("mbw_visit_cases:*")
            cleared += cache.delete_pattern("mbw_mother_cases:*")
            cleared += cache.delete_pattern("mbw_opp_metadata:*")
            cleared += cache.delete_pattern("mbw_registration_forms:*")
            cleared += cache.delete_pattern("mbw_gs_forms:*")
            logger.info(f"[HQ Cache] Busted {cleared} cache keys via pattern")
        else:
            logger.warning("[HQ Cache] Cache backend does not support delete_pattern; skipping bust")
    except Exception as e:
        logger.warning(f"[HQ Cache] Cache bust failed: {e}")
    return cleared


def fetch_gs_forms(
    request: HttpRequest,
    cc_domain: str,
    cc_app_id: str | None = None,
    gs_app_id: str | None = None,
    bust_cache: bool = False,
    opportunity_id: int | str = "",
) -> list[dict]:
    """Fetch Gold Standard Visit Checklist forms from CCHQ Form API v1.

    The GS form is in a separate supervisor app (not the deliver app).
    Uses the following strategy:
      1. If gs_app_id is provided (from workflow settings), look up xmlns directly
      2. Otherwise try the deliver app's cc_app_id
      3. Fall back to searching all apps via discover_form_xmlns()

    Note: Does NOT fall back to downloading all forms — if xmlns cannot be
    discovered, returns empty list. Configure gs_app_id in workflow settings.

    Args:
        request: HttpRequest with commcare_oauth in session
        cc_domain: CommCare HQ domain
        cc_app_id: Optional deliver app ID to try
        gs_app_id: Optional explicit GS supervisor app ID (preferred)
        bust_cache: If True, ignore cached data and re-fetch

    Returns:
        List of form dicts from CommCare Form API
    """
    cache_key = f"mbw_gs_forms:{opportunity_id}:{cc_domain}"
    if not bust_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"[MBW Dashboard] GS forms cache hit: {len(cached)} forms")
            return cached

    client = CommCareDataAccess(request, cc_domain)
    if not client.check_token_valid():
        raise ValueError("CommCare OAuth not configured or expired.")

    # Strategy 1: Use explicit GS app ID from workflow settings (preferred)
    xmlns = None
    if gs_app_id:
        xmlns = client.get_form_xmlns(gs_app_id, "Gold Standard Visit Checklist")
        if xmlns:
            logger.info(f"[MBW Dashboard] Found GS xmlns via gs_app_id ({gs_app_id}): {xmlns}")

    # Strategy 2: Try deliver app (quick check)
    if not xmlns and cc_app_id:
        xmlns = client.get_form_xmlns(cc_app_id, "Gold Standard Visit Checklist")

    # Strategy 3: Search all apps in the domain
    if not xmlns:
        xmlns = client.discover_form_xmlns("Gold Standard Visit Checklist")

    if xmlns:
        logger.info(f"[MBW Dashboard] Discovered GS form xmlns: {xmlns}")
        forms = client.fetch_forms(xmlns=xmlns)
    else:
        logger.warning(
            "[MBW Dashboard] Could not discover GS xmlns in any app. "
            "Configure the GS App ID in workflow settings. Returning empty list."
        )
        forms = []

    logger.info(f"[MBW Dashboard] Fetched {len(forms)} GS forms from CCHQ ({cc_domain})")

    cache.set(cache_key, forms, CASES_CACHE_TTL)
    return forms


def fetch_registration_forms(
    request: HttpRequest,
    cc_domain: str,
    cc_app_id: str | None = None,
    bust_cache: bool = False,
    opportunity_id: int | str = "",
) -> list[dict]:
    """Fetch 'Register Mother' forms from CCHQ Form API v1, cached for 1 hour.

    Dynamically discovers the xmlns for the "Register Mother" form via the
    Application Structure API, so the correct xmlns is used regardless of
    which app (production vs testing) is configured for the opportunity.

    Falls back to fetching all forms for the app and filtering client-side
    by form name if xmlns discovery fails.

    Args:
        request: HttpRequest with commcare_oauth in session
        cc_domain: CommCare HQ domain
        cc_app_id: Optional app ID to filter forms
        bust_cache: If True, ignore cached data and re-fetch

    Returns:
        List of form dicts from CommCare Form API
    """
    cache_key = f"mbw_registration_forms:{opportunity_id}:{cc_domain}"
    if not bust_cache:
        cached = cache.get(cache_key)
        if cached is not None:
            logger.info(f"[MBW Dashboard] Registration forms cache hit: {len(cached)} forms")
            return cached

    client = CommCareDataAccess(request, cc_domain)
    if not client.check_token_valid():
        raise ValueError("CommCare OAuth not configured or expired.")

    # Dynamically discover the xmlns for "Register Mother"
    xmlns = None
    if cc_app_id:
        xmlns = client.get_form_xmlns(cc_app_id, "Register Mother")
        if xmlns:
            logger.info(f"[MBW Dashboard] Discovered Register Mother xmlns: {xmlns}")
        else:
            logger.warning(
                f"[MBW Dashboard] Could not discover xmlns for 'Register Mother' "
                f"in app {cc_app_id}, falling back to client-side filtering"
            )

    if xmlns:
        # Happy path: fetch forms filtered by discovered xmlns
        forms = client.fetch_forms(xmlns=xmlns, app_id=cc_app_id)
    else:
        # Fallback: fetch all forms for the app and filter client-side
        forms = client.fetch_forms(app_id=cc_app_id)
        pre_filter_count = len(forms)
        forms = [f for f in forms if f.get("@name") == "Register Mother"]
        logger.info(
            f"[MBW Dashboard] Client-side filter: {len(forms)}/{pre_filter_count} " f"forms matched 'Register Mother'"
        )

    logger.info(f"[MBW Dashboard] Fetched {len(forms)} registration forms from CCHQ ({cc_domain})")

    cache.set(cache_key, forms, CASES_CACHE_TTL)
    return forms
