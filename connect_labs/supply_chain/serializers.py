"""Model -> wire dict, in one place.

The API and the MCP server both return whatever an operation handler returns,
so these functions ARE the published contract. They exist as their own module
for two reasons.

First, the wire shape must not change just because the storage did. The
domain's records used to be `LabsRecord`s whose `data` blob was returned
verbatim; now they are tables. Every key an operation used to emit is emitted
here, so a client written against the old shape still works.

Second, `Decimal` and `date` are not JSON. Numbers go out as strings, because
a float is how a monetary amount silently loses the precision the whole
pricing design refuses to lose -- the same reason the input schemas refuse to
accept one.
"""

from connect_labs.supply_chain.values import decimal_string


def _num(value):
    """A Decimal as a plain decimal string, or None.

    Shares `values.decimal_string` with the derived-figure wire format, so a
    stored quantity and a computed one are spelled the same way.
    """
    return None if value is None else decimal_string(value)


def _date(value):
    return value.isoformat() if value is not None else None


def _reference_scope(scope_key: str) -> str:
    """Which tier wrote this, kept on the wire as it always was.

    A programme-scoped reference record is one written when no numeric
    organisation id was available; saying so lets it be lifted to
    organisation scope later instead of being silently duplicated.
    """
    return "organization" if (scope_key or "").startswith("org:") else "program"


def party(obj) -> dict:
    return {
        "id": obj.pk,
        "slug": obj.slug,
        "name": obj.name,
        "kind": obj.kind,
        "connect_organization_id": obj.connect_organization_id,
        "is_linked": obj.is_linked,
        "roles": obj.roles,
        "country": obj.country,
        "contacts": obj.contacts,
        "notes": obj.notes,
    }


def commodity(obj) -> dict:
    return {
        "id": obj.pk,
        "slug": obj.slug,
        "name": obj.name,
        "category": obj.category,
        "base_unit": obj.base_unit,
        "pack_unit": obj.pack_unit,
        "base_per_pack": obj.base_per_pack,
        "base_unit_grams": obj.base_unit_grams,
        "shelf_life_months_minimum": obj.shelf_life_months_minimum,
        "spec_requirements": obj.spec_requirements,
        "course_definition": obj.course_definition,
        "spec_reference": obj.spec_reference,
        "unicef_material_number": obj.unicef_material_number,
        "reference_scope": _reference_scope(obj.scope_key),
    }


def item(obj) -> dict:
    return {
        "id": obj.pk,
        "sku": obj.sku,
        "name": obj.name,
        "commodity_slug": obj.commodity.slug,
        "manufacturer": obj.manufacturer,
        "base_unit": obj.base_unit,
        "pack_unit": obj.pack_unit,
        "base_per_pack": obj.base_per_pack,
        "pack_per_case": obj.pack_per_case,
        "base_unit_grams": obj.base_unit_grams,
        "shelf_life_months": obj.shelf_life_months,
        "gtin_base": obj.gtin_base,
        "gtin_pack": obj.gtin_pack,
        "gtin_case": obj.gtin_case,
        "gpc_brick": obj.gpc_brick,
        "spec_attributes": obj.spec_attributes,
        "status": obj.status,
        "reference_scope": _reference_scope(obj.scope_key),
    }


def supplier(obj) -> dict:
    return {
        "id": obj.pk,
        "name": obj.name,
        "type": obj.type,
        "country": obj.country,
        "city": obj.city,
        "status": obj.status,
        "contacts": obj.contacts,
        "qualifications": obj.qualifications,
        "connect_organization_id": obj.connect_organization_id,
        "party_id": obj.party_id,
        "notes": obj.notes,
        "reference_scope": _reference_scope(obj.scope_key),
    }


def round_(obj) -> dict:
    return {
        "id": obj.pk,
        "label": obj.label,
        "status": obj.status,
        "lines": obj.lines,
        "delivery_point": obj.delivery_point,
        "response_deadline": _date(obj.response_deadline),
        "reminder_interval_days": obj.reminder_interval_days,
        "shelf_life_months_minimum": obj.shelf_life_months_minimum,
        "notes_to_supplier": obj.notes_to_supplier,
    }


def outreach(obj) -> dict:
    return {
        "id": obj.pk,
        "round_id": obj.round_id,
        "supplier_id": obj.supplier_id,
        "channel": obj.channel,
        "sent_on": _date(obj.sent_on),
        "responded": obj.responded,
        "response_kind": obj.response_kind,
        "last_reminder_on": _date(obj.last_reminder_on),
        "notes": obj.notes,
    }


