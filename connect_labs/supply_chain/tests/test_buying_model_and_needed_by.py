"""How an order is bought, and the date a tender must start by.

Sophie Feintuch's RUTF feedback, "How buying works for us": an order records whether we buy and ship
to the partner or the partner buys on our quote and invoices us (`buying_model`, the key the Overview
reads); a tender records when the goods are needed and counts back to the day it must start
(`needed_by`, `start_by`).

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is invented.
"""

from datetime import date, timedelta

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.overview import buying_model_label, next_step
from connect_labs.supply_chain.procurement.services.timing import DEFAULT_REPLY_DAYS, timing

pytestmark = pytest.mark.django_db

PROGRAM = 10771
TODAY = date.today()


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
    supplier = op(da, "supplier_create", data={"name": "Quill Foods"})
    us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
    partner = op(da, "org_upsert", data={"slug": "partner-a", "name": "Partner A"})
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Tender 1",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    return {"supplier": supplier, "us": us, "partner": partner, "tender": tender}


def _order(da, world, **extra):
    return op(
        da,
        "contract_create",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": world["supplier"]["id"],
            "source": "we_recorded",
            **extra,
        },
    )


class TestBuyingModel:
    def test_an_order_the_partner_buys_defaults_to_partner_buys(self, da, world):
        order = _order(da, world, buyer_of_record="partner_org", buyer_org_id=world["partner"]["id"])
        assert order["buying_model"] == "partner_buys"
        assert buying_model_label(order) == "The partner buys on our quote and invoices us"

    def test_an_order_we_buy_defaults_to_programme_buys(self, da, world):
        order = _order(da, world, buyer_of_record="programme_org", buyer_org_id=world["us"]["id"])
        assert order["buying_model"] == "programme_buys"

    def test_it_is_chosen_per_order(self, da, world):
        order = _order(
            da,
            world,
            buyer_of_record="partner_org",
            buyer_org_id=world["partner"]["id"],
            buying_model="programme_buys",
        )
        assert order["buying_model"] == "programme_buys"
        changed = op(da, "contract_update", contract_id=order["id"], data={"buying_model": "partner_buys"})
        assert changed["buying_model"] == "partner_buys"

    def test_an_unknown_model_is_refused(self, da, world):
        with pytest.raises(Exception):
            _order(
                da,
                world,
                buyer_of_record="partner_org",
                buyer_org_id=world["partner"]["id"],
                buying_model="they_just_take_it",
            )


class TestNeededBy:
    def test_the_tender_counts_back_to_when_it_must_start(self, da, world):
        needed = TODAY + timedelta(days=120)
        tender = op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"needed_by": needed.isoformat(), "lead_time_days_expected": 60},
        )
        assert tender["needed_by"] == needed.isoformat()
        assert tender["start_by"] == (needed - timedelta(days=60 + DEFAULT_REPLY_DAYS)).isoformat()
        assert tender["late_to_start"] is False
        assert "Start by" in tender["start_by_note"]

    def test_a_quoted_lead_time_beats_the_expectation(self, da, world):
        needed = TODAY + timedelta(days=200)
        op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"needed_by": needed.isoformat(), "lead_time_days_expected": 30},
        )
        op(
            da,
            "quote_record",
            data={
                "tender_id": world["tender"]["id"],
                "commodity_slug": "rutf",
                "supplier_id": world["supplier"]["id"],
                "as_quoted_amount": "42.50",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "carton",
                "lead_time_days": 90,
            },
        )
        tender = op(da, "tender_get", tender_id=world["tender"]["id"])
        assert tender["start_by"] == (needed - timedelta(days=90 + DEFAULT_REPLY_DAYS)).isoformat()

    def test_a_draft_that_should_already_have_started_is_late(self, da, world):
        needed = TODAY + timedelta(days=40)
        tender = op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"needed_by": needed.isoformat(), "lead_time_days_expected": 60},
        )
        assert tender["late_to_start"] is True
        assert "Late to start" in tender["start_by_note"]

    def test_without_a_lead_time_no_start_date_is_invented(self, da, world):
        tender = op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"needed_by": (TODAY + timedelta(days=90)).isoformat()},
        )
        assert tender["start_by"] is None
        assert "how long suppliers need" in tender["start_by_note"]

    def test_a_tender_without_needed_by_is_as_it_was(self, da, world):
        tender = op(da, "tender_get", tender_id=world["tender"]["id"])
        assert tender["needed_by"] is None and tender["start_by"] is None and tender["late_to_start"] is False

    def test_the_overview_line_shows_the_serialised_dates(self, da, world):
        needed = date(2027, 2, 15)
        tender = op(
            da,
            "tender_update",
            tender_id=world["tender"]["id"],
            data={"needed_by": needed.isoformat(), "lead_time_days_expected": 60},
        )
        line = next_step(tenders=[tender], contracts=[], points=[], today=TODAY)["line"]
        assert "Needed by Feb 2027" in line and "start by" in line


def test_the_reply_window_is_the_open_window_once_the_tender_is_open():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    tender = SimpleNamespace(
        needed_by=date(2027, 3, 1),
        lead_time_days_expected=50,
        status="open",
        opened_at=datetime(2026, 12, 1, tzinfo=timezone.utc),
        response_deadline=date(2026, 12, 11),
    )
    counted = timing(tender, today=date(2026, 12, 5))
    assert counted["reply_days"] == 10
    assert counted["start_by"] == date(2027, 3, 1) - timedelta(days=60)
    assert counted["late"] is False
