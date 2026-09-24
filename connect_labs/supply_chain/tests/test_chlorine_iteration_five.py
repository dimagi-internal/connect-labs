"""What the chlorine stop-gap walkthrough showed at iteration 4.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.

- The stock page read "1.79 months, below min" after a restock with nothing to
  say it had been 0.72 a moment before, so the rise was invisible.
- The regulator's answer form said "None -- it rests on nothing on file" beside
  an approval the award page showed resting on a registration.
- The goods received note's expiry was typed in and then shown nowhere.
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import test_screens_polish as polish

# The same programme, account and award as the screen-polish tests.
account = polish.account
client_in_programme = polish.client_in_programme
da = polish.da
world = polish.world
op = polish.op
_ask = polish._ask
_registration = polish._registration

pytestmark = pytest.mark.django_db


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def days_ago(n):
    return (date.today() - timedelta(days=n)).isoformat()


@pytest.fixture
def store(da, world):
    point = op(
        da,
        "supply_point_upsert",
        data={
            "slug": "kano-store",
            "name": "A chlorine store",
            "kind": "central_store",
            "min_months_of_stock": "2",
            "max_months_of_stock": "4",
            "source": "we_recorded",
        },
    )

    def order(reference, quantity):
        return op(
            da,
            "contract_create",
            data={
                "commodity_slug": "chlorine",
                "supplier_id": world["supplier"]["id"],
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": reference,
                "status": "placed",
                "quantity": quantity,
                "quantity_unit": "jerry_can",
                "delivery_supply_point_id": point["id"],
                "source": "partner_reported",
            },
        )

    def receive(contract, reference, quantity, ago, **line):
        return op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": point["id"],
                "reference": reference,
                "received_on": days_ago(ago),
                "source": "partner_reported",
                "lines": [{"quantity_accepted": quantity, "quantity_unit": "jerry_can", **line}],
            },
        )

    first = order("DON-1", "300")
    receive(first, "GRN-1", "300", 100)
    for ago in (85, 55, 25):
        op(
            da,
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": days_ago(ago),
                "from_supply_point_id": point["id"],
                "commodity_slug": "chlorine",
                "quantity": "80",
                "quantity_unit": "jerry_can",
                "source": "partner_reported",
            },
        )
    return {"point": point, "order": order, "receive": receive}


def _row(da, point):
    return next(r for r in op(da, "network_stock")["points"] if r["supply_point_id"] == point["id"])


class TestTheStockPageShowsWhatTheLastReceiptChanged:
    def test_the_row_carries_cover_before_the_latest_receipt(self, da, store):
        stopgap = store["order"]("HHS-PO-1", "90")
        store["receive"](stopgap, "GRN-KANO-0431", "90", 0)
        row = _row(da, store["point"])
        before = row["last_receipt"]
        assert before["reference"] == "GRN-KANO-0431"
        assert Decimal(before["added"]["amount"]) == 90
        # 60 left before it, 150 after, over the same monthly rate.
        amc = Decimal(row["amc"]["amount"])
        assert Decimal(before["months_before"]) == (Decimal(60) / amc).quantize(Decimal("0.01"))
        assert Decimal(before["months_before"]) < Decimal(row["months_of_stock"])

    def test_the_page_says_it_beside_months_of_stock(self, client_in_programme, da, store):
        stopgap = store["order"]("HHS-PO-1", "90")
        store["receive"](stopgap, "GRN-KANO-0431", "90", 0)
        before = _row(da, store["point"])["last_receipt"]["months_before"]
        text = _text(client_in_programme.get(reverse("supply_chain:stock")).content.decode())
        assert f"was {before} before GRN-KANO-0431" in text
        assert "+90 jerry cans" in text

    def test_a_point_never_restocked_says_nothing_of_the_kind(self, da, world):
        point = op(
            da,
            "supply_point_upsert",
            data={"slug": "empty", "name": "Empty store", "kind": "central_store", "source": "we_recorded"},
        )
        assert _row(da, point)["last_receipt"] is None


class TestTheAnswerFormKeepsWhatTheRequestRestsOn:
    def test_the_registration_is_preselected(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world, rests_on_document_id=registration["id"])
        html = client_in_programme.get(reverse("supply_chain:approval_decide", args=[approval["id"]])).content.decode()
        selected = re.search(r'<option value="(\d+)"[^>]*selected', html)
        assert selected is not None and int(selected.group(1)) == registration["id"], html

    def test_a_request_resting_on_nothing_starts_on_nothing(self, client_in_programme, da, world):
        _registration(da, world)
        approval = _ask(da, world)
        html = client_in_programme.get(reverse("supply_chain:approval_decide", args=[approval["id"]])).content.decode()
        assert not re.search(r'name="rests_on_document"[\s\S]*?<option value="\d+"[^>]*selected', html)


class TestExpiryTypedOnAReceiptIsShown:
    def test_the_order_page_has_an_expiry_column(self, client_in_programme, da, store):
        stopgap = store["order"]("HHS-PO-1", "90")
        expiry = (date.today() + timedelta(days=365)).isoformat()
        store["receive"](stopgap, "GRN-KANO-0431", "90", 0, batch="AQ-2609-14", expiry=expiry)
        text = _text(
            client_in_programme.get(reverse("supply_chain:order_detail", args=[stopgap["id"]])).content.decode()
        )
        assert "Expiry" in text
        assert expiry in text

    def test_the_update_links_read_back_says_it(self, da, store):
        from connect_labs.supply_chain.update_links.service import _describe_receipt

        stopgap = store["order"]("HHS-PO-1", "90")
        receipt = store["receive"](stopgap, "GRN-KANO-0431", "90", 0, batch="AQ-2609-14", expiry="2027-09-24")
        assert "batch AQ-2609-14, expires 24 Sep 2027" in _describe_receipt(receipt["id"])
