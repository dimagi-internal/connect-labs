import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command

from connect_labs.supply.models import (
    RFP,
    Award,
    Bid,
    EOIRound,
    EOISubmission,
    Qualification,
    StaffRole,
    SupplierMember,
    SupplierOrg,
)

pytestmark = pytest.mark.django_db
User = get_user_model()


def _snapshot():
    return {
        "orgs": SupplierOrg.objects.count(),
        "quals": sorted(Qualification.objects.values_list("org__legal_name", "category", "expires_at")),
        "subs": sorted(EOISubmission.objects.values_list("org__legal_name", "round__title", "status")),
        "bids": Bid.objects.count(),
        "awards": Award.objects.count(),
    }


def test_seed_is_idempotent_and_deterministic():
    call_command("seed_supply_demo")
    first = _snapshot()
    call_command("seed_supply_demo")
    assert _snapshot() == first


def test_seed_personas_and_roles():
    call_command("seed_supply_demo")
    roles = dict(StaffRole.objects.values_list("user__username", "role"))
    assert roles["oes-lead@oes.example"] == "procurement_admin"
    assert roles["oes-review@oes.example"] == "reviewer"
    assert roles["gov-ng@oes.example"] == "gov_observer"
    assert roles["usg@oes.example"] == "funder"
    assert StaffRole.objects.get(user__username="gov-ng@oes.example").country == "NG"

    member = SupplierMember.objects.get(user__username="supplier@savanna.example")
    assert member.org.legal_name == "Savanna Nutrients Ltd"
    assert member.user.check_password("oes-demo-2026")


def test_seed_demo_logins_can_sign_in(client):
    call_command("seed_supply_demo")
    resp = client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    assert resp.status_code == 302 and resp.url == "/supply/"
    assert client.get("/supply/api/bootstrap/").json()["role"] == "procurement_admin"


def test_seed_world_shape():
    call_command("seed_supply_demo")
    assert SupplierOrg.objects.count() == 16
    # no RUTF manufacturer in Sudan — it is supplied through Port Sudan
    sudan = SupplierOrg.objects.filter(country="SD")
    assert sudan.exists()
    assert not Qualification.objects.filter(org__country="SD", category="rutf").exists()

    assert EOIRound.objects.filter(status=EOIRound.Status.CLOSED).count() == 1
    assert EOIRound.objects.filter(status=EOIRound.Status.OPEN).count() == 1

    open_round = EOIRound.objects.get(status=EOIRound.Status.OPEN)
    statuses = set(open_round.submissions.values_list("status", flat=True))
    assert statuses == {"draft", "submitted", "qualified", "rejected"}

    # a live solicitation mid-flight, and one fully awarded
    live = RFP.objects.get(title="RUTF Northeast Nigeria Q3 2026")
    assert live.status == RFP.Status.PUBLISHED
    assert live.lots.count() == 3
    assert all(lot.lot_bids.count() >= 3 for lot in live.lots.all())

    awarded = RFP.objects.get(title="RUTF Ethiopia Q2 2026")
    assert awarded.status == RFP.Status.AWARDED
    assert Award.objects.filter(lot__rfp=awarded).count() == 1


def test_seeded_registry_has_expiring_certifications():
    call_command("seed_supply_demo")
    from datetime import date, timedelta

    from connect_labs.supply.models import Certification

    soon = date.today() + timedelta(days=60)
    assert Certification.objects.filter(expiry_date__lte=soon).exists()


def test_seeded_supplier_sees_eligible_solicitations(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "supplier@savanna.example", "password": "oes-demo-2026"})
    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "supplier"
    assert body["org"]["legal_name"] == "Savanna Nutrients Ltd"
    assert any(q["category"] == "rutf" for q in body["org"]["qualifications"])
    assert any(r["title"] == "RUTF Northeast Nigeria Q3 2026" for r in body["eligible_rfps"])


def test_seed_execution_world():
    call_command("seed_supply_demo")
    from connect_labs.supply.models import Contract, Discrepancy, Shipment, SupplyEvent, SupplyNode

    assert SupplyNode.objects.count() == 28
    assert Contract.objects.count() == 4
    assert Shipment.objects.count() == 14

    # every ingestion tier is represented, so the demo shows the real gradient
    tiers = set(SupplyEvent.objects.values_list("source_tier", flat=True))
    assert tiers == {"epcis", "asn", "checkin", "portal"}

    # every shipment status appears
    assert set(Shipment.objects.values_list("status", flat=True)) == {
        "planned",
        "in_transit",
        "delivered",
        "confirmed",
    }

    # at least one receipt fails to reconcile, feeding the exception surface
    assert Discrepancy.objects.filter(status="open").exists()


