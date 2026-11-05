"""The Issues page and its write screens: report, follow up, resolve, and name a custodian.

The page reads through operations like every other (`stock_issue_list`,
`stock_issue_summary`, `custodian_list`). A partner's own page is the same page
filtered with `?partner=<organisation id>`: its stores, its issues, and a report
form whose places are only its own. Without the filter it is the roll-up across
every partner.
"""

import base64

from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.fulfilment.forms import DocumentForm
from connect_labs.supply_chain.stock.issue_forms import (
    CustodianForm,
    StockIssueFollowUpForm,
    StockIssueLogForm,
    StockIssueReportForm,
    StockIssueResolveForm,
)
from connect_labs.supply_chain.stock.services import issues as issue_service
from connect_labs.supply_chain.values import plain_number
from connect_labs.supply_chain.views import OperationBase

# Places stock rests that a custodian can be named for.
_NOT_STORAGE = ("in_transit",)


def _partner_param(request):
    value = request.GET.get("partner") or ""
    return int(value) if value.isdigit() else None


def _evidence_payload(upload, *, issue_id, source) -> dict:
    """A document_attach payload for a photo or message file kept against one issue."""
    return {
        "kind": "photo" if (upload.content_type or "").startswith("image/") else "other",
        "title": f"Evidence for stock issue {issue_id}",
        "filename": upload.name,
        "content_type": upload.content_type or "application/octet-stream",
        "content_base64": base64.b64encode(upload.read()).decode(),
        "stock_issue_id": issue_id,
        "source": source,
    }


def _issues_url(partner_org_id=None):
    url = reverse("supply_chain:issues")
    return f"{url}?partner={partner_org_id}" if partner_org_id else url


class IssuesView(OperationBase):
    """Stock that is missing, taken, damaged or expired: open first, per partner, with a roll-up."""

    template_name = "supply_chain/issues.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context
        from connect_labs.labs.models import LabsOrg

        partner = _partner_param(self.request)
        show_all = self.request.GET.get("show") == "all"
        points = {p["id"]: p for p in self.op("supply_point_list", include_inactive=True)}
        owner = issue_service.partner_by_point(_access(self.request).program_id)
        summary = self.op("stock_issue_summary")

        listed = self.op("stock_issue_list", open_only=not show_all)
        if partner is not None:
            listed = [i for i in listed if owner.get(i["supply_point_id"]) == partner]
        custodians = {c["supply_point_id"]: c for c in self.op("custodian_list", current_only=True)}
        by_id = {c["id"]: c for c in self.op("custodian_list")}
        evidence = {}
        for document in self.op("document_list"):
            issue_id = document["links"].get("stock_issue_id")
            if issue_id is not None:
                evidence.setdefault(issue_id, []).append(document)
        rows = [
            {
                **issue,
                "point": points.get(issue["supply_point_id"]),
                "custodian": by_id.get(issue["custodian_id"]),
                "documents": evidence.get(issue["id"], []),
            }
            for issue in listed
        ]
        legacy = [
            m
            for m in self.op("movement_list", kind="loss", limit=200)
            if not m["caused_by"].get("stock_issue_id")
            and (partner is None or owner.get(m["from_supply_point_id"]) == partner)
        ]
        # A field worker keeps stock at home for a partner that hands out straight to workers: they are the
        # custodian there, and how the stock is kept (a lockable box, say) is recorded like any store's.
        from connect_labs.supply_chain.partners.services import plans_by_partner

        at_home = {
            org
            for org, plan in plans_by_partner(_access(self.request).program_id).items()
            if plan.distribution_model in ("direct_to_workers", "mixed")
        }
        stores = [
            {"point": p, "custodian": custodians.get(p["id"]), "at_home": p["kind"] == "user_held"}
            for p in points.values()
            if p["kind"] not in _NOT_STORAGE
            and p["status"] == "active"
            and (partner is None or owner.get(p["id"]) == partner)
            and (p["kind"] != "user_held" or p["id"] in custodians or (owner.get(p["id"]) in at_home))
        ]
        stores.sort(key=lambda s: (s["at_home"], s["point"]["name"]))
        context.update(
            summary=summary,
            partner_id=partner,
            partner_name=next((r["name"] for r in summary["by_partner"] if r["org_id"] == partner), None)
            or (LabsOrg.objects.filter(pk=partner).values_list("name", flat=True).first() if partner else None),
            partners=summary["by_partner"],
            show_all=show_all,
            issues=rows,
            legacy_losses=[{**m, "point": points.get(m["from_supply_point_id"])} for m in legacy],
            stores=stores,
        )
        return context


