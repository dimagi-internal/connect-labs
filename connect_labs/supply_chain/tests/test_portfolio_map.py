"""The portfolio map: the same programs as the portfolio, laid out by place.

The access gate comes first for the reason test_portfolio.py gives -- a map is
a portfolio row with coordinates, and a row is what leaks. After it, the two
rules the map adds: a place with no coordinates is listed rather than dropped,
and a blocker lands on the place it bites.

Every name and figure here is an invented placeholder (public repo).
"""

import json
import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.models import Commodity, Contract, Supplier, SupplyPoint, scope_key
from connect_labs.supply_chain.portfolio.map_data import _attribute
from connect_labs.supply_chain.portfolio.models import Portfolio

pytestmark = pytest.mark.django_db

ONE = 10961
TWO = 10962
THREE = 10963
NAMES = {ONE: "Placeholder One", TWO: "Placeholder Two", THREE: "Placeholder Three"}


def _sign_in(client, django_user_model, program_ids):
    user = django_user_model.objects.create_user(username="jo", password="x")
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "placeholder-token",
        "organization_data": {
            "organizations": [],
            "programs": [{"id": pid, "name": NAMES[pid]} for pid in program_ids],
            "opportunities": [],
        },
    }
    session.save()
    return user


def _portfolio(program_ids=(ONE, TWO, THREE)):
    return Portfolio.objects.create(
        slug="a-placeholder-portfolio", name="A Placeholder Portfolio", program_ids=list(program_ids)
    )


def _payload(response):
    """The JSON the page draws from -- the thing that would leak, so the thing asserted on."""
    match = re.search(r'<script id="pm-data" type="application/json">(.*?)</script>', response.content.decode(), re.S)
    assert match, "the page carries no map payload"
    return json.loads(match.group(1))


def _store(program_id, *, slug="a-placeholder-store", lat=None, lng=None, kind="central_store"):
    return SupplyPoint.objects.create(
        program_id=program_id,
        slug=slug,
        name=slug.replace("-", " ").title(),
        kind=kind,
        latitude=lat,
        longitude=lng,
        source="we_recorded",
    )


def _order_to(store, *, lead_time_days):
    scope = scope_key(program_id=store.program_id)
    commodity = Commodity.objects.create(
        scope_key=scope, slug="a-placeholder-product", name="a placeholder product", base_unit="placeholder unit"
    )
    supplier = Supplier.objects.enrol(scope_key=scope, name="A Placeholder Supplier")
    return Contract.objects.create(
        program_id=store.program_id,
        supplier=supplier,
        commodity=commodity,
        buyer_of_record="program_org",
        status="placed",
        signed_on=date.today() - timedelta(days=30),
        quantity=100,
        quantity_unit="placeholder unit",
        delivery_supply_point=store,
        promised_lead_time_days=lead_time_days,
        source="we_recorded",
    )


def _url(portfolio):
    return reverse("supply_chain:portfolio_map", args=[portfolio.slug])


# ---------------------------------------------------------------------------
# 1. Access. The gate.
# ---------------------------------------------------------------------------


def test_the_map_carries_only_the_programs_the_viewer_can_already_reach(client, django_user_model):
    """MUTATED: `portfolio_map` changed to skip the `reachable` check -- red."""
    _sign_in(client, django_user_model, [ONE, TWO])
    for pid in (ONE, TWO, THREE):
        _store(pid, slug=f"a-store-in-{pid}", lat=9.0, lng=8.0)
    portfolio = _portfolio()

    response = client.get(_url(portfolio))
    payload = _payload(response)

    assert response.status_code == 200
    assert [p["program_id"] for p in payload["programs"]] == [ONE, TWO]
    assert payload["hidden"] == 1
    body = response.content.decode()
    assert NAMES[THREE] not in body
    assert f"a-store-in-{THREE}".replace("-", " ").title() not in body
    assert "1 of this portfolio's 3 programs is not shown" in body


