"""Supply procurement shows data, not verdicts (Jonathan, 2026-10-07).

"The system should make the data very clear and sortable / viewable in the table, and then a
human can trigger an AI to analyze based on the questions we have. But it's not a key feature
of the system that we have a ton of hardcoded 'comparable' rules."

So the screens no longer say which quotes are "comparable": the comparison shows every landed
price it can compute (and, where it cannot, what the figure still needs), every live quote can
be awarded, the counts are counts of data, and every sheet sorts by a click on its header. The
comparison SERVICE still partitions quotes -- the MCP and an AI read that -- it is only no
longer presented as a verdict.

Every supplier, person and price here is invented.
"""

import re
from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.procurement.status import _primary_action, comparison_grid
from connect_labs.supply_chain.standing import tender_stage
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, _kanem_quote, op

da = reality.da
world = reality.world
client_in_program = reality.client_in_program

pytestmark = pytest.mark.django_db


def _compare_url(world):
    return reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"


def _landed_cell(body, quote_id):
    return re.search(rf'<td [^>]*data-fact="landed" data-quote-id="{quote_id}"[^>]*>(.*?)</td>', body, re.S).group(0)


# ---- 1. the landed figure is shown whenever it can be computed ----------------------


class TestTheLandedFigureIsData:
    def test_a_quote_the_service_holds_out_of_its_ranking_still_shows_its_landed_price(
        self, da, world, client_in_program
    ):
        # The tender's terms make duty ours (waived); the quote says its CPT price includes duty.
        # The service holds it out of the ranking until the supplier restates its price -- but its
        # landed figure is computed, so the grid shows it, beside the fact still to ask.
        op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="buyer_waiver")
        quote = op(
            da,
            "quote_record",
            data=_kanem_quote(
                world,
                incoterm="CPT Kano",
                duties_basis="included",
                pack_spec_source="stated_on_quote",
                base_per_pack_stated=150,
                quantity_basis="2000",
                quantity_basis_unit="carton",
            ),
        )
        compared = op(da, "tender_compare", tender_id=world["tender"]["id"], commodity_slug="rutf")
        (row,) = compared["blocked"]
        assert row["quote_id"] == quote["id"] and row["is_comparable"] is False
        assert row["figures"]["landed_total_for_tender_quantity"].get("amount")

        body = client_in_program.get(_compare_url(world)).content.decode()
        cell = _landed_cell(body, quote["id"])
        assert re.search(r"USD 54\.50", cell)
        assert "landed-needs" not in cell and "not comparable" not in cell
        # Its sort value is the figure, and the count says it has a landed price.
        assert 'data-sort-value="54.50"' in cell
        assert re.search(r'data-testid="quote-count"[^>]*>1 quote · 1 with a landed price<', body)

    def test_a_figure_that_cannot_be_computed_names_the_inputs_it_needs(self):
        tender = SimpleNamespace(
            pk=1,
            lines=[{"commodity_slug": "rutf", "quantity": "100", "quantity_unit": "carton"}],
            duty_terms="",
            duty_estimate_percent=None,
            freight_estimate_per_unit=None,
            clearing_estimate_per_unit=None,
        )
        row = {
            "quote_id": 5,
            "supplier_id": 2,
            "supplier_name": "Sahel Nutrition",
            "is_comparable": False,
            "figures": {
                "landed_total_for_tender_quantity": {
                    "unconfirmed": [
                        "Quote is in EUR and no exchange rate was recorded",
                        "No freight estimate recorded on the tender",
                    ]
                }
            },
            "gaps": ["exchange rate", "freight estimate"],
            "as_quoted": "EUR 40.00 per carton",
            "pack_unit": "carton",
            "base_unit": "sachet",
        }
        grid = comparison_grid(tender, {"comparable": [], "blocked": [row]}, {})
        landed = next(r for r in grid["rows"] if r["key"] == "landed")
        (cell,) = landed["cells"]
        assert cell["v"] == "needs exchange rate, freight estimate"
        assert [n["label"] for n in cell["needs"]] == ["exchange rate", "freight estimate"]
        assert cell["sort"] == ""
        # A quote missing facts is still awardable, and no chip passes a verdict on it.
        (column,) = grid["quotes"]
        assert column["awardable"] is True
        assert not any("omparable" in chip["label"] for chip in column["chips"])

    def test_vs_lowest_is_over_every_quote_with_a_landed_figure(self):
        tender = SimpleNamespace(
            pk=1,
            lines=[{"commodity_slug": "rutf", "quantity": "10", "quantity_unit": "carton"}],
            duty_terms="",
            duty_estimate_percent=None,
            freight_estimate_per_unit=None,
            clearing_estimate_per_unit=None,
        )

        def row(qid, name, total, comparable):
            return {
                "quote_id": qid,
                "supplier_id": qid,
                "supplier_name": name,
                "is_comparable": comparable,
                "figures": {"landed_total_for_tender_quantity": {"amount": total, "currency": "USD"}},
                "gaps": [] if comparable else ["duties restated"],
                "as_quoted": "USD 1.00 per carton",
            }

        # The cheapest is one the service blocks: it still leads, and the other is measured from it.
        snapshot = {"comparable": [row(1, "Alpha", "500.00", True)], "blocked": [row(2, "Beta", "450.00", False)]}
        grid = comparison_grid(tender, snapshot, {})
        assert [q["quote_id"] for q in grid["quotes"]] == [2, 1]
        landed = next(r for r in grid["rows"] if r["key"] == "landed")
        assert landed["cells"][0]["v"] == "USD 45.00" and landed["cells"][0].get("lowest") is True
        above = next(r for r in grid["rows"] if r["key"] == "vs_lowest")
        assert above["cells"][1]["v"] == "+ USD 5.00"


