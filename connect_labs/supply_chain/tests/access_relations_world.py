"""Three organisations in three roles at once, for the relationship-ACL tests.

THIS REPOSITORY IS PUBLIC. Every name and id is invented.

    X  runs programme A, bids on programme B's tender (it was invited), and receives goods on programme C.
    Y  runs programme B.
    Z  runs programme C (and buys the order X receives).
    W  a competing supplier on B's tender, and the payee of a charge in C.
    N  related to nothing.

Rows are made straight through the ORM: what is under test is the relation paths, not the operations.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain import models
from connect_labs.supply_chain.runner.models import ProgrammeRunner

A, B, C = 30001, 30002, 30003
PROGRAMMES = (A, B, C)


@dataclass
class World:
    orgs: dict = field(default_factory=dict)
    rows: dict = field(default_factory=dict)

    def org(self, key):
        return self.orgs[key]

    def ids(self, *keys):
        return {self.orgs[k].pk for k in keys}


def _commodity(program_id):
    return models.Commodity.objects.create(
        scope_key=models.scope_key(program_id=program_id), slug="rutf", name="RUTF", base_unit="sachet"
    )


def _supplier(program_id, org):
    return models.Supplier.objects.create(scope_key=models.scope_key(program_id=program_id), org=org)


def _quote(tender, supplier, commodity, entered_by="supplier"):
    return models.Quote.objects.create(tender=tender, supplier=supplier, commodity=commodity, entered_by=entered_by)


def _point(program_id, name, org=None, kind="central_store"):
    return models.SupplyPoint.objects.create(
        program_id=program_id,
        name=name,
        slug=name.lower().replace(" ", "-").replace("'", ""),
        kind=kind,
        managed_by_org=org,
        source="we_recorded",
    )


def _contract(program_id, supplier, commodity, buyer, point=None, tender=None, ref=""):
    return models.Contract.objects.create(
        program_id=program_id,
        tender=tender,
        supplier=supplier,
        commodity=commodity,
        buyer_of_record="programme_org",
        buyer_org=buyer,
        delivery_supply_point=point,
        reference=ref,
        status="placed",
        source="we_recorded",
    )


def build() -> World:
    w = World()
    for key, connect_id in (("X", 501), ("Y", 502), ("Z", 503), ("W", 504), ("N", 505)):
        w.orgs[key] = LabsOrg.objects.create(
            slug=f"acl-{key.lower()}", name=f"Org {key}", connect_organization_id=connect_id
        )
    X, Y, Z, W, N = (w.orgs[k] for k in "XYZWN")

    for program_id, runner in ((A, X), (B, Y), (C, Z)):
        ProgrammeRunner.objects.create(program_id=program_id, org=runner)

    # --- A: X runs it. A plain programme tender, a supplier, an order.
    com_a = _commodity(A)
    sup_a = _supplier(A, W)
    tender_a = models.Tender.objects.create(program_id=A, label="A tender")
    quote_a = _quote(tender_a, sup_a, com_a)
    point_a = _point(A, "A store", X)
    contract_a = _contract(A, sup_a, com_a, X, point_a, tender_a)
    w.rows.update(tender_a=tender_a, quote_a=quote_a, contract_a=contract_a, point_a=point_a, supplier_a=sup_a)

    # --- B: Y runs it. Y's own tender, restricted: X is invited, W bids too.
    com_b = _commodity(B)
    tender_b = models.Tender.objects.create(
        program_id=B, label="B tender", owner_org=Y, visibility="private", status="open"
    )
    tender_b.invited_orgs.add(X)
    sup_bx = _supplier(B, X)
    sup_bw = _supplier(B, W)
    quote_bx = _quote(tender_b, sup_bx, com_b)
    quote_bw = _quote(tender_b, sup_bw, com_b)
    award_b = models.Award.objects.create(tender=tender_b, quote=quote_bx, supplier=sup_bx, commodity=com_b)
    approval_b = models.AwardApproval.objects.create(
        award=award_b, approver_org=Z, role="funder", requested_on=date(2026, 10, 1)
    )
    contract_b = _contract(B, sup_bx, com_b, Y, None, tender_b)
    w.rows.update(
        tender_b=tender_b,
        supplier_bx=sup_bx,
        supplier_bw=sup_bw,
        quote_bx=quote_bx,
        quote_bw=quote_bw,
        award_b=award_b,
        approval_b=approval_b,
        contract_b=contract_b,
    )

    # --- C: Z runs it and buys. X receives (it runs the store the order is delivered to).
    com_c = _commodity(C)
    sup_c = _supplier(C, W)
    store_x = _point(C, "X's clinic store", X, kind="facility")
    store_z = _point(C, "Z's depot", Z)
    contract_cx = _contract(C, sup_c, com_c, Z, store_x, None, "C-to-X")
    contract_cz = _contract(C, sup_c, com_c, Z, store_z, None, "C-to-Z")
    shipment_cx = models.Shipment.objects.create(contract=contract_cx, source="we_recorded")
    shipment_cz = models.Shipment.objects.create(contract=contract_cz, source="we_recorded")
    charge_cz = models.Charge.objects.create(
        shipment=shipment_cz, kind="customs", payee_org=W, amount=Decimal("5"), source="we_recorded"
    )
    receipt_cx = models.Receipt.objects.create(
        contract=contract_cx, supply_point=store_x, received_on=date(2026, 10, 2), source="we_recorded"
    )
    invoice_cz = models.Invoice.objects.create(contract=contract_cz, source="we_recorded")
    commitment_cw = models.Commitment.objects.create(
        program_id=C, kind="question", owed_to_org=W, text="when?", raised_on=date(2026, 10, 3), source="we_recorded"
    )
    movement_cx = models.Movement.objects.create(
        program_id=C,
        kind="receipt",
        occurred_on=date(2026, 10, 2),
        to_supply_point=store_x,
        commodity=com_c,
        quantity=Decimal("10"),
        quantity_unit="sachet",
        source="we_recorded",
    )
    w.rows.update(
        supplier_c=sup_c,
        store_x=store_x,
        store_z=store_z,
        contract_cx=contract_cx,
        contract_cz=contract_cz,
        shipment_cx=shipment_cx,
        shipment_cz=shipment_cz,
        charge_cz=charge_cz,
        receipt_cx=receipt_cx,
        invoice_cz=invoice_cz,
        commitment_cw=commitment_cw,
        movement_cx=movement_cx,
    )
    return w
