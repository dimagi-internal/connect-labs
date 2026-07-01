"""CommCare Case API facade for the campaign tool.

One entry point the campaign reads worker/KYC/region cases through, regardless of
whether the project space is synthetic or real — exactly how ``LabsRecordAPIClient``
hides the synthetic-vs-prod dispatch for Connect:

* synthetic domain  -> served in-app from ``WorkerCase`` (no HTTP)
* real domain       -> labs' ``CommCareDataAccess`` (Case API v2 over OAuth)

Making "go real" a per-domain fact, not a code change.
"""
from __future__ import annotations

from connect_labs.campaign.services import commcare_cases_backend


def fetch_cases(domain: str, case_type: str, *, request=None, limit: int = 1000) -> list[dict]:
    """Return cases of ``case_type`` for ``domain`` as Case-API-v2 dicts."""
    if commcare_cases_backend.is_synthetic_domain(domain):
        # In-app backend has no pages — it returns the full set (the real client below
        # paginates with ``limit`` per page to reach the same aggregated result).
        return commcare_cases_backend.fetch_cases(domain, case_type)

    # Real CommCare HQ project space — read over OAuth via labs' Case API client.
    from connect_labs.labs.integrations.commcare.api_client import CommCareDataAccess

    return CommCareDataAccess(request, domain).fetch_cases(case_type, limit=limit)
