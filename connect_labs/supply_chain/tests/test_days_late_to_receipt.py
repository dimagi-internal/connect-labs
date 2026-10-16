"""How late an order came is measured to its receipt, not to today.

TCN-PO-0001 (the phones demo) was due 7 Sep and received 15 Sep with six phones refused
at inspection. The refusals read as undelivered, so the order stayed "late" and its
Days late tile counted on to today: 38 and growing.
"""

from datetime import date

from connect_labs.supply_chain.fulfilment.status import order_status

CONTRACT = {"status": "received", "signed_on": "2026-08-24", "promised_lead_time_days": 14, "consideration": "priced"}
RECEIPTS = [{"received_on": "2026-09-15"}]
MATCH = {
    "status": "arrived_with_refusals",
    "ordered": {"amount": "1200", "unit": "phone"},
    "received": {"amount": "1194", "unit": "phone"},
    "refused": {"amount": "6", "unit": "phone"},
}


def _tile(**overrides):
    kwargs = {"receipts": RECEIPTS, "match": MATCH, "today": date(2026, 10, 15), **overrides}
    tiles = order_status(CONTRACT, **kwargs)["tiles"]
    return next(t for t in tiles if t["label"] == "Days late")


def test_an_order_that_arrived_late_counts_its_days_to_the_receipt():
    tile = _tile()
    assert tile["value"] == "8"
    assert "arrived" in tile["sub"]


def test_the_count_does_not_grow_with_today():
    assert _tile(today=date(2027, 1, 1))["value"] == "8"


def test_an_order_that_arrived_on_time_reads_zero():
    assert _tile(receipts=[{"received_on": "2026-09-06"}])["value"] == "0"


def test_a_contract_with_no_lead_time_is_never_called_late():
    contract = {**CONTRACT, "promised_lead_time_days": None}
    tiles = order_status(contract, receipts=RECEIPTS, match=MATCH, today=date(2026, 10, 15))["tiles"]
    assert next(t for t in tiles if t["label"] == "Days late")["value"] == "0"


def test_refused_goods_do_not_keep_a_delivered_contract_late():
    from types import SimpleNamespace

    from connect_labs.supply_chain.checks import _contract_lateness
    from connect_labs.supply_chain.values import Quantity

    contract = SimpleNamespace(status="received", signed_on=date(2026, 8, 24), promised_lead_time_days=14)
    match = {"outstanding": Quantity(6, "phone"), "still_to_arrive": Quantity(0, "phone")}
    assert _contract_lateness(contract, match, date(2026, 10, 15)) is None
