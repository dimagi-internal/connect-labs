"""The money chain in a browser: contracts, invoices, payments, documents.

Group 3a. Six operations that could previously only be reached over the API,
which meant an order could be *read* on labs and never recorded there.

**Where each screen hangs.** A contract is created from the orders board and
from an awarded round; everything else hangs off the order it belongs to,
because an invoice with no contract is an invoice against nothing and the URL
is where that relationship is stated. `fixed()` supplies the parent id from
the URL, so no screen offers a picker for a thing the page is already inside.
"""

from django.http import Http404
from django.urls import reverse

from connect_labs.supply_chain.api_views import _access
from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.fulfilment_forms import (
    ContractForm,
    DocumentForm,
    InvoiceForm,
    PaymentConfirmationForm,
    PaymentForm,
)
from connect_labs.supply_chain.models import Contract, Invoice


def _contract(request, contract_id):
    found = Contract.objects.filter(pk=contract_id, program_id=_access(request).program_id).first()
    if found is None:
        raise Http404(f"no contract {contract_id} in this programme")
    return found


def _invoice(request, invoice_id):
    """An invoice, reached through its contract's programme.

    `Invoice` has no `program_id` of its own — it belongs to a contract, and
    the contract belongs to a programme. Filtering through the relation is
    what stops an invoice id from another programme resolving here.
    """
    found = Invoice.objects.filter(pk=invoice_id, contract__program_id=_access(request).program_id).first()
    if found is None:
        raise Http404(f"no invoice {invoice_id} in this programme")
    return found


# ---- contracts ---------------------------------------------------------


class _ContractScreen(OperationFormView):
    form_class = ContractForm

    def breadcrumb(self, **kwargs):
        return [{"label": "Orders", "href": reverse("supply_chain:orders")}, {"label": self.title}]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:orders")

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[result["id"]])


class ContractCreateView(_ContractScreen):
    operation = "contract_create"
    title = "New order"
    intro = (
        "A contract is the commitment — separate from the award, because the organisation that "
        "decides is often not the one that buys. Who is buying decides the duty and the VAT, so "
        "it is asked rather than assumed."
    )
    submit_label = "Record order"

    def award(self):
        """The award this order is placed against, when it arrived from one.

        Scoped through the round's programme. The order then carries the
        award, which is what lets `contract_create` refuse it while an
        approval is pending or declined -- and say whose.
        """
        from connect_labs.supply_chain.models import Award

        raw = self.request.GET.get("award") or ""
        if not raw.isdigit():
            return None
        return (
            Award.objects.filter(pk=int(raw), round__program_id=_access(self.request).program_id)
            .select_related("quote", "supplier", "commodity")
            .first()
        )

    def get_initial(self):
        initial = super().get_initial()
        award = self.award()
        if award is not None:
            # Opening on what was awarded: the supplier, the product, the
            # trade item and the quantity and price that won.
            quote = award.quote
            initial.update(supplier=award.supplier_id, commodity=award.commodity_id)
            if quote.item_id:
                initial["item"] = quote.item_id
            if quote.quantity_basis is not None:
                initial.update(quantity=quote.quantity_basis, quantity_unit=quote.quantity_basis_unit)
            if quote.as_quoted_amount is not None and quote.as_quoted_unit:
                initial.update(
                    unit_price=quote.as_quoted_amount,
                    unit_price_unit=quote.as_quoted_unit,
                    currency=quote.as_quoted_currency,
                )
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        award = self.award() if context.get("has_program_context") else None
        if award is not None:
            context["intro"] = (
                f"Against the award to {award.supplier.name} for {award.commodity.name}, decided "
                f"{award.decided_on or 'undated'}. {self.intro}"
            )
        return context

    def fixed(self, **kwargs):
        award = self.award()
        if award is None:
            return {}
        return {"data": {"award_id": award.pk, "round_id": award.round_id}}


class ContractUpdateView(_ContractScreen):
    operation = "contract_update"
    title = "Edit order"
    submit_label = "Save changes"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = _contract(self.request, self.kwargs["contract_id"])
        return kwargs

    def fixed(self, **kwargs):
        return {"contract_id": int(kwargs["contract_id"])}

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


# ---- invoices ----------------------------------------------------------


class _UnderAContract(OperationFormView):
    """A screen for something that belongs to one order."""

    def contract(self):
        return _contract(self.request, self.kwargs["contract_id"])

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(self.contract()),
                "href": reverse("supply_chain:order_detail", args=[kwargs["contract_id"]]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[kwargs["contract_id"]])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.kwargs["contract_id"]])


