"""Contracted against required — the signal that should change the next tender.

Coverage says what ARRIVED against what is needed. This says what was BOUGHT
against what is needed, and the difference matters: no reallocation can close a
gap in cartons nobody purchased.
"""
from datetime import date

import pytest

from connect_labs.supply.models import SupplyNode
from connect_labs.supply.services import coverage

from . import factories as f

pytestmark = pytest.mark.django_db

BORNO = "NGA-2839"


def _district(children=5000, adm1_code=BORNO, name="Borno", country="NG"):
    f.CaseloadEstimateFactory(
        adm1_code=adm1_code, children_sam=children, adm1_name=name, country=country, month=date.today().replace(day=1)
    )


def _hub(place, adm1_code=BORNO, country="NG"):
    return f.SupplyNodeFactory(
        name=f"{place} Distribution Hub", kind=SupplyNode.Kind.DISTRIBUTION_HUB, adm1_code=adm1_code, country=country
    )


def _contract_into(place, cartons, country="NG", category="rutf"):
    """A contract committing cartons to a delivery place, via its award's lot."""
    org = f.SupplierOrgFactory()
    lot = f.LotFactory(category=category, delivery_place=place, delivery_country=country, quantity=cartons)
    return f.ContractFactory(org=org, award=f.AwardFactory(lot=lot), total_quantity=cartons, unit="cartons")


def test_a_district_with_nothing_bought_shows_its_whole_requirement_as_the_gap():
    _district(children=5000)
    _hub("Maiduguri")

    rows = coverage.procurement_gap(country="NG")
    borno = [r for r in rows if r["adm1_code"] == BORNO][0]
    assert borno["required_cartons"] == 5000
    assert borno["contracted_cartons"] == 0
    assert borno["gap_cartons"] == 5000
    assert borno["contracted_percent"] == 0.0


def test_contracted_volume_closes_the_gap_whether_or_not_it_has_shipped():
    """A contract is a purchase. The gap is about buying, not moving.

    A district fully contracted but with nothing yet on the road has a supply
    problem the command centre should raise — and no procurement problem, which
    is precisely the distinction this figure exists to draw.
    """
    _district(children=5000)
    _hub("Maiduguri")
    _contract_into("Maiduguri", 5000)

    borno = [r for r in coverage.procurement_gap(country="NG") if r["adm1_code"] == BORNO][0]
    assert borno["contracted_cartons"] == 5000
    assert borno["gap_cartons"] == 0
    assert borno["contracted_percent"] == 100.0


def test_a_partly_contracted_district_reports_the_remainder():
    _district(children=5000)
    _hub("Maiduguri")
    _contract_into("Maiduguri", 3000)

    borno = [r for r in coverage.procurement_gap(country="NG") if r["adm1_code"] == BORNO][0]
    assert borno["gap_cartons"] == 2000
    assert borno["contracted_percent"] == 60.0


def test_a_haulage_contract_buys_movement_not_cartons_into_a_district():
    """Counting freight as supply would report a district as bought when it is not."""
    _district(children=5000)
    _hub("Maiduguri")
    _contract_into("Maiduguri", 5000, category="transport")

    borno = [r for r in coverage.procurement_gap(country="NG") if r["adm1_code"] == BORNO][0]
    assert borno["contracted_cartons"] == 0
    assert borno["gap_cartons"] == 5000


def test_the_summary_leads_with_the_worst_district():
    _district(children=5000, adm1_code=BORNO, name="Borno")
    _district(children=1000, adm1_code="NGA-2873", name="Yobe")
    _hub("Maiduguri", adm1_code=BORNO)
    _hub("Damaturu", adm1_code="NGA-2873")
    _contract_into("Damaturu", 900)

    summary = coverage.procurement_gap_summary(country="NG")
    assert summary["districts_short"] == 2
    assert summary["worst"]["adm1_name"] == "Borno"
    assert summary["gap_cartons"] == 5000 + 100


def test_the_procurement_dashboard_receives_the_gap(client):
    """A gap nobody's dashboard shows is a gap no tender will close."""
    import json as _json

    from django.core.management import call_command

    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    body = _json.loads(client.get("/supply/api/bootstrap/").content)

    assert "procurement_gap" in body, "the procurement dashboard payload carries no gap analysis"
    gap = body["procurement_gap"]
    assert gap["districts_total"] > 0
    for row in gap["districts"]:
        assert {"adm1_name", "required_cartons", "contracted_cartons", "gap_cartons", "method"} <= set(row)
        # The identity the whole app is denominated in must hold on every row.
        assert row["gap_cartons"] == max(row["required_cartons"] - row["contracted_cartons"], 0)
