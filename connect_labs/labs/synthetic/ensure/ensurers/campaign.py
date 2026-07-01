"""The ``campaign`` ensurer: realize a Campaign Utility Tool national campaign.

Idempotently builds a national synthetic campaign for the Campaign Utility Tool —
worker cases on real Nigeria geography (labs ``AdminBoundary``), served via a
synthetic CommCare project space through the Case API. Reuses
``campaign.services.synthetic_campaign.build_synthetic_campaign`` (idempotent by
``code`` — it rebuilds that campaign in place), so the env framework gets the
campaign DDD demo into the right state the same canonical way the PAR env does for
its workflow runs: in-app, HTTP-free, durable, via ``synthetic_env_ensure``.
"""
from __future__ import annotations


def ensure_campaign(resource, ctx) -> dict:
    """Build (or rebuild) the campaign and return a readiness marker."""
    from connect_labs.campaign.services import geography, synthetic_campaign

    if not geography.is_loaded():
        raise RuntimeError(
            "Nigeria admin boundaries are not loaded in this environment — "
            "run `manage.py load_geopode_from_drive --iso NGA` first."
        )
    campaign = synthetic_campaign.build_synthetic_campaign(
        worker_count=resource.worker_count,
        states_limit=resource.states_limit,
        code=resource.code,
        name=resource.name,
    )
    return {
        f"campaign_{resource.code}": {
            "code": campaign.code,
            "commcare_domain": campaign.commcare_domain,
            "workers": campaign.worker_cases.count(),
            "states": campaign.regions.count(),
            "microplans": campaign.microplans.count(),
            "url": f"/campaign/?campaign={campaign.code}",
        }
    }
