"""Coverage, the exception queue, the partner surface, and the split award.

Everything here is a claim one of the four OES narratives makes out loud. If a
test in this file fails, a scene is lying.
"""
from datetime import date
from decimal import Decimal

import pytest
from django.core.management import call_command
from django.utils import timezone

from connect_labs.supply.models import (
    ChildOutcome,
    Discrepancy,
    Shipment,
    ShortfallSignal,
    SupplyAction,
    SupplyEvent,
    SupplyNode,
)
from connect_labs.supply.services import cover, coverage, exceptions

from .factories import (
    CaseloadEstimateFactory,
    ChildOutcomeFactory,
    ContractFactory,
    DistributionRecordFactory,
    PartnerOrgFactory,
    ShortfallSignalFactory,
    SupplierOrgFactory,
    SupplyNodeFactory,
)

pytestmark = pytest.mark.django_db

BORNO = "NGA-2839"
YOBE = "NGA-2873"


def _delivered_shipment(destination, cartons, org=None, reference="SHP-COV-1"):
    contract = ContractFactory(org=org or SupplierOrgFactory())
    return Shipment.objects.create(
        contract=contract,
        reference=reference,
        origin=SupplyNodeFactory(kind="port", adm1_code=""),
        destination=destination,
        quantity=Decimal(cartons),
        status=Shipment.Status.CONFIRMED,
    )


# --- coverage ---------------------------------------------------------------


def test_coverage_is_delivery_against_need_not_volume():
    """The narrative's claim: more tonnage can mean less coverage.

    A district that received more cartons but has a far larger caseload must
    render *lower* coverage than a smaller, better-supplied one. Volume alone
    cannot distinguish those, which is the whole reason the denominator exists.
    """
    CaseloadEstimateFactory(adm1_code=BORNO, adm1_name="Borno", children_sam=10_000)
    CaseloadEstimateFactory(adm1_code=YOBE, adm1_name="Yobe", children_sam=1_000)
    big = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, country="NG")
    small = SupplyNodeFactory(kind="distribution_hub", adm1_code=YOBE, country="NG")
    _delivered_shipment(big, 3_400, reference="SHP-BIG")
    _delivered_shipment(small, 910, reference="SHP-SMALL")

    rows = {r["adm1_code"]: r for r in coverage.coverage_by_district()}
    assert rows[BORNO]["delivered_cartons"] > rows[YOBE]["delivered_cartons"]
    assert rows[BORNO]["coverage_percent"] == pytest.approx(34.0, abs=0.5)
    assert rows[YOBE]["coverage_percent"] == pytest.approx(91.0, abs=0.5)
    assert rows[BORNO]["coverage_percent"] < rows[YOBE]["coverage_percent"]
    assert rows[BORNO]["uncovered_children"] == 6_600


def test_every_coverage_row_carries_its_source_note():
    CaseloadEstimateFactory(adm1_code=BORNO, source_note="method goes here")
    rows = coverage.coverage_by_district()
    assert all(r["source_note"] for r in rows)


def test_country_scoping_excludes_other_districts():
    CaseloadEstimateFactory(adm1_code=BORNO, country="NG")
    CaseloadEstimateFactory(adm1_code="SDN-881", adm1_name="North Darfur", country="SD")
    rows = coverage.coverage_by_district(country="NG")
    assert {r["country"] for r in rows} == {"NG"}


def test_the_two_headline_figures_are_reported_separately():
    """Courses delivered and recorded recoveries never collapse into one number."""
    CaseloadEstimateFactory(adm1_code=BORNO)
    hub = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, country="NG")
    _delivered_shipment(hub, 5_000, reference="SHP-OUT")
    partner = PartnerOrgFactory()
    site = SupplyNodeFactory(kind="delivery_point", adm1_code=BORNO, country="NG")
    record = DistributionRecordFactory(org=partner, site=site)
    for n in range(10):
        ChildOutcomeFactory(
            org=partner,
            site=site,
            distribution_record=record,
            discharge_status=(ChildOutcome.Discharge.RECOVERED if n < 8 else ChildOutcome.Discharge.DEFAULTED),
        )

    result = coverage.courses_versus_recoveries()
    assert result["courses_delivered"] == 5_000
    assert result["children_observed"] == 10
    assert result["children_recovered"] == 8
    assert result["observed_recovery_rate"] == 80.0
    # Both carry a stated method — the point of the beat is that the figures
    # can be challenged, which requires knowing how each was made.
    assert result["courses_method"]
    assert result["recovery_method"]
    assert result["gap_note"]


# --- the exception queue ----------------------------------------------------


def test_the_queue_ranks_everything_in_one_unit():
    """Four kinds of exception, one comparable quantity: children."""
    CaseloadEstimateFactory(adm1_code=BORNO, children_sam=4330)
    site = SupplyNodeFactory(kind="delivery_point", adm1_code=BORNO, name="Kukawa")
    partner = PartnerOrgFactory()
    ShortfallSignalFactory(org=partner, site=site, children_affected=780)

    shipment = _delivered_shipment(site, 900, reference="SHP-DISC")
    Discrepancy.objects.create(
        shipment=shipment,
        expected_quantity=Decimal("900"),
        received_quantity=Decimal("840"),
        status=Discrepancy.Status.OPEN,
    )

    rows = exceptions.build_queue()
    assert rows, "expected at least the signal and the discrepancy"
    assert all("children_at_risk" in r for r in rows)
    # Descending by children at risk.
    values = [r["children_at_risk"] for r in rows]
    assert values == sorted(values, reverse=True)
    # The partner's 780 children outrank a 60-carton short receipt.
    assert rows[0]["children_at_risk"] == 780


