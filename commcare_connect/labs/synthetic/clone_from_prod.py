"""Two-phase clone: Phase 1 profiles a real opp into a self-contained bundle
(prod-touching); Phase 2 generates fixtures from the bundle (offline, no prod)."""

from __future__ import annotations

import logging
from pathlib import Path

from .bundle import write_bundle
from .dump import _fetch_endpoint
from .generator.fixtures.profiler import profile as _profile

logger = logging.getLogger(__name__)


def profile_opp_to_bundle(source_opp_id: int, *, base_url: str, oauth_token: str, out_dir) -> Path:
    """Fetch real prod exports for *source_opp_id* and write a self-contained profile bundle.

    All prod network calls go through the module-level ``_fetch_endpoint`` name so that
    Phase-2 tests can patch it to assert zero prod calls::

        with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=...):
            ...

    Returns:
        Path to the written bundle directory.

    Raises:
        ValueError: if the opportunity has no user_visits (cannot profile).
    """
    detail = _fetch_endpoint(base_url, source_opp_id, "", oauth_token)
    user_visits = _fetch_endpoint(base_url, source_opp_id, "user_visits", oauth_token)
    user_data = _fetch_endpoint(base_url, source_opp_id, "user_data", oauth_token)
    app_structure = _fetch_endpoint(base_url, source_opp_id, "app_structure", oauth_token) or {}

    if not isinstance(user_visits, list) or not user_visits:
        raise ValueError(f"No user_visits for opportunity_id={source_opp_id}")

    manifest_yaml = _profile(
        opportunity_id=source_opp_id,
        user_visits=user_visits,
        user_data=user_data if isinstance(user_data, list) else [],
        opportunity_detail=detail if isinstance(detail, dict) else {},
        app_structure=app_structure if isinstance(app_structure, dict) else {},
    )
    return write_bundle(
        out_dir,
        source_opp_id,
        manifest_yaml=manifest_yaml,
        app_structure=app_structure if isinstance(app_structure, dict) else {},
        opportunity=detail if isinstance(detail, dict) else {},
    )


def profile_opps_bulk(source_ids, *, base_url: str, oauth_token: str, out_dir) -> list[Path]:
    """Profile multiple opportunities, isolating per-opp failures.

    A single bad opp (network error, empty visits, etc.) is logged and skipped;
    the remaining opps are still processed.

    Returns:
        List of successfully-written bundle Paths (one per succeeded opp).
    """
    bundles: list[Path] = []
    for sid in source_ids:
        try:
            bundles.append(profile_opp_to_bundle(sid, base_url=base_url, oauth_token=oauth_token, out_dir=out_dir))
        except Exception:  # noqa: BLE001
            logger.exception("profile_opps_bulk: failed for opp %s", sid)
    return bundles
