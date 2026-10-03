"""Unanswered round 002, batch 1: the overview's "Waiting on" says, for each
silent supplier, the day we asked it and the day we last chased it."""

import datetime

import pytest

from connect_labs.supply_chain.models import Outreach
from connect_labs.supply_chain.tests import test_sophie_batch5 as batch5
from connect_labs.supply_chain.tests.test_sophie_batch5 import (
    _cells,
    _home,
    _open,
    _outreach,
    _standing_row,
    _supplier,
    _tender_row,
)

registered_synthetic = batch5.registered_synthetic
da = batch5.da
sophie = batch5.sophie
ace = batch5.ace
order = batch5.order
client_in_program = batch5.client_in_program
base = batch5.base
home_client = batch5.home_client


@pytest.mark.django_db
class TestEachSilentSupplierSaysWhenAskedAndChased:
    def test_asked_and_chased_beside_each_name(self, da, base, home_client):
        tender_id = base["tender"]["id"]
        sahel = _supplier(da, "Sahel Nutrition")
        _open(da, tender_id)
        _outreach(da, tender_id, base["supplier"]["id"], "2026-09-09")
        _outreach(da, tender_id, sahel["id"], "2026-09-01")
        Outreach.objects.filter(tender_id=tender_id, supplier_id=sahel["id"]).update(
            last_reminder_on=datetime.date(2026, 9, 20)
        )

        row = _tender_row()
        assert row.waiting_on.heading == "No reply"
        assert row.waiting_on.lines == (
            ("Northwind Foods", "asked 9 Sep"),
            ("Sahel Nutrition", "asked 1 Sep · chased 20 Sep"),
        )

        cell = _cells(_standing_row(_home(home_client), tender_id))[2]
        assert "<strong>No reply</strong>:" in cell
        assert 'Northwind Foods <span class="text-gray-600 whitespace-nowrap">(asked 9 Sep)</span>' in cell
        assert (
            'Sahel Nutrition <span class="text-gray-600 whitespace-nowrap">(asked 1 Sep · chased 20 Sep)</span>'
            in cell
        )
        assert cell.count('data-testid="silent-supplier"') == 2