def quote(obj) -> dict:
    return {
        "id": obj.pk,
        "round_id": obj.round_id,
        "supplier_id": obj.supplier_id,
        "item_id": obj.item_id,
        "commodity_slug": obj.commodity.slug,
        "as_quoted_amount": _num(obj.as_quoted_amount),
        "as_quoted_unit": obj.as_quoted_unit,
        "as_quoted_currency": obj.as_quoted_currency,
        "quantity_basis": _num(obj.quantity_basis),
        "quantity_basis_unit": obj.quantity_basis_unit,
        "pack_spec_source": obj.pack_spec_source,
        "base_per_pack_stated": obj.base_per_pack_stated,
        "base_unit_grams_stated": obj.base_unit_grams_stated,
        "freight_basis": obj.freight_basis,
        "freight_amount": _num(obj.freight_amount),
        "duties_basis": obj.duties_basis,
        "duties_amount": _num(obj.duties_amount),
        "fx_rate_to_usd": _num(obj.fx_rate_to_usd),
        "shelf_life_months_stated": obj.shelf_life_months_stated,
        "moq": _num(obj.moq),
        "moq_unit": obj.moq_unit,
        "lead_time_days": obj.lead_time_days,
        "validity_until": _date(obj.validity_until),
        "incoterm": obj.incoterm,
        "stated_spec": obj.stated_spec,
        "received_on": _date(obj.received_on),
        "voided": obj.voided,
        "void_reason": obj.void_reason,
        "version": obj.version,
        "superseded_by_quote_id": obj.superseded_by_quote_id,
        "supersedes_quote_id": obj.supersedes_quote_id,
        "correction_reason": obj.correction_reason,
        "notes": obj.notes,
    }


def award(obj) -> dict:
    return {
        "id": obj.pk,
        "round_id": obj.round_id,
        "quote_id": obj.quote_id,
        "supplier_id": obj.supplier_id,
        "commodity_slug": obj.commodity.slug,
        "decided_on": _date(obj.decided_on),
        "decided_by": obj.decided_by,
        "rationale": obj.rationale,
        "comparison_snapshot": obj.comparison_snapshot,
        "provisional": obj.provisional,
        "assumed_buyer_of_record": obj.assumed_buyer_of_record,
    }


def _sourced(obj) -> dict:
    return {
        "source": obj.source,
        "recorded_by_party_id": obj.recorded_by_party_id,
        "witnessed": obj.witnessed,
        "note": obj.note,
    }


def contract(obj) -> dict:
    return {
        "id": obj.pk,
        "round_id": obj.round_id,
        "award_id": obj.award_id,
        "supplier_id": obj.supplier_id,
        "item_id": obj.item_id,
        "commodity_slug": obj.commodity.slug,
        "buyer_of_record": obj.buyer_of_record,
        "buyer_party_id": obj.buyer_party_id,
        "reference": obj.reference,
        "signed_on": _date(obj.signed_on),
        "status": obj.status,
        "currency": obj.currency,
        "quantity": _num(obj.quantity),
        "quantity_unit": obj.quantity_unit,
        "unit_price": _num(obj.unit_price),
        "unit_price_unit": obj.unit_price_unit,
        "freight_basis": obj.freight_basis,
        "freight_amount": _num(obj.freight_amount),
        "duties_basis": obj.duties_basis,
        "duties_amount": _num(obj.duties_amount),
        "vat_basis": obj.vat_basis,
        "vat_amount": _num(obj.vat_amount),
        "duty_relief_claimed": obj.duty_relief_claimed,
        "duty_relief_document_id": obj.duty_relief_document_id,
        "duty_relief_evidenced": obj.duty_relief_evidenced,
        "incoterm": obj.incoterm,
        "delivery_supply_point_id": obj.delivery_supply_point_id,
        "promised_lead_time_days": obj.promised_lead_time_days,
        **_sourced(obj),
    }


def shipment(obj) -> dict:
    return {
        "id": obj.pk,
        "contract_id": obj.contract_id,
        "reference": obj.reference,
        "sscc": obj.sscc,
        "status": obj.status,
        "is_in_transit": obj.is_in_transit,
        "dispatched_on": _date(obj.dispatched_on),
        "expected_on": _date(obj.expected_on),
        "carrier": obj.carrier,
        "lines": [
            {
                "item_id": line.item_id,
                "batch": line.batch,
                "expiry": _date(line.expiry),
                "quantity": _num(line.quantity),
                "quantity_unit": line.quantity_unit,
            }
            for line in obj.lines.all()
        ],
        "document_ids": [doc.pk for doc in obj.documents.all()],
        **_sourced(obj),
    }


def receipt(obj) -> dict:
    return {
        "id": obj.pk,
        "contract_id": obj.contract_id,
        "shipment_id": obj.shipment_id,
        "supply_point_id": obj.supply_point_id,
        "reference": obj.reference,
        "received_on": _date(obj.received_on),
        "lines": [
            {
                "item_id": line.item_id,
                "batch": line.batch,
                "expiry": _date(line.expiry),
                "quantity_accepted": _num(line.quantity_accepted),
                "quantity_rejected": _num(line.quantity_rejected),
                "rejection_reason": line.rejection_reason,
                "quantity_unit": line.quantity_unit,
            }
            for line in obj.lines.all()
        ],
        "document_ids": [doc.pk for doc in obj.documents.all()],
        **_sourced(obj),
    }