def test_seeded_shipments_belong_to_a_contract_in_their_own_country():
    """A Nigerian leg must not hang off an Ethiopian contract."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import Shipment

    for shipment in Shipment.objects.select_related("contract__org", "origin", "destination"):
        countries = {shipment.origin.country, shipment.destination.country}
        contract_country = shipment.contract.award.lot.delivery_country
        assert (
            contract_country in countries
        ), f"{shipment.reference} runs {countries} but belongs to a {contract_country} contract"


def test_seeded_nodes_have_valid_gs1_locations():
    call_command("seed_supply_demo")
    from connect_labs.supply import gs1
    from connect_labs.supply.models import SupplyNode

    for node in SupplyNode.objects.all():
        assert gs1.is_valid(node.gln), f"{node.name} has an invalid GLN"
        assert node.location is not None


def test_seeded_contracts_report_three_distinct_money_stages():
    call_command("seed_supply_demo")
    from connect_labs.supply.models import Contract

    contract = Contract.objects.get(reference="OES-C-2026-ET1")
    assert contract.obligated_value > 0
    # delivered is what arrived; disbursed is only what was CONFIRMED — the
    # funder view depends on these never collapsing into one number
    assert contract.delivered_quantity >= contract.disbursed_value / contract.unit_price


def test_reseeding_preserves_shipment_lifecycle_state():
    """Re-running without --reset must not strand shipments in 'planned'.

    Events are idempotent, so they are not replayed on a second run — if the
    seed reset lifecycle fields, every shipment would lose its status.
    """
    from collections import Counter

    from connect_labs.supply.models import Shipment

    call_command("seed_supply_demo")
    before = Counter(Shipment.objects.values_list("status", flat=True))
    assert before["planned"] < Shipment.objects.count(), "fixture should not be all-planned"

    call_command("seed_supply_demo")
    after = Counter(Shipment.objects.values_list("status", flat=True))
    assert after == before


def test_demo_password_can_be_overridden_by_environment(monkeypatch):
    """A deployed instance must not use the password published in the repo."""
    monkeypatch.setenv("SUPPLY_DEMO_PASSWORD", "not-the-repo-default")
    import importlib

    from connect_labs.supply.management.commands import seed_supply_demo as mod

    importlib.reload(mod)
    try:
        call_command(mod.Command(), "--reset")
        user = User.objects.get(username="oes-lead@oes.example")
        assert user.check_password("not-the-repo-default")
        assert not user.check_password("oes-demo-2026")
    finally:
        monkeypatch.delenv("SUPPLY_DEMO_PASSWORD", raising=False)
        importlib.reload(mod)


def test_seeded_routes_follow_corridors_not_straight_lines():
    """A rendered flow must trace the road/sea corridor, not cut across terrain."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import Shipment

    routed = Shipment.objects.exclude(route=None)
    assert routed.exists()

    # the long Sudan haul is digitised, so it must carry interior waypoints
    darfur = Shipment.objects.get(reference="SHP-2026-0202")
    assert darfur.route is not None
    assert len(darfur.route.coords) > 3, "expected a multi-point corridor, got a straight line"

    # every routed shipment starts at its origin and ends at its destination
    for shipment in routed.select_related("origin", "destination"):
        first, last = shipment.route.coords[0], shipment.route.coords[-1]
        assert first == pytest.approx((shipment.origin.location.x, shipment.origin.location.y))
        assert last == pytest.approx((shipment.destination.location.x, shipment.destination.location.y))


def test_sea_lane_avoids_cutting_across_land():
    """Lagos to Port Sudan must round the Cape and transit Bab-el-Mandeb."""
    from connect_labs.supply import routes

    lane = routes.waypoints_for("Port of Lagos (Apapa)", "Port Sudan")
    assert lane is not None
    lats = [lat for _lon, lat in lane]
    # it dips deep into the southern hemisphere rather than crossing the Sahara
    assert min(lats) < -20
    # and passes through the Bab-el-Mandeb strait
    assert any(abs(lon - 43.4) < 1.5 and abs(lat - 12.6) < 1.5 for lon, lat in lane)
