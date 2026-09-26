"""The sourcing tier's forms: tenders, invitations, quotes, approvals.

Split out of the top-level `forms.py`, which was 1,054 lines holding two
unrelated things -- the shared form base every supply screen builds on, and
procurement's own forms. The shared half stays where it was, because eight
modules import it and a top-level module holding the base is ordinary Django.
This half sits next to `procurement/views.py`, which is what uses it.

One import crosses a package boundary as a result: `distribution/views.py`
takes `QuoteCorrectionForm` from here. That is not an accident of the split
-- correcting a quote IS a procurement act, reached from a distribution
screen -- and it is better named than hidden behind a neutral module.
"""

from datetime import date

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Column, Field, Fieldset, Layout, Row
from django import forms
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import records
from connect_labs.supply_chain.forms import (
    DATE,
    INPUT,
    MONEY_INPUT,
    SEARCHABLE,
    SELECT,
    TEXTAREA,
    ScopedForm,
    currency_select,
    set_choices,
    to_payload,
)
from connect_labs.supply_chain.models import (
    AwardApproval,
    Commodity,
    Contract,
    Item,
    Outreach,
    Quote,
    Supplier,
    SupplyPoint,
    Tender,
)


class TenderForm(ScopedForm):
    """A tender's own details. Its product lines and delivery places are formsets.

    `lines` and `delivery_points` are JSONFields and are excluded: rendered by
    a ModelForm they would be a textarea of raw JSON, which is a worse way to
    ask for a quantity and a place name than asking for them.
    """

    class Meta:
        model = Tender
        fields = [
            "label",
            "response_deadline",
            "reminder_interval_days",
            "shelf_life_months_minimum",
            "incoterm_requested",
            "pickup_accepted",
            "notes_to_supplier",
            "visibility",
            "owner_org",
            "slug",
            "brief",
            "hue",
        ]
        widgets = {
            "visibility": forms.Select(attrs=SELECT),
            "label": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Tender 1 — RUTF, 500 cartons")}),
            "response_deadline": forms.DateInput(attrs=DATE),
            "reminder_interval_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "shelf_life_months_minimum": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "incoterm_requested": forms.TextInput(attrs={**INPUT, "placeholder": "DAP", "maxlength": 16}),
            "owner_org": forms.Select(attrs=SEARCHABLE),
            "slug": forms.TextInput(attrs={**INPUT, "placeholder": "e.g. rutf-sokoto-2026"}),
            "brief": forms.Textarea(attrs=TEXTAREA),
            "hue": forms.Select(attrs=SELECT),
            "notes_to_supplier": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "label": _("What to call this tender"),
            "response_deadline": _("Replies wanted by"),
            "reminder_interval_days": _("Chase every (days)"),
            "shelf_life_months_minimum": _("Minimum shelf life (months)"),
            "incoterm_requested": _("Terms asked for (Incoterm)"),
            "pickup_accepted": _("We can also collect from the supplier"),
            "notes_to_supplier": _("Anything else to tell suppliers"),
            "visibility": _("On the supplier marketplace"),
            "owner_org": _("Published by"),
            "slug": _("Its own address"),
            "brief": _("Brief for suppliers"),
            "hue": _("Colour"),
        }
        help_texts = {
            "shelf_life_months_minimum": _("Sea freight and clearance routinely eat four months of it."),
            "reminder_interval_days": _("Leave empty and nobody is chased automatically."),
            "owner_org": _("An organisation publishing this as its own tender. Its people can then run the listing."),
            "slug": _("Gives the tender a shareable address, /supply/market/t/<this>/. Letters, numbers and hyphens."),
            "brief": _("A few paragraphs suppliers read above the products."),
            "pickup_accepted": _(
                "Suppliers may then offer a price for us to collect. Its delivered cost stays unconfirmed "
                "until you enter what our own transport will cost."
            ),
            "visibility": _(
                "Open: while the tender is taking bids anyone can read it on the marketplace and any "
                "registered supplier can bid. Restricted: only the suppliers you invite can see it."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper.layout = Layout(
            Field("label"),
            Row(
                Column("response_deadline"), Column("reminder_interval_days"), css_class="grid md:grid-cols-2 gap-x-6"
            ),
            Row(
                Column("shelf_life_months_minimum"),
                Column("incoterm_requested"),
                css_class="grid md:grid-cols-2 gap-x-6",
            ),
            Field("pickup_accepted"),
            Field("notes_to_supplier"),
            Field("visibility"),
            Fieldset(
                str(_("As an organisation's own listing (optional)")),
                Row(Column("owner_org"), Column("slug"), css_class="grid md:grid-cols-2 gap-x-6"),
                Field("brief"),
                Field("hue"),
                css_class="pt-2",
            ),
        )
        self.fields["owner_org"].queryset = LabsOrg.objects.order_by("name")
        self.fields["owner_org"].required = False
        self.fields["owner_org"].empty_label = _("No one — a program tender")
        set_choices(self, "hue", [("", _("By product")), *records.LISTING_HUES], required=False)
        # Not required: a caller that does not say leaves the tender public,
        # the model's default -- the same as a tender created over the API.
        self.fields["visibility"].required = False
        set_choices(
            self,
            "visibility",
            [
                ("public", _("Open — any registered supplier can see it and bid")),
                ("private", _("Restricted — only the suppliers we invite")),
            ],
        )

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        # A checkbox left clear is False, which to_payload keeps; say so
        # explicitly so an edit can turn collection off again.
        data["pickup_accepted"] = bool(self.cleaned_data.get("pickup_accepted"))
        # Cleared on the form means cleared on the tender, not "left alone".
        data["slug"] = self.cleaned_data.get("slug") or None
        data["owner_org_id"] = self.cleaned_data["owner_org"].pk if self.cleaned_data.get("owner_org") else None
        data.pop("owner_org", None)
        data["hue"] = self.cleaned_data.get("hue") or ""
        data["brief"] = self.cleaned_data.get("brief") or ""
        return data


class TenderPlaceForm(forms.Form):
    """One place the buyer will take delivery at. Rendered as a formset."""

    key = forms.CharField(required=False, widget=forms.HiddenInput)
    name = forms.CharField(
        label=_("Place"),
        max_length=255,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Central store")}),
    )
    city = forms.CharField(label=_("City"), max_length=128, required=False, widget=forms.TextInput(attrs=INPUT))
    country_name = forms.CharField(
        label=_("Country"),
        max_length=64,
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Nigeria")}),
    )


class TenderLineForm(forms.Form):
    """One commodity a tender is asking for. Rendered as a formset.

    A tender can ask for several commodities and each needs its own quantity
    and unit, which is a repeating row -- the thing `formset_factory` exists
    for and the thing I was about to hand-roll in Alpine.
    """

    commodity_slug = forms.ChoiceField(label=_("Commodity"), widget=forms.Select(attrs=SEARCHABLE))
    quantity = forms.DecimalField(
        label=_("Quantity"),
        min_value=0,
        max_digits=18,
        decimal_places=4,
        widget=forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "500"}),
    )
    quantity_unit = forms.CharField(
        label=_("Unit"),
        max_length=32,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
    )

    def __init__(self, *args, commodities=(), **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(self, "commodity_slug", [("", "—")] + list(commodities))


class OutreachForm(ScopedForm):
    """Record that a quote request went out to a supplier."""

    class Meta:
        model = Outreach
        fields = ["supplier", "channel", "sent_on", "notes"]
        widgets = {
            "supplier": forms.Select(attrs=SEARCHABLE),
            "channel": forms.Select(attrs=SELECT),
            "sent_on": forms.DateInput(attrs=DATE),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "supplier": _("Who was asked"),
            "channel": _("How"),
            "sent_on": _("Sent on"),
            "notes": _("Notes"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scoped, not `Supplier.objects.all()`: an unscoped queryset offers
        # every supplier in the database and is a cross-programme leak.
        self.fields["supplier"].queryset = (
            Supplier.objects.filter(scope_key=self.access.scope_key).select_related("org", "org__supplier_profile")
            if self.access
            else Supplier.objects.none()
        )
        self.fields["supplier"].empty_label = _("Select a supplier…")
        set_choices(
            self,
            "channel",
            [
                ("manual", _("By hand (email, call)")),
                ("ses", _("Sent by the system")),
                ("api", _("Via the API")),
                ("mcp", _("Via an agent")),
            ],
        )
        self.fields["sent_on"].initial = date.today
        self.helper.layout = Layout(
            Field("supplier"),
            Row(Column("sent_on"), Column("channel"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("notes"),
        )


class OutreachReplyForm(ScopedForm):
    """What came back, if anything."""

    class Meta:
        model = Outreach
        fields = ["responded", "response_kind", "last_reminder_on", "notes"]
        widgets = {
            "responded": forms.CheckboxInput(attrs={"class": "simple-toggle"}),
            "response_kind": forms.Select(attrs=SELECT),
            "last_reminder_on": forms.DateInput(attrs=DATE),
            "notes": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "responded": _("They replied"),
            "response_kind": _("What the reply was"),
            "last_reminder_on": _("Last chased on"),
            "notes": _("Notes"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["response_kind"].required = False
        set_choices(
            self,
            "response_kind",
            [
                ("", "—"),
                ("quote", _("A quote")),
                ("declined", _("Declined to quote")),
                ("needs_info", _("Asked us for more information")),
                ("no_reply", _("No reply")),
            ],
        )
        self.helper.layout = Layout(
            Field("responded"),
            Row(Column("response_kind"), Column("last_reminder_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("notes"),
        )


class ReasonForm(forms.Form):
    """The reason a destructive operation requires.

    `quote_void` and `outreach_delete` both refuse without one, and that
    refusal is the point: a record removed with no account of why is worse
    than the record. Asking for it as an ordinary required field beats a
    confirmation dialogue nobody reads.
    """

    reason = forms.CharField(
        label=_("Why"),
        widget=forms.Textarea(attrs={**TEXTAREA, "placeholder": _("e.g. recorded against the wrong supplier")}),
        help_text=_("Kept in the write log, so the record of the mistake outlives the row."),
    )

    def __init__(self, *args, access=None, **kwargs):
        self.access = access
        super().__init__(*args, **kwargs)
        self.helper = FormHelper(self)
        self.helper.form_tag = False
        self.helper.disable_csrf = True

    def payload(self) -> dict:
        return to_payload(self.cleaned_data)


def _rests_on_field(form):
    """An optional picker of this programme's documents, registrations first.

    What a regulatory approval rests on is usually a product registration
    already on file; offering every document the programme holds, with the
    registrations at the top, lets the other cases through without making the
    common one hunt.
    """
    from django.db.models import Case, IntegerField, Value, When

    from connect_labs.supply_chain.models import Document
    from connect_labs.supply_chain.templatetags.supply_chain_extras import words

    field = form.fields["rests_on_document"]
    field.required = False
    field.queryset = (
        Document.objects.filter(program_id=form.access.program_id)
        .annotate(
            first=Case(When(kind="product_registration", then=Value(0)), default=Value(1), output_field=IntegerField())
        )
        .order_by("first", "kind", "title", "pk")
        if form.access is not None and form.access.program_id
        else Document.objects.none()
    )
    field.empty_label = _("None — it rests on nothing on file")
    field.label_from_instance = lambda document: " — ".join(
        part for part in (words(document.kind).capitalize(), document.title or document.filename) if part
    )


def _programme_first(queryset, in_programme):
    """Organisations already in this programme first, then the rest, each by name."""
    from django.db.models import Case, IntegerField, Value, When

    return queryset.annotate(
        _in_programme=Case(When(pk__in=in_programme, then=Value(0)), default=Value(1), output_field=IntegerField())
    ).order_by("_in_programme", "name")


class ApprovalRequestForm(ScopedForm):
    """Ask a third party to agree to an award before it becomes an order."""

    class Meta:
        model = AwardApproval
        fields = ["approver_org", "role", "requested_on", "note", "rests_on_document"]
        widgets = {
            "rests_on_document": forms.Select(attrs=SEARCHABLE),
            "approver_org": forms.Select(attrs=SEARCHABLE),
            "role": forms.Select(attrs=SELECT),
            "requested_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "approver_org": _("Who has to agree"),
            "role": _("As what"),
            "requested_on": _("Asked on"),
            "note": _("What was asked"),
            "rests_on_document": _("Rests on"),
        }
        help_texts = {
            "rests_on_document": _(
                "A document already on file it is granted against — for a regulatory approval, the "
                "product registration. The approver's own letter is attached to the approval itself."
            ),
            "approver_org": _("Not the person deciding the award — somebody whose agreement it needs."),
            "role": _("Technical: confirms the product. Funder: approves the use of funds. Regulatory: a licence."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Organisations are labs-wide, so this picker is deliberately unscoped --
        # but the ones already working in this programme come first. Labs-wide
        # and alphabetical, it opened on hundreds of unrelated organisations
        # and the technical partner the buyer means was pages down (the
        # test-kit render). The same correction `buyer_org` already carries;
        # this picker was simply one behind it.
        self.fields["approver_org"].queryset = _programme_first(LabsOrg.objects.all(), self._orgs_in_this_programme())
        self.fields["approver_org"].empty_label = _("Select an organisation…")
        self.fields["requested_on"].initial = date.today()
        set_choices(
            self,
            "role",
            [("technical", _("Technical")), ("funder", _("Funder")), ("regulatory", _("Regulatory"))],
        )
        self.helper.layout = Layout(
            Row(
                Column("approver_org"), Column("role"), Column("requested_on"), css_class="grid md:grid-cols-3 gap-x-6"
            ),
            Field("note"),
            Field("rests_on_document"),
        )
        _rests_on_field(self)

    def _orgs_in_this_programme(self):
        """Organisations this programme already works with, however they touch it."""
        program_id = getattr(self.access, "program_id", None) if self.access else None
        scope = getattr(self.access, "scope_key", None) if self.access else None
        if not program_id:
            return set()
        found = set(
            SupplyPoint.objects.filter(program_id=program_id)
            .exclude(managed_by_org=None)
            .values_list("managed_by_org_id", flat=True)
        )
        found |= set(
            Contract.objects.filter(program_id=program_id)
            .exclude(buyer_org=None)
            .values_list("buyer_org_id", flat=True)
        )
        found |= set(
            AwardApproval.objects.filter(award__tender__program_id=program_id)
            .exclude(approver_org=None)
            .values_list("approver_org_id", flat=True)
        )
        if scope:
            found |= set(Supplier.objects.filter(scope_key=scope).values_list("org_id", flat=True))
        return found


class ApprovalDecisionForm(ScopedForm):
    """The approver's answer. Final on its row: a reversal is a new request."""

    class Meta:
        model = AwardApproval
        fields = ["status", "decided_on", "note", "rests_on_document"]
        widgets = {
            "rests_on_document": forms.Select(attrs=SEARCHABLE),
            "status": forms.Select(attrs=SELECT),
            "decided_on": forms.DateInput(attrs=DATE),
            "note": forms.Textarea(attrs=TEXTAREA),
        }
        labels = {
            "status": _("Their answer"),
            "decided_on": _("Answered on"),
            "note": _("Note"),
            "rests_on_document": _("Rests on"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(self, "status", [("approved", _("Approved")), ("declined", _("Declined"))])
        self.fields["status"].initial = "approved"
        self.fields["decided_on"].initial = date.today()
        self.fields["note"].required = False
        self.helper.layout = Layout(
            Row(Column("status"), Column("decided_on"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("note"),
            Field("rests_on_document"),
        )
        _rests_on_field(self)
        if self.instance and self.instance.rests_on_document_id:
            self.fields["rests_on_document"].initial = self.instance.rests_on_document_id


class QuoteForm(ScopedForm):
    """A quote, as the supplier stated it.

    The last screen in this domain to be built by hand, and the most used. It
    was a 281-line template, a set of integer field names to coerce, and a
    POST handler that rebuilt the page from `request.POST` on a rejection so
    the typist did not lose their work. Django does every one of those things:
    a bound form re-renders with what was typed, a `ModelChoiceField` coerces
    and validates an id, and a `DecimalField` reports a bad number on the field
    it came from rather than as a sentence about a key name.

    `QuoteCorrectionForm` inherits the whole field set, so a quote's figures
    are declared once. What a correction may not change, it removes.

    **Figures are recorded AS STATED, not normalised.** `as_quoted_unit` says
    what the price is per, and the comparison converts. A screen that
    normalised on entry would be throwing away the only record of what the
    supplier actually wrote.
    """

    delivery_places = forms.CharField(
        label=_("Places this price covers"),
        required=False,
        widget=forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. Kano, Sokoto")}),
        help_text=_("As named on the tender, separated by commas. Leave empty when the tender has one place."),
    )

    class Meta:
        model = Quote
        fields = [
            "tender",
            "supplier",
            "commodity",
            "item",
            "as_quoted_amount",
            "as_quoted_unit",
            "as_quoted_currency",
            "quantity_basis",
            "quantity_basis_unit",
            "pack_spec_source",
            "base_per_pack_stated",
            "base_unit_grams_stated",
            "freight_basis",
            "freight_amount",
            "duties_basis",
            "duties_amount",
            "shelf_life_months_stated",
            "lead_time_days",
            "incoterm",
            "received_on",
            "delivery_mode",
            "pickup_location",
            "buyer_transport_amount",
        ]
        widgets = {
            "tender": forms.Select(attrs=SEARCHABLE),
            "supplier": forms.Select(attrs=SEARCHABLE),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "item": forms.Select(attrs=SEARCHABLE),
            "as_quoted_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "as_quoted_unit": forms.Select(attrs=SELECT),
            "as_quoted_currency": forms.TextInput(attrs={**INPUT, "placeholder": "USD", "maxlength": 3}),
            "quantity_basis": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "quantity_basis_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "pack_spec_source": forms.Select(attrs=SELECT),
            "base_per_pack_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "base_unit_grams_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "freight_basis": forms.Select(attrs=SELECT),
            "freight_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "duties_basis": forms.Select(attrs=SELECT),
            "duties_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "shelf_life_months_stated": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "lead_time_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "incoterm": forms.TextInput(attrs={**INPUT, "placeholder": "CIF"}),
            "received_on": forms.DateInput(attrs=DATE),
            "delivery_mode": forms.Select(attrs=SELECT),
            "pickup_location": forms.TextInput(
                attrs={**INPUT, "placeholder": _("e.g. their warehouse, Kano free zone")}
            ),
            "buyer_transport_amount": forms.NumberInput(attrs=MONEY_INPUT),
        }
        labels = {
            "tender": _("Against which tender"),
            "supplier": _("Who quoted"),
            "commodity": _("For what"),
            "item": _("Their trade item"),
            "as_quoted_amount": _("Price, as they stated it"),
            "as_quoted_unit": _("Per"),
            "as_quoted_currency": _("Currency"),
            "quantity_basis": _("For a quantity of"),
            "quantity_basis_unit": _("Unit"),
            "pack_spec_source": _("Where the pack size came from"),
            "base_per_pack_stated": _("Units per pack, as stated"),
            "base_unit_grams_stated": _("Grams per unit, as stated"),
            "freight_basis": _("Freight"),
            "freight_amount": _("Freight amount"),
            "duties_basis": _("Duties"),
            "duties_amount": _("Duties amount"),
            "shelf_life_months_stated": _("Shelf life stated (months)"),
            "lead_time_days": _("Lead time (days)"),
            "incoterm": _("Incoterm"),
            "received_on": _("Received on"),
            "delivery_mode": _("How the goods reach us"),
            "pickup_location": _("Collected from"),
            "buyer_transport_amount": _("Our own transport cost"),
        }
        help_texts = {
            "as_quoted_amount": _("As the supplier wrote it. Converting happens in the comparison, not here."),
            "as_quoted_unit": _("Without this a price cannot be compared with anything."),
            "item": _("Optional, and what fixes the pack size — two suppliers' cartons hold different numbers."),
            "pack_spec_source": _(
                "A pack size we assumed and one the supplier stated are different evidence, so a "
                "comparison that mixes them says which is which."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["tender"].queryset = self.tenders()
        self.fields["supplier"].queryset = self.scoped_to_programme(Supplier)
        self.fields["commodity"].queryset = self.scoped_to_programme(Commodity)
        self.fields["item"].queryset = self.scoped_to_programme(Item)
        self.fields["tender"].empty_label = _("Select a tender\u2026")
        self.fields["supplier"].empty_label = _("Select a supplier\u2026")
        self.fields["commodity"].empty_label = _("Select a product\u2026")
        self.fields["item"].empty_label = _("Not stated")

        basis = [("not_specified", _("Not specified")), ("included", _("Included")), ("excluded", _("Excluded"))]
        set_choices(self, "freight_basis", basis)
        set_choices(
            self,
            "delivery_mode",
            [("delivered", _("Delivered to our place")), ("pickup", _("We collect from them"))],
        )
        self.fields["delivery_mode"].required = False
        instance = getattr(self, "instance", None)
        if instance is not None and instance.pk and instance.delivery_point_keys:
            names = [
                (instance.tender.point(key) or {}).get("name") or (instance.tender.point(key) or {}).get("city") or key
                for key in instance.delivery_point_keys
            ]
            self.initial.setdefault("delivery_places", ", ".join(names))
        set_choices(self, "duties_basis", basis)
        set_choices(
            self,
            "as_quoted_unit",
            [
                ("", "\u2014"),
                ("per_base_unit", _("Per unit (sachet, tablet)")),
                ("per_pack", _("Per pack (carton)")),
                ("per_lot_total", _("Total for the lot")),
                ("per_metric_tonne", _("Per metric tonne")),
            ],
        )
        set_choices(
            self,
            "pack_spec_source",
            [
                ("not_stated", _("They did not say")),
                ("stated_on_quote", _("Stated on the quote")),
                ("trade_item_confirmed", _("Confirmed against the trade item")),
            ],
        )
        currency_select(self, "as_quoted_currency")
        self.helper.layout = self.build_layout()

    # ---- the two halves a correction reuses ----------------------------

    def tenders(self):
        program_id = getattr(self.access, "program_id", None) if self.access else None
        return Tender.objects.filter(program_id=program_id).order_by("-id") if program_id else Tender.objects.none()

    def scoped_to_programme(self, model):
        scope = getattr(self.access, "scope_key", None) if self.access else None
        if not scope:
            return model.objects.none()
        # A supplier's name is its organisation's; the model's own ordering
        # already sorts by it, and "name" is no longer a column to order on.
        found = model.objects.filter(scope_key=scope)
        return found if model is Supplier else found.order_by("name")

    def figures_layout(self):
        """Everything except who quoted, which a correction does not ask again."""
        return (
            Fieldset(
                str(_("What they quoted")),
                Row(
                    Column("as_quoted_amount"),
                    Column("as_quoted_unit"),
                    Column("as_quoted_currency"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                Row(
                    Column("quantity_basis"),
                    Column("quantity_basis_unit"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("How it is packed")),
                Row(
                    Column("pack_spec_source"),
                    Column("base_per_pack_stated"),
                    Column("base_unit_grams_stated"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("What else it includes")),
                Row(Column("freight_basis"), Column("freight_amount"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(Column("duties_basis"), Column("duties_amount"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(
                    Column("shelf_life_months_stated"),
                    Column("lead_time_days"),
                    Column("incoterm"),
                    Column("received_on"),
                    css_class="grid md:grid-cols-4 gap-x-6",
                ),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("Where the goods go")),
                Row(Column("delivery_mode"), Column("delivery_places"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(
                    Column("pickup_location"),
                    Column("buyer_transport_amount"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                css_class="pt-2",
            ),
        )

    def build_layout(self):
        return Layout(
            Fieldset(
                str(_("Whose quote this is")),
                Row(Column("tender"), Column("supplier"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(Column("commodity"), Column("item"), css_class="grid md:grid-cols-2 gap-x-6"),
                css_class="pt-1",
            ),
            *self.figures_layout(),
        )

    def clean_as_quoted_currency(self):
        return (self.cleaned_data.get("as_quoted_currency") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("as_quoted_amount") is not None and not cleaned.get("as_quoted_unit"):
            self.add_error("as_quoted_unit", _("A price has to say what it is per, or it compares with nothing."))
        item, commodity = cleaned.get("item"), cleaned.get("commodity")
        if item is not None and commodity is not None and item.commodity_id != commodity.pk:
            self.add_error(
                "item", _("That trade item is not a version of \u201c%(name)s\u201d.") % {"name": commodity.name}
            )
        self._match_places(cleaned)
        return cleaned

    def _match_places(self, cleaned):
        """Place names as typed, to the tender's place keys.

        A person reads places by name; a bid stores them by key, so a place
        renamed later is still the same place. Anything that names no place on
        the tender is refused with the list it could have been.
        """
        tender = cleaned.get("tender") or getattr(getattr(self, "instance", None), "tender", None)
        typed = [part.strip() for part in (cleaned.get("delivery_places") or "").split(",") if part.strip()]
        cleaned["delivery_point_keys"] = []
        if not typed or tender is None:
            return
        points = tender.delivery_points or []
        keys, unknown = [], []
        for name in typed:
            found = next(
                (
                    p
                    for p in points
                    if name.casefold() in {(p.get(f) or "").casefold() for f in ("key", "name", "city")}
                ),
                None,
            )
            (keys.append(found["key"]) if found else unknown.append(name))
        if unknown:
            offered = ", ".join(p.get("name") or p.get("city") for p in points) or _("none")
            self.add_error(
                "delivery_places",
                _("The tender has no place called %(names)s. Its places are: %(offered)s.")
                % {"names": ", ".join(unknown), "offered": offered},
            )
        cleaned["delivery_point_keys"] = keys

    def payload(self) -> dict:
        data = to_payload({k: v for k, v in self.cleaned_data.items() if k != "delivery_places"})
        data["delivery_point_keys"] = self.cleaned_data.get("delivery_point_keys") or []
        # The schema names the product by slug and the tender and supplier by
        # their own ids, which is what `to_payload` already produces for the
        # relations. Only the commodity needs saying differently.
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        return data


class QuoteCorrectionForm(QuoteForm):
    """A corrected version of a quote, superseding the one it came from.

    Inherits the field set rather than restating it, so the two screens can
    never drift into disagreeing about what a quote records.

    Not an edit. The original stays readable so a comparison run last month
    still reproduces the numbers it showed then \u2014 the whole reason quotes carry
    a version chain rather than being mutable rows.
    """

    reason = forms.CharField(
        label=_("What was wrong"),
        widget=forms.Textarea(
            attrs={**TEXTAREA, "placeholder": _("e.g. transcribed the per-carton price as per-sachet")}
        ),
        help_text=_("Kept on the new version. A chain of corrections is only worth having if each link says why."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Who quoted, against which tender, for what: not a correction. Changing
        # any of them makes a different quote, not a corrected one, and leaving
        # them editable invites exactly that.
        for name in ("tender", "supplier", "commodity", "item"):
            del self.fields[name]
        self.helper.layout = self.build_layout()

    def build_layout(self):
        return Layout(*self.figures_layout(), Field("reason"))

    def payload(self) -> dict:
        # `reason` is a top-level argument of `quote_correct`, describing the
        # correction rather than the offer, so the view lifts it out. Keeping
        # the split here would mean the form knowing the operation's shape.
        data = to_payload({k: v for k, v in self.cleaned_data.items() if k not in ("reason", "delivery_places")})
        data["delivery_point_keys"] = self.cleaned_data.get("delivery_point_keys") or []
        return data


TenderPlaceFormSet = forms.formset_factory(TenderPlaceForm, extra=1, min_num=0, can_delete=True)


# `extra=0`, not `extra=1`. A formset renders `max(initial, min_num) + extra`
# rows, so min_num=1 with extra=1 opened a new tender on TWO blank commodity
# rows -- one required, one not, and no way to tell which from looking. One
# row and an "add another" button is the same capability, said once.
TenderLineFormSet = forms.formset_factory(TenderLineForm, extra=0, min_num=1, validate_min=True, can_delete=True)
