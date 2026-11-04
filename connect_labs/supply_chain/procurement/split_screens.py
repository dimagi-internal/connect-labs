"""The screen where a draft tender is split by partner, and the screen that sizes each partner's share.

Sophie Feintuch's RUTF feedback, "Split tenders by partner" and "Help working out how much to buy". Both are
screens over operations that already exist: the split is written with `tender_update` (`partner_splits`, checked
by `procurement/services/splits.apply_splits`) and the sizing is read with `purchase_sizing`. Nothing here
decides a rule of its own: a refusal from the operation is shown on the form.
"""

from decimal import Decimal, InvalidOperation

import jsonschema
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.utils.translation import gettext_lazy as _
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.api_views import _access, has_program_context
from connect_labs.supply_chain.banner import program_line
from connect_labs.supply_chain.form_views import OperationFormView, SupplyWriteMixin
from connect_labs.supply_chain.forms import INPUT, SELECT, set_choices
from connect_labs.supply_chain.models import SupplyPoint, Tender
from connect_labs.supply_chain.navigation import supply_tabs
from connect_labs.supply_chain.stock.services.issues import partner_by_point

BUYING_CHOICES = [
    ("partner_buys", _("The partner buys, on our negotiated quote")),
    ("programme_buys", _("We buy and ship it to the partner")),
]


class PlaceSelect(forms.Select):
    """A select whose places remember which partner runs them, so the page can show only that partner's."""

    def __init__(self, *args, owners=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owners = owners or {}

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex=subindex, attrs=attrs)
        owner = self.owners.get(value)
        if owner:
            option["attrs"]["data-org"] = str(owner)
        return option


class SplitsForm(forms.Form):
    """The page's own (empty) form: the rows are a formset, and the operation takes them as one list."""

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)

    def payload(self) -> dict:
        return {}