# ---- 2. the counts are data -------------------------------------------------------------


class TestCountsAreData:
    def test_the_comparing_stage_counts_quotes(self):
        tender = SimpleNamespace(status="closed")
        assert tender_stage(tender, quoted=2) == (2, "Comparing · 2 quotes")
        assert tender_stage(tender, quoted=1) == (2, "Comparing · 1 quote")
        assert tender_stage(tender) == (2, "Comparing")

    def test_the_primary_action_reads_quotes_not_a_comparable_count(self):
        from connect_labs.supply_chain import moves as rules

        tender = SimpleNamespace(status="open")
        deadline = [SimpleNamespace(rule=rules.RULE_DEADLINE)]
        # Past the deadline with quotes in -- however many facts they lack -- the next step is to compare.
        assert _primary_action(tender, deadline, 2) == "compare"
        assert _primary_action(tender, deadline, 0) == "decide"
        assert _primary_action(SimpleNamespace(status="draft"), deadline, 2) == "open"

    def test_the_tender_page_has_no_comparable_chip_and_a_plain_quotes_tile(self, da, world, client_in_program):
        op(da, "quote_record", data=_kanem_quote(world, pack_spec_source="not_stated"))
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        assert 'data-testid="comparable-chip"' not in body and "omparable" not in body
        tiles = dict(
            re.findall(
                r'class="stat-tile-label">([^<]+)</div>\s*<div class="stat-tile-value[^"]*">([^<]+)</div>', body
            )
        )
        assert tiles["Quotes"] == "1"

    def test_the_check_reads_as_missing_facts(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import CHECK_LABELS
        from connect_labs.supply_chain.views import _headline

        assert CHECK_LABELS["quote_not_comparable"] == "Quote missing facts"
        assert _headline({"quote_not_comparable"}, 2) == "quotes missing facts"


# ---- 3. every sheet sorts by a click on its header ----------------------------------


def _sortable(table):
    head = re.search(r"<thead>.*?</thead>", table, re.S).group(0)
    return [re.sub(r"<[^>]+>", "", label).strip() for label in re.findall(r"<th [^>]*data-sort[^>]*>(.*?)</th>", head)]


class TestSheetsSort:
    def test_the_script_is_on_every_supply_page_the_past_included(self, client_in_program, world):
        body = client_in_program.get(reverse("supply_chain:suppliers")).content.decode()
        assert '<script src="/static/supply_chain/sheet_sort.js"></script>' in body

    def test_the_tender_sheets_mark_their_headers_and_cells(self, da, world, client_in_program):
        quote = op(da, "quote_record", data=_kanem_quote(world, lead_time_days=21, received_on="2026-07-08"))
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        suppliers = re.search(r'<table class="sheet" data-testid="supplier-table">.*?</table>', body, re.S).group(0)
        assert _sortable(suppliers) == [
            "Supplier",
            "State",
            "Asked",
            "Replied",
            "Last chased",
            "Price",
            "Delivery term",
            "Pack",
            "Missing",
        ]
        # Dates sort as ISO, figures as figures.
        assert 'data-sort-value="2026-07-06"' in suppliers
        assert re.search(r'data-testid="supplier-quote" data-sort-value="54\.50*"', suppliers)
        quotes = re.search(r'<table class="sheet" data-testid="quotes-table">.*?</table>', body, re.S).group(0)
        assert _sortable(quotes)[:3] == ["Supplier", "Product", "Price"]
        row = re.search(rf'<tr data-testid="quote-row" data-quote-id="{quote["id"]}">.*?</tr>', quotes, re.S).group(0)
        assert 'data-sort-value="21"' in row and 'data-sort-value="2026-07-08"' in row

    def test_the_comparison_grid_sorts_by_supplier_landed_and_price(self, da, world, client_in_program):
        op(da, "quote_record", data=_kanem_quote(world))
        body = client_in_program.get(_compare_url(world)).content.decode()
        grid = re.search(r'<table class="sheet" data-testid="comparison-grid">.*?</table>', body, re.S).group(0)
        labels = _sortable(grid)
        assert labels[0] == "Supplier"
        assert any(label.startswith("Landed per") for label in labels)
        assert any(label.startswith("Quoted price") for label in labels)
        assert re.search(r'data-testid="grid-quote" data-sort-value="Kanem Foods Rehearsal"', grid)

    def test_orders_and_the_suppliers_directory_sort(self, da, world, client_in_program):
        _contract(da, world, signed_on=(reality.TODAY - timedelta(days=2)).isoformat())
        orders = client_in_program.get(reverse("supply_chain:orders")).content.decode()
        table = re.search(r'<table class="sheet[^"]*" data-testid="orders-table">.*?</table>', orders, re.S).group(0)
        assert _sortable(table) == [
            "Reference",
            "Supplier",
            "Buyer of record",
            "Quantity",
            "Received",
            "Unit price",
            "Currency",
            "Status",
            "Signed",
            "Told by",
        ]
        assert f'data-sort-value="{(reality.TODAY - timedelta(days=2)).isoformat()}"' in table
        assert re.search(r'data-sort-value="49\.80*"', table)
        directory = client_in_program.get(reverse("supply_chain:suppliers")).content.decode()
        table = re.search(r'<table class="sheet[^"]*" data-testid="suppliers-table">.*?</table>', directory, re.S)
        assert _sortable(table.group(0))[:2] == ["Supplier", "Type"]


def test_the_service_still_partitions_for_its_callers(da, world):
    """The verdict left the screens, not the service: tender_compare still says what it can rank."""
    op(da, "quote_record", data=_kanem_quote(world, pack_spec_source="not_stated"))
    compared = op(da, "tender_compare", tender_id=world["tender"]["id"], commodity_slug="rutf")
    assert {"comparable", "blocked", "comparable_count", "priced_count"} <= set(compared)
    assert Tender.objects.filter(pk=world["tender"]["id"]).exists()
