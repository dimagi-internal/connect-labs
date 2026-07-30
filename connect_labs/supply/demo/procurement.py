"""Orchestrates the procurement seed and owns the reset.

The writing itself lives next door — :mod:`.organisations` for who exists,
:mod:`.solicitations` for what they bid on — so this module stays a readable
statement of the order things happen in.
"""
from django.db import transaction

from ..models import (
    RFP,
    Award,
    Bid,
    BidScore,
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
from .demand import demand_summary, reset_demand, seed_demand
from .execution import execution_summary, reset_execution, seed_execution
from .organisations import _seed_orgs, _seed_staff, _seed_supplier_login
from .solicitations import (
    _seed_awarded_rfp,
    _seed_closed_round,
    _seed_corridor_awards,
    _seed_live_rfp,
    _seed_open_round,
    _seed_split_award_rfp,
)


class ProcurementSeeder:
    """Writes the procurement world, then hands the same PRNG to execution."""

    def __init__(self, rng):
        self.rng = rng

    @transaction.atomic
    def seed(self, reset=False):
        rng = self.rng

        if reset:
            self._reset()

        orgs = _seed_orgs(rng)
        staff = _seed_staff()
        _seed_supplier_login(orgs)

        # A closed round first: it is what populated the registry the live
        # solicitations then draw their bidders from.
        _seed_closed_round(rng, orgs, staff)
        _seed_open_round(rng, orgs, staff)
        _seed_live_rfp(rng, orgs, staff)
        _seed_awarded_rfp(rng, orgs, staff)
        _seed_split_award_rfp(rng, orgs, staff)
        _seed_corridor_awards(orgs, staff)

        nodes, _contracts = seed_execution(rng, orgs, staff)

        # Demand last: the caseload rows key off the nodes execution just
        # wrote, and the outcome cohorts hang off real delivered batches.
        seed_demand(rng, orgs, nodes)

        return {
            "suppliers": SupplierOrg.objects.count(),
            "qualifications": Qualification.objects.count(),
            "solicitations": RFP.objects.count(),
            "awards": Award.objects.count(),
            "execution": execution_summary(),
            "demand": demand_summary(),
        }

    def _reset(self):
        """Delete the demo world, demand and execution first so FKs unwind."""
        reset_demand()
        reset_execution()
        Award.objects.all().delete()
        BidScore.objects.all().delete()
        LotBid.objects.all().delete()
        Bid.objects.all().delete()
        Lot.objects.all().delete()
        RFP.objects.all().delete()
        Qualification.objects.all().delete()
        EOIReview.objects.all().delete()
        EOISubmission.objects.all().delete()
        EOIRound.objects.all().delete()
        Certification.objects.all().delete()
        SupplierMember.objects.all().delete()
        SupplierOrg.objects.all().delete()
        StaffRole.objects.all().delete()
