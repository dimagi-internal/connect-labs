"""Our own stock on the road: a consignment between two of our places.

The rule under test is design §19.1 -- in transit is a real position and never
stock. So a dispatch must drop the sender's stock at once WITHOUT adding to the
destination's, and only receipt moves it in. Every balance stays movements in
minus movements out; the consignment only names the pair of ledger legs.

Placeholder names and figures only (public repo).
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Consignment, SupplyPoint, scope_key
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import ledger

pytestmark = pytest.mark.django_db

PROGRAM = 10981


def _access():
    return SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM)


def _op(name, **payload):
    return call_operation(name, _access(), payload)


@pytest.fixture
def network():
    Commodity.objects.create(
        scope_key=scope_key(program_id=PROGRAM), slug="a-product", name="A Product", base_unit="unit"
    )
    warehouse = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="a-warehouse", name="A Warehouse", kind="central_store", source="we_recorded"
    )
    office = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="an-office", name="An Office", kind="facility", source="we_recorded"
    )
    _op(
        "movement_record",
        data={
            "kind": "adjustment",
            "to_supply_point_id": warehouse.pk,
            "occurred_on": (date.today() - timedelta(days=30)).isoformat(),
            "commodity_slug": "a-product",
            "quantity": "100",
            "quantity_unit": "unit",
            "source": "we_recorded",
        },
    )
    return warehouse, office


def _dispatch(warehouse, office, *, quantity="40", expected_in_days=5, left_days_ago=3):
    data = {
        "from_supply_point_id": warehouse.pk,
        "to_supply_point_id": office.pk,
        "commodity_slug": "a-product",
        "quantity": quantity,
        "quantity_unit": "unit",
        "dispatched_on": (date.today() - timedelta(days=left_days_ago)).isoformat(),
        "source": "we_recorded",
    }
    if expected_in_days is not None:
        data["expected_on"] = (date.today() + timedelta(days=expected_in_days)).isoformat()
    return _op("consignment_dispatch", data=data)


def _amount(value):
    return getattr(value, "amount", None)


def test_dispatch_drops_the_sender_at_once_and_does_not_add_to_the_destination(network):
    """The whole point: on the road is neither here nor there.

    MUTATED: the dispatch leg posted straight into the destination -- the
    office's on-hand came back 40 and this went red.
    """
    warehouse, office = network
    consignment = _dispatch(warehouse, office)

    assert consignment["status"] == "dispatched"
    assert _amount(ledger.balance(PROGRAM, warehouse)) == Decimal("60")
    assert _amount(ledger.balance(PROGRAM, office)) in (None, Decimal("0"))
    assert _amount(ledger.in_transit(PROGRAM, office)) == Decimal("40")


def test_receipt_moves_it_in_and_off_the_road(network):
    warehouse, office = network
    consignment = _dispatch(warehouse, office)

    received = _op(
        "consignment_receive", consignment_id=consignment["id"], data={"received_on": date.today().isoformat()}
    )

    assert received["status"] == "received"
    assert _amount(ledger.balance(PROGRAM, office)) == Decimal("40")
    assert _amount(ledger.in_transit(PROGRAM, office)) in (None, Decimal("0"))
    via = SupplyPoint.objects.get(pk=received["via_supply_point_id"])
    assert _amount(ledger.balance(PROGRAM, via)) in (None, Decimal("0"))


def test_a_short_arrival_writes_the_rest_off_rather_than_leaving_it_on_the_road(network):
    """MUTATED: the loss leg removed -- 10 units stayed at the in-transit point forever."""
    warehouse, office = network
    consignment = _dispatch(warehouse, office)

    _op(
        "consignment_receive",
        consignment_id=consignment["id"],
        data={"received_on": date.today().isoformat(), "quantity_received": "30"},
    )

    via = SupplyPoint.objects.get(kind="in_transit", program_id=PROGRAM)
    assert _amount(ledger.balance(PROGRAM, office)) == Decimal("30")
    assert _amount(ledger.balance(PROGRAM, via)) in (None, Decimal("0"))
    losses = _op("movement_list", kind="loss")
    assert [(m["quantity"], m["from_supply_point_id"]) for m in losses] == [("10", via.pk)] or [
        (Decimal(str(m["quantity"])), m["from_supply_point_id"]) for m in losses
    ] == [(Decimal("10"), via.pk)]


def test_a_consignment_is_received_once(network):
    warehouse, office = network
    consignment = _dispatch(warehouse, office)
    _op("consignment_receive", consignment_id=consignment["id"], data={"received_on": date.today().isoformat()})

    with pytest.raises(ValueError, match="already received"):
        _op("consignment_receive", consignment_id=consignment["id"], data={"received_on": date.today().isoformat()})


def test_a_consignment_cannot_go_to_where_it_left_from(network):
    warehouse, _ = network
    with pytest.raises(ValueError, match="cannot be sent to the place it leaves from"):
        _dispatch(warehouse, warehouse)


def test_one_past_its_expected_date_is_a_check_and_one_with_no_date_is_not(network):
    """No date promised means it cannot be late -- only open (design §6a)."""
    warehouse, office = network
    late = _dispatch(warehouse, office, expected_in_days=-2, left_days_ago=9)
    _dispatch(warehouse, office, quantity="5", expected_in_days=None)

    found = [c for c in _op("checks_list")["checks"] if c["kind"] == "consignment_overdue"]

    assert [(c["subject"]["id"], c["facts"]["days_late"], c["facts"]["to_supply_point_id"]) for c in found] == [
        (late["id"], 2, office.pk)
    ]


def test_purge_clears_consignments_with_their_legs(network):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=PROGRAM, program_id=PROGRAM, labs_only=True, label="placeholder"
    )
    warehouse, office = network
    _dispatch(warehouse, office)

    counts = _access().purge()

    assert counts.get("consignments") == 1
    assert not Consignment.objects.filter(program_id=PROGRAM).exists()


def test_the_dispatch_screen_sends_one(client, django_user_model, network, monkeypatch):
    """The screen drives the same operation; posting it leaves a consignment on the road."""
    from connect_labs.supply_chain import form_views, stock_views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    warehouse, office = network
    client.force_login(django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com"))

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    monkeypatch.setattr("connect_labs.supply_chain.form_views.has_program_context", lambda request: True)
    monkeypatch.setattr("connect_labs.supply_chain.form_views._access", _scoped)
    monkeypatch.setattr("connect_labs.supply_chain.stock_views._access", _scoped)

    commodity = Commodity.objects.get(slug="a-product", scope_key=scope_key(program_id=PROGRAM))
    response = client.post(
        reverse("supply_chain:consignment_dispatch"),
        {
            "from_supply_point": warehouse.pk,
            "to_supply_point": office.pk,
            "commodity": commodity.pk,
            "quantity": "12",
            "quantity_unit": "unit",
            "dispatched_on": date.today().isoformat(),
            "source": "we_recorded",
        },
    )

    assert response.status_code == 302, [
        line.strip() for line in response.content.decode().splitlines() if "error" in line.lower()
    ][:10]
    assert Consignment.objects.filter(program_id=PROGRAM, to_supply_point=office, status="dispatched").count() == 1


def test_the_road_is_not_a_place_the_network_counts_or_checks(network):
    """The in-transit point parks goods on the ledger; it is not a store.

    MUTATED: the `exclude(kind="in_transit")` dropped from network_stock -- the
    road showed up as a place with a "never reported stock" check on it.
    """
    warehouse, office = network
    _dispatch(warehouse, office)
    via = SupplyPoint.objects.get(kind="in_transit", program_id=PROGRAM)

    points = {row["supply_point_id"] for row in _op("network_stock")["points"]}
    checked = {c["subject"]["id"] for c in _op("checks_list")["checks"] if c["subject"]["type"] == "supply_point"}
    summary = _op("chain_summary")

    assert via.pk not in points
    assert via.pk not in checked
    assert summary["deliver"]["network"]["supply_points"] == 2


def test_a_link_from_the_map_prefills_the_dispatch_and_ignores_another_programs_place(
    client, django_user_model, network, monkeypatch
):
    """The map's "Send from there" link names places by id; only this program's are used.

    MUTATED: the program filter dropped from get_initial -- the other program's
    store was prefilled as the sender, red.
    """
    from connect_labs.supply_chain import form_views, stock_views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    warehouse, office = network
    elsewhere = SupplyPoint.objects.create(
        program_id=PROGRAM + 1, slug="elsewhere", name="Elsewhere", kind="central_store", source="we_recorded"
    )
    client.force_login(django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com"))

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    monkeypatch.setattr("connect_labs.supply_chain.form_views.has_program_context", lambda request: True)
    monkeypatch.setattr("connect_labs.supply_chain.form_views._access", _scoped)
    monkeypatch.setattr("connect_labs.supply_chain.stock_views._access", _scoped)

    url = reverse("supply_chain:consignment_dispatch")
    ours = client.get(
        url,
        {"from_supply_point": warehouse.pk, "to_supply_point": office.pk, "commodity": "a-product", "quantity": "7"},
    )
    theirs = client.get(url, {"from_supply_point": elsewhere.pk})

    initial = ours.context["form"].initial
    assert (initial["from_supply_point"], initial["to_supply_point"], initial["quantity"]) == (
        warehouse.pk,
        office.pk,
        "7",
    )
    assert initial["commodity"] == Commodity.objects.get(slug="a-product", scope_key=scope_key(program_id=PROGRAM)).pk
    assert "from_supply_point" not in theirs.context["form"].initial
