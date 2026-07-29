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
    # 16 suppliers plus Komadugu, the implementing partner — which is an org of
    # a different kind, not a seventeenth supplier.
    assert SupplierOrg.objects.count() == 17
    assert SupplierOrg.objects.filter(kind=SupplierOrg.Kind.SUPPLIER).count() == 16
    assert SupplierOrg.objects.filter(kind=SupplierOrg.Kind.IMPLEMENTING_PARTNER).count() == 1
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
    # 4 lots across two corridors — the Djibo lot is what lets the live tender
    # demonstrate awarding corridors separately rather than showing a past split.
    assert live.lots.count() == 4
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

    # 29 OES-network nodes plus Komadugu's 11 Borno feeding sites
    assert SupplyNode.objects.count() == 40
    assert Contract.objects.count() == 4
    # 15 corridor consignments; 10 delivered into Komadugu's sites (a partner
    # site holds stock only if something actually delivered to it) plus an
    # earlier, since-consumed wave of 10 so cohorts exist that have had time to
    # finish a course; and 2 still on the road for the calendar's inbound column.
    assert Shipment.objects.count() == 39

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
    call_command("seed_supply_demo", "--reset")
    user = User.objects.get(username="oes-lead@oes.example")
    assert user.check_password("not-the-repo-default")
    assert not user.check_password("oes-demo-2026")


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


def test_reseeding_rotates_demo_passwords(monkeypatch):
    """Rotating the secret must take effect, not leave the old one working."""
    call_command("seed_supply_demo", "--reset")
    user = User.objects.get(username="oes-lead@oes.example")
    assert user.check_password("oes-demo-2026")

    monkeypatch.setenv("SUPPLY_DEMO_PASSWORD", "rotated-secret-1")
    call_command("seed_supply_demo")  # no --reset: users already exist
    user.refresh_from_db()
    assert user.check_password("rotated-secret-1")
    assert not user.check_password("oes-demo-2026")


def test_seeded_nigeria_coverage_inverts_tonnage():
    """The scene the government view exists for, on the data it actually gets.

    Hauwa's page is scoped to Nigeria on the server, so the well-covered
    district she is compared against has to be a Nigerian one — an earlier
    seed put it in Sudan, where her page could never show it. The narration
    says one district is covered to ninety-one percent while another, which
    received MORE cartons, sits at thirty-four with thirty-one thousand
    children still uncovered. If a reseed ever ranks them the same way by
    tonnage and by coverage, the scene stops demonstrating anything.
    """
    from connect_labs.supply.services import coverage

    call_command("seed_supply_demo", "--reset")
    rows = {r["adm1_name"]: r for r in coverage.coverage_by_district(country="NG")}

    best, worst = rows["Gombe"], rows["Borno"]
    assert best["coverage_percent"] == pytest.approx(91.0, abs=0.5)
    assert worst["coverage_percent"] == pytest.approx(34.0, abs=0.5)
    # the inversion: more cartons, less covered
    assert worst["courses_delivered"] > best["courses_delivered"]
    assert worst["coverage_percent"] < best["coverage_percent"]
    # "thirty-one thousand children still uncovered"
    assert 31_000 <= worst["uncovered_children"] < 32_000


def test_the_seeded_world_produces_all_four_exception_kinds():
    """The command centre narrates "all four exception kinds" — over three.

    Every seeded batch carried a 540-day shelf life, so every expiry landed in
    January 2028 and the expiry-risk exception could not fire at all. The
    service, its cover calculation and its queue row were written, tested and
    unreachable: pytest passed, the recipe resolved, and the only way to notice
    was to count the kinds in a rendered frame.
    """
    from connect_labs.supply.services import exceptions

    call_command("seed_supply_demo", "--reset")
    kinds = {r["kind"] for r in exceptions.build_queue()}
    assert kinds == {"Late", "Short receipt", "Partner shortfall", "Expiry risk"}


