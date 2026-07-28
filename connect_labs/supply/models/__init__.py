"""Supply domain models, split by lifecycle stage.

Procurement runs up to the award decision; execution carries that decision out;
demand is the denominator both of those lack — the caseload a district is
expected to have and what a treated child's measurements did. They are separate
modules because they change for different reasons and are worked on at
different times — but they remain one Django app, so every model is re-exported
here and ``from connect_labs.supply.models import X`` keeps working regardless
of which third X lives in.
"""
from .demand import (
    MUAC_MAM_MAX_MM,
    MUAC_RECOVERED_MIN_MM,
    MUAC_SAM_MAX_MM,
    SAM_PREVALENCE_BY_IPC_PHASE,
    TREATMENT_WEEKS,
    WEEKS_PER_MONTH,
    CaseloadEstimate,
    ChildOutcome,
    DistributionPlan,
    DistributionRecord,
    ShortfallSignal,
    SupplyAction,
)
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
    # demand
    "CaseloadEstimate",
    "DistributionPlan",
    "ShortfallSignal",
    "SupplyAction",
    "DistributionRecord",
    "ChildOutcome",
    "SAM_PREVALENCE_BY_IPC_PHASE",
    "TREATMENT_WEEKS",
    "WEEKS_PER_MONTH",
    "MUAC_SAM_MAX_MM",
    "MUAC_MAM_MAX_MM",
    "MUAC_RECOVERED_MIN_MM",
]