def test_the_map_leaves_the_unreachable_one_out_even_with_the_layer_below_removed(
    client, django_user_model, monkeypatch
):
    """With data access built as SYSTEM, the reachability check is the only gate.

    MUTATED, with this substitution in place: the `reachable` check removed
    from `portfolio_map`. THREE's store appeared in the payload -- red.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain import data_access as data_access_module

    def _unauthorised(*args, caller=None, **kwargs):
        return data_access_module.SupplyDataAccess(*args, caller=SYSTEM, **kwargs)

    monkeypatch.setattr("connect_labs.supply_chain.portfolio.map_data.SupplyDataAccess", _unauthorised)
    _sign_in(client, django_user_model, [ONE])
    _store(THREE, slug="a-store-nobody-here-holds", lat=9.0, lng=8.0)

    payload = _payload(client.get(_url(_portfolio())))

    assert [p["program_id"] for p in payload["programs"]] == [ONE]
    assert "A Store Nobody Here Holds" not in json.dumps(payload)


def test_the_map_needs_a_sign_in(client):
    response = client.get(_url(_portfolio()))
    assert response.status_code == 302


def test_a_portfolio_nobody_has_named_is_not_found(client, django_user_model):
    _sign_in(client, django_user_model, [ONE])
    response = client.get(reverse("supply_chain:portfolio_map", args=["no-such-portfolio"]))
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# 2. Nothing located is dropped in silence.
# ---------------------------------------------------------------------------


def test_a_place_with_no_coordinates_is_listed_rather_than_dropped(client, django_user_model):
    """The demo's stores have no coordinates today; a map that dropped them would show nothing.

    MUTATED: the `unplaced` branch changed to `continue` -- red.
    """
    _sign_in(client, django_user_model, [ONE])
    _store(ONE, slug="a-placed-store", lat=9.05, lng=7.49)
    _store(ONE, slug="an-unplaced-store")

    program = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]

    assert [p["name"] for p in program["points"]] == ["A Placed Store"]
    assert [p["name"] for p in program["unplaced"]] == ["An Unplaced Store"]
    # Unplaced still carries what a placed one does: the status is the point.
    assert program["unplaced"][0]["status"]
    assert program["unplaced"][0]["links"]["edit"].endswith(f"?program_id={ONE}")


def test_coordinates_off_the_globe_are_not_drawn(client, django_user_model):
    _sign_in(client, django_user_model, [ONE])
    _store(ONE, slug="a-mistyped-store", lat=907.0, lng=8.0)

    program = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]

    assert program["points"] == []
    assert [p["name"] for p in program["unplaced"]] == ["A Mistyped Store"]


# ---------------------------------------------------------------------------
# 3. A blocker lands on the place it bites.
# ---------------------------------------------------------------------------


def test_an_overdue_order_is_a_blocker_on_the_store_it_is_owed_to(client, django_user_model):
    """The check is about the ORDER; the map puts it on the order's destination.

    MUTATED: `_attribute` returning None for contract subjects -- the check
    moved to `unlocated_checks` and this went red.
    """
    _sign_in(client, django_user_model, [ONE])
    store = _store(ONE, slug="a-waiting-store", lat=9.0, lng=8.0)
    _order_to(store, lead_time_days=10)

    program = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]
    point = program["points"][0]

    assert "contract_delivery_overdue" in {c["kind"] for c in point["checks"]}
    assert "contract_delivery_overdue" not in {c["kind"] for c in program["unlocated_checks"]}
    assert point["expected_inbound"][0]["overdue"] is True
    assert point["expected_inbound"][0]["order_url"].endswith(f"?program_id={ONE}")


def test_attribution_follows_a_shipment_to_its_orders_destination_and_leaves_the_rest_unplaced():
    contracts = {7: {"delivery_supply_point_id": 3}}
    shipments = {11: {"contract_id": 7}}

    def check(type_, id_, facts=None):
        return {"subject": {"type": type_, "id": id_}, "facts": facts or {}}

    assert _attribute(check("supply_point", 5), contracts, shipments) == 5
    assert _attribute(check("contract", 7), contracts, shipments) == 3
    assert _attribute(check("shipment", 11), contracts, shipments) == 3
    assert _attribute(check("payment", 2, {"contract_id": 7}), contracts, shipments) == 3
    assert _attribute(check("quote", 1), contracts, shipments) is None
    assert _attribute(check("contract", 99), contracts, shipments) is None


def test_the_portfolio_page_links_to_its_map(client, django_user_model):
    _sign_in(client, django_user_model, [ONE])
    portfolio = _portfolio([ONE])
    body = client.get(reverse("supply_chain:portfolio", args=[portfolio.slug])).content.decode()
    assert _url(portfolio) in body


# ---------------------------------------------------------------------------
# 4. What is in the way, by stage, and what is on its way.
# ---------------------------------------------------------------------------


def test_every_check_is_in_the_program_list_with_its_stage_whether_or_not_it_has_a_place(client, django_user_model):
    """Most of what blocks a chain is not at a place; the panel must still carry it.

    MUTATED: `every.append(wired)` removed -- the program's `checks` came back empty.
    """
    _sign_in(client, django_user_model, [ONE])
    store = _store(ONE, slug="a-waiting-store", lat=9.0, lng=8.0)
    _order_to(store, lead_time_days=10)

    program = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]
    overdue = [c for c in program["checks"] if c["kind"] == "contract_delivery_overdue"]

    assert overdue and overdue[0]["stage"] == "order"
    assert overdue[0]["supply_point_id"] == store.pk
    assert overdue[0]["href"].endswith(f"?program_id={ONE}")


def test_an_order_with_no_promised_date_is_on_its_way_with_no_date_not_dropped(client, django_user_model):
    """The chlorine chain's story: owed, and nobody has said when.

    MUTATED: orders filtered to those with an expected date -- red.
    """
    _sign_in(client, django_user_model, [ONE])
    store = _store(ONE, slug="an-owed-store", lat=9.0, lng=8.0)
    contract = _order_to(store, lead_time_days=None)

    program = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]

    assert [
        (o["contract_id"], o["to_supply_point_id"], o["expected_on"], o["overdue"]) for o in program["orders"]
    ] == [(contract.pk, store.pk, None, False)]


def test_a_supplier_is_placed_from_its_own_country_and_says_how_finely(client, django_user_model):
    _sign_in(client, django_user_model, [ONE])
    store = _store(ONE, slug="a-store", lat=9.0, lng=8.0)
    contract = _order_to(store, lead_time_days=10)
    # A supplier's country is its company's, on the organisation (#2019).
    LabsOrg.objects.filter(supplier_links__pk=contract.supplier_id).update(country="IN")

    supplier = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]["suppliers"][0]

    assert supplier["location"]["precision"] == "country"
    assert supplier["location"]["lat"] is not None


def test_a_supplier_with_no_country_is_not_given_a_place(client, django_user_model):
    _sign_in(client, django_user_model, [ONE])
    _order_to(_store(ONE, slug="a-store", lat=9.0, lng=8.0), lead_time_days=10)

    supplier = _payload(client.get(_url(_portfolio([ONE]))))["programs"][0]["suppliers"][0]

    assert supplier["location"] is None
