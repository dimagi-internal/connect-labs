"""Supply domain models, split by lifecycle stage.

Procurement runs up to the award decision; execution carries that decision out.
They are separate modules because they change for different reasons and are
worked on at different times — but they remain one Django app, so every model
is re-exported here and ``from connect_labs.supply.models import X`` keeps
working regardless of which half X lives in.
"""
from .execution import (
    ApiToken,
    Appropriation,
    Contract,
    Discrepancy,
    Milestone,
    Shipment,
    ShipmentLine,
    SupplyEvent,
    SupplyNode,
)
from .procurement import (
    RFP,
    AuditLog,
    Award,
    Bid,
    BidScore,
    Category,
    Certification,
    EOIReview,
    EOIRound,
    EOISubmission,
    Lot,
    LotBid,
    Qualification,
    StaffRole,
    SupplierMember,
    SupplierOrg,
)

__all__ = [
    # procurement
    "Category",
    "SupplierOrg",
    "SupplierMember",
    "Certification",
    "EOIRound",
    "EOISubmission",
    "EOIReview",
    "Qualification",
    "RFP",
    "Lot",
    "Bid",
    "LotBid",
    "BidScore",
    "Award",
    "StaffRole",
    "AuditLog",
    # execution
    "SupplyNode",
    "Appropriation",
    "Contract",
    "Shipment",
    "ShipmentLine",
    "Milestone",
    "SupplyEvent",
    "Discrepancy",
    "ApiToken",
]
