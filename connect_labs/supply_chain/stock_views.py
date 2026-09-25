"""The physical chain in a browser: shipments, receipts, movements, counts.

Group 3b, and the last of the write screens. Every operation the domain
exposes now has one.

**Where the line formsets go.** A receipt and a shipment both carry several
batches, each with its own expiry, so both use `BatchLineFormSet` and both
build their `lines` in `form_valid` the way `_TenderScreen` does. The receipt's
lines additionally carry what was refused; a dispatch has nothing to refuse
yet, so those two fields are removed rather than shown and ignored.

**There is deliberately no screen for editing a movement.** The ledger is
append-only — a correction is another movement naming its cause — and offering
an edit form would be offering to make a past balance irreproducible.
"""

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.fulfilment_forms import DocumentForm
from connect_labs.supply_chain.fulfilment_views import _contract
from connect_labs.supply_chain.models import Item, Shipment
from connect_labs.supply_chain.stock_forms import (
    BatchLineFormSet,
    ChargeForm,
    MovementForm,
    ReceiptForm,
    RequiredDocumentForm,
    ShipmentForm,
    ShipmentStatusForm,
    StockCountForm,
)


def _shipment(request, shipment_id):
    """A shipment, reached through its contract's programme.

    Like `Invoice`, a `Shipment` carries no `program_id` of its own — it
    belongs to a contract, and the contract belongs to a programme.
    """
    found = Shipment.objects.filter(pk=shipment_id, contract__program_id=_access(request).program_id).first()
    if found is None:
        raise Http404(f"no shipment {shipment_id} in this programme")
    return found


