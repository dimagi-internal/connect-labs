"""LabsRecord type constants for the supply domain.

Tiers, and how each is scoped (see the design doc, sections 4, 17 and 18):

  reference    -> program_id        commodities, items, suppliers
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
# Organisations are NOT here: they are `labs.LabsOrg`, one labs-wide table,
# not a per-scope LabsRecord. The `supply_party` type constant that used to
# sit in this list was read by nothing at all once the model moved.
COMMODITY_TYPE = "supply_commodity"
ITEM_TYPE = "supply_item"
SUPPLIER_TYPE = "supply_supplier"

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

REFERENCE_TYPES = (COMMODITY_TYPE, ITEM_TYPE, SUPPLIER_TYPE)
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

# Who imports, which is a fact about THE PURCHASE and not about the
# organisation: the same body is the buyer of record on one contract and not
# on the next, and import duty and VAT follow the contract. `PARTY_KINDS`
# used to sit beside this saying the opposite -- that an organisation IS a
# partner_org or an agency, everywhere, for ever -- and it was read by one
# schema that then threw the value away. Gone.
BUYER_OF_RECORD = ("programme_org", "partner_org", "agency")

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

# The documents that certify what is in a consignment, as opposed to the ones
# that move it through a port. `shipment_without_certificate` and the order
# page's Certificate column both read this, so they cannot disagree.
CERTIFICATE_KINDS = ("certificate_of_analysis", "certificate_of_conformity")

DOCUMENT_KINDS = (
    "purchase_order",
    "order_confirmation",
    "quotation",
    "pro_forma_invoice",
    "certificate_of_analysis",
    "certificate_of_conformity",
    "duty_exemption",
    "dispatch_note",
    "goods_received_note",
    "invoice",
    "proof_of_payment",
    "proof_of_delivery",
    "specification_sheet",
    "photo",
    "stock_report",
    # Import clearance. What a consignment needs to leave the port, and what
    # a clearing agent or customs will ask for -- each owed by somebody, which
    # is what `Shipment.required_documents` records.
    "airway_bill",
    "bill_of_lading",
    "packing_list",
    "commercial_invoice",
    "import_permit",
    "customs_declaration",
    # A national regulator's product registration (NAFDAC in Nigeria).
    "product_registration",
    "other",
)

# What a document can be evidence FOR. Declared here, once, because three
# places need the same list: the model's foreign keys, the operation schema
# that accepts `<name>_id`, and the query that filters on it. It used to live
# in the repository with the schema restating it by hand, so a new target was
# two edits and a silent omission.
#
# Explicit foreign keys rather than a GenericForeignKey, even at this length.
# A document is not decoration here -- two derivations turn on one EXISTING
# (a claimed duty relief, a batch's conformity) -- so a row pointing at a
# deleted contract would silently un-evidence a figure. A generic relation
# gives up the database's help with exactly that, and gives up joining.
DOCUMENT_LINKS = (
    # sourcing: what a supplier sent, and what we sent them
    "quote",
    "round",
    "award",
    # ordering and paying
    "contract",
    "shipment",
    "receipt",
    "invoice",
    "payment",
    # holding and handing out -- a worker's confirmation that stock arrived
    # is the same kind of fact as a goods received note, one step further on
    "distribution",
    "stock_count",
    "supply_point",
    # the organisations and things themselves: a certification, a spec sheet, a
    # photograph of the product
    "supplier",
    "item",
    # a customs or clearing receipt, against the charge it evidences
    "charge",
    # the approver's letter or email, against the approval it records
    "approval",
)

# What it costs to land a consignment, paid to somebody other than the
# supplier: customs, a clearing agent, a haulier from the port, a warehouse.
# Who, other than the person deciding an award, has to agree to it: a
# technical partner confirming a product, a funder approving a use of funds,
# a regulator. And where that stands.
APPROVAL_ROLES = ("technical", "funder", "regulatory")
APPROVAL_STATUSES = ("requested", "approved", "declined")

CHARGE_KINDS = ("customs_duty", "customs_fee", "clearing", "inland_freight", "storage", "other")

BASIS = ("included", "excluded", "not_specified")

# What the programme gives for the goods. `priced` is a purchase and the
# default. `in_kind` is a donation -- a donor still supplies, ships and is
# received from, but nobody pays for the goods. `bundled` is paid for out of
# something else, typically a partner's setup fee. Only a priced contract
# expects a unit price, an invoice or a landed cost.
CONSIDERATIONS = ("priced", "in_kind", "bundled")

# When a priced order is paid. `on_delivery` is the control three-way match
# was built for: pay for what arrived. `advance` is how a distributor who buys
# from manufacturers on our behalf is paid -- before any carton exists -- and
# paying it is the agreed terms, not a discrepancy.
PAYMENT_TERMS = ("on_delivery", "advance")

# Which level of a trade item is one full treatment course, if either is.
# Empty means "not a course, or nobody has said", and is the default.
ONE_COURSE_IS = ("", "base_unit", "pack")

# A durable item is held and moved but never consumed -- a dispenser, a
# scale -- so consumption-rate figures are refused for it rather than
# computed from nothing.
STOCK_CLASSES = ("consumable", "durable")
