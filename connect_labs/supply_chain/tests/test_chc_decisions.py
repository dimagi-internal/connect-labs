"""Five decisions the CHC co-pack walkthrough put to the programme, as built.

1. Paying a distributor up front is the workflow, not an anomaly: an order
   can be on `advance` payment terms, and the match then reads it that way.
2. An update link confirms only an order that was placed.
3. An award can be dated the day it was decided, never a day not yet come.
4. A store that releases rather than dispenses has a demand rate from its
   releases, labelled as such -- so the distributor's warehouse has a
   reorder figure.
5. A round can state the kit contents it buys, and the comparison then
   ranks the kits that hold them and refuses the rest, saying why.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.tests import test_read_as_words as _words

# The fixtures and helper are shared with the display tests: the same chain.
chain = _words.chain
client_in_programme = _words.client_in_programme
da = _words.da
op = _words.op

pytestmark = pytest.mark.django_db

TODAY = date.today()


def _advance_order(da, chain, status="confirmed"):
    contract = chain["contract"]
    op(da, "contract_update", contract_id=contract["id"], data={"payment_terms": "advance", "status": status})
    return op(da, "contract_get", contract_id=contract["id"])


def _invoice_and_pay(da, contract):
    invoice = op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "amount": "18000.00",
            "quantity_billed": "30000",
            "quantity_unit": "co-pack",
            "source": "supplier_reported",
        },
    )
    op(
        da,
        "payment_record",
        data={
            "invoice_id": invoice["id"],
            "paid_on": TODAY.isoformat(),
            "amount": "18000.00",
            "source": "we_recorded",
        },
    )


def _fresh_order(da, chain, **extra):
    base = chain["contract"]
    data = {
        "supplier_id": base["supplier_id"],
        "item_id": base["item_id"],
        "commodity_slug": "ors-zinc-copack",
        "buyer_of_record": "programme_org",
        "buyer_org_id": base["buyer_org_id"],
        "reference": "CHC-2",
        "quantity": "30000",
        "quantity_unit": "co-pack",
        "unit_price": "0.60",
        "unit_price_unit": "per_base_unit",
        "currency": "USD",
        "freight_basis": "included",
        "duties_basis": "included",
        "vat_basis": "included",
        "status": "placed",
        "source": "we_recorded",
        **extra,
    }
    return op(da, "contract_create", data=data)


class TestAdvancePayment:
    def test_terms_default_to_on_delivery_and_round_trip(self, da, chain):
        assert chain["contract"]["payment_terms"] == "on_delivery"
        order = _fresh_order(da, chain, payment_terms="advance")
        assert op(da, "contract_get", contract_id=order["id"])["payment_terms"] == "advance"

    def test_paid_in_advance_before_delivery_is_awaiting_delivery_not_over_billed(self, da, chain):
        order = _fresh_order(da, chain, payment_terms="advance")
        _invoice_and_pay(da, order)
        match = op(da, "contract_match", contract_id=order["id"])
        assert match["status"] == "paid_in_advance"
        assert match["awaiting_delivery"] == {"amount": "30000", "unit": "co-pack"}
        assert match["over_invoiced"] is None
        assert match["recoverable"] == {"amount": "0", "currency": "USD"}
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"] if c["subject"]["id"] == order["id"]]
        assert "invoice_over_billed" not in kinds

    def test_refused_goods_become_recoverable_money(self, da, chain):
        # chain's contract has 596 cartons accepted; add 4 refused.
        contract = _advance_order(da, chain)
        _invoice_and_pay(da, contract)
        op(
            da,
            "receipt_record",
            data={
                "contract_id": contract["id"],
                "supply_point_id": _warehouse_id(da),
                "received_on": TODAY.isoformat(),
                "source": "supplier_reported",
                "lines": [
                    {
                        "item_id": contract["item_id"],
                        "quantity_accepted": "0",
                        "quantity_rejected": "4",
                        "rejection_reason": "punctured",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        match = op(da, "contract_match", contract_id=contract["id"])
        # 4 cartons x 50 co-packs x 0.60 paid for and refused.
        assert match["recoverable"] == {"amount": "120", "currency": "USD"}
        assert match["awaiting_delivery"] == {"amount": "0", "unit": "co-pack"}
        assert match["over_invoiced"] is None
        assert match["status"] != "over_invoiced"

    def test_a_short_delivery_on_a_closed_order_is_recoverable_too(self, da, chain):
        contract = _advance_order(da, chain, status="received")
        _invoice_and_pay(da, contract)
        match = op(da, "contract_match", contract_id=contract["id"])
        # 596 of 600 cartons arrived and the order is closed out: 200 co-packs short.
        assert match["recoverable"] == {"amount": "120", "currency": "USD"}

    def test_on_delivery_orders_are_matched_as_before(self, da, chain):
        order = _fresh_order(da, chain)
        _invoice_and_pay(da, order)
        match = op(da, "contract_match", contract_id=order["id"])
        assert match["status"] != "paid_in_advance"
        assert "awaiting_delivery" not in match or match["awaiting_delivery"] is None

    def test_the_order_page_says_paid_in_advance_not_safe_to_pay_nothing(self, client_in_programme, da, chain):
        order = _fresh_order(da, chain, payment_terms="advance")
        _invoice_and_pay(da, order)
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        assert "Paid in advance" in body
        assert "awaiting delivery of 30,000 co-packs" in body
        assert "Safe to pay now" not in body
        assert "Billed beyond what arrived" not in body

    def test_the_order_form_offers_the_terms(self, client_in_programme, chain):
        body = client_in_programme.get(
            reverse("supply_chain:contract_edit", args=[chain["contract"]["id"]])
        ).content.decode()
        assert 'name="payment_terms"' in body
        assert "Paid in advance" in body


def _invoice_only(da, contract):
    return op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "amount": "18000.00",
            "quantity_billed": "30000",
            "quantity_unit": "co-pack",
            "source": "supplier_reported",
        },
    )


def _refuse_four_cartons(da, contract):
    op(
        da,
        "receipt_record",
        data={
            "contract_id": contract["id"],
            "supply_point_id": _warehouse_id(da),
            "received_on": TODAY.isoformat(),
            "source": "supplier_reported",
            "lines": [
                {
                    "item_id": contract["item_id"],
                    "quantity_accepted": "0",
                    "quantity_rejected": "4",
                    "rejection_reason": "punctured",
                    "quantity_unit": "carton",
                }
            ],
        },
    )


def _match_panel(body):
    """The ordered/received panel only, so a label elsewhere on the page cannot pass a test."""
    start = body.index("Ordered · received")
    return body[start : body.index("Shipments", start)]


class TestAdvanceWordsFollowPayments:
    """ "Paid in advance" printed on an order with USD 0.00 paid read as paid."""

    def test_nothing_paid_is_unpaid(self, da, chain):
        order = _fresh_order(da, chain, payment_terms="advance")
        _invoice_only(da, order)
        assert op(da, "contract_match", contract_id=order["id"])["advance_state"] == "unpaid"

    def test_part_paid_and_paid(self, da, chain):
        order = _fresh_order(da, chain, payment_terms="advance")
        invoice = _invoice_only(da, order)
        pay = {"invoice_id": invoice["id"], "paid_on": TODAY.isoformat(), "source": "we_recorded"}
        op(da, "payment_record", data={**pay, "amount": "6000.00"})
        assert op(da, "contract_match", contract_id=order["id"])["advance_state"] == "part_paid"
        op(da, "payment_record", data={**pay, "amount": "12000.00"})
        assert op(da, "contract_match", contract_id=order["id"])["advance_state"] == "paid"

    def test_on_delivery_orders_have_no_advance_state(self, da, chain):
        order = _fresh_order(da, chain)
        assert op(da, "contract_match", contract_id=order["id"])["advance_state"] is None

    def test_the_page_says_payable_not_paid_when_nothing_is_paid(self, client_in_programme, da, chain):
        order = _fresh_order(da, chain, payment_terms="advance")
        _invoice_only(da, order)
        panel = _match_panel(
            client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        )
        assert "payable in advance" in panel
        assert "paid in advance" not in panel.lower()
        assert "To pay in advance" in panel
        assert "nothing paid yet" in panel
        assert "USD 18,000.00" in panel

    def test_the_page_keeps_paid_in_advance_once_paid(self, client_in_programme, da, chain):
        order = _fresh_order(da, chain, payment_terms="advance")
        _invoice_and_pay(da, order)
        panel = _match_panel(
            client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        )
        assert "· paid in advance" in panel
        assert "Paid in advance" in panel
        assert "nothing paid yet" not in panel


class TestRefusedGoodsOnAnAdvanceOrder:
    """ "Still outstanding 200" sat above "nothing more to come" when the 200 were refused."""

    def test_the_match_states_what_was_refused(self, da, chain):
        contract = _advance_order(da, chain)
        _invoice_and_pay(da, contract)
        _refuse_four_cartons(da, contract)
        match = op(da, "contract_match", contract_id=contract["id"])
        assert match["refused"] == {"amount": "200", "unit": "co-pack"}

    def test_refused_is_its_own_row_and_outstanding_is_what_is_still_to_come(self, client_in_programme, da, chain):
        contract = _advance_order(da, chain)
        _invoice_and_pay(da, contract)
        _refuse_four_cartons(da, contract)
        panel = _match_panel(
            client_in_programme.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        )
        assert "Refused on arrival" in panel
        refused_row = panel[panel.index("Refused on arrival") :]
        assert "200 co-packs" in refused_row[: refused_row.index("</tr>")]
        outstanding_row = panel[panel.index("Still outstanding") :]
        assert "0 co-packs" in outstanding_row[: outstanding_row.index("</tr>")]
        assert "200 co-packs" not in outstanding_row[: outstanding_row.index("</tr>")]
        assert "nothing more to come" in panel

    def test_on_delivery_orders_keep_outstanding_as_ordered_less_received(self, client_in_programme, da, chain):
        panel = _match_panel(
            client_in_programme.get(
                reverse("supply_chain:order_detail", args=[chain["contract"]["id"]])
            ).content.decode()
        )
        assert "Refused on arrival" not in panel
        outstanding_row = panel[panel.index("Still outstanding") :]
        assert "200 co-packs" in outstanding_row[: outstanding_row.index("</tr>")]


def _warehouse_id(da):
    return next(p["id"] for p in op(da, "supply_point_list") if p["slug"] == "wh")


class TestConfirmingOnlyAPlacedOrder:
    def _link(self, da, chain):
        from connect_labs.supply_chain.update_links.models import UpdateLink

        org = next(o["id"] for o in op(da, "org_list") if o["slug"] == "harmattan-words")
        issued = op(da, "update_link_issue", data={"org_id": org, "contract_ids": [chain["contract"]["id"]]})
        return UpdateLink.objects.get(pk=issued["id"])

    def test_a_draft_is_not_offered_and_is_refused(self, da, chain):
        from connect_labs.supply_chain.update_links import service
        from connect_labs.supply_chain.update_links.forms import ConfirmOrderForm

        Contract.objects.filter(pk=chain["contract"]["id"]).update(status="draft")
        link = self._link(da, chain)
        assert not ConfirmOrderForm(scope=service.scope_for(link)).is_available()
        with pytest.raises(ValueError, match="not been placed"):
            service.submit(link, "confirm_order", {"contract": Contract.objects.get(pk=chain["contract"]["id"])})
        assert Contract.objects.get(pk=chain["contract"]["id"]).status == "draft"

    def test_a_placed_order_is_confirmed(self, da, chain):
        from connect_labs.supply_chain.update_links import service

        Contract.objects.filter(pk=chain["contract"]["id"]).update(status="placed")
        link = self._link(da, chain)
        service.submit(link, "confirm_order", {"contract": Contract.objects.get(pk=chain["contract"]["id"])})
        assert Contract.objects.get(pk=chain["contract"]["id"]).status == "confirmed"


class TestAwardDate:
    def _quote(self, chain):
        return chain["quotes"][1]

    def test_an_award_can_be_dated_the_day_it_was_decided(self, da, chain):
        decided = (TODAY - timedelta(days=30)).isoformat()
        award = op(
            da,
            "award_create",
            round_id=chain["round"]["id"],
            quote_id=self._quote(chain)["id"],
            rationale="decided at the July meeting",
            decided_on=decided,
        )
        assert award["decided_on"] == decided

    def test_it_defaults_to_today(self, da, chain):
        award = op(
            da, "award_create", round_id=chain["round"]["id"], quote_id=self._quote(chain)["id"], rationale="today"
        )
        assert award["decided_on"] == TODAY.isoformat()

    def test_it_may_not_be_in_the_future(self, da, chain):
        with pytest.raises(ValueError, match="future"):
            op(
                da,
                "award_create",
                round_id=chain["round"]["id"],
                quote_id=self._quote(chain)["id"],
                rationale="not yet",
                decided_on=(TODAY + timedelta(days=1)).isoformat(),
            )

    def test_the_award_form_asks_for_the_date(self, client_in_programme, chain):
        url = (
            reverse("supply_chain:procurement_comparison", args=[chain["round"]["id"]]) + "?commodity=ors-zinc-copack"
        )
        body = client_in_programme.get(url).content.decode()
        assert 'name="decided_on"' in body


class TestReleasesAreDemandAtAStoreThatDoesNotDispense:
    def _store_with(self, da, chain, kind):
        item_id = chain["contract"]["item_id"]
        store = op(
            da,
            "supply_point_upsert",
            data={
                "slug": f"store-{kind}",
                "name": f"Store {kind}",
                "kind": "central_store",
                "min_months_of_stock": "2",
                "max_months_of_stock": "4",
                "source": "we_recorded",
            },
        )
        other = op(
            da,
            "supply_point_upsert",
            data={"slug": f"llo-{kind}", "name": f"LLO {kind}", "kind": "regional_store", "source": "we_recorded"},
        )
        op(
            da,
            "movement_record",
            data={
                "kind": "receipt",
                "occurred_on": (TODAY - timedelta(days=120)).isoformat(),
                "commodity_slug": "ors-zinc-copack",
                "item_id": item_id,
                "to_supply_point_id": store["id"],
                "quantity": "1000",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        for days_ago in (89, 59, 29):
            op(
                da,
                "movement_record",
                data={
                    "kind": kind,
                    "occurred_on": (TODAY - timedelta(days=days_ago)).isoformat(),
                    "commodity_slug": "ors-zinc-copack",
                    "item_id": item_id,
                    "from_supply_point_id": store["id"],
                    "to_supply_point_id": other["id"],
                    "quantity": "30",
                    "quantity_unit": "carton",
                    "source": "we_recorded",
                },
            )
        return store

    @pytest.mark.parametrize("kind", ["transfer", "issue"])
    def test_a_warehouse_gets_a_rate_from_its_releases(self, da, chain, kind):
        store = self._store_with(da, chain, kind)
        plan = op(da, "resupply_plan", supply_point_id=store["id"], item_id=chain["contract"]["item_id"])
        assert plan["amc_basis"] == "releases"
        # 90 cartons over 90 days -> 30 cartons a month.
        assert Decimal(plan["amc"]["amount"]) == Decimal("30")
        assert plan["amc"]["unit"] == "carton"
        assert plan["status"] == "overstocked"

    def test_a_store_that_dispenses_is_rated_on_what_it_dispenses(self, da, chain):
        store = self._store_with(da, chain, "transfer")
        op(
            da,
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": (TODAY - timedelta(days=45)).isoformat(),
                "commodity_slug": "ors-zinc-copack",
                "item_id": chain["contract"]["item_id"],
                "from_supply_point_id": store["id"],
                "quantity": "300",
                "quantity_unit": "co-pack",
                "source": "connect_visit",
            },
        )
        plan = op(da, "resupply_plan", supply_point_id=store["id"], item_id=chain["contract"]["item_id"])
        assert plan["amc_basis"] == "consumption"

    def test_releases_keep_the_minimum_window_rule(self, da, chain):
        item_id = chain["contract"]["item_id"]
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "young", "name": "Young store", "kind": "central_store", "source": "we_recorded"},
        )
        op(
            da,
            "movement_record",
            data={
                "kind": "transfer",
                "occurred_on": (TODAY - timedelta(days=5)).isoformat(),
                "commodity_slug": "ors-zinc-copack",
                "item_id": item_id,
                "from_supply_point_id": store["id"],
                "to_supply_point_id": _warehouse_id(da),
                "quantity": "10",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        plan = op(da, "resupply_plan", supply_point_id=store["id"], item_id=item_id)
        assert "unconfirmed" in plan["amc"]

    def test_the_stock_page_labels_the_rate(self, client_in_programme, da, chain):
        self._store_with(da, chain, "transfer")
        body = client_in_programme.get(reverse("supply_chain:stock")).content.decode()
        assert "releases a month" in body


class TestARoundStatesTheContentsItBuys:
    def test_kits_holding_the_rounds_contents_rank_and_others_are_refused(self, da, chain):
        from connect_labs.supply_chain.models import Round

        round_ = Round.objects.get(pk=chain["round"]["id"])
        round_.lines = [
            {
                **round_.lines[0],
                "components": [
                    {"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet"},
                ],
            }
        ]
        round_.save(update_fields=["lines"])
        four = op(
            da,
            "item_upsert",
            data={
                "sku": "four",
                "name": "Four-sachet co-pack",
                "commodity_slug": "ors-zinc-copack",
                "base_unit": "co-pack",
                "pack_unit": "carton",
                "base_per_pack": 40,
                "components": [
                    {"commodity_slug": "ors", "quantity": 4, "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": 10, "base_unit": "tablet"},
                ],
            },
        )
        op(
            da,
            "quote_record",
            data={
                "round_id": round_.pk,
                "commodity_slug": "ors-zinc-copack",
                "supplier_id": chain["quotes"][0]["supplier_id"],
                "item_id": four["id"],
                "as_quoted_amount": "0.55",
                "as_quoted_unit": "per_base_unit",
                "quantity_basis": "30000",
                "quantity_basis_unit": "co-pack",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
        comparison = op(da, "round_compare", round_id=round_.pk, commodity_slug="ors-zinc-copack")
        assert comparison["comparable_count"] == 2
        refused = [row for row in comparison["not_comparable"] if row["item_name"] == "Four-sachet co-pack"]
        assert len(refused) == 1
        reasons = refused[0]["figures"]["landed_total_for_round_quantity"]["unconfirmed"]
        assert any("not the contents this round buys" in reason for reason in reasons)

    def test_other_contents_are_terminal_not_missing_info(self, da, chain):
        round_ = self._round_with_a_four_sachet_offer(da, chain)
        comparison = op(da, "round_compare", round_id=round_.pk, commodity_slug="ors-zinc-copack")
        assert [row["item_name"] for row in comparison["not_comparable"]] == ["Four-sachet co-pack"]
        assert all(row["item_name"] != "Four-sachet co-pack" for row in comparison["blocked"])
        # Nothing to ask anyone: the contents are what they are.
        assert comparison["not_comparable"][0]["questions"] == []
        # Every other offer is complete, so the ranking is not provisional.
        assert comparison["provisional"] is False
        questions = op(da, "round_outstanding_questions", round_id=round_.pk, commodity_slug="ors-zinc-copack")
        assert all(entry["quote_id"] != comparison["not_comparable"][0]["quote_id"] for entry in questions)

    def test_the_comparison_page_files_it_as_not_comparable(self, client_in_programme, da, chain):
        round_ = self._round_with_a_four_sachet_offer(da, chain)
        url = reverse("supply_chain:procurement_comparison", args=[round_.pk]) + "?commodity=ors-zinc-copack"
        body = client_in_programme.get(url).content.decode()
        assert "Not comparable — different contents" in body
        section = body[body.index("Not comparable — different contents") :]
        assert "Four-sachet co-pack" in section
        assert "4 sachets ors" in section
        assert "2 sachets ors" in section
        assert "Ask the supplier" not in section
        assert "Needs info" not in body
        assert "PROVISIONAL" not in body

    def _round_with_a_four_sachet_offer(self, da, chain):
        from connect_labs.supply_chain.models import Round

        round_ = Round.objects.get(pk=chain["round"]["id"])
        round_.lines = [
            {
                **round_.lines[0],
                "components": [
                    {"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet"},
                ],
            }
        ]
        round_.save(update_fields=["lines"])
        four = op(
            da,
            "item_upsert",
            data={
                "sku": "four",
                "name": "Four-sachet co-pack",
                "commodity_slug": "ors-zinc-copack",
                "base_unit": "co-pack",
                "pack_unit": "carton",
                "base_per_pack": 40,
                "components": [
                    {"commodity_slug": "ors", "quantity": 4, "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": 10, "base_unit": "tablet"},
                ],
            },
        )
        op(
            da,
            "quote_record",
            data={
                "round_id": round_.pk,
                "commodity_slug": "ors-zinc-copack",
                "supplier_id": chain["quotes"][0]["supplier_id"],
                "item_id": four["id"],
                "as_quoted_amount": "0.55",
                "as_quoted_unit": "per_base_unit",
                "quantity_basis": "30000",
                "quantity_basis_unit": "co-pack",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
        return round_

    def test_the_round_page_says_what_it_buys(self, client_in_programme, chain):
        from connect_labs.supply_chain.models import Round

        round_ = Round.objects.get(pk=chain["round"]["id"])
        round_.lines = [
            {
                **round_.lines[0],
                "components": [
                    {"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet"},
                ],
            }
        ]
        round_.save(update_fields=["lines"])
        body = client_in_programme.get(
            reverse("supply_chain:procurement_round_detail", args=[round_.pk])
        ).content.decode()
        assert "What this round buys" in body
        assert "2 sachet ORS + 10 tablet Zinc" in body


class TestARoundIsAwardedOnceEveryLineIs:
    """A round with every line awarded read "open" nine days past its deadline."""

    def _two_line_round(self, da, chain, status="open"):
        from connect_labs.supply_chain.models import Round

        round_ = op(
            da,
            "round_create",
            data={
                "label": "Two lines",
                "delivery_point": {"city": "Kano"},
                "lines": [
                    {"commodity_slug": "ors-zinc-copack", "quantity": "30000", "quantity_unit": "co-pack"},
                    {"commodity_slug": "ors", "quantity": "1000", "quantity_unit": "sachet"},
                ],
            },
        )
        Round.objects.filter(pk=round_["id"]).update(status=status)
        supplier_id = chain["quotes"][0]["supplier_id"]
        quotes = {}
        for slug, unit, basis in (("ors-zinc-copack", "co-pack", "30000"), ("ors", "sachet", "1000")):
            quotes[slug] = op(
                da,
                "quote_record",
                data={
                    "round_id": round_["id"],
                    "commodity_slug": slug,
                    "supplier_id": supplier_id,
                    "as_quoted_amount": "0.60",
                    "as_quoted_unit": "per_base_unit",
                    "quantity_basis": basis,
                    "quantity_basis_unit": unit,
                    "freight_basis": "included",
                    "duties_basis": "included",
                },
            )
        return round_, quotes

    def _award(self, da, round_, quote):
        op(da, "award_create", round_id=round_["id"], quote_id=quote["id"], rationale="the one we chose")

    def test_a_single_line_round_is_awarded_by_its_award(self, da, chain):
        assert op(da, "round_get", round_id=chain["round"]["id"])["status"] == "awarded"

    def test_it_stays_open_while_a_line_is_unawarded(self, da, chain):
        round_, quotes = self._two_line_round(da, chain)
        self._award(da, round_, quotes["ors-zinc-copack"])
        assert op(da, "round_get", round_id=round_["id"])["status"] == "open"
        self._award(da, round_, quotes["ors"])
        assert op(da, "round_get", round_id=round_["id"])["status"] == "awarded"

    def test_a_closed_round_stays_closed(self, da, chain):
        round_, quotes = self._two_line_round(da, chain, status="closed")
        self._award(da, round_, quotes["ors-zinc-copack"])
        self._award(da, round_, quotes["ors"])
        assert op(da, "round_get", round_id=round_["id"])["status"] == "closed"

    def test_the_overview_and_round_page_say_awarded(self, client_in_programme, chain):
        overview = client_in_programme.get(reverse("supply_chain:home")).content.decode()
        row = overview[overview.index(">CHC<") :]
        assert "awarded" in row[: row.index("</tr>")].lower()
        page = client_in_programme.get(
            reverse("supply_chain:procurement_round_detail", args=[chain["round"]["id"]])
        ).content.decode()
        assert "Status: Awarded" in page
