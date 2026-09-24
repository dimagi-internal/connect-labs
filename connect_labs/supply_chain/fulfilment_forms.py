"""Write screens for the money chain: contracts, invoices, payments, documents.

Group 3a of the supply UI. Where the sourcing screens record what suppliers
*said*, these record what was actually committed, billed and settled — three
facts with three dates, which is why they are three tables rather than the one
`purchase_record` row they replaced.

**Everything here carries provenance**, so every form inherits
`ProvenancedForm` and asks "How do you know?". That is not ceremony: between
raising a purchase order and receiving the goods there are five consecutive
stages the programme may not witness, because the buyer of record may not be
the programme. A row that does not say how it was known would read as
first-hand.

**`buyer_of_record` has no default and the form does not invent one.** Import
duty and VAT depend on who imports, so a landed total computed without knowing
the buyer carries an invisible assumption — the exact failure this domain
exists to refuse. The field is required, and the picker for the organisation
beside it is required with it.

**A claimed duty relief is not a relief.** `duty_relief_claimed` without a
document attached makes the duty line derive as *Unconfirmed*, not as zero.
The screen says so where the tickbox is, rather than letting somebody tick it
and believe the number moved.
"""

from crispy_forms.layout import Column, Field, Fieldset, Layout, Row
from django import forms
from django.db.models import Case, Value, When
from django.utils.translation import gettext_lazy as _

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import records
from connect_labs.supply_chain.forms import (
    DATE,
    INPUT,
    MONEY_INPUT,
    SEARCHABLE,
    SELECT,
    ScopedForm,
    currency_select,
    set_choices,
    to_payload,
)
from connect_labs.supply_chain.models import (
    Commodity,
    Contract,
    Document,
    Invoice,
    Item,
    Payment,
    Supplier,
    SupplyPoint,
)
from connect_labs.supply_chain.network_forms import SOURCE_CHOICES

__all__ = ["ContractForm", "DocumentForm", "InvoiceForm", "PaymentConfirmationForm", "PaymentForm"]


