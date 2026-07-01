"""In-app synthetic CommCare Case-API backend.

The Case-API analogue of labs' ``local_records_backend``: when a domain is a
registered + enabled ``SyntheticCommCareDomain``, the campaign tool's case reads
are served HERE from ``WorkerCase`` — shaped as CommCare Case API v2 JSON — instead
of going over HTTP to CommCare HQ. The campaign code calls the Case API the same
way for synthetic and real domains; this is the transparent short-circuit.
"""
from __future__ import annotations

from connect_labs.campaign.models import Campaign, SyntheticCommCareDomain, WorkerCase

WORKER_CASE_TYPE = "campaign_worker"


def is_synthetic_domain(domain: str) -> bool:
    """True if ``domain`` is a registered, enabled synthetic project space."""
    if not domain:
        return False
    return SyntheticCommCareDomain.objects.filter(domain=domain, enabled=True).exists()


def _case_json(wc: WorkerCase) -> dict:
    """A WorkerCase as a CommCare Case API v2 case object."""
    props = dict(wc.properties)
    return {
        "case_id": wc.case_id,
        "case_type": wc.case_type,
        "case_name": props.get("name", ""),
        "external_id": props.get("worker_id", ""),
        "owner_id": wc.region_id,
        "closed": False,
        "indices": {},
        "properties": props,
    }


def fetch_cases(domain: str, case_type: str) -> list[dict]:
    """Serve ALL the synthetic domain's cases of ``case_type`` as Case-API-v2 dicts.

    The real Case API's ``limit`` is a per-page size that its client paginates over to
    return the full set; the in-app backend has no pages, so it returns everything in
    one call (matching the client's aggregated result)."""
    if case_type != WORKER_CASE_TYPE:
        return []
    campaign = Campaign.objects.filter(commcare_domain=domain).first()
    if campaign is None:
        return []
    return [_case_json(wc) for wc in campaign.worker_cases.all().iterator(chunk_size=2000)]
