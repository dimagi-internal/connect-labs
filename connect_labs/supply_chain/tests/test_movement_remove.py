"""Removing a ledger entry: it stays on the ledger, marked removed, and stops counting everywhere.

Through `call_operation`, the surface a screen, an MCP client and an agent share.
"""

from datetime import date

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Movement
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10611
OPP = 10611
DAY = date(2026, 9, 22)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def item(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "chlorine",
            "name": "Chlorine 1.2%",
            "base_unit": "liter",
            "pack_unit": "jerrycan",
            "base_per_pack": 20,
        },
    )
    return op(
        da,
        "item_upsert",
        data={
            "sku": "chl-20l",
            "name": "Chlorine 20 L",
            "commodity_slug": "chlorine",
            "base_unit": "liter",
            "pack_unit": "jerrycan",
            "base_per_pack": 20,
        },
    )


@pytest.fixture
def store(da):
    return op(
        da,
        "supply_point_upsert",
        data={"slug": "llo-store", "name": "LLO store", "kind": "central_store", "source": "we_recorded"},
    )


@pytest.fixture
def worker(da):
    return op(
        da,
        "supply_point_upsert",
        data={
            "slug": "cr-1",
            "name": "CR 1",
            "kind": "user_held",
            "opportunity_id": OPP,
            "connect_username": "cr-1",
            "source": "we_recorded",
        },
    )


def _receive(da, store, item, jerrycans, batch="EA-1"):
    return op(
        da,
        "movement_record",
        data={
            "kind": "receipt",
            "occurred_on": DAY.isoformat(),
            "commodity_slug": "chlorine",
            "item_id": item["id"],
            "batch": batch,
            "to_supply_point_id": store["id"],
            "quantity": str(jerrycans),
            "quantity_unit": "jerrycan",
            "source": "we_recorded",
        },
    )


def _ledger(da, point, item):
    return op(da, "stock_on_hand", supply_point_id=point["id"], item_id=item["id"], unit="jerrycan")["ledger"]


class TestRemovingAnEntry:
    def test_a_removed_receipt_stops_counting_and_stays_on_the_ledger_marked_removed(self, da, item, store):
        _receive(da, store, item, 12)
        wrong = _receive(da, store, item, 10, batch="EA-BAD")
        assert _ledger(da, store, item)["amount"] == "22"

        removal = op(da, "movement_remove", movement_id=wrong["id"], reason="QC failed, never usable")

        assert _ledger(da, store, item)["amount"] == "12"
        assert removal["kind"] == "adjustment"
        assert removal["reverses_movement_id"] == wrong["id"]
        assert removal["occurred_on"] == wrong["occurred_on"], "dated with the entry it cancels"
        rows = {m["id"]: m for m in op(da, "movement_list", supply_point_id=store["id"])}
        assert wrong["id"] in rows and removal["id"] in rows, "nothing is deleted"
        assert rows[wrong["id"]]["removed"]["movement_id"] == removal["id"]
        assert "QC failed, never usable" in rows[wrong["id"]]["removed"]["said"]
        assert Movement.objects.filter(pk=wrong["id"]).exists()

    def test_a_removed_handout_leaves_the_workers_issued_figure_as_well_as_their_balance(
        self, da, item, store, worker
    ):
        _receive(da, store, item, 12)
        run = op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "chlorine",
                "distributed_on": DAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item["id"],
                        "quantity": "3",
                        "quantity_unit": "jerrycan",
                    }
                ],
            },
        )
        op(da, "movement_remove", movement_id=run["lines"][0]["movement_id"], reason="handout never happened")

        assert _ledger(da, store, item)["amount"] == "12"
        assert _ledger(da, worker, item)["amount"] == "0"
        stock = op(da, "worker_stock", item_id=item["id"])
        row = next(w for w in stock["workers"] if w["supply_point_id"] == worker["id"])
        assert row["issued"]["amount"] == "0", "a removed handout was never issued"

    def test_a_reason_is_required(self, da, item, store):
        entry = _receive(da, store, item, 5)
        with pytest.raises(Exception):
            op(da, "movement_remove", movement_id=entry["id"], reason="no")
        assert not Movement.objects.filter(reverses_id=entry["id"]).exists()

    def test_an_entry_is_removed_once_and_a_removal_is_never_removed(self, da, item, store):
        entry = _receive(da, store, item, 5)
        removal = op(da, "movement_remove", movement_id=entry["id"], reason="entered twice")
        with pytest.raises(ValueError, match="already been removed"):
            op(da, "movement_remove", movement_id=entry["id"], reason="entered twice again")
        with pytest.raises(ValueError, match="record the entry again"):
            op(da, "movement_remove", movement_id=removal["id"], reason="changed my mind")

    def test_an_entry_from_another_programme_is_not_found(self, da, item, store):
        entry = _receive(da, store, item, 5)
        other = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        with pytest.raises(ValueError, match="not on this programme's ledger"):
            op(other, "movement_remove", movement_id=entry["id"], reason="not mine to remove")

    def test_the_removal_is_signed_with_the_persons_name(self, da, item, store, django_user_model):
        entry = _receive(da, store, item, 5)
        da.user = django_user_model.objects.create(username="asha.k", email="asha@example.org", name="Asha K")
        da.remove_movement(entry["id"], "entered twice")
        row = next(m for m in op(da, "movement_list") if m["id"] == entry["id"])
        assert row["removed"]["said"] == "by Asha K: entered twice"