class ProvenancedForm(ScopedForm):
    """A ModelForm for a record that says who put it here and how they knew.

    `source` is declared as a form field rather than left to
    `stamp_provenance`, because `call_operation` validates the payload BEFORE
    stamping it — so an operation whose schema requires `source` refuses a
    payload that omits it, however reliably the stamper would have filled it
    in. Asking is also the honest thing: for most of these records the answer
    genuinely is "a partner told us".
    """

    source = forms.ChoiceField(
        label=_("How do you know?"),
        choices=SOURCE_CHOICES,
        initial="we_recorded",
        widget=forms.Select(attrs=SELECT),
        help_text=_("Kept with the row. A record that does not say how it was known is weaker, not stronger."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and getattr(self.instance, "source", ""):
            self.fields["source"].initial = self.instance.source

    def scoped(self, model):
        """This programme's rows of `model`, or none at all without one."""
        scope = getattr(self.access, "scope_key", None) if self.access else None
        return model.objects.filter(scope_key=scope) if scope else model.objects.none()

    def in_program(self, model):
        """The programme-scoped tiers key on `program_id`, not `scope_key`."""
        program_id = getattr(self.access, "program_id", None) if self.access else None
        return model.objects.filter(program_id=program_id) if program_id else model.objects.none()


class ContractForm(ProvenancedForm):
    """The commitment. Who is buying, from whom, how much, and on what terms."""

    class Meta:
        model = Contract
        fields = [
            "supplier",
            "commodity",
            "item",
            "buyer_of_record",
            "buyer_org",
            "reference",
            "signed_on",
            "status",
            "consideration",
            "payment_terms",
            "currency",
            "quantity",
            "quantity_unit",
            "unit_price",
            "unit_price_unit",
            "freight_basis",
            "freight_amount",
            "duties_basis",
            "duties_amount",
            "vat_basis",
            "vat_amount",
            "duty_relief_claimed",
            "incoterm",
            "delivery_supply_point",
            "promised_lead_time_days",
            "covers_shortfall_of",
        ]
        widgets = {
            "supplier": forms.Select(attrs=SEARCHABLE),
            "commodity": forms.Select(attrs=SEARCHABLE),
            "item": forms.Select(attrs=SEARCHABLE),
            "buyer_of_record": forms.Select(attrs=SELECT),
            "buyer_org": forms.Select(attrs=SEARCHABLE),
            "reference": forms.TextInput(attrs={**INPUT, "placeholder": _("their PO number, or ours")}),
            "signed_on": forms.DateInput(attrs=DATE),
            "status": forms.Select(attrs=SELECT),
            "consideration": forms.Select(attrs=SELECT),
            "payment_terms": forms.Select(attrs=SELECT),
            "currency": forms.TextInput(attrs={**INPUT, "placeholder": "USD", "maxlength": 3}),
            "quantity": forms.NumberInput(attrs={**INPUT, "step": "any", "placeholder": "500"}),
            "quantity_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
            "unit_price": forms.NumberInput(attrs={**MONEY_INPUT, "placeholder": "52.42"}),
            "unit_price_unit": forms.Select(attrs=SELECT),
            "freight_basis": forms.Select(attrs=SELECT),
            "freight_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "duties_basis": forms.Select(attrs=SELECT),
            "duties_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "vat_basis": forms.Select(attrs=SELECT),
            "vat_amount": forms.NumberInput(attrs=MONEY_INPUT),
            "duty_relief_claimed": forms.CheckboxInput(attrs={"class": "simple-toggle"}),
            "incoterm": forms.TextInput(attrs={**INPUT, "placeholder": "CIF"}),
            "delivery_supply_point": forms.Select(attrs=SEARCHABLE),
            "promised_lead_time_days": forms.NumberInput(attrs={**INPUT, "min": 0}),
            "covers_shortfall_of": forms.Select(attrs=SEARCHABLE),
        }
        labels = {
            "supplier": _("Buying from"),
            "commodity": _("What"),
            "item": _("Which trade item"),
            "buyer_of_record": _("Who is buying"),
            "buyer_org": _("Which organisation"),
            "reference": _("Reference"),
            "signed_on": _("Signed on"),
            "status": _("Status"),
            "consideration": _("Paid for how"),
            "payment_terms": _("When it is paid"),
            "currency": _("Currency"),
            "quantity": _("Quantity"),
            "quantity_unit": _("Unit"),
            "unit_price": _("Unit price"),
            "unit_price_unit": _("Priced per"),
            "freight_basis": _("Freight"),
            "freight_amount": _("Freight amount"),
            "duties_basis": _("Duties"),
            "duties_amount": _("Duties amount"),
            "vat_basis": _("VAT"),
            "vat_amount": _("VAT amount"),
            "duty_relief_claimed": _("Duty relief claimed"),
            "incoterm": _("Incoterm"),
            "delivery_supply_point": _("Delivered to"),
            "promised_lead_time_days": _("Promised lead time (days)"),
            "covers_shortfall_of": _("Buys the shortfall on"),
        }
        help_texts = {
            "buyer_of_record": _(
                "No default, deliberately. Import duty and VAT depend on who imports, so a landed "
                "total worked out without this would have an invisible assumption inside it."
            ),
            "item": _("Optional. Set it once you know whose product it is — that is what fixes the pack size."),
            "duty_relief_claimed": _(
                "A claimed relief is not a relief. With no exemption document attached, the duty "
                "line derives as Unconfirmed rather than as zero."
            ),
            "currency": _("Three letters, ISO 4217."),
            "covers_shortfall_of": _(
                "When this order buys what another could not deliver — the main supplier sent 450 of 700 "
                "and a partner bought the rest locally. That order then reads as covered, by name."
            ),
            "payment_terms": _(
                "Paid in advance when the supplier is paid before it delivers — a distributor who buys "
                "from manufacturers for you. The order then reads what is still owed to you, not a bill "
                "ahead of the goods."
            ),
            "consideration": _(
                "Only a bought order has a price. A donation, or goods paid for out of a setup fee, "
                "still ships and is received — it just has no landed cost to find."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = self.scoped(Supplier).order_by("name")
        self.fields["commodity"].queryset = self.scoped(Commodity).order_by("name")
        self.fields["item"].queryset = self.scoped(Item).order_by("name")
        self.fields["delivery_supply_point"].queryset = self.in_program(SupplyPoint).order_by("name")
        # Organisations are labs-wide, so this one is deliberately unscoped -- but
        # the organisations already in this programme (running its stores,
        # supplying it, buying for it) come first. Labs-wide and alphabetical, the
        # partner a programme officer means was off the picker's first page and
        # had to be searched for (the IPTSc render).
        in_programme = (
            set(self.in_program(SupplyPoint).exclude(managed_by_org=None).values_list("managed_by_org_id", flat=True))
            | set(self.in_program(Contract).exclude(buyer_org=None).values_list("buyer_org_id", flat=True))
            | set(self.scoped(Supplier).exclude(org=None).values_list("org_id", flat=True))
        )
        self.fields["buyer_org"].queryset = LabsOrg.objects.annotate(
            _in_programme=Case(When(pk__in=in_programme, then=Value(0)), default=Value(1))
        ).order_by("_in_programme", "name")

        self.fields["supplier"].empty_label = _("Select a supplier…")
        self.fields["commodity"].empty_label = _("Select a product…")
        self.fields["item"].empty_label = _("Not decided yet")
        self.fields["buyer_org"].empty_label = _("Select an organisation…")
        self.fields["delivery_supply_point"].empty_label = _("Not recorded")
        orders = self.in_program(Contract).select_related("supplier").order_by("-created_at")
        if self.instance and self.instance.pk:
            orders = orders.exclude(pk=self.instance.pk)
        self.fields["covers_shortfall_of"].queryset = orders
        self.fields["covers_shortfall_of"].empty_label = _("No — its own order")

        # The operation requires both halves of the buyer, so the form does.
        self.fields["buyer_of_record"].required = True
        self.fields["buyer_org"].required = True
        set_choices(
            self,
            "buyer_of_record",
            [
                ("", "—"),
                ("programme_org", _("We are")),
                ("partner_org", _("A partner is")),
                ("agency", _("A procurement agency is")),
            ],
        )
        self.fields["unit_price_unit"].required = False
        set_choices(
            self,
            "unit_price_unit",
            [
                ("", "—"),
                ("per_base_unit", _("Per unit (sachet, tablet)")),
                ("per_pack", _("Per pack (carton)")),
                ("per_lot_total", _("Total for the lot")),
                ("per_metric_tonne", _("Per metric tonne")),
            ],
        )
        basis = [(value, str(value).replace("_", " ").capitalize()) for value in records.BASIS]
        for name in ("freight_basis", "duties_basis", "vat_basis"):
            set_choices(self, name, basis)
        # Required for a bought order only -- enforced in clean(), which knows
        # how the goods were paid for.
        for name in self.PRICED_ONLY:
            self.fields[name].required = False
        set_choices(
            self,
            "status",
            [(value, str(value).replace("_", " ").capitalize()) for value in records.CONTRACT_STATUSES],
        )
        set_choices(
            self,
            "consideration",
            [
                ("priced", _("Bought — we pay a price")),
                ("in_kind", _("In kind — donated, nobody pays")),
                ("bundled", _("Bundled — paid out of another cost, such as a setup fee")),
            ],
            # Not required: a post that omits it has not said the goods are
            # donated, and the model's own default -- priced -- then applies.
            required=False,
        )
        set_choices(
            self,
            "payment_terms",
            [
                ("on_delivery", _("On delivery — we pay for what arrived")),
                ("advance", _("Paid in advance — before the goods arrive")),
            ],
            required=False,
        )

        currency_select(self, "currency")
        self.helper.layout = Layout(
            Row(Column("supplier"), Column("commodity"), Column("item"), css_class="grid md:grid-cols-3 gap-x-6"),
            Fieldset(
                str(_("Who is buying")),
                Row(Column("buyer_of_record"), Column("buyer_org"), css_class="grid md:grid-cols-2 gap-x-6"),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("The commitment")),
                Row(
                    Column("reference"),
                    Column("signed_on"),
                    Column("status"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                Row(Column("consideration"), Column("payment_terms"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(
                    Column("quantity"),
                    Column("quantity_unit"),
                    Column("unit_price"),
                    Column("unit_price_unit"),
                    css_class="grid md:grid-cols-4 gap-x-6",
                ),
                Field("currency"),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("What else it costs to land")),
                Row(
                    Column("freight_basis"),
                    Column("freight_amount"),
                    css_class="grid md:grid-cols-2 gap-x-6",
                ),
                Row(Column("duties_basis"), Column("duties_amount"), css_class="grid md:grid-cols-2 gap-x-6"),
                Row(Column("vat_basis"), Column("vat_amount"), css_class="grid md:grid-cols-2 gap-x-6"),
                Field("duty_relief_claimed"),
                css_class="pt-2",
            ),
            Fieldset(
                str(_("Delivery")),
                Row(
                    Column("incoterm"),
                    Column("delivery_supply_point"),
                    Column("promised_lead_time_days"),
                    css_class="grid md:grid-cols-3 gap-x-6",
                ),
                Field("covers_shortfall_of"),
                css_class="pt-2",
            ),
            Field("source"),
        )

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()

    # Asked of a bought order only. A donation or a purchase paid out of a
    # setup fee has no currency to price it in and nobody paying to land it,
    # and requiring the four made a partner's local purchase unrecordable
    # without inventing answers (the IPTSc walkthrough).
    PRICED_ONLY = ("currency", "freight_basis", "duties_basis", "vat_basis")

    def clean(self):
        cleaned = super().clean()
        priced = cleaned.get("consideration") in (None, "", "priced")
        if priced:
            for name in self.PRICED_ONLY:
                if not cleaned.get(name) and name not in self.errors:
                    self.add_error(name, _("This field is required."))
        if not priced and cleaned.get("unit_price") is not None:
            # Said on the field rather than left to the operation's refusal,
            # which would land as a banner over twenty fields.
            self.add_error("unit_price", _("Goods that are not bought have no unit price."))
        if cleaned.get("unit_price") is not None and not cleaned.get("unit_price_unit"):
            # A price with no basis cannot be compared with anything, and the
            # comparison is what the whole tier exists for.
            self.add_error("unit_price_unit", _("A price has to say what it is per."))
        item = cleaned.get("item")
        commodity = cleaned.get("commodity")
        if item is not None and commodity is not None and item.commodity_id != commodity.pk:
            self.add_error("item", _("That trade item is not a version of “%(name)s”.") % {"name": commodity.name})
        return cleaned

    def payload(self) -> dict:
        data = to_payload(self.cleaned_data)
        # The schema names the product by slug, and the two relations by their
        # own keys rather than Django's.
        commodity = self.cleaned_data.get("commodity")
        if commodity is not None:
            data["commodity_slug"] = commodity.slug
        data.pop("commodity_id", None)
        if self.cleaned_data.get("covers_shortfall_of") is not None:
            data["covers_shortfall_of_id"] = self.cleaned_data["covers_shortfall_of"].pk
        elif self.instance and self.instance.covers_shortfall_of_id:
            # Un-naming it is an edit, and `to_payload` would drop the None
            # that says so -- leaving the old short order named for ever.
            data["covers_shortfall_of_id"] = None
        data.pop("covers_shortfall_of", None)
        if "delivery_supply_point_id" not in data and self.cleaned_data.get("delivery_supply_point"):
            data["delivery_supply_point_id"] = self.cleaned_data["delivery_supply_point"].pk
        # Nothing here for `duty_relief_claimed`. A cleared checkbox cleans to
        # False, and `to_payload` drops only None and "" -- so False survives
        # and un-claiming a relief reaches the operation as False rather than
        # as an omission `update_contract` would skip. That is load bearing and
        # invisible, so TestThePayloadBoundary asserts it directly; a
        # `to_payload` "simplified" to `if not value` would break it silently.
        return data


class InvoiceForm(ProvenancedForm):
    """What the supplier billed. Not what was committed, and not what was paid."""

    class Meta:
        model = Invoice
        fields = ["reference", "issued_on", "status", "currency", "amount", "quantity_billed", "quantity_unit"]
        widgets = {
            "reference": forms.TextInput(attrs={**INPUT, "placeholder": _("their invoice number")}),
            "issued_on": forms.DateInput(attrs=DATE),
            "status": forms.Select(attrs=SELECT),
            "currency": forms.TextInput(attrs={**INPUT, "placeholder": "USD", "maxlength": 3}),
            "amount": forms.NumberInput(attrs={**MONEY_INPUT, "placeholder": "26210.00"}),
            "quantity_billed": forms.NumberInput(attrs={**INPUT, "step": "any"}),
            "quantity_unit": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. carton")}),
        }
        labels = {
            "reference": _("Invoice number"),
            "issued_on": _("Issued on"),
            "status": _("Status"),
            "currency": _("Currency"),
            "amount": _("Amount"),
            "quantity_billed": _("Quantity billed"),
            "quantity_unit": _("Unit"),
        }
        help_texts = {
            "quantity_billed": _(
                "Give this and the three-way match can run: what was ordered, what arrived, "
                "what was billed. Without it the match cannot be done at all."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        set_choices(
            self,
            "status",
            [(value, str(value).replace("_", " ").capitalize()) for value in records.INVOICE_STATUSES],
        )
        currency_select(self, "currency")
        self.helper.layout = Layout(
            Row(Column("reference"), Column("issued_on"), Column("status"), css_class="grid md:grid-cols-3 gap-x-6"),
            Row(Column("amount"), Column("currency"), css_class="grid md:grid-cols-2 gap-x-6"),
            Row(Column("quantity_billed"), Column("quantity_unit"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("source"),
        )

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()


class PaymentForm(ProvenancedForm):
    """A settlement against an invoice. The invoice's status follows from it."""

    class Meta:
        model = Payment
        fields = ["paid_on", "amount", "currency", "method", "reference"]
        widgets = {
            "paid_on": forms.DateInput(attrs=DATE),
            "amount": forms.NumberInput(attrs={**MONEY_INPUT, "placeholder": "26210.00"}),
            "currency": forms.TextInput(attrs={**INPUT, "placeholder": "USD", "maxlength": 3}),
            "method": forms.TextInput(attrs={**INPUT, "placeholder": _("e.g. bank transfer")}),
            "reference": forms.TextInput(attrs=INPUT),
        }
        labels = {
            "paid_on": _("Paid on"),
            "amount": _("Amount"),
            "currency": _("Currency"),
            "method": _("How"),
            "reference": _("Reference"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        currency_select(self, "currency")
        self.helper.layout = Layout(
            Row(Column("paid_on"), Column("amount"), Column("currency"), css_class="grid md:grid-cols-3 gap-x-6"),
            Row(Column("method"), Column("reference"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("source"),
        )

    def clean_currency(self):
        return (self.cleaned_data.get("currency") or "").strip().upper()


class PaymentConfirmationForm(ScopedForm):
    """The payee's word that the money arrived, and when they said so."""

    class Meta:
        model = Payment
        fields = ["confirmed_by_payee_on"]
        widgets = {"confirmed_by_payee_on": forms.DateInput(attrs=DATE)}
        labels = {"confirmed_by_payee_on": _("Confirmed received on")}
        help_texts = {
            "confirmed_by_payee_on": _(
                "The date the payee confirmed it — not the date we paid, which is already on file."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from datetime import date

        self.fields["confirmed_by_payee_on"].required = True
        self.fields["confirmed_by_payee_on"].initial = date.today()
        self.helper.layout = Layout(Field("confirmed_by_payee_on"))

    def payload(self) -> dict:
        return {"confirmed_on": self.cleaned_data["confirmed_by_payee_on"].isoformat()}


class DocumentForm(ProvenancedForm):
    """Evidence: a file, or a link to where it legitimately lives.

    Exactly one of the two. A document that is neither is a row claiming to be
    evidence of something nobody can look at, and a document that is both is
    two documents that can later disagree.

    That rule is `attach_document`'s, not this form's. There was a copy here;
    mutation testing removed it and nothing went red, because the repository
    refuses both cases with a better-worded message than the copy had. One
    rule, in the one write path every surface goes through.
    """

    upload = forms.FileField(
        label=_("The file"),
        required=False,
        # A styled button and the chosen file's name, not the browser's bare
        # control. These exact utilities already ship in the Tailwind build
        # (the admin upload pages use them), so no rebuild is needed.
        widget=forms.ClearableFileInput(
            attrs={
                "class": (
                    "block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded "
                    "file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 "
                    "hover:file:bg-blue-100"
                )
            }
        ),
        help_text=_("Stored here. Use this for anything that has no other home."),
    )

    class Meta:
        model = Document
        fields = ["kind", "title", "external_url"]
        widgets = {
            "kind": forms.Select(attrs=SEARCHABLE),
            "title": forms.TextInput(attrs=INPUT),
            "external_url": forms.TextInput(attrs={**INPUT, "placeholder": "https://…"}),
        }
        labels = {
            "kind": _("What it is"),
            "title": _("Title"),
            "external_url": _("…or a link to it"),
        }
        help_texts = {
            "external_url": _("Use this when the document already lives somewhere it should stay."),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # `document` is offered here and nowhere else, and is the default:
        # attaching a document IS documentary evidence, which is the one place
        # that claim is the plain truth rather than a boast. It is a witnessed
        # source, so `stamp_provenance` still refuses it from a partner acting
        # for somebody else -- the screen offers it, the domain decides.
        set_choices(self, "source", [("document", _("The document itself is the evidence"))] + SOURCE_CHOICES)
        self.fields["source"].initial = "document"
        set_choices(
            self,
            "kind",
            [("", "—")] + [(value, str(value).replace("_", " ").capitalize()) for value in records.DOCUMENT_KINDS],
        )
        self.helper.layout = Layout(
            Row(Column("kind"), Column("title"), css_class="grid md:grid-cols-2 gap-x-6"),
            Field("upload"),
            Field("external_url"),
            Field("source"),
        )

    def payload(self) -> dict:
        import base64

        data = to_payload({k: v for k, v in self.cleaned_data.items() if k != "upload"})
        upload = self.cleaned_data.get("upload")
        if upload is not None:
            # The operation takes the bytes, not a Django file: it is the same
            # operation whether the file arrived from a browser, a script or an
            # agent, and only one of those has an `UploadedFile`.
            data["filename"] = upload.name
            data["content_type"] = upload.content_type or "application/octet-stream"
            data["content_base64"] = base64.b64encode(upload.read()).decode()
        return data
