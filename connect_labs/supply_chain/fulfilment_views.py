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
from connect_labs.supply_chain.fulfilment_forms import ContractForm, DocumentForm, InvoiceForm, PaymentForm
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
