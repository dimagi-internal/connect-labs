import factory
from django.contrib.auth import get_user_model

from connect_labs.supply import models as m


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = get_user_model()
        django_get_or_create = ("username",)

    username = factory.Sequence(lambda n: f"supply-user-{n}")
    email = factory.LazyAttribute(lambda o: f"{o.username}@example.com")


class SupplierOrgFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.SupplierOrg

    legal_name = factory.Sequence(lambda n: f"Supplier {n}")
    country = "NG"


class SupplierMemberFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.SupplierMember

    user = factory.SubFactory(UserFactory)
    org = factory.SubFactory(SupplierOrgFactory)


class StaffRoleFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.StaffRole

    user = factory.SubFactory(UserFactory)
    role = m.StaffRole.Role.PROCUREMENT_ADMIN


class CertificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Certification

    org = factory.SubFactory(SupplierOrgFactory)
    cert_type = "ISO 22000"
    issuer = "ISO"


class EOIRoundFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.EOIRound

    title = factory.Sequence(lambda n: f"EOI Round {n}")
    categories = ["rutf"]
    status = m.EOIRound.Status.OPEN


class EOISubmissionFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.EOISubmission

    org = factory.SubFactory(SupplierOrgFactory)
    round = factory.SubFactory(EOIRoundFactory)
    categories = ["rutf"]


class QualificationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Qualification

    org = factory.SubFactory(SupplierOrgFactory)
    category = "rutf"
    granted_at = factory.Faker("date_this_year")
    expires_at = factory.Faker("date_between", start_date="+6m", end_date="+2y")


class RFPFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.RFP

    title = factory.Sequence(lambda n: f"RFP {n}")
    categories = ["rutf"]
    countries = ["NG"]
    status = m.RFP.Status.PUBLISHED


class LotFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Lot

    rfp = factory.SubFactory(RFPFactory)
    category = "rutf"
    description = "60,000 cartons RUTF"
    quantity = 60000
    unit = "cartons"
    delivery_country = "NG"
    delivery_place = "Maiduguri"


class BidFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Bid

    org = factory.SubFactory(SupplierOrgFactory)
    rfp = factory.SubFactory(RFPFactory)


class LotBidFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.LotBid

    bid = factory.SubFactory(BidFactory)
    lot = factory.SubFactory(LotFactory)
    unit_price = 42


class SupplyNodeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.SupplyNode

    name = factory.Sequence(lambda n: f"Node {n}")
    kind = "warehouse"
    country = "NG"
    gln = factory.Sequence(lambda n: f"629123450{n:04d}")


class AwardFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Award

    lot = factory.SubFactory(LotFactory)
    lot_bid = factory.SubFactory(LotBidFactory)


class AppropriationFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Appropriation

    funder_name = "US Government"
    title = factory.Sequence(lambda n: f"Appropriation {n}")
    amount = 10_000_000


class ContractFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Contract

    award = factory.SubFactory(AwardFactory)
    org = factory.SubFactory(SupplierOrgFactory)
    reference = factory.Sequence(lambda n: f"OES-C-{n:04d}")
    total_quantity = 60000
    unit_price = 42


class ShipmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Shipment

    contract = factory.SubFactory(ContractFactory)
    reference = factory.Sequence(lambda n: f"SHP-{n:04d}")
    origin = factory.SubFactory(SupplyNodeFactory)
    # Lands at the place its contract promised to deliver to, so a shipment
    # built with no opinion about geography counts toward that contract. The
    # contract measure counts only arrivals at its own delivery place (a carton
    # once, not once per leg), and a default destination named "Node 7" would
    # silently zero every quantity assertion in the suite.
    destination = factory.LazyAttribute(lambda s: SupplyNodeFactory(name=s.contract.award.lot.delivery_place))
    quantity = 60000


class MilestoneFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Milestone

    shipment = factory.SubFactory(ShipmentFactory)
    node = factory.SubFactory(SupplyNodeFactory)
    kind = "arrive"


class DiscrepancyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.Discrepancy

    shipment = factory.SubFactory(ShipmentFactory)
    expected_quantity = 1000
    received_quantity = 900


class CaseloadEstimateFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.CaseloadEstimate

    country = "NG"
    adm1_code = "NGA-2839"
    adm1_name = "Borno"
    month = factory.LazyFunction(lambda: __import__("datetime").date.today().replace(day=1))
    ipc_phase = 5
    under5_population = 1_000_000
    children_sam = 4330
    source_note = "test fixture"


class PartnerOrgFactory(SupplierOrgFactory):
    legal_name = factory.Sequence(lambda n: f"Partner {n}")
    kind = m.SupplierOrg.Kind.IMPLEMENTING_PARTNER


class DistributionPlanFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.DistributionPlan

    site = factory.SubFactory(SupplyNodeFactory, kind="delivery_point", adm1_code="NGA-2839")
    org = factory.SubFactory(PartnerOrgFactory)
    scheduled_for = factory.LazyFunction(lambda: __import__("datetime").date.today())
    expected_children = 800
    cartons_required = 800


class ShortfallSignalFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.ShortfallSignal

    site = factory.SubFactory(SupplyNodeFactory, kind="delivery_point", adm1_code="NGA-2839")
    org = factory.SubFactory(PartnerOrgFactory)
    raised_on = factory.LazyFunction(lambda: __import__("datetime").date.today())
    needed_by = factory.LazyFunction(
        lambda: __import__("datetime").date.today() + __import__("datetime").timedelta(days=7)
    )
    children_affected = 780
    cartons_short = 780


class DistributionRecordFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.DistributionRecord

    site = factory.SubFactory(SupplyNodeFactory, kind="delivery_point", adm1_code="NGA-2839")
    org = factory.SubFactory(PartnerOrgFactory)
    distributed_on = factory.LazyFunction(lambda: __import__("datetime").date.today())
    cartons_dispensed = 200
    children_served = 200
    batch_lot = "LOT-TEST"


class ChildOutcomeFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = m.ChildOutcome

    anon_id = factory.Sequence(lambda n: f"CHILD-{n:04d}")
    site = factory.SubFactory(SupplyNodeFactory, kind="delivery_point", adm1_code="NGA-2839")
    org = factory.SubFactory(PartnerOrgFactory)
    batch_lot = "LOT-TEST"
    admitted_on = factory.LazyFunction(lambda: __import__("datetime").date.today())
    admission_muac_mm = 108
    measurements = factory.LazyFunction(lambda: [{"date": "2026-06-01", "muac_mm": 108}])
