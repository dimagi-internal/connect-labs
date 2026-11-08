"""What supply writes to suppliers and partners, sent from labs.

Supply already drafts every email a tender owes (`tender_drafts_render`: quote
requests, reminders, follow-ups, replies to a supplier's questions,
clarifications) and the emails an award owes (`selection_drafts_render`). A
communication of these kinds is those drafts, one recipient each, ready to
edit and send from labs instead of copying into a mailbox. Sending one does
what "mark sent" used to be a separate click for:

* a quote request sets the invitation's `sent_on`;
* a reminder or follow-up sets its `last_reminder_on`;
* a reply resolves the questions it answers and records the reply sent.

Each runs as the person sending, through the same operations, so supply's own
history records it. Only the programme -- its running organisation's people,
or support -- prepares and sends them (`runner.standing`).
"""

from __future__ import annotations

import logging
from datetime import date

from django.urls import reverse

from connect_labs.communications import kinds
from connect_labs.communications.kinds import DraftSpec, RecipientSpec
from connect_labs.supply_chain.values import day_text

logger = logging.getLogger(__name__)


def supply_access(caller, program_id):
    """The supply scope for `program_id`, as the caller (PermissionDenied if they may not use it)."""
    from connect_labs.labs.access.scopes import Caller as ScopeCaller
    from connect_labs.supply_chain.data_access import SupplyDataAccess

    request = caller.request
    if request is not None and hasattr(request, "session"):
        token = (request.session.get("labs_oauth") or {}).get("access_token")
        scope_caller = ScopeCaller(request=request, user=caller.user)
    else:
        from connect_labs.mcp.connect_token import require_connect_token

        token = require_connect_token(caller.user)
        scope_caller = ScopeCaller(user=caller.user, access_token=token)
    return SupplyDataAccess(access_token=token, program_id=program_id, user=caller.user, caller=scope_caller)


def _is_programme(caller, program_id) -> bool:
    from connect_labs.supply_chain.runner import standing

    if not caller.is_authenticated or program_id is None:
        return False
    try:
        return standing(supply_access(caller, program_id)).is_programme
    except Exception:  # noqa: BLE001 -- an unknowable standing grants nothing
        return False


_LABELS = {
    "request": "Quote request",
    "reminder": "Reminder",
    "followup": "Follow-up",
    "reply": "Reply",
    "clarification": "Clarification",
    "selection": "Selected",
    "handover": "Handover",
}