def test_a_late_row_that_harms_nobody_still_says_so_in_children():
    """The queue's best argument for its own ranking, said out loud.

    A row with no children behind it fell back to "SHP-2026-0402 is 6 days
    behind plan", which argued in days while every other row argued in
    children — and threw away the strongest evidence the ranking produces:
    that a 6-day delay sorts BELOW a 1-day one because the destination is
    holding enough stock to absorb it.
    """
    from connect_labs.supply.services import exceptions

    call_command("seed_supply_demo", "--reset")
    rows = exceptions.build_queue()
    zero_risk = [r for r in rows if r["kind"] == "Late" and not r["children_at_risk"]]
    assert zero_risk, "the demo needs a delay that costs nobody a course"
    for row in zero_risk:
        assert "No children go without" in row["what"]
        assert "weeks of cover" in row["why"]

    # and the point of it: worse lateness, lower rank
    worst_late = max(zero_risk, key=lambda r: r["why"])
    harmful = [r for r in rows if r["kind"] == "Late" and r["children_at_risk"]]
    assert harmful, "expected at least one delay that does cost courses"
    assert rows.index(worst_late) > rows.index(harmful[-1])


def test_a_site_only_distributes_what_it_actually_received():
    """The chain the closing scene follows has to be a real one.

    The seeder round-robined the first six ShipmentLines in the database
    across all eleven sites, so every distribution cited a consignment that
    had gone somewhere else, every distribution predated the receipt that
    supposedly supplied it, and Biu served 280 children out of a batch it had
    never been sent while its own cover row read "awaiting first consignment".
    """
    from connect_labs.supply.models import DistributionRecord

    call_command("seed_supply_demo", "--reset")
    records = DistributionRecord.objects.select_related("site", "shipment_line__shipment")
    assert records.exists()

    for record in records:
        shipment = record.shipment_line.shipment
        assert shipment.destination_id == record.site_id, (
            f"{record.site.name} handed out {record.batch_lot}, which was sent to " f"{shipment.destination.name}"
        )
        arrived = shipment.delivered_at
        assert arrived is not None
        assert record.distributed_on > arrived.date(), (
            f"{record.site.name} distributed {record.batch_lot} on "
            f"{record.distributed_on}, before it arrived on {arrived.date()}"
        )
        assert record.cartons_dispensed <= record.shipment_line.quantity


def test_a_site_awaiting_its_first_consignment_has_distributed_nothing():
    """Its own cover row says so on the same screen."""
    from connect_labs.supply.models import DistributionRecord, SupplyNode
    from connect_labs.supply.services import cover

    call_command("seed_supply_demo", "--reset")
    awaiting = [r for r in cover.cover_by_node() if r.get("awaiting_first_delivery")]
    assert awaiting, "the demo needs a site with nothing delivered yet"

    for row in awaiting:
        node = SupplyNode.objects.get(id=row["node_id"])
        assert not DistributionRecord.objects.filter(
            site=node
        ).exists(), f"{node.name} is awaiting its first consignment and has distribution records"


def test_the_queue_ranks_on_who_goes_without_soonest():
    """ "Where, and by when" has to be the ordering, not just the copy.

    Ranking on the raw figure put 907 children whose cartons expire in
    December above 87 children who go without next week. Both are real; only
    one is actionable this month, and a worklist that cannot tell them apart
    is a leaderboard.
    """
    from connect_labs.supply.services import exceptions

    call_command("seed_supply_demo", "--reset")
    rows = exceptions.build_queue()

    distant = [r for r in rows if r["children_at_risk"] and not r["children_at_risk_soon"]]
    imminent = [r for r in rows if r["children_at_risk_soon"]]
    assert distant and imminent, "the demo needs one of each for this to be checkable"

    # every row costing children within the horizon outranks every row that does not,
    # even where the distant row's raw figure is larger
    assert max(rows.index(r) for r in imminent) < min(rows.index(r) for r in distant)
    assert any(d["children_at_risk"] > i["children_at_risk"] for d in distant for i in imminent)
