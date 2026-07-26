"""Wire shapes, split to mirror :mod:`connect_labs.supply.models`.

Same organising principle as the models: procurement up to the award decision,
execution afterwards. Everything is re-exported, so callers keep importing
``from connect_labs.supply.serializers import x`` without caring which half x
belongs to.
"""
from .execution import (
    api_token_dict,
    appropriation_dict,
    contract_dict,
    discrepancy_dict,
    event_dict,
    milestone_dict,
    node_dict,
    shipment_dict,
    shipment_line_dict,
)
from .procurement import (
    bid_dict,
    certification_dict,
    lot_bid_dict,
    lot_dict,
    org_dict,
    qualification_dict,
    rfp_dict,
    round_dict,
    submission_dict,
)

__all__ = [
    # procurement
    "certification_dict",
    "qualification_dict",
    "org_dict",
    "round_dict",
    "submission_dict",
    "lot_dict",
    "rfp_dict",
    "lot_bid_dict",
    "bid_dict",
    # execution
    "node_dict",
    "milestone_dict",
    "shipment_line_dict",
    "shipment_dict",
    "event_dict",
    "discrepancy_dict",
    "contract_dict",
    "appropriation_dict",
    "api_token_dict",
]