class TenderEmailsKind(kinds.Kind):
    key = "supply_chain.tender_emails"
    label = "Emails a tender owes"
    about_model = "supply_chain.tender"
    placeholders = ()
    description = (
        "Every email a tender owes now, drafted by labs (quote requests, reminders, follow-ups, replies to a "
        "supplier's questions, clarifications), one recipient each with its own subject and text. `about_id` "
        "is the tender's id. Pass `draft_kinds` in params (request, reminder, followup, reply, clarification) "
        "to include only those. Sending one marks it sent on the tender (the invitation's send date, the "
        "reminder date, the reply). Only the programme prepares and sends them."
    )

    def about_label(self, about) -> str:
        return about.label if about is not None else ""

    def about_url(self, about) -> str:
        return reverse("supply_chain:procurement_tender_detail", args=[about.pk]) if about is not None else ""

    def can_send(self, caller, about, communication=None) -> bool:
        return about is not None and _is_programme(caller, about.program_id)

    def new_draft(self, caller, about, params: dict) -> DraftSpec:
        from connect_labs.supply_chain.models import Supplier
        from connect_labs.supply_chain.operations import call_operation

        access = supply_access(caller, about.program_id)
        result = call_operation("tender_drafts_render", access, {"tender_id": about.pk})
        wanted = set(params.get("draft_kinds") or [])
        suppliers = {s.pk: s for s in Supplier.objects.filter(pk__in=[d.get("supplier_id") for d in result["drafts"]])}
        recipients = []
        for draft in result["drafts"]:
            if wanted and draft["kind"] not in wanted:
                continue
            supplier = suppliers.get(draft.get("supplier_id"))
            ref = {
                key: draft[key]
                for key in ("kind", "supplier_id", "outreach_id", "quote_id", "commitment_ids", "answered_ids")
                if draft.get(key) not in (None, "", [])
            }
            recipients.append(
                RecipientSpec(
                    email=draft.get("to") or "",
                    name=draft.get("supplier_name") or "",
                    org=getattr(supplier, "org", None),
                    cc=list(draft.get("cc") or []),
                    subject=draft["subject"],
                    body=draft["text"],
                    label=f"{_LABELS.get(draft['kind'], draft['kind'])}: {draft.get('supplier_name') or ''}",
                    ref={**ref, "why": draft.get("why", "")},
                )
            )
        return DraftSpec(
            title=f"Emails for {about.label}",
            recipients=recipients,
            program_id=about.program_id,
            context={"tender_id": about.pk, "drafted_on": result["today"]},
        )

    def after_sent(self, communication, recipient, sent, about, caller) -> dict:
        from connect_labs.supply_chain.models import Commitment
        from connect_labs.supply_chain.operations import call_operation

        ref = recipient.ref or {}
        kind = ref.get("kind")
        today = date.today()
        access = supply_access(caller, about.program_id)
        if kind == "request" and ref.get("outreach_id"):
            call_operation(
                "outreach_update", access, {"outreach_id": ref["outreach_id"], "data": {"sent_on": today.isoformat()}}
            )
            return {"outreach_sent_on": today.isoformat()}
        if kind in ("reminder", "followup") and ref.get("outreach_id"):
            call_operation(
                "outreach_update",
                access,
                {"outreach_id": ref["outreach_id"], "data": {"last_reminder_on": today.isoformat()}},
            )
            return {"outreach_reminded_on": today.isoformat()}
        if kind == "reply" and ref.get("commitment_ids"):
            ids = [int(i) for i in ref["commitment_ids"]]
            # A question still open is answered in the reply itself (as "Mark this reply sent" does).
            for pk in Commitment.objects.filter(
                pk__in=ids, program_id=about.program_id, resolved_on__isnull=True
            ).values_list("pk", flat=True):
                call_operation(
                    "commitment_resolve",
                    access,
                    {
                        "commitment_id": pk,
                        "resolution": f"Answered in our reply of {day_text(today)}.",
                        "resolved_on": today.isoformat(),
                    },
                )
            call_operation("commitment_reply_sent", access, {"commitment_ids": ids})
            return {"reply_sent_for": ids}
        return {}


kinds.register(TenderEmailsKind())


class AwardEmailsKind(kinds.Kind):
    key = "supply_chain.award_emails"
    label = "Emails an award owes"
    about_model = "supply_chain.award"
    placeholders = ()
    description = (
        "The emails to send once a tender is awarded: `selection` to the winning supplier and, on a tender "
        "split by partner, a `handover` to each partner. `about_id` is the award's id. Only the programme "
        "prepares and sends them."
    )

    def about_label(self, about) -> str:
        return f"{about.tender.label}: {about.supplier.name}" if about is not None else ""

    def about_url(self, about) -> str:
        if about is None:
            return ""
        return reverse("supply_chain:procurement_tender_detail", args=[about.tender_id])

    def can_send(self, caller, about, communication=None) -> bool:
        return about is not None and _is_programme(caller, about.tender.program_id)

    def new_draft(self, caller, about, params: dict) -> DraftSpec:
        from connect_labs.labs.models import LabsOrg
        from connect_labs.supply_chain.operations import call_operation

        access = supply_access(caller, about.tender.program_id)
        result = call_operation("selection_drafts_render", access, {"award_id": about.pk})
        recipients = []
        for draft in result["drafts"]:
            org = about.supplier.org if draft["kind"] == "selection" else None
            if draft.get("org_id"):
                org = LabsOrg.objects.filter(pk=draft["org_id"]).first()
            recipients.append(
                RecipientSpec(
                    email=draft.get("to") or "",
                    name=org.name if org is not None else "",
                    org=org,
                    cc=list(draft.get("cc") or []),
                    subject=draft["subject"],
                    body=draft["text"],
                    label=draft.get("label") or _LABELS.get(draft["kind"], draft["kind"]),
                    ref={"kind": draft["kind"]},
                )
            )
        return DraftSpec(
            title=f"Award emails for {about.tender.label}",
            recipients=recipients,
            program_id=about.tender.program_id,
            context={"award_id": about.pk},
        )


kinds.register(AwardEmailsKind())
