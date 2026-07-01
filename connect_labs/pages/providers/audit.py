"""Core-object card provider for audits, scoped to an opportunity."""

from __future__ import annotations

from django.urls import reverse

from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.labs.context import get_org_data
from connect_labs.pages.providers import base, register


def _access_token(request) -> str:
    return (request.session or {}).get("labs_oauth", {}).get("access_token", "")


@register
class AuditCardProvider(base.CardProvider):
    key = "audit"
    label = "Audit summary"
    target_kind = "opportunity"

    def entitled(self, request, target: dict) -> bool:
        opp_id = target.get("opportunity_id")
        if opp_id is None:
            return False
        org_data = get_org_data(request)
        allowed = {str(o.get("id")) for o in org_data.get("opportunities", [])}
        return str(opp_id) in allowed

    def get_card_data(self, request, target: dict, options: dict) -> base.CardPayload:
        opp_id = int(target["opportunity_id"])
        name = target.get("opportunity_name") or f"Opportunity {opp_id}"

        ada = AuditDataAccess(access_token=_access_token(request), opportunity_id=opp_id)
        visit_ids = ada.get_visit_ids_for_audit(opportunity_ids=[opp_id]) or []

        cta_url = f"{reverse('audit:session_list')}?opportunity_id={opp_id}"
        return base.CardPayload(
            title=options.get("title") or name,
            card_type="audit_summary",
            status="ready" if visit_ids else "empty",
            metrics=[{"label": "Visits available", "value": len(visit_ids)}],
            cta={"label": "Open audit", "url": cta_url},
            data={"opportunity_id": opp_id},
        )
