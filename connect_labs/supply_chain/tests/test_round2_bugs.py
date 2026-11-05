"""Round-2 walk of the demo: expiring stock, plain numbers, the chase list's count and its partner section.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, Movement, StockIssue, SupplyPoint, scope_key
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.portfolio.chase import chase_items, grouped
from connect_labs.supply_chain.values import plain_number

pytestmark = pytest.mark.django_db

PROGRAM = 10981


def _access():
    return SupplyDataAccess(access_token="placeholder", program_id=PROGRAM, caller=SYSTEM)


def _op(name, **payload):
    return call_operation(name, _access(), payload)


class TestPlainNumber:
    @pytest.mark.parametrize(
        "value, shown",
        [
            (Decimal("40"), "40"),
            (Decimal("40.0000"), "40"),
            (Decimal("1288.4000"), "1,288.4"),
            (Decimal("1500"), "1,500"),
            (Decimal("0.5"), "0.5"),
            ("40", "40"),
            (Decimal("1E+3"), "1,000"),
        ],
    )
    def test_a_quantity_is_a_plain_number_with_thousands_separated(self, value, shown):
        assert plain_number(value) == shown
        assert "E" not in plain_number(value)


@pytest.fixture
def depot():
    commodity = Commodity.objects.create(
        scope_key=scope_key(program_id=PROGRAM), slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton"
    )
    item = Item.objects.create(
        scope_key=scope_key(program_id=PROGRAM),
        sku="rutf-150",
        name="RUTF 150",
        commodity=commodity,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    partner = LabsOrg.objects.create(slug="north-star", name="North Star")
    point = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="ns-wh",
        name="North Star warehouse",
        kind="central_store",
        managed_by_org=partner,
        source="we_recorded",
    )
    expiry = date.today() + timedelta(days=71)
    Movement.objects.create(
        program_id=PROGRAM,
        kind="adjustment",
        occurred_on=date.today() - timedelta(days=30),
        to_supply_point=point,
        commodity=commodity,
        item=item,
        batch="DEMO-B1",
        expiry=expiry,
        quantity=Decimal("100"),
        quantity_unit="carton",
        source="we_recorded",
    )
    return {"point": point, "expiry": expiry, "partner": partner}


def _report(depot, kind, quantity="40", occurred_on=None):
    return _op(
        "stock_issue_report",
        data={
            "supply_point_id": depot["point"].pk,
            "kind": kind,
            "occurred_on": (occurred_on or date.today()).isoformat(),
            "commodity_slug": "rutf",
            "batch": "DEMO-B1",
            "quantity": quantity,
            "quantity_unit": "carton",
            "source": "partner_reported",
        },
    )


class TestExpiringIsAWarningNotALoss:
    def test_an_expired_report_on_a_batch_still_in_date_is_a_warning_and_takes_nothing_off_stock(self, depot):
        issue = _report(depot, "expired")
        assert issue["kind"] == "expiring"
        stored = StockIssue.objects.get()
        assert stored.kind == "expiring"
        assert not Movement.objects.filter(stock_issue=stored).exists(), "a warning moves no stock"

    def test_a_batch_past_its_date_is_expired_and_comes_off_stock(self, depot):
        Movement.objects.filter(batch="DEMO-B1").update(expiry=date.today() - timedelta(days=3))
        issue = _report(depot, "expired")
        assert issue["kind"] == "expired"
        assert Movement.objects.filter(stock_issue_id=issue["id"], kind="expiry").count() == 1

    def test_reporting_it_as_expiring_soon_directly_is_a_warning_too(self, depot):
        issue = _report(depot, "expiring")
        assert issue["kind"] == "expiring"
        assert not Movement.objects.filter(stock_issue_id=issue["id"]).exists()

    def test_writing_a_warning_off_takes_the_stock_off_only_then(self, depot):
        issue = _report(depot, "expiring")
        _op(
            "stock_issue_resolve",
            issue_id=issue["id"],
            data={"outcome": "written_off", "resolved_on": date.today().isoformat(), "source": "we_recorded"},
        )
        assert Movement.objects.filter(stock_issue_id=issue["id"], kind="expiry").count() == 1

    def test_using_it_first_leaves_the_stock_alone(self, depot):
        issue = _report(depot, "expiring")
        _op(
            "stock_issue_resolve",
            issue_id=issue["id"],
            data={"outcome": "recovered", "resolved_on": date.today().isoformat(), "source": "we_recorded"},
        )
        assert not Movement.objects.filter(stock_issue_id=issue["id"]).exists()

    def test_the_overview_says_expiring_soon_with_a_plain_forty_and_no_loss_count(self, depot):
        from connect_labs.supply_chain.issues import open_issues
        from connect_labs.supply_chain.stock.services.issues import open_issue_summary

        _report(depot, "expired")
        row = open_issues(PROGRAM)[0]
        assert row["title"].startswith("Expiring soon: 40 carton of RUTF at North Star warehouse")
        assert "E+" not in row["title"]
        assert row["warning"] is True and "expires in 71 days" in row["when"]
        summary = open_issue_summary(PROGRAM)
        assert summary["expiring_soon"] == 1 and summary["quantity_by_unit"] == {}

    def test_chase_list_and_issues_panel_use_the_same_name_and_the_same_age(self, depot):
        from connect_labs.supply_chain.issues import open_issues

        check = {
            "kind": "stock_expiring",
            "audience": "internal",
            "subject": {"label": "North Star warehouse — batch DEMO-B1"},
            "facts": {"expiry": depot["expiry"].isoformat(), "quantity": "40 carton", "expired": False},
            "days_open": 18,
        }
        items = chase_items(
            program_id=PROGRAM,
            program_name="Demo",
            checks=[check],
            consignments=[],
            orders=[],
            suppliers={},
            places={},
            check_labels={"stock_expiring": "Stock expiring soon or already expired"},
        )
        _report(depot, "expired")
        panel = open_issues(PROGRAM)[0]
        assert items[0]["what"] == "Expiring soon" and panel["title"].startswith("Expiring soon")
        assert "expires in 71 days" in items[0]["about"] and "expires in 71 days" in panel["when"]
        assert items[0]["days"] is None and panel["days_open"] is None, "a warning has no 'days open'"

    def test_a_past_date_is_expired_and_aged_from_the_day_it_expired(self):
        check = {
            "kind": "stock_expiring",
            "audience": "internal",
            "subject": {"label": "A store — batch B"},
            "facts": {"expiry": (date.today() - timedelta(days=1)).isoformat(), "quantity": "5 carton"},
            "days_open": 91,
        }
        items = chase_items(
            program_id=1,
            program_name="Demo",
            checks=[check],
            consignments=[],
            orders=[],
            suppliers={},
            places={},
            check_labels={},
        )
        assert items[0]["what"] == "Expired" and items[0]["days"] == 1
        assert "expired 1 day ago" in items[0]["about"]


class TestChaseListCountsWhatItShows:
    def _check(self, kind, audience, label="X"):
        return {"kind": kind, "audience": audience, "subject": {"label": label}, "facts": {}, "days_open": 3}

    def _items(self, **extra):
        checks = [
            self._check("quote_not_comparable", "supplier", "DABS — quote"),
            self._check("stock_variance", "internal", "flw_001"),
            # "ours" is a real audience the checks emit; it used to fall out of every section.
            self._check("charge_paid_unevidenced", "ours", "Shipment 1"),
            self._check("shipment_quantity_unaccounted", "ours", "Shipment 2"),
        ]
        return chase_items(
            program_id=1,
            program_name="Demo",
            checks=checks,
            consignments=[],
            orders=[],
            suppliers={},
            places={},
            check_labels={},
            **extra,
        )

    def test_every_item_is_in_a_section_so_the_header_matches_the_rows(self):
        items = self._items()
        shown = sum(len(g["items"]) for g in grouped(items))
        assert len(items) == shown == 4

    def test_a_split_partner_that_has_not_ordered_is_a_partner_item(self):
        tender = {
            "id": 7,
            "label": "Demo split tender",
            "status": "awarded",
            "lines": [],
            "partner_splits": [
                {"org_id": 1, "org_name": "North Star", "quantity": "900", "quantity_unit": "carton"},
                {"org_id": 2, "org_name": "Savanna", "quantity": "600", "quantity_unit": "carton"},
            ],
        }
        ordered = [{"tender_id": 7, "status": "placed", "partner_org_id": 1, "quantity": "900"}]
        sections = grouped(self._items(tenders=[tender], contracts=ordered))
        partners = next(g for g in sections if g["audience"] == "partner")
        assert [i["who"] for i in partners["items"]] == ["Savanna"]
        assert partners["items"][0]["ask"].startswith("Ask Savanna to place their order")
        assert sum(len(g["items"]) for g in sections) == 5

    def test_an_open_taken_issue_is_followed_up_with_the_partner(self):
        issue = {
            "kind": "taken",
            "partner": "North Star",
            "title": "Taken: 8 carton of RUTF at North Star warehouse",
            "status": "being followed up",
            "days_open": 12,
            "href": "/supply/issues/",
        }
        sections = grouped(self._items(issues=[issue]))
        partners = next(g for g in sections if g["audience"] == "partner")
        assert partners["items"][0]["who"] == "North Star" and partners["items"][0]["days"] == 12


class TestSuppliersToChaseAgreeWithTheReminders:
    def test_a_supplier_never_written_to_is_not_counted_as_one_to_chase(self):
        from connect_labs.supply_chain.overview import next_step

        tender = {"id": 1, "label": "Feb re-quote", "status": "open"}
        contract = {"id": 1, "status": "placed", "tender_id": 0}
        said = next_step(
            tenders=[tender],
            contracts=[contract],
            points=[],
            tender_replies={1: (10, 2, 4)},
        )["next"]
        # 10 invited, 4 never contacted and 2 silent after being asked: 4 answered.
        assert "4 of 10 suppliers answered" in said and "chase 2" in said and "4 not yet written to" in said
        assert "chase the other 6" not in said

    def test_never_contacted_is_the_reminder_rule(self):
        from types import SimpleNamespace

        from connect_labs.supply_chain.moves import never_contacted

        supplier = SimpleNamespace(notes="")
        sent = SimpleNamespace(sent_on=date.today(), notes="", supplier=supplier)
        unsent = SimpleNamespace(sent_on=None, notes="", supplier=supplier)
        noted = SimpleNamespace(sent_on=date.today(), notes="Not yet contacted", supplier=supplier)
        assert never_contacted([sent]) is False
        assert never_contacted([unsent]) is True
        assert never_contacted([noted]) is True


# ---- a store's next delivery counts a supplier's shipment on the road ---------------------------------

from connect_labs.supply_chain.tests import test_stock_page_honesty as honesty  # noqa: E402

da = honesty.da
world = honesty.world


class TestNextDeliveryCountsSupplierShipments:
    def _undated_order(self, world):
        """A placed order with no promised lead time: nothing but its shipment can say when it arrives."""
        return world["contract"](reference="DON-9", status="placed", quantity="600", signed_on=None)

    def test_a_shipment_in_transit_gives_the_store_its_next_delivery(self, da, world):
        from connect_labs.supply_chain.models import Shipment
        from connect_labs.supply_chain.stock.services import network

        order = self._undated_order(world)
        arrives = date.today() + timedelta(days=15)
        Shipment.objects.create(
            contract_id=order["id"], reference="SHP-1", status="in_transit", expected_on=arrives, source="we_recorded"
        )
        points = list(SupplyPoint.objects.filter(program_id=honesty.PROGRAM))
        inbound = network._expected_inbound(honesty.PROGRAM, points)
        mine = [o for rows in inbound.values() for o in rows if o["contract_id"] == order["id"]]
        assert mine and mine[0]["expected_on"] == arrives and mine[0]["overdue"] is False

    def test_without_a_shipment_an_undated_order_still_has_no_date(self, da, world):
        from connect_labs.supply_chain.stock.services import network

        order = self._undated_order(world)
        points = list(SupplyPoint.objects.filter(program_id=honesty.PROGRAM))
        inbound = network._expected_inbound(honesty.PROGRAM, points)
        mine = [o for rows in inbound.values() for o in rows if o["contract_id"] == order["id"]]
        assert mine and mine[0]["expected_on"] is None

    def test_the_store_row_says_when_and_otherwise_says_why(self):
        from connect_labs.supply_chain.plan.service import store_rows

        base = {"kind": "facility", "need": ["10", "10", "10", "10"], "workers": 2, "own_on_hand": None}
        fc = {
            "programme": {"inbound": [{"supply_point_id": 1, "expected_on": "2026-11-20", "overdue": False}]},
            "stores": [
                {**base, "supply_point_id": 1, "name": "Riverbend", "runs_dry_on": "2026-10-23"},
                {**base, "supply_point_id": 2, "name": "Savanna store", "runs_dry_on": "2026-10-30"},
            ],
        }
        rows = {
            r["name"]: r
            for r in store_rows(fc, reasons={2: {"text": "Savanna hasn't ordered yet", "href": "/supply/award/1/"}})
        }
        assert rows["Riverbend"]["next_delivery"] == "2026-11-20" and rows["Riverbend"]["no_delivery_reason"] is None
        assert rows["Savanna store"]["next_delivery"] is None
        assert rows["Savanna store"]["no_delivery_reason"]["text"] == "Savanna hasn't ordered yet"
