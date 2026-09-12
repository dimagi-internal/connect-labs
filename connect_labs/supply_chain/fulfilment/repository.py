"""Reads and writes for the fulfilment tier: shipments, receipts, invoices,
payments and documents.

The tier that exists because the buyer of record may not be us. Two things
follow, and they shape every method here.

**Anyone can write.** A local partner records its own shipments, receipts,
invoices and documents through these same methods -- there is no
partner-only path, no shadow model and no reduced validation. What differs
between us and them is the `source` and `recorded_by_party` on the row, and
nothing else. A design where the partner emails a spreadsheet and we retype
it is the design this replaces.

**Evidence is a first-class object.** A document is either a file stored
through Django's configured storage (S3 on the deployment, the filesystem
locally) or a link to where it legitimately lives. Two derivations depend on
one existing at all: a claimed duty relief, and a batch's conformity.
"""

import base64
import hashlib
from datetime import UTC, datetime

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction

from connect_labs.supply_chain.models import Document, Invoice, Payment, Receipt, ReceiptLine, Shipment, ShipmentLine
from connect_labs.supply_chain.stock.services import posting

# Generous for a scanned certificate, small enough that a JSON body carrying
# one cannot exhaust the web tier. Anything larger belongs in Drive with an
# external_url pointing at it.
MAX_UPLOAD_BYTES = 12 * 1024 * 1024

_DOCUMENT_LINKS = ("contract", "shipment", "receipt", "invoice", "supply_point", "supplier")