class InvoiceRecordView(_UnderAContract):
    operation = "invoice_record"
    form_class = InvoiceForm
    title = "Record an invoice"
    intro = (
        "What the supplier billed — which is not what was committed and not what was paid. "
        "Give the quantity billed and the three-way match can run: ordered, arrived, billed."
    )
    submit_label = "Record invoice"

    def fixed(self, **kwargs):
        return {"data": {"contract_id": int(kwargs["contract_id"])}}


class InvoiceUpdateView(OperationFormView):
    operation = "invoice_update"
    form_class = InvoiceForm
    title = "Edit invoice"
    submit_label = "Save changes"
    footnote = "Set the status to query or reject it. Payments recorded against it are left alone."

    def invoice(self):
        return _invoice(self.request, self.kwargs["invoice_id"])

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["instance"] = self.invoice()
        return kwargs

    def fixed(self, **kwargs):
        # `contract_id` as well as the invoice's own id. `_INVOICE_DATA`
        # requires it on an update as well as a create -- data_access indexes
        # it with `[]` -- and the form does not show it, because moving an
        # invoice to a different contract is not an edit, it is a different
        # invoice. So it is re-sent from the row rather than asked for.
        return {
            "invoice_id": int(kwargs["invoice_id"]),
            "data": {"contract_id": self.invoice().contract_id},
        }

    def breadcrumb(self, **kwargs):
        invoice = self.invoice()
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(invoice.contract),
                "href": reverse("supply_chain:order_detail", args=[invoice.contract_id]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[self.invoice().contract_id])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.invoice().contract_id])


class PaymentRecordView(OperationFormView):
    operation = "payment_record"
    form_class = PaymentForm
    title = "Record a payment"
    intro = "A settlement against this invoice. The invoice's status follows from what has been paid."
    submit_label = "Record payment"

    def invoice(self):
        return _invoice(self.request, self.kwargs["invoice_id"])

    def get_initial(self):
        initial = super().get_initial()
        invoice = self.invoice()
        # Opening on what is outstanding, not on nothing: a part payment is
        # the exception and a full one is the common case.
        initial.setdefault("currency", invoice.currency)
        if invoice.amount is not None:
            initial.setdefault("amount", invoice.amount)
        return initial

    def fixed(self, **kwargs):
        return {"data": {"invoice_id": int(kwargs["invoice_id"])}}

    def breadcrumb(self, **kwargs):
        invoice = self.invoice()
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {
                "label": str(invoice.contract),
                "href": reverse("supply_chain:order_detail", args=[invoice.contract_id]),
            },
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[self.invoice().contract_id])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.invoice().contract_id])


class PaymentConfirmView(OperationFormView):
    """The payee says the money arrived. One date, and the check clears."""

    operation = "payment_confirm"
    form_class = PaymentConfirmationForm
    title = "Payee confirmed receipt"
    intro = (
        'When the supplier says the payment arrived. "We sent it" and "we got it" are two facts '
        "from two people, and until the second is recorded an older payment stays on the checks list."
    )
    submit_label = "Record confirmation"

    def payment(self):
        from connect_labs.supply_chain.models import Payment

        found = (
            Payment.objects.filter(
                pk=self.kwargs["payment_id"], invoice__contract__program_id=_access(self.request).program_id
            )
            .select_related("invoice__contract")
            .first()
        )
        if found is None:
            raise Http404(f"no payment {self.kwargs['payment_id']} in this programme")
        return found

    def fixed(self, **kwargs):
        return {"payment_id": int(kwargs["payment_id"])}

    def breadcrumb(self, **kwargs):
        contract = self.payment().invoice.contract
        return [
            {"label": "Orders", "href": reverse("supply_chain:orders")},
            {"label": str(contract), "href": reverse("supply_chain:order_detail", args=[contract.pk])},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return reverse("supply_chain:order_detail", args=[self.payment().invoice.contract_id])

    def redirect_to(self, result):
        return reverse("supply_chain:order_detail", args=[self.payment().invoice.contract_id])


# ---- documents ---------------------------------------------------------


class DocumentAttachView(_UnderAContract):
    """Evidence against one order.

    Contract-scoped rather than programme-wide, because the two derivations
    that turn on a document existing — a claimed duty relief and a batch's
    conformity — both ask about a particular contract. A programme-level
    document with no link is legitimate and is still reachable over the API;
    it is just not what anyone arrives at this screen wanting.
    """

    operation = "document_attach"
    form_class = DocumentForm
    title = "Attach a document"
    intro = (
        "Evidence for this order: a purchase order, an order confirmation, a duty exemption, a "
        "certificate of analysis. Upload the file or link to where it lives — one or the other."
    )
    submit_label = "Attach"
    footnote = (
        "Uploads are hashed, so a copy can later be checked against the one a derivation used. "
        "Over 12 MB, store it elsewhere and give a link."
    )

    def fixed(self, **kwargs):
        return {"data": {"contract_id": int(kwargs["contract_id"])}}
