"""What the supply screens show a person, as found by filming a real procurement.

The CHC co-pack walkthrough put these screens in front of a user-artifact judge,
who read them as a procurement lead would. Each test here is one thing it found
on a live page: an identifier where a word belongs, a figure carried to four
decimal places, a caption that contradicts the numbers under it, a decision
already made still offered as a button.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10612


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def client_in_programme(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views

    account = django_user_model.objects.create_user(username="words", password="x", email="words@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "reference_views", "fulfilment_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    monkeypatch.setattr(procurement_views, "_access", _scoped)
    monkeypatch.setattr(procurement_views, "has_program_context", lambda request: True)
    from connect_labs.supply_chain.alerts import views as alert_views

    monkeypatch.setattr(alert_views, "_access", _scoped)
    monkeypatch.setattr(alert_views, "has_program_context", lambda request: True)
    return client


@pytest.fixture
def chain(da):
    """A co-pack round from one distributor, awarded, ordered and partly received."""
    op(da, "commodity_upsert", data={"slug": "ors", "name": "ORS", "base_unit": "sachet"})
    op(da, "commodity_upsert", data={"slug": "zinc", "name": "Zinc", "base_unit": "tablet"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "ors-zinc-copack",
            "name": "ORS/zinc co-pack",
            "category": "oral_rehydration",
            "base_unit": "co-pack",
            "pack_unit": "carton",
            "base_per_pack": 50,
        },
    )

    def kit(sku, name, ors):
        return op(
            da,
            "item_upsert",
            data={
                "sku": sku,
                "name": name,
                "commodity_slug": "ors-zinc-copack",
                "base_unit": "co-pack",
                "pack_unit": "carton",
                "base_per_pack": 50,
                "components": [
                    {"commodity_slug": "ors", "quantity": ors, "base_unit": "sachet"},
                    {"commodity_slug": "zinc", "quantity": 10, "base_unit": "tablet"},
                ],
            },
        )

    a = kit("a", "Kaduna co-pack", 2)
    b = kit("b", "Lagoon co-pack", 2)
    supplier = op(da, "supplier_create", data={"name": "Harmattan Health Supplies", "type": "distributor"})
    distributor = op(da, "org_upsert", data={"slug": "harmattan-words", "name": "Harmattan Health Supplies"})
    ours = op(da, "org_upsert", data={"slug": "programme-words", "name": "Child Health Programme"})
    round_ = op(
        da,
        "round_create",
        data={
            "label": "CHC",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "ors-zinc-copack", "quantity": "30000", "quantity_unit": "co-pack"}],
        },
    )
    quotes = []
    for item, price in ((a, "0.60"), (b, "0.64")):
        quotes.append(
            op(
                da,
                "quote_record",
                data={
                    "round_id": round_["id"],
                    "commodity_slug": "ors-zinc-copack",
                    "supplier_id": supplier["id"],
                    "item_id": item["id"],
                    "as_quoted_amount": price,
                    "as_quoted_unit": "per_base_unit",
                    "quantity_basis": "30000",
                    "quantity_basis_unit": "co-pack",
                    "pack_spec_source": "trade_item_confirmed",
                    "freight_basis": "included",
                    "duties_basis": "included",
                },
            )
        )
    award = op(da, "award_create", round_id=round_["id"], quote_id=quotes[0]["id"], rationale="cheapest like contents")
    store = op(
        da,
        "supply_point_upsert",
        data={"slug": "wh", "name": "Harmattan warehouse", "kind": "central_store", "source": "we_recorded"},
    )
    contract = op(
        da,
        "contract_create",
        data={
            "round_id": round_["id"],
            "award_id": award["id"],
            "supplier_id": supplier["id"],
            "item_id": a["id"],
            "commodity_slug": "ors-zinc-copack",
            "buyer_of_record": "programme_org",
            "buyer_org_id": ours["id"],
            "reference": "CHC-1",
            "quantity": "30000",
            "quantity_unit": "co-pack",
            "unit_price": "0.60",
            "unit_price_unit": "per_base_unit",
            "currency": "USD",
            "freight_basis": "included",
            "duties_basis": "included",
            "vat_basis": "included",
            "status": "confirmed",
            "source": "we_recorded",
        },
    )
    op(
        da,
        "receipt_record",
        data={
            "contract_id": contract["id"],
            "supply_point_id": store["id"],
            "received_on": "2026-09-20",
            "reference": "GRN-1",
            "source": "supplier_reported",
            "recorded_by_org_id": distributor["id"],
            "lines": [{"item_id": a["id"], "quantity_accepted": "596", "quantity_unit": "carton"}],
        },
    )
    return {"round": round_, "quotes": quotes, "contract": contract}


class TestTheComparison:
    def _page(self, client, chain):
        url = (
            reverse("supply_chain:procurement_comparison", args=[chain["round"]["id"]]) + "?commodity=ors-zinc-copack"
        )
        response = client.get(url)
        assert response.status_code == 200
        return response.content.decode()

    def test_it_is_titled_by_the_product_not_its_slug(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        assert "ORS/zinc co-pack" in body
        assert "round {}, ors-zinc-copack".format(chain["round"]["id"]) not in body

    def test_money_reads_as_money(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        assert "USD 18,000.00" in body
        assert "18000.0000" not in body

    def test_the_chosen_offer_is_marked_and_not_offered_again(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        # One Award form left: the offer not chosen.
        assert body.count('name="rationale"') == 1

    def test_the_awarded_offer_is_marked_beside_its_name(self, client_in_programme, chain):
        # At 1280px the rightmost column ran off the page, and the marker with it.
        body = self._page(client_in_programme, chain)
        table = body[body.index("<table") : body.index("</table>")]
        supplier_cell = table[table.index("Kaduna co-pack") :]
        supplier_cell = supplier_cell[: supplier_cell.index("</td>")]
        assert "Awarded" in supplier_cell
        other = table[table.index("Lagoon co-pack") :]
        assert "Awarded" not in other[: other.index("</td>")]

    def test_identical_landed_totals_are_one_column(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        head = body[body.index("<thead") : body.index("</thead>")]
        assert "Landed total (as quoted)" not in head
        assert head.count("Landed total") == 1

    def test_differing_landed_totals_stay_two_columns(self):
        from connect_labs.supply_chain.procurement.views import table_columns

        columns = [
            {"key": "landed_total_as_quoted", "label": "Landed total (as quoted)"},
            {"key": "landed_total_for_round_quantity", "label": "Landed total (this round)"},
        ]
        same = {"amount": "18000", "currency": "USD"}
        rows = [
            {"figures": {"landed_total_as_quoted": same, "landed_total_for_round_quantity": same}},
            {
                "figures": {
                    "landed_total_as_quoted": {"amount": "9000", "currency": "USD"},
                    "landed_total_for_round_quantity": same,
                }
            },
        ]
        assert [c["key"] for c in table_columns({"columns": columns, "comparable": rows})] == [
            "landed_total_as_quoted",
            "landed_total_for_round_quantity",
        ]
        assert [c["key"] for c in table_columns({"columns": columns, "comparable": rows[:1]})] == [
            "landed_total_for_round_quantity"
        ]


class TestTheRoundsQuotes:
    """Three co-pack quotes differing only by price, told apart by nothing but the price."""

    def _quotes_table(self, client, chain):
        response = client.get(reverse("supply_chain:procurement_round_detail", args=[chain["round"]["id"]]))
        assert response.status_code == 200
        body = response.content.decode()
        start = body.index(">Quotes<")
        return body[start : body.index("</table>", start)]

    def test_each_quote_names_its_trade_item_and_contents(self, client_in_programme, chain):
        table = self._quotes_table(client_in_programme, chain)
        assert "Kaduna co-pack" in table
        assert "Lagoon co-pack" in table
        assert "2 sachets ors + 10 tablets zinc" in table

    def test_the_commodity_is_named_not_slugged(self, client_in_programme, chain):
        table = self._quotes_table(client_in_programme, chain)
        assert "ORS/zinc co-pack" in table
        assert "ors-zinc-copack" not in table
        assert "ors zinc copack" not in table

    def test_prices_read_as_money(self, client_in_programme, chain):
        table = self._quotes_table(client_in_programme, chain)
        assert "USD 0.60" in table
        assert "per_base_unit" not in table


class TestTheOrder:
    def _page(self, client, chain):
        response = client.get(reverse("supply_chain:order_detail", args=[chain["contract"]["id"]]))
        assert response.status_code == 200
        return response.content.decode()

    def test_the_buyer_of_record_is_said_in_words(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        assert "programme_org" not in body
        assert "programme org" not in body

    def test_it_does_not_claim_three_different_amounts_when_they_are_the_same(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        assert "costing three different amounts" not in body

    def test_a_receipt_says_who_told_us(self, client_in_programme, chain):
        body = self._page(client_in_programme, chain)
        assert "supplier_reported" not in body
        assert "Harmattan Health Supplies" in body.split("GRN-1", 1)[1][:2000]


class TestTheStockPage:
    def test_a_point_kind_and_its_rate_read_as_words_and_sensible_figures(self, client_in_programme, da, chain):
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "llo", "name": "Partner store", "kind": "regional_store", "source": "we_recorded"},
        )
        op(
            da,
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": "2026-09-01",
                "commodity_slug": "ors-zinc-copack",
                "from_supply_point_id": store["id"],
                "quantity": "1000",
                "quantity_unit": "co-pack",
                "source": "connect_visit",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:stock")).content.decode()
        assert "regional_store" not in body and "central_store" not in body
        assert "regional store" in body


class TestTheQuote:
    def test_it_names_no_api_operation_and_shows_the_kit_contents(self, client_in_programme, chain):
        quote_id = chain["quotes"][1]["id"]
        body = client_in_programme.get(
            reverse("supply_chain:procurement_quote_detail", args=[quote_id])
        ).content.decode()
        assert "document_attach" not in body
        assert "Sachets per pack" not in body
        assert "2 sachet ORS + 10 tablet Zinc" in body


class TestTheRound:
    def test_the_compare_buttons_name_the_product(self, client_in_programme, chain):
        body = client_in_programme.get(
            reverse("supply_chain:procurement_round_detail", args=[chain["round"]["id"]])
        ).content.decode()
        assert "Compare ORS/zinc co-pack" in body
        assert "Compare ors-zinc-copack" not in body


class TestWhatWasSetAside:
    def test_the_comparison_shows_an_offer_set_aside_and_why(self, client_in_programme, da, chain):
        """A voided quote drops out of the ranking, and with it out of sight:
        the comparison showed two co-packs ranked and never the one excluded,
        so the rule that kits rank only against the same contents did no
        visible work on the one screen that applies it."""
        extra = op(
            da,
            "item_upsert",
            data={
                "sku": "c",
                "name": "Benue co-pack (4 sachets)",
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
        quote = op(
            da,
            "quote_record",
            data={
                "round_id": chain["round"]["id"],
                "commodity_slug": "ors-zinc-copack",
                "supplier_id": chain["quotes"][0]["supplier_id"],
                "item_id": extra["id"],
                "as_quoted_amount": "0.55",
                "as_quoted_unit": "per_base_unit",
            },
        )
        op(da, "quote_void", quote_id=quote["id"], reason="holds 4 ORS sachets, not the protocol 2")
        url = (
            reverse("supply_chain:procurement_comparison", args=[chain["round"]["id"]]) + "?commodity=ors-zinc-copack"
        )
        body = client_in_programme.get(url).content.decode()
        assert "Set aside" in body
        assert "Benue co-pack (4 sachets)" in body
        assert "holds 4 ORS sachets, not the protocol 2" in body


class TestTheOrderShowsWhatCameThroughTheLink:
    def test_the_suppliers_own_updates_are_listed_on_the_order(self, client_in_programme, da, chain):
        from connect_labs.supply_chain.models import Contract
        from connect_labs.supply_chain.update_links import service

        link = op(
            da,
            "update_link_issue",
            data={
                "org_id": next(o["id"] for o in op(da, "org_list") if o["slug"] == "harmattan-words"),
                "contract_ids": [chain["contract"]["id"]],
            },
        )
        from connect_labs.supply_chain.update_links.models import UpdateLink

        Contract.objects.filter(pk=chain["contract"]["id"]).update(status="placed")
        service.submit(
            UpdateLink.objects.get(pk=link["id"]),
            "confirm_order",
            {"contract": Contract.objects.get(pk=chain["contract"]["id"])},
        )
        body = client_in_programme.get(
            reverse("supply_chain:order_detail", args=[chain["contract"]["id"]])
        ).content.decode()
        assert "Through Harmattan Health Supplies" in body
        assert "CHC-1 is confirmed" in body

    def test_received_goods_with_no_dispatch_are_not_nothing(self, client_in_programme, chain):
        body = client_in_programme.get(
            reverse("supply_chain:order_detail", args=[chain["contract"]["id"]])
        ).content.decode()
        assert "Nothing dispatched yet" not in body
        assert "No dispatch was recorded" in body


class TestTheStockPageSpeaksInPacks:
    def test_the_rate_is_also_given_in_the_unit_the_stock_is_counted_in(self, client_in_programme, da, chain):
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "llo2", "name": "Partner store", "kind": "regional_store", "source": "we_recorded"},
        )
        item_id = chain["contract"]["item_id"]
        op(
            da,
            "movement_record",
            data={
                "kind": "transfer",
                "occurred_on": "2026-06-01",
                "commodity_slug": "ors-zinc-copack",
                "item_id": item_id,
                "to_supply_point_id": store["id"],
                "quantity": "100",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        for day in ("2026-08-01", "2026-09-01"):
            op(
                da,
                "movement_record",
                data={
                    "kind": "consumption",
                    "occurred_on": day,
                    "commodity_slug": "ors-zinc-copack",
                    "item_id": item_id,
                    "from_supply_point_id": store["id"],
                    "quantity": "1500",
                    "quantity_unit": "co-pack",
                    "source": "connect_visit",
                },
            )
        body = client_in_programme.get(reverse("supply_chain:stock")).content.decode()
        assert " cartons a month" in body or " cartons)" in body or " carton)" in body


class TestAlertChips:
    def test_a_subscription_names_its_checks_in_words(self, client_in_programme, da):
        op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["stock_below_minimum"], "recipient_email": "stores@example.org"},
        )
        body = client_in_programme.get(reverse("supply_chain:alerts")).content.decode()
        assert "Below its own minimum" in body
