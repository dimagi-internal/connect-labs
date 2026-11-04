"""The Issues page and its write screens: report, follow up, resolve, and name a custodian.

The page reads through operations like every other (`stock_issue_list`,
`stock_issue_summary`, `custodian_list`). A partner's own page is the same page
filtered with `?partner=<organisation id>`: its stores, its issues, and a report
form whose places are only its own. Without the filter it is the roll-up across
every partner.
"""

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.stock.issue_forms import (
    CustodianForm,
    StockIssueFollowUpForm,
    StockIssueReportForm,
    StockIssueResolveForm,
)
from connect_labs.supply_chain.stock.services import issues as issue_service
from connect_labs.supply_chain.views import OperationBase

# Places stock rests that a custodian can be named for.
_NOT_STORAGE = ("in_transit",)


def _partner_param(request):
    value = request.GET.get("partner") or ""
    return int(value) if value.isdigit() else None


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
        rows = [
            {**issue, "point": points.get(issue["supply_point_id"]), "custodian": by_id.get(issue["custodian_id"])}
            for issue in listed
        ]
        legacy = [
            m
            for m in self.op("movement_list", kind="loss", limit=200)
            if not m["caused_by"].get("stock_issue_id")
            and (partner is None or owner.get(m["from_supply_point_id"]) == partner)
        ]
        stores = [
            {"point": p, "custodian": custodians.get(p["id"])}
            for p in points.values()
            if p["kind"] != "user_held"
            and p["kind"] not in _NOT_STORAGE
            and p["status"] == "active"
            and (partner is None or owner.get(p["id"]) == partner)
        ]
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
            f"{issue.get_kind_display().capitalize()}: {issue.quantity.normalize()} {issue.quantity_unit} of "
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