class FulfilmentRepositoryMixin:
    # ---- shipments -------------------------------------------------------

    def list_shipments(self, contract_id=None, status=None):
        qs = Shipment.objects.filter(contract__program_id=self._require_program()).prefetch_related(
            "lines", "documents"
        )
        if contract_id is not None:
            qs = qs.filter(contract_id=contract_id)
        if status is not None:
            qs = qs.filter(status=status)
        return list(qs)

    def get_shipment(self, shipment_id):
        return (
            Shipment.objects.filter(contract__program_id=self._require_program(), pk=shipment_id)
            .prefetch_related("lines", "documents")
            .first()
        )

    def _require_contract(self, contract_id):
        found = self.get_contract(contract_id)
        if found is None:
            raise ValueError(f"contract {contract_id} does not exist in this programme")
        return found

    @transaction.atomic
    def record_shipment(self, data):
        """A dispatch notice, with its lines.

        Lines carry batch and expiry because that is where those facts first
        become knowable, and a receipt later matches against them. A shipment
        posts nothing to the ledger: goods in transit are not stock.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        contract = self._require_contract(data["contract_id"])
        shipment = Shipment.objects.create(contract=contract, **_columns(Shipment, data))
        for line in data.get("lines") or []:
            ShipmentLine.objects.create(
                shipment=shipment,
                item=self._resolve_item(line.get("item_id")),
                batch=line.get("batch") or "",
                expiry=line.get("expiry"),
                quantity=line["quantity"],
                quantity_unit=line["quantity_unit"],
            )
        return _fresh(shipment)

    def update_shipment(self, shipment_id, data):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        found = self.get_shipment(shipment_id)
        if found is None:
            raise ValueError(f"shipment {shipment_id} not found")
        for key, value in _columns(Shipment, data).items():
            setattr(found, key, value)
        found.save()
        return _fresh(found)

    # ---- receipts --------------------------------------------------------

    def list_receipts(self, contract_id=None, supply_point_id=None):
        qs = Receipt.objects.filter(supply_point__program_id=self._require_program()).prefetch_related(
            "lines", "documents"
        )
        if contract_id is not None:
            qs = qs.filter(contract_id=contract_id)
        if supply_point_id is not None:
            qs = qs.filter(supply_point_id=supply_point_id)
        return list(qs)

    def get_receipt(self, receipt_id):
        return (
            Receipt.objects.filter(supply_point__program_id=self._require_program(), pk=receipt_id)
            .prefetch_related("lines", "documents")
            .first()
        )

    @transaction.atomic
    def record_receipt(self, data):
        """A goods received note -- the event that brings stock into existence.

        Posts one movement per accepted line (see stock.services.posting).
        Rejected quantity stays on the line as a record of what was refused
        and never enters the ledger.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        contract = self._require_contract(data["contract_id"]) if data.get("contract_id") else None
        point = self._require_supply_point(data["supply_point_id"], "receiving supply point")
        shipment = None
        if data.get("shipment_id") is not None:
            shipment = self.get_shipment(data["shipment_id"])
            if shipment is None:
                raise ValueError(f"shipment {data['shipment_id']} does not exist in this programme")

        lines = data.get("lines") or []
        if not lines:
            raise ValueError("a receipt with no lines records nothing; give it at least one line")

        receipt = Receipt.objects.create(
            contract=contract, shipment=shipment, supply_point=point, **_columns(Receipt, data)
        )
        for line in lines:
            ReceiptLine.objects.create(
                receipt=receipt,
                item=self._resolve_item(line.get("item_id")),
                batch=line.get("batch") or "",
                expiry=line.get("expiry"),
                quantity_accepted=line["quantity_accepted"],
                quantity_rejected=line.get("quantity_rejected") or 0,
                rejection_reason=line.get("rejection_reason") or "",
                quantity_unit=line["quantity_unit"],
            )

        commodity = contract.commodity if contract else self._require_commodity(data["commodity_slug"])
        posting.post_receipt(receipt, commodity, self._require_program())

        if contract is not None:
            self._advance_contract_status(contract)
        return _fresh(receipt)

    def _advance_contract_status(self, contract):
        """Move a contract's status to match what has actually arrived.

        Derived from the receipts rather than set by hand, so the status
        cannot disagree with the goods. Left alone once closed or cancelled --
        those are decisions, not consequences.
        """
        from connect_labs.supply_chain.fulfilment.services.match import three_way_match

        if contract.status in ("closed", "cancelled"):
            return
        match = three_way_match(contract)
        mapping = {
            "part_received": "part_received",
            "fully_received": "received",
            "over_received": "received",
            "over_invoiced": "part_received",
        }
        new_status = mapping.get(match["status"])
        if new_status and new_status != contract.status:
            contract.status = new_status
            contract.save(update_fields=["status", "updated_at"])

    # ---- invoices and payments ------------------------------------------

    def list_invoices(self, contract_id=None, status=None):
        qs = Invoice.objects.filter(contract__program_id=self._require_program()).prefetch_related(
            "payments", "documents"
        )
        if contract_id is not None:
            qs = qs.filter(contract_id=contract_id)
        if status is not None:
            qs = qs.filter(status=status)
        return list(qs)

    def get_invoice(self, invoice_id):
        return (
            Invoice.objects.filter(contract__program_id=self._require_program(), pk=invoice_id)
            .prefetch_related("payments", "documents")
            .first()
        )

    def record_invoice(self, data):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        contract = self._require_contract(data["contract_id"])
        return _fresh(Invoice.objects.create(contract=contract, **_columns(Invoice, data)))

    def update_invoice(self, invoice_id, data):
        from connect_labs.supply_chain.data_access import _columns, _fresh

        found = self.get_invoice(invoice_id)
        if found is None:
            raise ValueError(f"invoice {invoice_id} not found")
        for key, value in _columns(Invoice, data).items():
            setattr(found, key, value)
        found.save()
        return _fresh(found)

    @transaction.atomic
    def record_payment(self, data):
        """A settlement against an invoice, and the invoice status that follows.

        The status is derived from what has been paid rather than asserted,
        so "paid" cannot be true of an invoice with money outstanding.
        """
        from django.db.models import Sum

        from connect_labs.supply_chain.data_access import _columns, _fresh

        invoice = self.get_invoice(data["invoice_id"])
        if invoice is None:
            raise ValueError(f"invoice {data['invoice_id']} does not exist in this programme")
        payment = Payment.objects.create(invoice=invoice, **_columns(Payment, data))

        settled = invoice.payments.aggregate(total=Sum("amount"))["total"] or 0
        if invoice.amount is not None:
            invoice.status = "paid" if settled >= invoice.amount else "part_paid"
            invoice.save(update_fields=["status", "updated_at"])
        return _fresh(payment)

    # ---- documents -------------------------------------------------------

    def list_documents(self, kind=None, **links):
        qs = Document.objects.filter(program_id=self._require_program())
        if kind is not None:
            qs = qs.filter(kind=kind)
        for name in _DOCUMENT_LINKS:
            value = links.get(f"{name}_id")
            if value is not None:
                qs = qs.filter(**{f"{name}_id": value})
        return list(qs)

    def get_document(self, document_id):
        return Document.objects.filter(program_id=self._require_program(), pk=document_id).first()

    def attach_document(self, data):
        """Store a file, or record a link to one that lives elsewhere.

        Both are legitimate. A certificate of analysis that arrived as an
        email attachment belongs here as a stored file; a customs file the
        partner keeps in Drive belongs here as a URL, because copying it
        would create a second version that drifts.

        The sha256 is computed for stored files so a later copy can be
        checked against the one the derivation was based on.
        """
        from connect_labs.supply_chain.data_access import _columns, _fresh

        content_base64 = data.get("content_base64")
        external_url = data.get("external_url")
        if not content_base64 and not external_url:
            raise ValueError(
                "a document needs either content_base64 (to store the file) or an "
                "external_url (to point at where it already lives)"
            )
        if content_base64 and external_url:
            raise ValueError(
                "give content_base64 or external_url, not both -- two locations for one "
                "document is two documents that can disagree"
            )

        fields = _columns(Document, data)
        fields.pop("storage_key", None)

        if content_base64:
            try:
                raw = base64.b64decode(content_base64, validate=True)
            except Exception as error:
                raise ValueError(f"content_base64 is not valid base64: {error}") from error
            if len(raw) > MAX_UPLOAD_BYTES:
                raise ValueError(
                    f"this file is {len(raw)} bytes, over the {MAX_UPLOAD_BYTES}-byte limit. "
                    "Put it somewhere durable and attach it with external_url instead."
                )
            filename = data.get("filename") or "document"
            key = f"supply/{self._require_program()}/{data['kind']}/{hashlib.sha256(raw).hexdigest()[:16]}-{filename}"
            fields["storage_key"] = default_storage.save(key, ContentFile(raw))
            fields["sha256"] = hashlib.sha256(raw).hexdigest()
            fields["size_bytes"] = len(raw)

        document = Document.objects.create(
            program_id=self._require_program(),
            uploaded_at=datetime.now(UTC),
            **fields,
            **{name: self._resolve_document_link(name, data.get(f"{name}_id")) for name in _DOCUMENT_LINKS},
        )
        return _fresh(document)

    def _resolve_document_link(self, name, value):
        if value is None:
            return None
        getters = {
            "contract": self.get_contract,
            "shipment": self.get_shipment,
            "receipt": self.get_receipt,
            "invoice": self.get_invoice,
            "supply_point": self.get_supply_point,
            "supplier": self.get_supplier,
        }
        found = getters[name](value)
        if found is None:
            raise ValueError(f"{name} {value} does not exist in this programme")
        return found

    def document_url(self, document_id):
        """Where to fetch a document from, stored or external."""
        document = self.get_document(document_id)
        if document is None:
            raise ValueError(f"document {document_id} not found")
        if document.external_url:
            return document.external_url
        return default_storage.url(document.storage_key)