def invoice(obj) -> dict:
    return {
        "id": obj.pk,
        "contract_id": obj.contract_id,
        "reference": obj.reference,
        "issued_on": _date(obj.issued_on),
        "status": obj.status,
        "currency": obj.currency,
        "amount": _num(obj.amount),
        "quantity_billed": _num(obj.quantity_billed),
        "quantity_unit": obj.quantity_unit,
        "payments": [
            {
                "id": payment.pk,
                "paid_on": _date(payment.paid_on),
                "amount": _num(payment.amount),
                "currency": payment.currency,
                "method": payment.method,
                "reference": payment.reference,
                **_sourced(payment),
            }
            for payment in obj.payments.all()
        ],
        "document_ids": [doc.pk for doc in obj.documents.all()],
        **_sourced(obj),
    }


def document(obj) -> dict:
    return {
        "id": obj.pk,
        "kind": obj.kind,
        "title": obj.title,
        "filename": obj.filename,
        "content_type": obj.content_type,
        "size_bytes": obj.size_bytes,
        "is_stored": obj.is_stored,
        "external_url": obj.external_url,
        "sha256": obj.sha256,
        "uploaded_at": obj.uploaded_at.isoformat() if obj.uploaded_at else None,
        "links": {
            "contract_id": obj.contract_id,
            "shipment_id": obj.shipment_id,
            "receipt_id": obj.receipt_id,
            "invoice_id": obj.invoice_id,
            "supply_point_id": obj.supply_point_id,
            "supplier_id": obj.supplier_id,
        },
        **_sourced(obj),
    }


def supply_point(obj) -> dict:
    return {
        "id": obj.pk,
        "slug": obj.slug,
        "name": obj.name,
        "kind": obj.kind,
        "is_user_held": obj.is_user_held,
        "opportunity_id": obj.opportunity_id,
        "parent_supply_point_id": obj.parent_id,
        "managed_by_party_id": obj.managed_by_party_id,
        "connect_username": obj.connect_username,
        "connect_user_id": obj.connect_user_id,
        "admin_area": obj.admin_area,
        "latitude": obj.latitude,
        "longitude": obj.longitude,
        "min_months_of_stock": _num(obj.min_months_of_stock),
        "max_months_of_stock": _num(obj.max_months_of_stock),
        "status": obj.status,
        **_sourced(obj),
    }


def movement(obj) -> dict:
    return {
        "id": obj.pk,
        "kind": obj.kind,
        "occurred_on": _date(obj.occurred_on),
        "from_supply_point_id": obj.from_supply_point_id,
        "to_supply_point_id": obj.to_supply_point_id,
        "item_id": obj.item_id,
        "commodity_slug": obj.commodity.slug,
        "batch": obj.batch,
        "expiry": _date(obj.expiry),
        "quantity": _num(obj.quantity),
        "quantity_unit": obj.quantity_unit,
        "opportunity_id": obj.opportunity_id,
        "reference": obj.reference,
        "caused_by": {
            "receipt_id": obj.receipt_id,
            "shipment_id": obj.shipment_id,
            "distribution_id": obj.distribution_id,
            "stock_count_id": obj.stock_count_id,
        },
        **_sourced(obj),
    }


def stock_count(obj) -> dict:
    return {
        "id": obj.pk,
        "supply_point_id": obj.supply_point_id,
        "item_id": obj.item_id,
        "commodity_slug": obj.commodity.slug,
        "batch": obj.batch,
        "kind": obj.kind,
        "counted_on": _date(obj.counted_on),
        "quantity": _num(obj.quantity),
        "quantity_unit": obj.quantity_unit,
        "reason": obj.reason,
        "opportunity_id": obj.opportunity_id,
        "form_submission_id": obj.form_submission_id,
        "visit_id": obj.visit_id,
        "connect_username": obj.connect_username,
        "adjustment_movement_id": obj.adjustment_movement_id,
        **_sourced(obj),
    }


def distribution(obj) -> dict:
    return {
        "id": obj.pk,
        "supply_point_id": obj.supply_point_id,
        "opportunity_id": obj.opportunity_id,
        "distributed_on": _date(obj.distributed_on),
        "reference": obj.reference,
        "lines": [
            {
                "to_supply_point_id": line.to_supply_point_id,
                "connect_username": line.connect_username,
                "item_id": line.item_id,
                "batch": line.batch,
                "quantity": _num(line.quantity),
                "quantity_unit": line.quantity_unit,
                "movement_id": line.movement_id,
            }
            for line in obj.lines.all()
        ],
        "movement_ids": [m.pk for m in obj.movements.all()],
        **_sourced(obj),
    }
