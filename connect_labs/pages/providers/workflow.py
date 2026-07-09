"""Card provider backed by a workflow definition's declared CARD block.

A workflow template opts into a card by adding a "card" block to its DEFINITION
dict. Once instantiated, the block lives on the definition record's data, read
as record.data["card"]:

    "card": {
        "card_type": "summary",
        "title": "Weekly Performance Review",
        "metrics": [{"label": "Cadence", "value": "Weekly"}],  # optional, static (v1)
        "render_code": "…optional JSX…",                       # optional escape hatch
    }

v1 renders the declared block plus a CTA the provider builds to the workflow
runner. Wiring live run/pipeline metrics into the card is a documented
follow-up — a workflow *definition* carries no runtime state.
"""

from __future__ import annotations

from django.urls import reverse

from connect_labs.labs.context import get_org_data
from connect_labs.pages.providers import base, register
from connect_labs.workflow.data_access import WorkflowDataAccess


def _access_token(request) -> str:
    return (request.session or {}).get("labs_oauth", {}).get("access_token", "")


def _load_definition(request, definition_id: int, opportunity_id=None):
    # Scope the read by the card's opportunity so the prod API authorizes reading
    # an opp-scoped workflow_definition record (get_record_by_id only sends scope
    # params carried by the client instance). Without the opp, an opp-scoped
    # definition is unreadable and the card would be wrongly dropped.
    wda = WorkflowDataAccess(access_token=_access_token(request), opportunity_id=opportunity_id)
    return wda.get_definition(definition_id)


@register
class WorkflowCardProvider(base.CardProvider):
    key = "workflow"
    label = "Workflow card"
    target_kind = "workflow"

    def entitled(self, request, target: dict) -> bool:
        definition_id = target.get("definition_id")
        if definition_id is None:
            return False
        allowed = {str(o.get("id")) for o in get_org_data(request).get("opportunities", [])}

        # Preferred: the card carries its opportunity, so entitlement is a plain
        # opp-membership check (mirrors the audit provider) — no definition read.
        opportunity_id = target.get("opportunity_id")
        if opportunity_id is not None:
            return str(opportunity_id) in allowed

        # Legacy fallback (target has no opportunity): derive scope from the
        # definition itself. Requires the definition to be readable unscoped.
        record = _load_definition(request, int(definition_id))
        if record is None:
            return False
        opp_ids = record.opportunity_ids or ([record.opportunity_id] if record.opportunity_id else [])
        return any(str(opp_id) in allowed for opp_id in opp_ids)

    def get_card_data(self, request, target: dict, options: dict) -> base.CardPayload:
        definition_id = int(target["definition_id"])
        opportunity_id = target.get("opportunity_id")

        wda = WorkflowDataAccess(access_token=_access_token(request), opportunity_id=opportunity_id)
        record = wda.get_definition(definition_id)
        runs = wda.list_runs(definition_id) or []

        # Newest first (created_at, fallback id), capped for the card.
        runs = sorted(runs, key=lambda r: (getattr(r, "created_at", "") or "", r.id), reverse=True)
        run_rows = [
            {
                "id": r.id,
                "created": (getattr(r, "created_at", "") or "")[:10],
                "status": r.status,
                "open_url": (
                    f"{reverse('labs:workflow:run', kwargs={'definition_id': definition_id})}"
                    f"?run_id={r.id}&opportunity_id={opportunity_id}"
                ),
            }
            for r in runs[:8]
        ]

        title = options.get("title") or (record.name if record else f"Workflow {definition_id}")
        # Carry the opportunity scope so the client card can lazy-fetch each run's
        # audit sessions on expand (window.LabsAudit.fetchSessions) and render the
        # per-FLW breakdown inline — one card, every run expandable.
        opp_ids = (record.opportunity_ids if record else None) or (
            [opportunity_id] if opportunity_id is not None else []
        )
        opp_names = {}
        allowed = {str(o.get("id")): (o.get("name") or "") for o in get_org_data(request).get("opportunities", [])}
        for oid in opp_ids:
            if allowed.get(str(oid)):
                opp_names[str(oid)] = allowed[str(oid)]
        return base.CardPayload(
            title=title,
            card_type="workflow_runs",
            body=(record.description if record else None),
            data={
                "definition_id": definition_id,
                "runs": run_rows,
                "run_count": len(runs),
                "opportunity_id": opportunity_id,
                "opportunity_ids": [int(o) if str(o).isdigit() else o for o in opp_ids],
                "opp_names": opp_names,
            },
        )
