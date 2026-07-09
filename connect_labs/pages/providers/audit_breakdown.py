"""Card provider for the per-FLW audit breakdown of a workflow run.

Renders the same "Audit results by field worker" panel the opp-level workflow
run page shows — the client card fetches the run's sessions and hands them to the
shared ``window.LabsAudit.renderFlwBreakdown`` primitive (see
connect_labs/static/js/labs_audit_breakdown.js). The provider itself only
resolves the target + entitlement and passes the run/opp identifiers through.
"""

from __future__ import annotations

from connect_labs.labs.context import get_org_data
from connect_labs.pages.providers import base, register


@register
class AuditBreakdownCardProvider(base.CardProvider):
    key = "audit_breakdown"
    label = "Audit results by field worker"
    target_kind = "workflow_run"

    def _allowed_opps(self, request) -> dict[str, str]:
        """{str(opp_id): name} for opps the viewer belongs to."""
        return {
            str(o.get("id")): (o.get("name") or f"Opportunity {o.get('id')}")
            for o in get_org_data(request).get("opportunities", [])
        }

    def _target_opp_ids(self, target: dict) -> list[str]:
        ids = target.get("opportunity_ids")
        if ids:
            return [str(i) for i in ids]
        one = target.get("opportunity_id")
        return [str(one)] if one is not None else []

    def entitled(self, request, target: dict) -> bool:
        """Viewer must belong to every opp the card exposes (opp-membership,
        mirroring the audit provider). No opp → not entitled."""
        opp_ids = self._target_opp_ids(target)
        if not opp_ids:
            return False
        allowed = set(self._allowed_opps(request))
        return all(oid in allowed for oid in opp_ids)

    def get_card_data(self, request, target: dict, options: dict) -> base.CardPayload:
        run_id = target.get("workflow_run_id")
        opp_ids = self._target_opp_ids(target)
        allowed = self._allowed_opps(request)
        opp_names = {oid: allowed.get(oid, f"Opportunity {oid}") for oid in opp_ids}

        return base.CardPayload(
            title=options.get("title") or "Audit results by field worker",
            card_type="flw_audit_breakdown",
            data={
                "workflow_run_id": run_id,
                "opportunity_ids": [int(o) if str(o).isdigit() else o for o in opp_ids],
                "opp_names": opp_names,
            },
        )
