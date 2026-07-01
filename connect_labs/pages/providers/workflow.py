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


def _load_definition(request, definition_id: int):
    wda = WorkflowDataAccess(access_token=_access_token(request))
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
        record = _load_definition(request, int(definition_id))
        if record is None:
            return False
        opp_ids = record.opportunity_ids or ([record.opportunity_id] if record.opportunity_id else [])
        allowed = {str(o.get("id")) for o in get_org_data(request).get("opportunities", [])}
        return any(str(opp_id) in allowed for opp_id in opp_ids)

    def get_card_data(self, request, target: dict, options: dict) -> base.CardPayload:
        definition_id = int(target["definition_id"])
        record = _load_definition(request, definition_id)
        card = (record.data.get("card", {}) if record else {}) or {}

        cta = card.get("cta") or {
            "label": "Open workflow",
            "url": reverse("labs:workflow:run", kwargs={"definition_id": definition_id}),
        }
        title = options.get("title") or card.get("title") or (record.name if record else f"Workflow {definition_id}")
        return base.CardPayload(
            title=title,
            card_type=card.get("card_type", "summary"),
            metrics=card.get("metrics", []),
            cta=cta,
            render_code=card.get("render_code"),
            data={"definition_id": definition_id},
        )
