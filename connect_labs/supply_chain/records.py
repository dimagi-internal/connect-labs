"""LabsRecord type constants for the supply domain.

Tiers, and how each is scoped (see the design doc, sections 4, 17 and 18):

  reference    -> organization_id   commodities, items, suppliers, parties
  procurement  -> program_id        rounds, outreach, quotes, awards, purchases
  fulfilment   -> program_id        contracts, shipments, receipts, invoices, documents
  network      -> program_id        supply points (carrying opportunity_id in data)
  stock        -> program_id        movements, counts, distributions

Everything below the contract is programme-scoped and carries opportunity_id
as a DATA field rather than a record scope. Two reasons. A network manager
needs both "this opportunity's workers" and "the whole programme's network"
from one operation plus a filter; and LabsRecordAPIClient treats a real
opportunity_id as a hard scope, so a record written while one opportunity was
selected would go invisible the moment another was -- the trap the
procurement tier already hit (see data_access._routing_opportunity_id).
"""

EXPERIMENT_PREFIX = "supply"

# ---- reference: reused across a programme's rounds ---------------------
COMMODITY_TYPE = "supply_commodity"
ITEM_TYPE = "supply_item"
SUPPLIER_TYPE = "supply_supplier"
PARTY_TYPE = "supply_party"

# ---- procurement: source to award -------------------------------------
ROUND_TYPE = "supply_round"
OUTREACH_TYPE = "supply_outreach"
QUOTE_TYPE = "supply_quote"
AWARD_TYPE = "supply_award"
PURCHASE_TYPE = "supply_purchase"

# ---- fulfilment: contract to receipt ----------------------------------
CONTRACT_TYPE = "supply_contract"
SHIPMENT_TYPE = "supply_shipment"
RECEIPT_TYPE = "supply_receipt"
INVOICE_TYPE = "supply_invoice"
DOCUMENT_TYPE = "supply_document"

# ---- network: where stock can rest ------------------------------------
SUPPLY_POINT_TYPE = "supply_point"

# ---- stock: the append-only ledger and what is reported over it -------
MOVEMENT_TYPE = "supply_movement"
STOCK_COUNT_TYPE = "supply_stock_count"
DISTRIBUTION_TYPE = "supply_distribution"

REFERENCE_TYPES = (COMMODITY_TYPE, ITEM_TYPE, SUPPLIER_TYPE, PARTY_TYPE)
PROCUREMENT_TYPES = (ROUND_TYPE, OUTREACH_TYPE, QUOTE_TYPE, AWARD_TYPE, PURCHASE_TYPE)
FULFILMENT_TYPES = (CONTRACT_TYPE, SHIPMENT_TYPE, RECEIPT_TYPE, INVOICE_TYPE, DOCUMENT_TYPE)
NETWORK_TYPES = (SUPPLY_POINT_TYPE,)
STOCK_TYPES = (MOVEMENT_TYPE, STOCK_COUNT_TYPE, DISTRIBUTION_TYPE)


# ---- vocabularies shared by schemas, proxies and services -------------
#
# Declared here rather than in operations.py because three layers need the
# same list and a drifting copy is a silent bug: a schema that accepts a
# value no service handles, or a service branching on a value no schema
# permits.

BUYER_OF_RECORD = ("programme_org", "partner_org", "agency")
PARTY_KINDS = ("programme_org", "partner_org", "supplier", "agency")

# Who told us. Required on every fulfilment, network and stock record
# (design doc section 17.3) -- a fact we did not witness is a claim.
SOURCES = (
    "we_recorded",
    "partner_reported",
    "supplier_reported",
    "commcare_form",
    "connect_visit",
    "document",
)

SUPPLY_POINT_KINDS = (
    "central_store",
    "regional_store",
    "facility",
    "user_held",
    "supplier_site",
    "in_transit",
    "customs",
)

# Signs are fixed here, not at each call site: a movement kind that is
# counted the wrong way round silently doubles or zeroes a balance, and
# "which kinds add" is exactly the sort of knowledge that gets re-derived
# differently in two places.
MOVEMENT_KINDS = (
    "receipt",
    "issue",
    "transfer",
    "distribution",
    "consumption",
    "adjustment",
    "loss",
    "expiry",
    "return",
)

# An adjustment is the only kind whose quantity may be negative: it is how a
# stock count's variance gets into the ledger (design doc section 20).
SIGNED_MOVEMENT_KINDS = ("adjustment",)

STOCK_COUNT_KINDS = ("self_reported", "physical_count", "override")

SHIPMENT_STATUSES = (
    "planned",
    "dispatched",
    "in_transit",
    "at_customs",
    "cleared",
    "delivered",
    "lost",
)

# Dispatched but not yet received. These are the statuses position() counts as
# in_transit and REFUSES to count as on hand (design doc section 19.1).
IN_TRANSIT_STATUSES = ("dispatched", "in_transit", "at_customs", "cleared")

CONTRACT_STATUSES = (
    "draft",
    "placed",
    "confirmed",
    "part_received",
    "received",
    "closed",
    "cancelled",
)

INVOICE_STATUSES = ("received", "queried", "approved", "part_paid", "paid", "rejected")

DOCUMENT_KINDS = (
    "purchase_order",
    "order_confirmation",
    "certificate_of_analysis",
    "certificate_of_conformity",
    "duty_exemption",
    "dispatch_note",
    "goods_received_note",
    "invoice",
    "proof_of_payment",
    "stock_report",
    "other",
)

BASIS = ("included", "excluded", "not_specified")