class IssueReportView(OperationFormView):
    operation = "stock_issue_report"
    form_class = StockIssueReportForm
    title = "Report a stock issue"
    intro = (
        "Stock that is missing, taken, damaged or expired. The quantity comes off that place's stock now, so the "
        "figures show what is really there; if it is found again, resolve the issue and it goes back."
    )
    submit_label = "Report it"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["partner_org_id"] = _partner_param(self.request)
        return kwargs

    def form_valid(self, form):
        self._upload = form.cleaned_data.get("upload")
        return super().form_valid(form)

    def succeeded(self, result):
        """Keep a photo or message given with the report as the issue's first evidence.

        The issue is already saved, so a file the store refuses does not undo it: the person lands on the
        screen that adds evidence, with the reason, rather than losing the report.
        """
        upload = getattr(self, "_upload", None)
        if upload is None:
            return super().succeeded(result)
        try:
            self.op("document_attach", data=_evidence_payload(upload, issue_id=result["id"], source=result["source"]))
        except ValueError:
            return redirect(reverse("supply_chain:issue_evidence", args=[result["id"]]))
        return super().succeeded(result)

    def get_initial(self):
        initial = super().get_initial()
        point = self.request.GET.get("supply_point") or ""
        if point.isdigit():
            initial["supply_point"] = int(point)
        return initial

    def breadcrumb(self, **kwargs):
        return [{"label": "Issues", "href": _issues_url(_partner_param(self.request))}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return _issues_url(_partner_param(self.request))

    def redirect_to(self, result):
        return _issues_url(_partner_param(self.request))


class IssueEvidenceView(OperationFormView):
    """A photo, a screenshot of the message, or a link, kept with a stock issue as evidence."""

    operation = "document_attach"
    form_class = DocumentForm
    title = "Add evidence to a stock issue"
    submit_label = "Add it"
    footnote = "Over 12 MB, store the file elsewhere and give a link."

    def issue(self):
        from connect_labs.supply_chain.models import StockIssue

        found = (
            StockIssue.objects.filter(pk=self.kwargs["issue_id"], program_id=_access(self.request).program_id)
            .select_related("supply_point", "commodity")
            .first()
        )
        if found is None:
            raise Http404(f"no stock issue {self.kwargs['issue_id']} in this programme")
        return found

    @property
    def intro(self):
        issue = self.issue()
        return (
            f"Evidence for the {issue.get_kind_display().lower()} stock at {issue.supply_point.name} on "
            f"{issue.occurred_on}: a photo, a screenshot of the WhatsApp message, or a link to it."
        )

    def get_initial(self):
        initial = super().get_initial()
        initial["kind"] = "photo"
        return initial

    def fixed(self, **kwargs):
        self.issue()
        return {"data": {"stock_issue_id": int(kwargs["issue_id"])}}

    def breadcrumb(self, **kwargs):
        return [{"label": "Issues", "href": _issues_url()}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return _issues_url()

    def redirect_to(self, result):
        return _issues_url()


class _OnAnIssue(OperationFormView):
    def issue(self):
        from connect_labs.supply_chain.models import StockIssue

        found = (
            StockIssue.objects.filter(pk=self.kwargs["issue_id"], program_id=_access(self.request).program_id)
            .select_related("supply_point", "commodity")
            .first()
        )
        if found is None:
            raise Http404(f"no stock issue {self.kwargs['issue_id']} in this programme")
        return found

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.issue()
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = self.issue()
        context["intro"] = (
            f"{issue.get_kind_display().capitalize()}: {plain_number(issue.quantity)} {issue.quantity_unit} of "
            f"{issue.commodity.name} at {issue.supply_point.name}, {issue.occurred_on}."
        )
        return context

    def fixed(self, **kwargs):
        return {"issue_id": int(kwargs["issue_id"])}

    def breadcrumb(self, **kwargs):
        return [{"label": "Issues", "href": reverse("supply_chain:issues")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:issues")

    def redirect_to(self, result):
        return reverse("supply_chain:issues")


class IssueFollowUpView(_OnAnIssue):
    operation = "stock_issue_update"
    form_class = StockIssueFollowUpForm
    title = "Follow up a stock issue"
    submit_label = "Save"


class IssueLogView(_OnAnIssue):
    """Add a line to an issue's follow-up log: who, what was done, when."""

    operation = "stock_issue_follow_up_add"
    form_class = StockIssueLogForm
    title = "Log a follow-up"
    submit_label = "Add to the log"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs.pop("instance", None)
        return kwargs

    def fixed(self, **kwargs):
        return {"issue_id": int(kwargs["issue_id"])}


class IssueResolveView(_OnAnIssue):
    operation = "stock_issue_resolve"
    form_class = StockIssueResolveForm
    title = "Close a stock issue"
    submit_label = "Close it"
    footnote = (
        "Recovered or replaced stock goes back on the place's stock, linked to this issue. Written off adds nothing: "
        "the quantity already came off when the issue was reported."
    )


class CustodianAssignView(OperationFormView):
    operation = "custodian_assign"
    form_class = CustodianForm
    title = "Who is responsible for this storage point"
    intro = (
        "A named person, their phone and the security in place. They confirm deliveries and own the balance here, "
        "so a missing carton points to a person and a day. Recording a new custodian keeps the earlier one as history."
    )
    submit_label = "Save"

    def get_initial(self):
        initial = super().get_initial()
        point = self.request.GET.get("supply_point") or ""
        if point.isdigit():
            initial["supply_point"] = int(point)
        return initial

    def breadcrumb(self, **kwargs):
        return [{"label": "Issues", "href": reverse("supply_chain:issues")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:issues")

    def redirect_to(self, result):
        return reverse("supply_chain:issues")