class _WithBatchLines(OperationFormView):
    """A screen whose record carries one row per batch."""

    show_rejection = True
    lines_context = "lines"

    def items(self):
        return Item.objects.filter(scope_key=_access(self.request).scope_key).order_by("name")

    def line_formset(self, data=None):
        return BatchLineFormSet(
            data,
            prefix="lines",
            form_kwargs={"items": self.items(), "show_rejection": self.show_rejection},
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context") and self.lines_context not in context:
            context[self.lines_context] = self.line_formset(
                self.request.POST if self.request.method == "POST" else None
            )
        return context

    def line_payload(self, row):
        raise NotImplementedError

    def form_valid(self, form):
        lines = self.line_formset(self.request.POST)
        if not lines.is_valid():
            return self.render_to_response(self.get_context_data(form=form, **{self.lines_context: lines}))

        kept = [
            self.line_payload(row)
            for row in lines.cleaned_data
            if row and not row.get("DELETE") and row.get("quantity") is not None
        ]
        if not kept:
            form.add_error(None, self.empty_message)
            return self.render_to_response(self.get_context_data(form=form, **{self.lines_context: lines}))

        self._lines = kept
        return super().form_valid(form)


# ---- shipments ---------------------------------------------------------


class ShipmentRecordView(_WithBatchLines):
    operation = "shipment_record"
    form_class = ShipmentForm
    template_name = "supply_chain/batch_lines_form.html"
    show_rejection = False
    title = "Record a dispatch"
    intro = (
        "What left, and in which batches. Dispatched and not yet received is never counted as "
        "stock on hand — a network that counted it would be reporting holdings it does not have."
    )
    submit_label = "Record dispatch"
    empty_message = "A dispatch has to say what was in it."

    def line_payload(self, row):
        payload = {"quantity": str(row["quantity"]), "quantity_unit": row["quantity_unit"]}
        if row.get("item"):
            payload["item_id"] = row["item"].pk
        if row.get("batch"):
            payload["batch"] = row["batch"]
        if row.get("expiry"):
            payload["expiry"] = row["expiry"].isoformat()
        return payload

    def fixed(self, **kwargs):
        return {"data": {"contract_id": int(kwargs["contract_id"]), "lines": getattr(self, "_lines", [])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(_contract(self.request, kwargs["contract_id"])),
                "href": reverse("supply_chain:order_detail", args=[kwargs["contract_id"]]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[kwargs["contract_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.kwargs["contract_id"]])


class ShipmentStatusView(OperationFormView):
    operation = "shipment_update"
    form_class = ShipmentStatusForm
    title = "Move a consignment along"
    intro = (
        "Where it has got to. Only the status and the expected date — asking for the whole "
        "dispatch again is how a carrier gets wiped by somebody recording that it cleared customs."
    )
    submit_label = "Save"

    def shipment(self):
        return _shipment(self.request, self.kwargs["shipment_id"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.shipment()
        return kwargs

    def fixed(self, **kwargs):
        # `contract_id` is required by `_SHIPMENT_DATA` on an update as well as
        # a create, and is not shown: moving a dispatch to another contract is
        # not a status change.
        return {
            "shipment_id": int(kwargs["shipment_id"]),
            "data": {"contract_id": self.shipment().contract_id},
        }

    def breadcrumb(self, **kwargs):
        shipment = self.shipment()
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(shipment.contract),
                "href": reverse("supply_chain:order_detail", args=[shipment.contract_id]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[self.shipment().contract_id])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.shipment().contract_id])


class _UnderAShipment(OperationFormView):
    """A screen for something that belongs to one consignment."""

    def shipment(self):
        if not hasattr(self, "_shipment"):
            self._shipment = _shipment(self.request, self.kwargs["shipment_id"])
        return self._shipment

    def breadcrumb(self, **kwargs):
        shipment = self.shipment()
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(shipment.contract),
                "href": reverse("supply_chain:order_detail", args=[shipment.contract_id]),
            },
            {
                "label": f"Shipment {shipment.reference or shipment.pk}",
                "href": reverse("supply_chain:shipment_detail", args=[shipment.pk]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:shipment_detail", args=[self.kwargs["shipment_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:shipment_detail", args=[self.kwargs["shipment_id"]])


class ShipmentRequireDocumentView(_UnderAShipment):
    """Add a document the consignment needs to clear, and who owes it."""

    operation = "shipment_update"
    form_class = RequiredDocumentForm
    title = "Require a document"
    intro = (
        "What this consignment needs before it can clear — an airway bill, a packing list, a "
        "product registration — and who has to produce it. Until one is attached to the shipment "
        "it stays on the checks list, naming who owes it."
    )
    submit_label = "Add to the list"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.shipment()
        return kwargs

    def fixed(self, **kwargs):
        # `contract_id` rides along because the shipment schema requires it on
        # an update; it is not asked, because it is not being changed.
        return {"shipment_id": int(kwargs["shipment_id"]), "data": {"contract_id": self.shipment().contract_id}}


class ShipmentRequirementRemoveView(OperationActionView):
    """Take one document off what a consignment needs. A button, not a page.

    Removing is an edit of the list and nothing else, so it records nothing
    about provenance beyond what the shipment already says.
    """

    operation = "shipment_update"
    success_message = "Removed from what this consignment needs."

    def fixed(self, **kwargs):
        shipment = _shipment(self.request, kwargs["shipment_id"])
        kind = self.request.POST.get("kind")
        return {
            "shipment_id": shipment.pk,
            "data": {
                "contract_id": shipment.contract_id,
                "source": shipment.source,
                "required_documents": [e for e in shipment.required_documents or [] if e.get("kind") != kind],
            },
        }

    def redirect_to(self, **kwargs):
        return reverse("supply_chain:shipment_detail", args=[kwargs["shipment_id"]])


class ShipmentDocumentAttachView(_UnderAShipment):
    """Evidence for one consignment -- most often one of the documents it needs."""

    operation = "document_attach"
    form_class = DocumentForm
    title = "Attach a document to this shipment"
    intro = (
        "An airway bill, a packing list, a customs declaration — attached here it satisfies the "
        "matching line on this consignment's list. Upload the file or link to where it lives."
    )
    submit_label = "Attach"

    def get_initial(self):
        initial = super().get_initial()
        # Arriving from an outstanding line on the checklist, the kind is the answer.
        kind = self.request.GET.get("kind")
        if kind:
            initial["kind"] = kind
        return initial

    def checklist_line(self):
        """The required-documents line this attach fills, when it came from one."""
        kind = self.request.GET.get("kind") or self.request.POST.get("kind")
        if not kind:
            return None
        return next((e for e in self.shipment().required_documents or [] if e.get("kind") == kind), None)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        line = self.checklist_line()
        if line is not None:
            from connect_labs.labs.models import LabsOrg
            from connect_labs.supply_chain.templatetags.supply_chain_extras import words

            shipment = self.shipment()
            owed_by = LabsOrg.objects.filter(pk=line.get("owed_by_org_id")).values_list("name", flat=True).first()
            context["intro"] = (
                f"This fills the “{words(line['kind']).capitalize()}” line on the checklist for shipment "
                f"{shipment.reference or shipment.pk}"
                + (f", owed by {owed_by}" if owed_by else "")
                + ". Upload the file or link to where it lives."
            )
        return context

    def fixed(self, **kwargs):
        return {"data": {"shipment_id": int(kwargs["shipment_id"])}}


class ChargeRecordView(_UnderAShipment):
    operation = "charge_record"
    form_class = ChargeForm
    title = "Record a charge"
    intro = (
        "What it cost to land this consignment, paid to somebody who is not the supplier: customs, "
        "a clearing agent, the lorry from the port. It is added to the order's landed cost, one line each."
    )
    submit_label = "Record charge"

    def fixed(self, **kwargs):
        return {"data": {"shipment_id": int(kwargs["shipment_id"])}}


# ---- receipts ----------------------------------------------------------


class ReceiptRecordView(_WithBatchLines):
    operation = "receipt_record"
    form_class = ReceiptForm
    template_name = "supply_chain/batch_lines_form.html"
    title = "Record a receipt"
    intro = (
        "The goods received note — the only event that brings stock into existence. What arrived, "
        "in which batches, and what was refused."
    )
    submit_label = "Record receipt"
    footnote = (
        "Zero accepted is a real receipt: the consignment arrived and was refused in full. "
        "Recording it is what stops the order reading as outstanding forever."
    )
    empty_message = "A receipt has to say what arrived."

    def line_payload(self, row):
        payload = {
            "quantity_accepted": str(row["quantity"]),
            "quantity_unit": row["quantity_unit"],
        }
        if row.get("item"):
            payload["item_id"] = row["item"].pk
        if row.get("batch"):
            payload["batch"] = row["batch"]
        if row.get("expiry"):
            payload["expiry"] = row["expiry"].isoformat()
        if row.get("quantity_rejected") is not None:
            payload["quantity_rejected"] = str(row["quantity_rejected"])
        if row.get("rejection_reason"):
            payload["rejection_reason"] = row["rejection_reason"]
        return payload

    def fixed(self, **kwargs):
        return {"data": {"contract_id": int(kwargs["contract_id"]), "lines": getattr(self, "_lines", [])}}

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(_contract(self.request, kwargs["contract_id"])),
                "href": reverse("supply_chain:order_detail", args=[kwargs["contract_id"]]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[kwargs["contract_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.kwargs["contract_id"]])


# ---- the ledger and what is reported over it ---------------------------


class MovementRecordView(OperationFormView):
    operation = "movement_record"
    form_class = MovementForm
    title = "Record stock in or out"
    intro = (
        "One line of the ledger: a transfer between stores, a loss, an expiry, or the adjustment "
        "that carries a stock count's variance."
    )
    submit_label = "Post it"
    footnote = (
        "The ledger is append-only. There is no way to edit this afterwards, and that is what makes "
        "a balance on any past date reproducible — a correction is another movement naming its cause."
    )

    def breadcrumb(self, **kwargs):
        return [{"label": "Stock", "href": reverse("supply_chain:stock")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:stock")

    def redirect_to(self, result):
        return reverse("supply_chain:stock")


class StockCountRecordView(OperationFormView):
    operation = "stock_count_record"
    form_class = StockCountForm
    title = "Record a count"
    # The intro said a count never replaces the ledger, above a form whose
    # third option is "Replace the ledger with this count" (the CHC render).
    intro = (
        "What somebody says is actually there. Usually kept beside the ledger, so any difference shows "
        "up; recorded as an override, it replaces the ledger's figure — for a count you trust over the "
        "records, with the reason in the notes."
    )
    submit_label = "Record count"
    footnote = (
        "An override writes the difference into the ledger as an adjustment, so the two agree afterwards "
        "and the adjustment says why. The other two kinds move nothing."
    )

    template_name = "supply_chain/stock_count_form.html"

    def breadcrumb(self, **kwargs):
        return [{"label": "Stock", "href": reverse("supply_chain:stock")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:stock")

    def redirect_to(self, result):
        return reverse("supply_chain:stock")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if context.get("has_program_context"):
            context["ledger_balances"] = ledger_balances(_access(self.request).program_id)
        return context


def ledger_balances(program_id) -> dict:
    """{"<point id>:<item id>": {...}} -- what the ledger holds, for the count form.

    A count is recorded against a balance the person counting cannot see: the
    stock take that "comes in short" came in short of a figure the form never
    showed, and an override replaced it without saying what it replaced. So
    the form is handed the balance of every point and item that has moved,
    and says it beside "Found" as they are chosen. The key "<point id>:" is the
    point's balance when it has only ever held one item (ledger.sole_item),
    which is what a count without a trade item is compared against.

    Figures only. Nothing here suggests a count; a count is what somebody
    found, and showing them the ledger is not asking them to agree with it.
    """
    from connect_labs.supply_chain.models import Movement, SupplyPoint, scope_key
    from connect_labs.supply_chain.stock.services import ledger
    from connect_labs.supply_chain.values import Quantity, quantity_digits, unit_noun

    moved = Movement.objects.for_program(program_id)
    pairs = set(moved.exclude(to_supply_point=None).values_list("to_supply_point_id", "item_id"))
    pairs |= set(moved.exclude(from_supply_point=None).values_list("from_supply_point_id", "item_id"))
    # Both re-state the scope rather than trusting that `pairs` came from a
    # programme-filtered queryset. It did -- but that is a fact about six
    # lines above, not about these two, and a read keyed only by id is one
    # refactor away from reading another programme's rows.
    points = SupplyPoint.objects.filter(program_id=program_id).in_bulk({point for point, _ in pairs})
    items = (
        Item.objects.filter(scope_key=scope_key(program_id=program_id))
        .select_related("commodity")
        .in_bulk({item for _, item in pairs if item})
    )

    def described(figure):
        if not isinstance(figure, Quantity):
            return {"known": False, "reasons": list(getattr(figure, "reasons", ()))}
        return {
            "known": True,
            "amount": str(figure.amount),
            "unit": unit_noun(figure.unit),
            "plural": unit_noun(figure.unit, 2),
            "text": f"{quantity_digits(figure.amount)} {unit_noun(figure.unit, figure.amount)}".strip(),
        }

    balances = {}
    for point_id, item_id in pairs:
        point = points.get(point_id)
        if point is None:
            continue
        item = items.get(item_id) if item_id else None
        if item is not None:
            balances[f"{point_id}:{item_id}"] = described(ledger.balance(program_id, point, item=item))
    for point in points.values():
        balances[f"{point.pk}:"] = described(
            ledger.balance(program_id, point, item=ledger.sole_item(program_id, point))
        )
    return balances