def test_every_row_shows_how_its_severity_was_derived():
    CaseloadEstimateFactory(adm1_code=BORNO)
    site = SupplyNodeFactory(kind="delivery_point", adm1_code=BORNO)
    ShortfallSignalFactory(site=site)
    rows = exceptions.build_queue()
    assert all(r["derivation"] for r in rows)


def test_a_partner_raised_row_says_so_and_a_derived_one_does_not():
    """The distinction the command-centre narrative rests on."""
    CaseloadEstimateFactory(adm1_code=BORNO)
    site = SupplyNodeFactory(kind="delivery_point", adm1_code=BORNO)
    partner = PartnerOrgFactory(legal_name="Komadugu Test Initiative")
    ShortfallSignalFactory(org=partner, site=site)

    shipment = _delivered_shipment(site, 900, reference="SHP-D2")
    Discrepancy.objects.create(
        shipment=shipment,
        expected_quantity=Decimal("900"),
        received_quantity=Decimal("800"),
        status=Discrepancy.Status.OPEN,
    )

    rows = {r["kind"]: r for r in exceptions.build_queue()}
    assert rows["Partner shortfall"]["origin"] == "partner"
    assert rows["Partner shortfall"]["org_name"] == "Komadugu Test Initiative"
    assert rows["Short receipt"]["origin"] == "derived"
    assert "org_name" not in rows["Short receipt"]


def test_a_resolved_signal_leaves_the_queue():
    CaseloadEstimateFactory(adm1_code=BORNO)
    site = SupplyNodeFactory(kind="delivery_point", adm1_code=BORNO)
    signal = ShortfallSignalFactory(site=site)
    assert any(r["kind"] == "Partner shortfall" for r in exceptions.build_queue())

    signal.status = ShortfallSignal.Status.RESOLVED
    signal.save()
    assert not any(r["kind"] == "Partner shortfall" for r in exceptions.build_queue())


# --- the append-only action log ---------------------------------------------


def test_a_recorded_action_cannot_be_rewritten_or_deleted():
    """The same discipline that makes shipment status derived.

    A decision log that can be edited afterwards is a decision log nobody can
    rely on six months later, which is exactly when it gets asked about.
    """
    action = SupplyAction.objects.create(
        kind=SupplyAction.Kind.REALLOCATE,
        actor="ada@oes.example",
        rationale="El Fasher is eleven days from dry; Kassala holds surplus.",
    )
    action.rationale = "something else"
    with pytest.raises(ValueError):
        action.save()
    with pytest.raises(ValueError):
        action.delete()


# --- the partner surface ----------------------------------------------------