class PartnerSplitForm(forms.Form):
    """One partner's share of the tender: how much, where it is delivered, who receives it."""

    org = forms.ChoiceField(label=_("Partner"), widget=forms.Select(attrs=SELECT))
    commodity_slug = forms.ChoiceField(label=_("Product"), required=False, widget=forms.Select(attrs=SELECT))
    quantity = forms.DecimalField(
        label=_("How much"),
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any", "data-split-quantity": "1"}),
    )
    place = forms.ChoiceField(label=_("Delivered to"), required=False)
    new_place = forms.CharField(
        label=_("Or a new place"),
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Kano warehouse")}),
    )
    receiver_name = forms.CharField(
        label=_("Who receives it"), required=False, max_length=255, widget=forms.TextInput(attrs=INPUT)
    )
    receiver_contact = forms.CharField(
        label=_("Their phone or email"), required=False, max_length=255, widget=forms.TextInput(attrs=INPUT)
    )
    buying_model = forms.ChoiceField(
        label=_("Who buys"), choices=BUYING_CHOICES, initial="partner_buys", widget=forms.Select(attrs=SELECT)
    )

    def __init__(self, *args, partners=(), products=(), places=(), owners=None, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(self, "org", [("", "—")] + list(partners))
        set_choices(self, "commodity_slug", list(products))
        set_choices(self, "place", [("", "—")] + list(places))
        self.fields["place"].widget = PlaceSelect(attrs=SELECT, owners=owners)
        self.fields["place"].choices = [("", "—")] + list(places)
        self.owners = owners or {}

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("org"):
            return cleaned
        place = cleaned.get("place") or ""
        if not place and not cleaned.get("new_place"):
            self.add_error("place", _("Say where this partner's share is delivered."))
        owner = self.owners.get(place)
        if owner and str(owner) != str(cleaned["org"]):
            self.add_error("place", _("That place is run by another partner."))
        return cleaned


PartnerSplitFormSet = forms.formset_factory(PartnerSplitForm, extra=1, can_delete=True)


def _number(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return Decimal(0)


class TenderSplitsView(OperationFormView):
    """Enter and edit how a draft tender is split between partners; the tender's total is their sum."""

    operation = "tender_update"
    template_name = "supply_chain/procurement/tender_splits_form.html"
    title = "Split this tender between partners"
    intro = (
        "One row per partner: how much, where it is delivered and who receives it. The tender asks suppliers "
        "for the total of the rows. Leave every row empty to ask for one total again."
    )
    submit_label = "Save the split"
    form_class = SplitsForm

    # ---- what the page knows --------------------------------------------

    def tender(self):
        if not hasattr(self, "_tender"):
            found = Tender.objects.filter(
                pk=self.kwargs["tender_id"], program_id=_access(self.request).program_id
            ).first()
            if found is None:
                raise Http404(f"no tender {self.kwargs['tender_id']} in this programme")
            self._tender = found
        return self._tender

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and getattr(_access(request), "program_id", None):
            tender = self.tender()
            if tender.status != "draft":
                messages.error(request, "A tender can only be split between partners while it is a draft.")
                return redirect("supply_chain:procurement_tender_detail", tender_id=tender.pk)
        return super().dispatch(request, *args, **kwargs)

    def products(self):
        names = {c["slug"]: c["name"] for c in self.op("commodity_list")}
        return [
            (line["commodity_slug"], names.get(line["commodity_slug"], line["commodity_slug"]))
            for line in self.tender().lines or []
        ]

    def units(self):
        return {line["commodity_slug"]: line.get("quantity_unit") or "" for line in self.tender().lines or []}

    def partner_options(self):
        program_id = _access(self.request).program_id
        ids = {org for org in partner_by_point(program_id).values() if org}
        ids |= {s["org_id"] for s in self.tender().partner_splits or []}
        orgs = LabsOrg.objects.filter(pk__in=ids).order_by("name")
        return [(str(o.pk), o.name) for o in orgs]

        """(choices, owners): the tender's own places, then each partner's; owners maps a value to its partner."""
        """(choices, owners): the tender's own places, then each partner's places; owners maps a value to its partner."""
        program_id = _access(self.request).program_id
        choices = [
            (f"dp:{p['key']}", p.get("name") or p.get("city") or p["key"])
            for p in self.tender().delivery_points or []
            if p.get("key")
        ]
        owners = {}
        by_partner = partner_by_point(program_id)
        points = SupplyPoint.objects.filter(program_id=program_id).exclude(kind__in=("user_held", "supplier_site"))
        for point in points.order_by("name"):
            org = by_partner.get(point.pk)
            if org:
                value = f"sp:{point.pk}"
                choices.append((value, point.name))
                owners[value] = org
        return choices, owners

    def formset(self, data=None):
        choices, owners = self.place_options()
        products = self.products()
        initial = None
        if data is None:
            initial = [self._initial_row(s, choices) for s in self.tender().partner_splits or []]
        return PartnerSplitFormSet(
            data,
            initial=initial,
            prefix="splits",
            form_kwargs={
                "partners": self.partner_options(),
                "products": products,
                "places": choices,
                "owners": owners,
            },
        )

    def _initial_row(self, split, choices):
        values = {value for value, _label in choices}
        place = f"sp:{split['supply_point_id']}" if split.get("supply_point_id") else ""
        if place not in values:
            place = f"dp:{split['delivery_point_key']}" if split.get("delivery_point_key") else ""
        return {
            "org": str(split["org_id"]),
            "commodity_slug": split.get("commodity_slug"),
            "quantity": split.get("quantity"),
            "place": place if place in values else "",
            "receiver_name": split.get("receiver_name", ""),
            "receiver_contact": split.get("receiver_contact", ""),
            "buying_model": split.get("buying_model") or "partner_buys",
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context"):
            rows = context.get("splits") or self.formset(self.request.POST if self.request.method == "POST" else None)
            context["splits"] = rows
            context["tender"] = self.tender()
            units = self.units()
            context["unit"] = next(iter(units.values()), "")
            context["single_product"] = len(units) < 2
            total = sum((_number(s["quantity"]) for s in self.tender().partner_splits or []), Decimal(0))
            context["current_total"] = total
        return context

    # ---- saving ----------------------------------------------------------

    def form_valid(self, form):
        rows = self.formset(self.request.POST)
        if not rows.is_valid():
            return self.render_to_response(self.get_context_data(form=form, splits=rows))
        prior = {(str(s["org_id"]), s.get("commodity_slug")): s for s in self.tender().partner_splits or []}
        products = self.products()
        splits = []
        for row in rows.cleaned_data:
            if not row or row.get("DELETE") or not row.get("org"):
                continue
            slug = row.get("commodity_slug") or (products[0][0] if len(products) == 1 else "")
            split = {
                "org_id": int(row["org"]),
                "commodity_slug": slug,
                "quantity": str(row["quantity"]),
                "receiver_name": row["receiver_name"],
                "receiver_contact": row["receiver_contact"],
                "buying_model": row["buying_model"],
            }
            place = row.get("place") or ""
            old = prior.get((row["org"], slug)) or {}
            if row.get("new_place"):
                split["delivery_point"] = {"name": row["new_place"]}
            elif place.startswith("sp:"):
                point = SupplyPoint.objects.get(pk=int(place[3:]), program_id=_access(self.request).program_id)
                split["supply_point_id"] = point.pk
                split["delivery_point"] = {"name": point.name, "city": point.admin_area}
            else:
                split["delivery_point_key"] = place[3:]
                if old.get("delivery_point_key") == place[3:] and old.get("supply_point_id"):
                    split["supply_point_id"] = old["supply_point_id"]
            splits.append(split)
        self._splits = splits
        return super().form_valid(form)

    def fixed(self, **kwargs):
        return {"tender_id": int(kwargs["tender_id"]), "data": {"partner_splits": getattr(self, "_splits", [])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Sourcing", "href": reverse("supply_chain:procurement_tender_board")},
            {"label": self.tender().label, "href": self.cancel_href()},
            {"label": "Split between partners"},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:procurement_tender_detail", args=[self.kwargs["tender_id"]])

    def redirect_to(self, result):
        return self.cancel_href()


# ---- sizing -----------------------------------------------------------------

# What `tender_update` takes of a split; a stored split also carries `key` and `org_name`, which it rebuilds.
SPLIT_FIELDS = (
    "org_id",
    "commodity_slug",
    "quantity",
    "quantity_unit",
    "delivery_point_key",
    "receiver_name",
    "receiver_contact",
    "buying_model",
    "supply_point_id",
)

COURSE_BASIS_WORDS = {
    "given": "the figure you gave",
    "protocol": "the product's protocol (no forecast yet from children who have finished)",
    "forecast": "the forecast, from children who have finished their course",
}


def _plain_number(value) -> str:
    number = _number(value)
    return f"{number:,.0f}" if number == number.to_integral() else f"{number:,.2f}".rstrip("0").rstrip(".")


@method_decorator(login_required, name="dispatch")
class PurchaseSizingView(SupplyWriteMixin, TemplateView):
    """How much to buy for each partner: target cases x the course, plus a buffer, with the working shown.

    A read: it calls `purchase_sizing` and prints what it returns. The only write is the button beside a
    figure, which posts to `TenderSizingApplyView` and goes through `tender_update`.
    """

    template_name = "supply_chain/procurement/tender_sizing.html"

    def dispatch(self, request, *args, **kwargs):
        if not has_program_context(request):
            raise Http404("no programme selected")
        return super().dispatch(request, *args, **kwargs)

    def tender(self):
        found = Tender.objects.filter(pk=self.kwargs["tender_id"], program_id=_access(self.request).program_id).first()
        if found is None:
            raise Http404(f"no tender {self.kwargs['tender_id']} in this programme")
        return found

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        tender = self.tender()
        get = self.request.GET
        lines = [line for line in tender.lines or [] if line.get("commodity_slug")]
        slug = get.get("product") or (lines[0]["commodity_slug"] if lines else "")
        line = next((line for line in lines if line["commodity_slug"] == slug), None)
        partners = {s["org_id"]: s["org_name"] for s in tender.partner_splits or []}
        for org in LabsOrg.objects.filter(
            pk__in={o for o in partner_by_point(_access(self.request).program_id).values() if o}
        ):
            partners.setdefault(org.pk, org.name)
        rows = [
            {
                "org_id": pk,
                "org_name": name,
                "cases": get.get(f"cases_{pk}", ""),
                "buffer": get.get(f"buffer_{pk}", ""),
            }
            for pk, name in sorted(partners.items(), key=lambda item: item[1].lower())
        ]
        context.update(
            supply_tabs=supply_tabs(self.request),
            supply_program_line=program_line(self.request),
            has_program_context=True,
            tender=tender,
            products=lines,
            product=slug,
            rows=rows,
            buffer=get.get("buffer", ""),
            course=get.get("course", ""),
            sized=None,
            error="",
        )
        asked = [r for r in rows if r["cases"].strip()]
        if not asked or line is None:
            return context
        payload = {
            "commodity_slug": slug,
            "partners": [
                {
                    "org_id": r["org_id"],
                    "target_cases": float(r["cases"]),
                    **({"buffer_percent": float(r["buffer"])} if r["buffer"].strip() else {}),
                }
                for r in asked
            ],
        }
        if context["buffer"].strip():
            payload["buffer_percent"] = float(context["buffer"])
        if context["course"].strip():
            payload["course_units"] = float(context["course"])
        try:
            result = self.op("purchase_sizing", **payload)
        except (jsonschema.ValidationError, ValueError, TypeError) as exc:
            context["error"] = getattr(exc, "message", str(exc))
            return context
        commodity = next((c for c in self.op("commodity_list") if c["slug"] == slug), {})
        in_packs = result.get("total_packs") is not None and line.get("quantity_unit") != commodity.get("base_unit")
        unit = line.get("quantity_unit") or ""
        for row in result["partners"]:
            row["figure"] = row["packs"] if in_packs else row["units"]
            row["figure_text"] = _plain_number(row["figure"])
            row["sentence"] = (
                f"{_plain_number(row['target_cases'])} children x {_plain_number(row['course_units'])} per child's "
                f"course = {_plain_number(_number(row['target_cases']) * _number(row['course_units']))}; "
                f"plus a {_plain_number(row['buffer_percent'])}% buffer = {_plain_number(row['units'])} "
                f"{commodity.get('base_unit') or 'units'}"
                + (
                    f"; in {commodity.get('pack_unit') or 'packs'} of {result['base_per_pack']} that is "
                    f"{_plain_number(row['packs'])}."
                    if row["packs"] is not None
                    else "."
                )
            )
        context.update(
            sized=result,
            unit=unit,
            in_packs=in_packs,
            course_basis_text=COURSE_BASIS_WORDS.get(result["course_basis"], result["course_basis"]),
            course_text=_plain_number(result["partners"][0]["course_units"]),
            total_text=_plain_number(result["total_packs"] if in_packs else result["total_units"]),
            changed=get.get("changed", ""),
        )
        return context


@method_decorator(login_required, name="dispatch")
class TenderSizingApplyView(SupplyWriteMixin, View):
    """Drop a sized figure into one partner's quantity on the tender's split (through `tender_update`)."""

    def post(self, request, tender_id):
        if not has_program_context(request):
            raise Http404("no programme selected")
        tender = Tender.objects.filter(pk=tender_id, program_id=_access(request).program_id).first()
        if tender is None:
            raise Http404(f"no tender {tender_id} in this programme")
        back = request.POST.get("next") or ""
        if not back.startswith("/supply/"):
            back = reverse("supply_chain:procurement_tender_sizing", args=[tender_id])
        org, slug = request.POST.get("org_id", ""), request.POST.get("commodity_slug", "")
        try:
            quantity = Decimal(request.POST.get("quantity", ""))
        except InvalidOperation:
            messages.error(request, "That figure is not a number.")
            return redirect(back)
        if tender.status != "draft":
            messages.error(request, "A tender can only be split between partners while it is a draft.")
            return redirect(back)
        splits = [{k: v for k, v in s.items() if k in SPLIT_FIELDS} for s in tender.partner_splits or []]
        mine = next((s for s in splits if str(s["org_id"]) == org and s.get("commodity_slug") == slug), None)
        if mine is not None:
            mine["quantity"] = str(quantity)
        elif org.isdigit():
            splits.append({"org_id": int(org), "commodity_slug": slug, "quantity": str(quantity)})
        try:
            self.op("tender_update", tender_id=int(tender_id), data={"partner_splits": splits})
        except (jsonschema.ValidationError, ValueError, TypeError) as exc:
            messages.error(request, getattr(exc, "message", str(exc)))
            return redirect(back)
        name = (mine or {}).get("org_name") or LabsOrg.objects.filter(pk=org).values_list("name", flat=True).first()
        messages.success(request, f"{name}'s share of the tender is now {_plain_number(quantity)}.")
        if mine is None:
            messages.info(request, "Say where it is delivered on the split page before opening the tender.")
        return redirect(back)
