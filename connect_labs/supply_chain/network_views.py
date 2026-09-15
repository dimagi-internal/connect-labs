"""The network tier in a browser: organisations and supply points.

Two directories and their write screens. Neither registry had a page at all —
an organisation could be created only through `org_upsert`, and the buyer of
record a contract requires had to be found by reading a list over the API.

**Why organisations sit under Suppliers rather than getting their own tab.**
They are infrastructure, not a daily destination: you go there to bind a
partner to Connect or to fold a duplicate away, and then you leave. The nav
stays the shape of the work.

**Supply points do get a tab**, between Sourcing and Stock, because that is
where they sit in the work: you need somewhere for stock to arrive before
there is any stock to look at.
"""

from django.http import Http404
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.models import SupplyPoint
from connect_labs.supply_chain.network_forms import OrgForm, OrgMergeForm, SupplyPointForm
from connect_labs.supply_chain.views import OperationBase

# The kinds, in the order a network reads top-down. A dict rather than a sort
# key so a kind nobody has any of simply does not appear.
KIND_ORDER = [
    ("central_store", "Central stores"),
    ("regional_store", "Regional stores"),
    ("facility", "Facilities"),
    ("user_held", "Field workers"),
    ("supplier_site", "Supplier sites"),
    ("in_transit", "In transit"),
    ("customs", "Customs"),
]


# ---- the two directories -----------------------------------------------


class NetworkView(OperationBase):
    """Where stock can rest in this programme, grouped by what each place is.

    Grouped rather than sorted, because the groups are the network's shape:
    four central stores and two hundred field workers is a different thing
    from the reverse, and a flat alphabetical list hides which it is.
    """

    template_name = "supply_chain/network.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if not context["has_program_context"]:
            return context

        points = self.op("supply_point_list", include_inactive=True)
        by_id = {p["id"]: p for p in points}
        for point in points:
            parent = by_id.get(point.get("parent_id"))
            point["parent_name"] = parent["name"] if parent else ""

        grouped = []
        for kind, label in KIND_ORDER:
            of_kind = [p for p in points if p["kind"] == kind]
            if of_kind:
                grouped.append({"kind": kind, "label": label, "points": of_kind})
        context["groups"] = grouped
        context["total"] = len(points)
        return context


class OrganisationDirectoryView(OperationBase):
    """Every organisation labs knows, and whether Connect knows it too.

    The unlinked ones are the backlog, and the page says so rather than
    showing a blank column: an organisation with no `connect_organization_id`
    is one whose own staff cannot sign in and record their own shipments.
    """

    template_name = "supply_chain/organisations.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        orgs = self.op("org_list")
        context["orgs"] = orgs
        context["linked"] = sum(1 for o in orgs if o.get("connect_organization_id"))
        return context


# ---- organisations -----------------------------------------------------


class _OrgScreen(OperationFormView):
    operation = "org_upsert"
    form_class = OrgForm

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Organisations", "href": reverse("supply_chain:organisations")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:organisations")

    def redirect_to(self, result):
        return reverse("supply_chain:organisations")


class OrgCreateView(_OrgScreen):
    title = "New organisation"
    intro = (
        "Anyone labs needs to name: a manufacturer, an implementing partner, a procurement "
        "agency, us. One registry for the whole of labs — an organisation is the same body "
        "in every programme it appears in."
    )
    submit_label = "Add organisation"


class OrgUpdateView(_OrgScreen):
    title = "Edit organisation"
    submit_label = "Save changes"
    footnote = (
        "This registry is labs-wide, so a change here shows in every programme this organisation "
        "appears in. Aliases are left as they are — they are curated by hand for slugs no rule can reach."
    )

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        found = LabsOrg.objects.filter(pk=self.kwargs["org_id"]).first()
        if found is None:
            raise Http404(f"no organisation {self.kwargs['org_id']}")
        kwargs["instance"] = found
        return kwargs


class OrgMergeView(OperationFormView):
    """Two rows that turn out to be one organisation."""

    operation = "org_merge"
    form_class = OrgMergeForm
    title = "Merge two organisations"
    intro = (
        "Every reference moves to the one you keep, the folded-away key survives as an alias so "
        "old references still resolve, and the empty row is deleted. This cannot be undone."
    )
    submit_label = "Merge them"
    danger = True
    footnote = (
        "Refused if the two are linked to different Connect organisations. Two Connect ids means two "
        "organisations, whatever the names look like."
    )

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Organisations", "href": reverse("supply_chain:organisations")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:organisations")

    def redirect_to(self, result):
        return reverse("supply_chain:organisations")


# ---- supply points -----------------------------------------------------


class _SupplyPointScreen(OperationFormView):
    operation = "supply_point_upsert"
    form_class = SupplyPointForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Network", "href": reverse("supply_chain:network")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:network")

    def redirect_to(self, result):
        return reverse("supply_chain:network")


class SupplyPointCreateView(_SupplyPointScreen):
    title = "New supply point"
    intro = (
        "Anywhere stock can rest — a central store, a facility, or one field worker's own "
        "holding. A worker is a supply point rather than a special case, which is what lets "
        "a delivery to one use the same ledger as a transfer between stores."
    )
    submit_label = "Add supply point"


class SupplyPointUpdateView(_SupplyPointScreen):
    title = "Edit supply point"
    submit_label = "Save changes"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        found = SupplyPoint.objects.filter(
            pk=self.kwargs["supply_point_id"], program_id=_access(self.request).program_id
        ).first()
        if found is None:
            raise Http404(f"no supply point {self.kwargs['supply_point_id']} in this programme")
        kwargs["instance"] = found
        return kwargs
