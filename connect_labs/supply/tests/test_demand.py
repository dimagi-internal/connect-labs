"""Coverage, the exception queue, the partner surface, and the split award.

Everything here is a claim one of the four OES narratives makes out loud. If a
test in this file fails, a scene is lying.
"""
from decimal import Decimal

import pytest
from django.core.management import call_command

from connect_labs.supply.models import ChildOutcome, Discrepancy, Shipment, ShortfallSignal, SupplyAction, SupplyNode
from connect_labs.supply.services import coverage, exceptions

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
    """Scene 8 of oes-supply-base: two corridors, two suppliers, one tender."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import RFP, Award

    rfp = RFP.objects.get(title="RUTF Sahel and Lake Chad Corridors Q3 2026")
    assert rfp.status == RFP.Status.AWARDED
    awards = Award.objects.filter(lot__rfp=rfp).select_related("lot_bid__bid__org", "lot")
    assert awards.count() == 2
    winners = {a.lot_bid.bid.org.legal_name for a in awards}
    assert len(winners) == 2, f"the split has to be visible, got {winners}"
    places = {a.lot.delivery_place for a in awards}
    assert places == {"Maiduguri", "Djibo"}


def test_the_price_leader_differs_by_lot():
    """The information a per-tender comparison would have hidden."""
    call_command("seed_supply_demo")
    from connect_labs.supply.models import RFP
    from connect_labs.supply.services import rfp_actions

    rfp = RFP.objects.get(title="RUTF Sahel and Lake Chad Corridors Q3 2026")
    leaders = []
    for lot in rfp.lots.all().order_by("delivery_place"):
        ranked = rfp_actions.lot_comparison(lot)
        leaders.append(ranked[0].bid.org.legal_name)
    assert len(set(leaders)) == 2, f"expected two different price leaders, got {leaders}"


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
    total = ChildOutcome.objects.count()
    assert total > 50, "need a cohort big enough for the rates to mean anything"
    recovered = ChildOutcome.objects.filter(discharge_status=ChildOutcome.Discharge.RECOVERED).count()
    defaulted = ChildOutcome.objects.filter(discharge_status=ChildOutcome.Discharge.DEFAULTED).count()
    assert recovered / total > 0.75
    assert defaulted / total < 0.15


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
