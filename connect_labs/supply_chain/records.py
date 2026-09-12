"""LabsRecord type constants for the supply domain.

Two tiers with different scoping (see the design doc, section 4):
  reference   -> organization_id   commodities and suppliers, reused across programmes
  procurement -> program_id        rounds, outreach, quotes, awards, purchases

Tracking and distribution will add their own tier constants here; the
opportunity-scoped fulfilment types land in phase 2.
"""

EXPERIMENT_PREFIX = "supply"

COMMODITY_TYPE = "supply_commodity"
ITEM_TYPE = "supply_item"
SUPPLIER_TYPE = "supply_supplier"

ROUND_TYPE = "supply_round"
OUTREACH_TYPE = "supply_outreach"
QUOTE_TYPE = "supply_quote"
AWARD_TYPE = "supply_award"
PURCHASE_TYPE = "supply_purchase"

REFERENCE_TYPES = (COMMODITY_TYPE, ITEM_TYPE, SUPPLIER_TYPE)
PROCUREMENT_TYPES = (ROUND_TYPE, OUTREACH_TYPE, QUOTE_TYPE, AWARD_TYPE, PURCHASE_TYPE)