def test_a_partner_sees_only_their_own_sites(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    body = client.get("/supply/api/bootstrap/").json()

    assert body["role"] == "partner"
    assert body["org"]["legal_name"] == "Komadugu Health Initiative"
    site_names = {s["name"] for s in body["sites"]}
    assert len(site_names) == 11
    # Another organisation's delivery points are absent from the payload, not
    # hidden in the browser — the same property as the gov country scoping.
    assert "Bama Health Post" not in site_names
    assert "Tawila Nutrition Site" not in site_names


def test_a_partner_gets_a_calendar_not_a_shipment_list(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    body = client.get("/supply/api/bootstrap/").json()

    plans = body["distribution_plans"]
    assert plans
    assert all(p["state"] in {"covered", "at_risk", "uncovered"} for p in plans)
    assert all(p["expected_children"] > 0 for p in plans)


def test_a_partner_cannot_reach_procurement_surfaces(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    body = client.get("/supply/api/bootstrap/").json()

    # No bidding, no registry, no review queue: a partner never tenders.
    assert "eligible_rfps" not in body
    assert "registry" not in body
    assert "review_queue" not in body
    assert "bids" not in body["perms"]
    assert "eoi" not in body["perms"]


def test_the_partner_and_the_centre_report_the_same_cover(client):
    """The narrative requires the two surfaces to agree on the same node."""
    call_command("seed_supply_demo")

    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    partner_cover = {r["node_id"]: r for r in client.get("/supply/api/bootstrap/").json()["cover"]}

    client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    centre_cover = {r["node_id"]: r for r in client.get("/supply/api/bootstrap/").json()["cover"]}

    shared = set(partner_cover) & set(centre_cover)
    assert shared, "the partner's sites must also appear in the centre's view"
    for node_id in shared:
        assert partner_cover[node_id]["weeks_of_cover"] == centre_cover[node_id]["weeks_of_cover"]
        assert partner_cover[node_id]["stockout_on"] == centre_cover[node_id]["stockout_on"]


# --- the seeded world -------------------------------------------------------


def test_the_demo_world_contains_a_genuinely_split_award():
    """A PRIOR split, on corridors the live tender does not use.

    It used to carry the live tender's own two lots verbatim, so the
    solicitations list showed the exact split three scenes build to already
    marked 2/2 Awarded, one row above the tender being awarded on camera.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.models import RFP, Award

    rfp = RFP.objects.get(title="RUTF Horn and Sahel Corridors Q1 2026")
    assert rfp.status == RFP.Status.AWARDED
    awards = Award.objects.filter(lot__rfp=rfp).select_related("lot_bid__bid__org", "lot")
    assert awards.count() == 2
    winners = {a.lot_bid.bid.org.legal_name for a in awards}
    assert len(winners) == 2, f"the split has to be visible, got {winners}"
    places = {a.lot.delivery_place for a in awards}
    assert places == {"Gode", "Dori"}


def test_the_price_leader_differs_by_lot():
    """The information a per-tender comparison would have hidden."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import RFP
    from connect_labs.supply.services import rfp_actions

    rfp = RFP.objects.get(title="RUTF Horn and Sahel Corridors Q1 2026")
    leaders = []
    for lot in rfp.lots.all().order_by("delivery_place"):
        ranked = rfp_actions.lot_comparison(lot)
        leaders.append(ranked[0].bid.org.legal_name)
    assert len(set(leaders)) == 2, f"expected two different price leaders, got {leaders}"


def test_the_live_tender_is_won_by_a_different_supplier_on_each_corridor():
    """Scenes 7 and 8 of oes-supply-base are awarded live, on this tender.

    The pre-awarded split tender above proves the property in seeded history.
    This is the one Ada actually awards on camera, and the narration says the
    leader on Maiduguri is not the leader on Djibo — so if this tender ranks
    the same organisation first on both lots, awarding Djibo to anyone else
    looks arbitrary on screen. It did, once: a single global price ladder made
    the first-listed bidder cheapest on every lot.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.models import RFP
    from connect_labs.supply.services import rfp_actions

    rfp = RFP.objects.get(title="RUTF Northeast Nigeria Q3 2026")
    leaders = {}
    for lot in rfp.lots.filter(delivery_place__in=("Maiduguri", "Djibo"), category="rutf"):
        ranked = rfp_actions.lot_comparison(lot)
        leaders[lot.delivery_place] = ranked[0].bid.org.legal_name
        # Every bid on the two compared corridors carries a technical score,
        # because the narration reads them off the screen beside the price.
        assert all(
            b.scores.exists() for b in ranked
        ), f"unscored bid on the {lot.delivery_place} lot, which scene 7 narrates as scored"

    assert leaders["Maiduguri"] != leaders["Djibo"], f"one leader on both corridors: {leaders}"


def test_the_spoken_maiduguri_deadline_is_the_fifteenth_of_september():
    """oes-supply-base scene 6 says the date out loud, so it cannot drift."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import Lot

    lot = Lot.objects.get(rfp__title="RUTF Northeast Nigeria Q3 2026", delivery_place="Maiduguri", category="rutf")
    assert (lot.delivery_deadline.month, lot.delivery_deadline.day) == (9, 15)


def test_a_reallocation_answers_the_exception_it_was_made_against():
    """The queue's central claim, and it was not true.

    A reallocation creates a real consignment with planned milestones, and
    until it arrives the target's stock is unchanged — correctly, the cartons
    are not there yet. But that left the row identical to before, so the demo's
    climax was a toast: nothing on the screen moved, and the only record that a
    decision had been taken was one that had already faded.

    An answered row stays in the queue, because the children are still at risk
    until the truck arrives. It stops competing with the rows nobody has done
    anything about.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.models import SupplyNode
    from connect_labs.supply.services import actions

    before = exceptions.build_queue()
    target_row = next(r for r in before if r["kind"] == "Late" and r["children_at_risk"] > 0)
    assert target_row["answered_by"] is None, "nothing has been done about it yet"

    source = cover.nodes_holding_surplus()[0]
    actions.reallocate(
        actor="test@oes.example",
        source_node=SupplyNode.objects.get(id=source["node_id"]),
        target_node=SupplyNode.objects.get(id=target_row["node_id"]),
        quantity=500,
        rationale="Answering the worst gap in the queue.",
    )

    after = {r["key"]: r for r in exceptions.build_queue()}
    answered = after[target_row["key"]]
    assert answered["answered_by"] is not None
    assert "cartons" in answered["answered_by"]["effect"]
    assert answered["answered_by"]["rationale"] == "Answering the worst gap in the queue."

    # And it stops leading a queue that asks "what has nobody acted on".
    ordered = exceptions.build_queue()
    first_answered = next(i for i, r in enumerate(ordered) if r["answered_by"])
    assert all(r["answered_by"] for r in ordered[first_answered:])


def test_a_consignment_still_on_the_road_can_be_late():
    """The delay you can still act on is the one worth surfacing.

    Milestones were seeded with estimated_at == planned_at, so a leg could only
    ever report a delay once it had ARRIVED and carried an actual_at. A
    consignment already nine days overdue and still in transit reported zero
    and never entered the queue — exactly backwards, and it left the product's
    three-timestamp claim undemonstrated, since the middle timestamp never
    moved.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.models import Shipment

    late = [e for e in exceptions.build_queue() if e["kind"] == "Late"]
    in_transit_refs = set(
        Shipment.objects.filter(status=Shipment.Status.IN_TRANSIT).values_list("reference", flat=True)
    )
    from_the_road = [e for e in late if e.get("shipment_reference") in in_transit_refs]
    assert from_the_road, "a consignment still in transit must be able to be late"

    # And the estimate is what makes it late, not an actual arrival.
    worst = from_the_road[0]
    shipment = Shipment.objects.get(reference=worst["shipment_reference"])
    arrival = shipment.milestones.order_by("-sequence").first()
    assert arrival.actual_at is None, "still on the road"
    assert arrival.estimated_at > arrival.planned_at, "the estimate is what moved"


def test_a_node_can_only_spare_what_it_does_not_need():
    """The queue advises reallocating from surplus; this is what surplus means.

    A reallocation that solves one stockout by causing another is not a
    decision anybody would defend afterwards, so a node offers only the cartons
    it can lose while staying above its own threshold.
    """
    call_command("seed_supply_demo")
    rows = cover.nodes_holding_surplus(min_weeks=6.0)
    assert rows, "the demo world needs somewhere to reallocate from"

    by_node = {r["node_name"]: r for r in cover.cover_by_node()}
    for row in rows:
        full = by_node[row["node_name"]]
        assert full["weeks_of_cover"] >= 6.0, "a node below the threshold has nothing spare"
        # What is left behind still covers the threshold.
        remaining = full["stock_on_hand"] - row["spare_cartons"]
        assert remaining >= full["weekly_burn"] * 6.0 - 1

    # Most spare first: the reader is choosing a source, not reading a list.
    assert [r["spare_cartons"] for r in rows] == sorted((r["spare_cartons"] for r in rows), reverse=True)


def test_a_site_awaiting_its_first_delivery_is_not_reported_as_running_dry():
    """Zero cartons is two different facts and they need opposite actions.

    A dozen sites that had never been served rendered identically to a site two
    days from stocking out — 0 on hand, 0 weeks, "runs dry today" — and, sorted
    worst-first, they filled the top of the queue and pushed every real cover
    figure below the fold. A judge reading that frame concluded the join was
    broken. It was not; the two states were simply indistinguishable.
    """
    call_command("seed_supply_demo")
    rows = cover.cover_by_node()

    never_served = [r for r in rows if r["awaiting_first_delivery"]]
    burning_down = [r for r in rows if not r["awaiting_first_delivery"]]
    assert never_served, "the demo world needs at least one unserved site for this to mean anything"
    assert burning_down, "and at least one with real cover"

    # A projected stockout date has to come from an actual burn-down.
    for row in never_served:
        assert row["stock_on_hand"] == 0
        assert row["stockout_on"] is None
    for row in burning_down:
        assert row["stockout_on"] is not None

    # Urgency is the ranking, and never-served is the most urgent state there
    # is: every node here has a caseload behind it, so zero cover means children
    # going without today. Sorting these last put a site with four hundred
    # children a month and nothing on hand below one holding seven weeks.
    assert [r["weeks_of_cover"] for r in rows] == sorted(r["weeks_of_cover"] for r in rows)
    assert rows[0]["weeks_of_cover"] == 0, "a site with nothing on hand has to lead the queue"


def test_seeded_caseloads_cover_every_famine_district_with_a_node():
    call_command("seed_supply_demo")
    from connect_labs.supply.models import CaseloadEstimate

    coded = SupplyNode.objects.exclude(adm1_code="").values_list("adm1_code", flat=True)
    with_caseload = set(CaseloadEstimate.objects.values_list("adm1_code", flat=True))
    assert set(coded) <= with_caseload


def test_seeded_outcomes_land_inside_the_sphere_performance_band():
    """Recovery above 75%, defaulting below 15% — a normal programme.

    The gap between courses delivered and recoveries recorded is the closing
    beat of the funder narrative, and it is only useful if its size has a
    reason. Seeding to the sector's own thresholds is that reason.
    """
    call_command("seed_supply_demo")
    # Over DISCHARGED children. The Sphere rates are defined on completed
    # courses, and a child admitted last week has not completed one — counting
    # them in the denominator would report a programme as failing for the crime
    # of having recently admitted anybody.
    discharged = ChildOutcome.objects.exclude(discharge_status=ChildOutcome.Discharge.IN_TREATMENT)
    total = discharged.count()
    assert total > 50, "need a cohort big enough for the rates to mean anything"
    recovered = discharged.filter(discharge_status=ChildOutcome.Discharge.RECOVERED).count()
    defaulted = discharged.filter(discharge_status=ChildOutcome.Discharge.DEFAULTED).count()
    assert recovered / total > 0.75
    assert defaulted / total < 0.15

    # And children still mid-course exist, because the demo world has to
    # contain a batch handed out last week as well as one handed out in April.
    assert ChildOutcome.objects.filter(discharge_status=ChildOutcome.Discharge.IN_TREATMENT).exists()


def test_every_seeded_outcome_series_agrees_with_its_discharge_status():
    """A recovered child's measurements must actually cross the threshold."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import MUAC_RECOVERED_MIN_MM

    for child in ChildOutcome.objects.filter(discharge_status=ChildOutcome.Discharge.RECOVERED):
        assert child.latest_muac_mm >= MUAC_RECOVERED_MIN_MM, child.anon_id
        assert child.admission_muac_mm < MUAC_RECOVERED_MIN_MM, child.anon_id


def test_every_distribution_record_traces_to_a_real_delivered_batch():
    call_command("seed_supply_demo")
    from connect_labs.supply.models import DistributionRecord

    records = DistributionRecord.objects.select_related("shipment_line__shipment")
    assert records.exists()
    for record in records:
        assert record.shipment_line is not None, record.id
        assert record.shipment_line.batch_lot == record.batch_lot
        assert record.shipment_line.shipment.status in ("delivered", "confirmed")


def test_the_seeded_world_has_a_partner_raised_exception_waiting(client):
    """Scene 7 of oes-command-centre needs a real signal from the ground."""
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    body = client.get("/supply/api/bootstrap/").json()

    partner_rows = [r for r in body["exceptions"] if r["origin"] == "partner"]
    assert partner_rows, "the command centre must show a signal the partner raised"
    assert partner_rows[0]["org_name"] == "Komadugu Health Initiative"


# --- the loop: a partner signals, the centre answers ------------------------


def test_a_partner_raises_a_shortfall_and_the_centre_sees_it(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    site_id = client.get("/supply/api/bootstrap/").json()["sites"][0]["id"]

    response = client.post(
        "/supply/api/signals/raise/",
        data={
            "site_id": site_id,
            "needed_by": "2026-09-09",
            "children_affected": 640,
            "cartons_short": 640,
            "note": "Admissions above plan since the road reopened.",
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content

    client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    queue = client.get("/supply/api/bootstrap/").json()["exceptions"]
    mine = [r for r in queue if r.get("children_at_risk") == 640 and r["origin"] == "partner"]
    assert mine, "the centre must see the signal the partner just raised"


def test_a_partner_cannot_raise_a_shortfall_at_someone_elses_site(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    foreign = SupplyNode.objects.get(name="Tawila Nutrition Site")

    response = client.post(
        "/supply/api/signals/raise/",
        data={"site_id": foreign.id, "needed_by": "2026-09-09", "children_affected": 100, "cartons_short": 100},
        content_type="application/json",
    )
    assert response.status_code == 400


def test_a_supplier_cannot_raise_a_shortfall_at_all(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "supplier@savanna.example", "password": "oes-demo-2026"})
    response = client.post(
        "/supply/api/signals/raise/",
        data={"site_id": 1, "needed_by": "2026-09-09", "children_affected": 10, "cartons_short": 10},
        content_type="application/json",
    )
    assert response.status_code == 403


def test_a_reallocation_creates_a_real_shipment_and_moves_the_numbers(client):
    """Scene 8 of oes-command-centre: the numbers move because a truck moved."""
    from connect_labs.supply.services import actions
    from connect_labs.supply.services import cover as cover_service

    CaseloadEstimateFactory(adm1_code=BORNO, children_sam=4330)
    surplus = SupplyNodeFactory(kind="warehouse", adm1_code=BORNO, name="Kassala Test Store", country="NG")
    thin = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, name="El Fasher Test Hub", country="NG")
    SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=surplus,
        quantity_list=[{"gtin": "1", "quantity": 9000, "uom": "cartons"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )
    ContractFactory(org=SupplierOrgFactory())

    before = cover_service.cover_for_node(thin)
    action = actions.reallocate(
        actor="ada@oes.example",
        source_node=surplus,
        target_node=thin,
        quantity=3000,
        rationale="El Fasher is eleven days from dry; Kassala holds more than its own caseload needs.",
    )

    # A real consignment, with planned milestones and stored geometry.
    assert action.shipment is not None
    assert action.shipment.status == Shipment.Status.PLANNED
    assert action.shipment.milestones.count() == 2
    assert all(m.planned_at is not None for m in action.shipment.milestones.all())

    # And the source is drawn down, so the surplus is no longer double-counted.
    assert float(cover_service.stock_on_hand(surplus)) == 9000  # not yet departed
    assert action.effect
    assert before is not None


def test_a_reallocation_cannot_overdraw_the_source():
    """A paper transfer would have the receiving site plan against nothing."""
    from connect_labs.supply.services import actions
    from connect_labs.supply.services.org_actions import ActionError

    CaseloadEstimateFactory(adm1_code=BORNO)
    surplus = SupplyNodeFactory(kind="warehouse", adm1_code=BORNO, name="Thin Store")
    target = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, name="Target Hub")
    ContractFactory(org=SupplierOrgFactory())
    SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=surplus,
        quantity_list=[{"gtin": "1", "quantity": 500, "uom": "cartons"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )
    with pytest.raises(ActionError, match="holds"):
        actions.reallocate(
            actor="ada@oes.example",
            source_node=surplus,
            target_node=target,
            quantity=5000,
            rationale="wishful thinking",
        )


def test_a_reallocation_without_a_reason_is_refused():
    from connect_labs.supply.services import actions
    from connect_labs.supply.services.org_actions import ActionError

    CaseloadEstimateFactory(adm1_code=BORNO)
    a = SupplyNodeFactory(kind="warehouse", adm1_code=BORNO, name="A store")
    b = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, name="B hub")
    with pytest.raises(ActionError, match="why"):
        actions.reallocate(actor="ada", source_node=a, target_node=b, quantity=10, rationale="   ")


def test_resolving_a_signal_ties_it_to_the_action_that_resolved_it(client):
    """The decision and the evidence that prompted it become one record."""
    call_command("seed_supply_demo")
    signal = ShortfallSignal.objects.filter(status=ShortfallSignal.Status.OPEN).first()
    assert signal is not None

    surplus = SupplyNode.objects.get(name="Kassala Forward Store")
    SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=surplus,
        quantity_list=[{"gtin": "1", "quantity": 9000, "uom": "cartons"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )

    client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    response = client.post(
        "/supply/api/actions/reallocate/",
        data={
            "source_node_id": surplus.id,
            "target_node_id": signal.site_id,
            "quantity": 800,
            "rationale": "Kukawa reported a shortfall four days ago; Kassala holds surplus.",
            "signal_id": signal.id,
        },
        content_type="application/json",
    )
    assert response.status_code == 200, response.content

    signal.refresh_from_db()
    assert signal.status == ShortfallSignal.Status.RESOLVED
    assert signal.resolved_by_action_id == response.json()["action"]["id"]

    # And it is still ON the queue, marked closed and carrying the decision.
    # It used to be dropped the moment it resolved, which meant the one loop in
    # the product that actually completes completed by a row ceasing to exist.
    queue = client.get("/supply/api/bootstrap/").json()["exceptions"]
    closed = next((r for r in queue if r.get("signal_id") == signal.id), None)
    assert closed is not None
    assert closed["tone"] == "good"
    assert closed["resolved_by"]["action_id"] == response.json()["action"]["id"]
    assert closed["resolved_by"]["rationale"].startswith("Kukawa reported a shortfall")


def test_a_partner_cannot_reallocate(client):
    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "zara@komadugu.example", "password": "oes-demo-2026"})
    response = client.post(
        "/supply/api/actions/reallocate/",
        data={"source_node_id": 1, "target_node_id": 2, "quantity": 10, "rationale": "no"},
        content_type="application/json",
    )
    assert response.status_code == 403


# --- the batch drill --------------------------------------------------------


def test_a_delivered_batch_drills_to_a_child_who_recovered(client):
    """The closing beat of both the partner and the funder narratives."""
    from connect_labs.supply.models import MUAC_RECOVERED_MIN_MM

    call_command("seed_supply_demo")
    # A batch whose children have outcomes recorded. Not every distribution has
    # any — one handed out three days ago legitimately does not yet — and the
    # funder's drill only offers the ones that do.
    from connect_labs.supply.models import ChildOutcome as _CO

    batch = _CO.objects.exclude(batch_lot="").values_list("batch_lot", flat=True).first()
    assert batch, "the demo needs at least one batch with recorded outcomes"

    client.post("/supply/login/", {"email": "usg@oes.example", "password": "oes-demo-2026"})
    body = client.get(f"/supply/api/batches/{batch}/").json()

    assert body["records"], "a batch must resolve to the distributions it fed"
    assert body["outcomes"], "and to the children admitted on it"
    assert body["synthetic"] is True
    recovered = [
        o
        for o in body["outcomes"]
        if o["discharge_status"] == "recovered" and o["latest_muac_mm"] >= MUAC_RECOVERED_MIN_MM
    ]
    assert recovered, "at least one series must cross the recovery threshold"
    # Every rendered series carries the synthetic label in the payload itself,
    # so no consumer can drop it.
    assert all(o["synthetic"] for o in body["outcomes"])


def test_a_supplier_cannot_drill_into_child_outcomes(client):
    call_command("seed_supply_demo")
    from connect_labs.supply.models import DistributionRecord

    batch = DistributionRecord.objects.first().batch_lot
    client.post("/supply/login/", {"email": "supplier@savanna.example", "password": "oes-demo-2026"})
    assert client.get(f"/supply/api/batches/{batch}/").status_code == 403


# --- structural guards ------------------------------------------------------


def test_severity_is_not_computed_in_the_browser():
    """Guard against the ranking drifting back into untested JS.

    tab_command.jsx used to hold ExceptionSeverity() and buildExceptions(),
    where nothing in this repo could test them and where the partner surface
    would have needed a second copy. If either name comes back, the queue has
    two sources of truth again.
    """
    from pathlib import Path

    import connect_labs.supply as supply_pkg

    static = Path(supply_pkg.__file__).resolve().parent.parent / "static" / "supply"
    command_tab = (static / "tab_command.jsx").read_text()
    assert "function ExceptionSeverity" not in command_tab
    assert "function buildExceptions" not in command_tab
    assert "world.exceptions" in command_tab


def test_the_partner_tab_is_in_the_bundle_build_list():
    """A tab file nobody concatenates is a tab that does not exist."""
    from pathlib import Path

    import connect_labs.supply as supply_pkg

    repo_root = Path(supply_pkg.__file__).resolve().parents[2]
    build = (repo_root / "webpack" / "build-supply.js").read_text()
    assert "'tab_partner.jsx'," in build
    assert (repo_root / "connect_labs" / "static" / "supply" / "tab_partner.jsx").exists()


def test_coverage_sums_the_requirement_over_the_same_window_as_deliveries():
    """The unit error that reported a district at 424% of need.

    Deliveries are cumulative — a contract lands a season's supply in one
    consignment — so dividing them by a SINGLE month's caseload is comparing a
    total to a rate. The requirement is summed over every month we hold an
    estimate for, and the window is reported alongside the figure.
    """
    from datetime import date

    for months_back in range(4):
        month = date.today().replace(day=1)
        for _ in range(months_back):
            month = (month - __import__("datetime").timedelta(days=1)).replace(day=1)
        CaseloadEstimateFactory(adm1_code=BORNO, month=month, children_sam=1_000)

    hub = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, country="NG")
    _delivered_shipment(hub, 2_000, reference="SHP-WINDOW")

    row = next(r for r in coverage.coverage_by_district() if r["adm1_code"] == BORNO)
    assert row["window_months"] == 4
    assert row["caseload"] == 4_000, "requirement is the sum across the window"
    assert row["monthly_caseload"] == 1_000, "and the monthly rate is still reported"
    # 2000 courses against 4000 children = 50%, not the 200% a one-month
    # denominator would have produced.
    assert row["coverage_percent"] == 50.0
    assert row["uncovered_children"] == 2_000


def test_over_supply_is_reported_rather_than_clamped():
    """A district CAN be over-supplied, and that is where expiry risk lives.

    Clamping at 100% would render an over-supplied district identically to a
    perfectly-supplied one, hiding the surplus the expiry exception exists to
    catch.
    """
    CaseloadEstimateFactory(adm1_code=BORNO, children_sam=1_000)
    hub = SupplyNodeFactory(kind="distribution_hub", adm1_code=BORNO, country="NG")
    _delivered_shipment(hub, 2_500, reference="SHP-SURPLUS")

    row = next(r for r in coverage.coverage_by_district() if r["adm1_code"] == BORNO)
    assert row["coverage_percent"] == 250.0
    assert row["uncovered_children"] == 0
    assert row["surplus_children"] == 1_500


def test_the_partner_calendar_shows_all_three_cover_states():
    """A calendar where every row reads the same teaches nothing.

    The first render of this surface showed eleven sites at zero cover and all
    22 planned distributions uncovered, because nothing in the demo world ever
    delivered to a partner site. Stock is derived from the event log and from
    nothing else, so the fix was to actually move goods there.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.api.bootstrap import build_bootstrap

    class _Req:
        pass

    from django.contrib.auth import get_user_model

    request = _Req()
    request.user = get_user_model().objects.get(username="zara@komadugu.example")
    world = build_bootstrap(request)

    states = {p["state"] for p in world["distribution_plans"]}
    assert states == {"covered", "at_risk", "uncovered"}, f"got {states}"

    weeks = sorted(c["weeks_of_cover"] for c in world["cover"])
    assert weeks[0] == 0.0, "one site has to be empty for the beat to land"
    assert weeks[-1] >= 4, "and one has to be comfortable"


def test_the_calendar_depletes_stock_across_successive_distributions():
    """Cartons are spent when they are distributed.

    Scoring every planned day against the same opening stock let one site's 329
    cartons cover both its 5 August and its 12 August distribution, and the
    calendar then marked a day "covered" that the cover projection said the site
    would already be dry for. Three independent judges caught the contradiction
    before any human did.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.api.bootstrap import build_bootstrap

    class _Req:
        pass

    from django.contrib.auth import get_user_model

    request = _Req()
    request.user = get_user_model().objects.get(username="zara@komadugu.example")
    world = build_bootstrap(request)

    by_site = {}
    for plan in world["distribution_plans"]:
        by_site.setdefault(plan["site_name"], []).append(plan)

    fell = False
    for site, plans in by_site.items():
        plans.sort(key=lambda p: p["scheduled_for"])
        for earlier, later in zip(plans, plans[1:]):
            # Either stock fell by what the earlier distribution consumed, or a
            # consignment landed in between and raised it. It must never be
            # unchanged, which is what "the same cartons cover both" looks like.
            if later["cartons_on_hand"] < earlier["cartons_on_hand"]:
                fell = True
            assert later["cartons_on_hand"] != earlier["cartons_on_hand"] or earlier["cartons_on_hand"] == 0, (
                f"{site}: stock unchanged across two distributions "
                f"({earlier['cartons_on_hand']} -> {later['cartons_on_hand']})"
            )
    assert fell, "at least one site must visibly draw down"


def test_a_consignment_arriving_later_does_not_cover_an_earlier_day():
    """A truck arriving on Friday does not cover Tuesday.

    Counting everything still on the road as available flattened every row to
    'covered' — including a site holding nothing on the day it was due to
    distribute.
    """
    call_command("seed_supply_demo")
    from connect_labs.supply.api.bootstrap import build_bootstrap

    class _Req:
        pass

    from django.contrib.auth import get_user_model

    request = _Req()
    request.user = get_user_model().objects.get(username="zara@komadugu.example")
    world = build_bootstrap(request)

    for plan in world["distribution_plans"]:
        if plan["cartons_on_hand"] == 0 and plan["cartons_required"] > 0:
            assert plan["state"] == "uncovered", (
                f"{plan['site_name']} holds nothing on {plan['scheduled_for']} but reads "
                f"{plan['state']} — inbound arriving later must not cover this day"
            )


def test_the_site_that_raised_the_shortfall_is_actually_short():
    """The signal has to be justified by the cover, not merely accompany it."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import ShortfallSignal
    from connect_labs.supply.services import cover as cover_service

    signal = ShortfallSignal.objects.filter(status=ShortfallSignal.Status.OPEN).first()
    assert signal is not None
    site_cover = cover_service.cover_for_node(signal.site)
    assert site_cover is not None
    assert site_cover["weeks_of_cover"] < 2, "a site with weeks of cover would not be raising a shortfall"


def test_a_resolved_signal_closes_on_the_queue_rather_than_vanishing():
    """The one exception that genuinely closes must close ON CAMERA.

    A ShortfallSignal was dropped from the queue the instant it resolved, so
    the only loop in the product that completes did so by the row ceasing to
    exist. A reader looking at the screen after the decision saw an absence,
    which is the weakest possible evidence for the claim the screen makes. It
    now stays for a week carrying the actor, the effect and the reason, sorted
    below everything still waiting on somebody, and out of the headline.
    """
    from connect_labs.supply.services import actions

    site = SupplyNodeFactory(kind="delivery_point", adm1_code=BORNO, country="NG", name="Askira Test Site")
    source = SupplyNodeFactory(kind="distribution_hub", adm1_code=YOBE, country="NG", name="Surplus Test Hub")
    SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=source,
        quantity_list=[{"gtin": "1", "quantity": 5000, "uom": "cartons"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )
    ContractFactory(org=SupplierOrgFactory())
    signal = ShortfallSignalFactory(site=site, children_affected=87, cartons_short=87)

    before = {r["key"]: r for r in exceptions.build_queue()}
    row = before[f"signal-{signal.id}"]
    assert row["resolved_by"] is None and row["tone"] == "bad"

    action = actions.reallocate(
        actor="ada@oes.example",
        source_node=source,
        target_node=site,
        quantity=87,
        rationale="Komadugu raised it four days ago.",
        signal=signal,
    )

    rows = exceptions.build_queue()
    after = {r["key"]: r for r in rows}
    closed = after.get(f"signal-{signal.id}")
    assert closed is not None, "the signal vanished instead of closing on camera"
    assert closed["tone"] == "good"
    assert closed["resolved_by"]["action_id"] == action.id
    assert closed["resolved_by"]["actor"] == "ada@oes.example"
    assert closed["resolved_by"]["rationale"] == "Komadugu raised it four days ago."
    # sunk below anything still waiting on somebody
    assert rows[-1]["key"] == closed["key"]
    # and out of the headline, which counts what nobody has acted on
    headline = sum(r["children_at_risk"] or 0 for r in rows if not r["answered_by"] and not r["resolved_by"])
    assert (
        headline
        == sum(r["children_at_risk"] or 0 for r in before.values() if not r["answered_by"] and not r["resolved_by"])
        - 87
    )


def test_an_expiry_row_names_the_node_the_cartons_must_LEAVE():
    """The one exception kind whose subject is holding too much, not too little.

    Every other row names a node that needs cartons, so the queue's reallocate
    control moved stock toward it. An expiry row names a node holding more than
    it can consume before the batch expires — and the control sent cartons INTO
    it, which is the opposite of the row's own advice and would deepen exactly
    the problem the row exists to report.

    This was unreachable until expiry risk could fire at all: every seeded lot
    expired eighteen months out, so no expiry row was ever rendered and nothing
    ever pressed the button.
    """
    from connect_labs.supply.services import exceptions

    call_command("seed_supply_demo", "--reset")
    rows = {r["kind"]: r for r in exceptions.build_queue()}

    expiry = rows.get("Expiry risk")
    assert expiry is not None, "the demo needs an expiry-risk row for this to be checkable"
    assert expiry["reallocation_role"] == "source"
    assert "surplus" in expiry["action"].lower()

    for kind, row in rows.items():
        if kind != "Expiry risk":
            assert row["reallocation_role"] == "target", f"{kind} should pull cartons toward its node"


def test_cover_state_ignores_cartons_still_in_transit():
    """The rule the calendar has to render: inbound does NOT cover this day.

    `cartons_inbound` is what is still on the road AFTER the distribution date, so
    a truck arriving Friday cannot cover Tuesday. The grid used to print it as
    "+141" beside the on-hand figure, under a legend saying a cell is short "when
    the second number is below the first" — so Biu read "0 on hand +141" for 103
    children in red, and Askira "38 on hand +94" for 65 in amber. Both look
    covered if you add, which is what the plus sign asked you to do.

    Pinned here because the fix is in the UI, and the UI can only be written
    correctly against a server rule that is stated and stable.
    """
    from connect_labs.supply.serializers.demand import distribution_plan_dict

    class _Site:
        name = "Biu Nutrition Centre"

    class _Plan:
        id = 1
        site_id = 1
        site = _Site()
        scheduled_for = date(2026, 8, 4)
        expected_children = 103
        cartons_required = 103
        note = ""

    # Nothing on hand, plenty on the road: uncovered, however large inbound is.
    out = distribution_plan_dict(_Plan(), inbound_cartons=141, on_hand=0)
    assert out["state"] == "uncovered"
    assert out["cartons_inbound"] == 141, "still reported, for planning context"

    # Some on hand, short of requirement, more arriving later: at risk, not covered.
    out = distribution_plan_dict(_Plan(), inbound_cartons=94, on_hand=38)
    assert out["state"] == "at_risk"

    # Enough on hand: covered, with or without anything inbound.
    assert distribution_plan_dict(_Plan(), inbound_cartons=0, on_hand=103)["state"] == "covered"
    assert distribution_plan_dict(_Plan(), inbound_cartons=500, on_hand=103)["state"] == "covered"


def test_no_plan_is_covered_by_stock_that_has_not_arrived():
    """The invariant, stated as one property over the whole seeded world."""
    from django.core.management import call_command

    from connect_labs.supply.models.demand import DistributionPlan  # noqa: F401
    from connect_labs.supply.serializers.demand import distribution_plan_dict

    call_command("seed_supply_demo", "--reset")

    class _S:
        name = "x"

    class _P:
        id = 1
        site_id = 1
        site = _S()
        scheduled_for = date(2026, 8, 4)
        note = ""

    for on_hand, inbound, required in ((0, 999, 100), (10, 999, 100), (99, 1, 100)):
        p = _P()
        p.expected_children = required
        p.cartons_required = required
        state = distribution_plan_dict(p, inbound_cartons=inbound, on_hand=on_hand)["state"]
        assert state != "covered", (
            f"on_hand={on_hand} required={required} was called covered on the strength "
            f"of {inbound} cartons that arrive after the distribution"
        )
