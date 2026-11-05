"""What an alert watches, in the words the people who set alerts up use.

`CHECK_LABELS` (templatetags) are the checks' own names, shared with the chase list and the Checks page. These are
for the Alerts page and its form only: a person choosing what to be told about reads "A count differs from the
records", not the check's working name. A kind with no entry here falls back to the shared label, so a new check
kind appears in the form before anyone has worded it.
"""

WATCH_LABELS = {
    "quote_not_comparable": "A supplier's quote is missing facts",
    "contract_cost_unconfirmed": "An order's total cost cannot be worked out",
    "contract_reference_unknown": "An order has no purchase order number",
    "duty_relief_unevidenced": "A duty exemption is claimed with no proof",
    "shipment_without_certificate": "A delivery has no batch certificate",
    "commodity_course_undefined": "A product has no course length set",
    "stock_unconfirmed": "A place's stock cannot be worked out",
    "stock_never_reported": "A field worker has never reported their stock",
    "award_not_contracted": "A tender was awarded but no order is placed",
    "invoice_over_billed": "An invoice bills for more than arrived",
    "stock_variance": "A count differs from the records",
    "stock_negative": "More was given out than was ever received",
    "stock_stockout": "A place has run out of stock",
    "stock_below_minimum": "A place holds less than its minimum",
    "stock_expiring": "Stock is close to its expiry date, or past it",
    "delivery_after_stockout": "A place runs out before its next delivery",
    "tender_start_by": "It is time to start the next tender",
    "item_fails_specification": "A product does not meet its specification",
    "shipment_overdue": "A shipment is later than expected",
    "shipment_documents_outstanding": "A shipment is missing documents",
    "shipment_delivered_unevidenced": "A delivery is recorded with no proof",
    "charge_paid_unevidenced": "A carrier was paid with no proof of delivery",
    "shipment_quantity_unaccounted": "Fewer cartons arrived than were sent",
    "award_awaiting_approval": "An award is waiting for approval",
    "payment_unconfirmed": "Nobody has confirmed they received a payment",
    "invoice_above_contract": "An invoice is above the agreed price",
    "commitment_open": "We owe somebody an answer",
    "contract_delivery_overdue": "An order is later than promised",
    "consignment_overdue": "Stock on the road is later than expected",
}


def watch_label(kind: str) -> str:
    from connect_labs.supply_chain.templatetags.supply_chain_extras import check_label

    return WATCH_LABELS.get(kind) or check_label(kind)
