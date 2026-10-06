"""Sophie's RUTF walkthrough, judged again: the fifth batch of what it found.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the third and fourth batches: a synthetic program, history
recorded through `call_operation` with `seed_overrides`, and the pages read
back as rendered.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.timeline import timeline_for_contract, timeline_for_tender
from connect_labs.supply_chain.models import Commodity, Tender
from connect_labs.supply_chain.procurement.services.compliance import requirement_line
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.stock.services import ledger
from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests import test_sophie_batch3 as batch3
from connect_labs.supply_chain.tests.test_history_timeline import (
    _COMPARABLE,
    AUG_3,
    AUG_20,
    AUG_28,
    EMAIL,
    PROGRAM,
    _correct_pack,
    _quote_with,
    op,
)
from connect_labs.supply_chain.tests.test_sophie_batch3 import (
    _ALL_BUT_PACK,
    _home,
)

registered_synthetic = timeline.registered_synthetic
da = timeline.da
sophie = timeline.sophie
ace = timeline.ace
base = timeline.base
order = timeline.order
client_in_program = timeline.client_in_program
home_client = batch3.home_client
listed_tender = batch3.listed_tender

# Freight and duties stated, the pack stated: comparable unless something is taken away.
_DELIVERED = {**_COMPARABLE, "freight_basis": "included", "duties_basis": "included"}


def _tender_row(today=datetime.date(2026, 9, 12)):
    (row,) = (r for r in standing_rows(PROGRAM, today) if r.kind == "tender")
    return row


def _standing_row(body, tender_id):
    return re.search(
        rf'<tr [^>]*data-testid="overview-row"[^>]*data-tender-id="{tender_id}"[^>]*>.*?</tr>', body, re.S
    ).group(0)


def _cells(row_html):
    return re.findall(r"<td[^>]*>(.*?)</td>", row_html, re.S)


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _supplier(da, name):
    return op(da, "supplier_create", AUG_3, data={"name": name})


def _award(da, tender_id, quote_id, **kwargs):
    op(da, "award_create", AUG_28, tender_id=tender_id, quote_id=quote_id, rationale="cheapest", **kwargs)


# ---- 1. the overview flags every quote the comparison blocks ---------------


# ---- 2. waiting on names who ----------------------------------------------


@pytest.mark.django_db
class TestWaitingOnNamesWho:
    def test_after_an_award_a_contract_with_the_awardee(self, da, base):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        _award(da, base["tender"]["id"], quote["id"])
        row = _tender_row()
        assert row.stage == "Awarding · to Northwind Foods"
        assert row.stage_index == 3

    def test_a_provisional_award_names_its_awardee(self, da, base):
        other = _supplier(da, "Sahel Nutrition")
        _quote_with(da, base["tender"]["id"], other["id"], AUG_20, {})  # blocked
        chosen = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        _award(da, base["tender"]["id"], chosen["id"])
        assert _tender_row().stage == "Awarding · to Northwind Foods"


# ---- 3. last change: two lines, one compact AI pill ------------------------


@pytest.mark.django_db
class TestTheLastChange:
    def test_the_count_and_the_day_are_a_line_each(self, da, base, home_client):
        # Today: a count and a day, each on a line. (A past date shows the day alone: batch 6.)
        cell = _cells(_standing_row(_home(home_client), base["tender"]["id"]))[4]
        assert re.search(r'data-testid="last-change"[^>]*>[^<]+<', cell)
        assert re.search(r'data-testid="last-change-day"[^>]*>[^<]+<', cell)
        assert "·" not in _text(cell)

    def test_the_ai_pill_is_one_compact_badge(self, da, base, ace, home_client):
        _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {},
            channel="mcp",
            actor=ace,
            source={"ref": "<msg-9@northwind.example>", "excerpt": "Our price is 42.50 a carton."},
        )
        cell = _cells(_standing_row(_home(home_client), base["tender"]["id"]))[4]
        badge = re.search(
            r'<span [^>]*data-ai data-testid="ai-badge" title="([^"]*)"[^>]*>(.*?)</span>\s*$', cell, re.S
        )
        # Who told us, beside the AI source mark, and on hover what was recorded.
        assert _text(re.sub(r"<[^>]+>", "", badge.group(2))) == "AI assistant"
        assert 'data-src="ai"' in badge.group(2)
        assert badge.group(1) == "ACE recorded Quote · Northwind Foods from a forwarded email"


# ---- 4. the as-of control and the flags under it ---------------------------


@pytest.mark.django_db
class TestTheAsOfControl:
    def test_the_chosen_day_is_said_in_the_page_s_format(self, da, base, home_client):
        body = _home(home_client, as_of="2026-08-20")
        control = re.search(r'data-testid="as-of-control".*?</form>', body, re.S).group(0)
        assert re.search(r"<label[^>]*>View as of</label>", control)
        # Since batch 8 the day is the field's own value; the banner says it in the page's format.
        assert re.search(r'<input id="supply-as-of"[^>]*value="20 Aug 2026"[^>]*data-testid="as-of-date"', control)
        assert "Viewing as of 20 Aug 2026" in body
        assert 'data-testid="as-of-date"' not in _home(home_client)

    def test_the_flags_are_said_to_be_for_today_once_in_their_header(self, da, base, home_client):
        past = _home(home_client, as_of="2026-08-20")
        # There are no flags at all now: the row's next move is the only judgement it shows.
        assert ">Flags</th>" not in past and ">Flags</th>" not in _home(home_client)
        assert len(_cells(_standing_row(past, base["tender"]["id"]))) == 5

    def test_the_chain_says_program(self, da, base, home_client, lineless_order):
        body = _home(home_client)
        chain = body[body.index('data-testid="fold-chain"') : body.index('data-testid="fold-checks"')]
        assert "bought by the program" in chain
        assert "programme" not in chain.lower()


# ---- 5. an order on the road with no shipment lines ------------------------


@pytest.fixture
def lineless_order(da, base):
    """600 cartons, dispatched 20 Aug on a shipment recorded with no lines, received 1 Sep."""
    contract = op(
        da,
        "contract_create",
        AUG_3,
        data={
            "supplier_id": base["supplier"]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": base["us"]["id"],
            "reference": "PO-NOLINES",
            "quantity": "600",
            "quantity_unit": "carton",
            "status": "placed",
            "source": "we_recorded",
        },
    )
    shipment = op(
        da,
        "shipment_record",
        AUG_20,
        data={
            "contract_id": contract["id"],
            "reference": "SH-NOLINES",
            "dispatched_on": "2026-08-20",
            "expected_on": "2026-09-01",
            "source": "supplier_reported",
        },
    )
    store = op(
        da,
        "supply_point_upsert",
        AUG_3,
        data={"slug": "kano-store", "name": "Kano store", "kind": "central_store", "source": "we_recorded"},
    )
    op(
        da,
        "receipt_record",
        datetime.datetime(2026, 9, 1, 9, tzinfo=datetime.UTC),
        data={
            "contract_id": contract["id"],
            "shipment_id": shipment["id"],
            "supply_point_id": store["id"],
            "received_on": "2026-09-01",
            "source": "we_recorded",
            "lines": [{"quantity_accepted": "600", "quantity_unit": "carton"}],
        },
    )
    return contract


def _chain_cell(body, label):
    cell = body[body.index(f">{label}</div>") :]
    return " ".join(re.search(r"<div[^>]*>(.*?)</div>", cell[len(f">{label}</div>") :], re.S).group(1).split())


@pytest.mark.django_db
class TestAShipmentWithNoLinesIsStillOnTheRoad:
    def test_between_dispatch_and_receipt_the_order_s_quantity_is_in_transit(
        self, da, base, home_client, lineless_order
    ):
        past = _home(home_client, as_of="2026-08-25")
        assert _chain_cell(past, "Dispatched") == "1"
        assert _chain_cell(past, "In transit") == "600 cartons"

    def test_once_received_it_is_not(self, da, base, home_client, lineless_order):
        assert _chain_cell(_home(home_client), "In transit") != "600 cartons"

    def test_a_line_on_another_shipment_is_not_counted_twice(self, da, base):
        contract = op(
            da,
            "contract_create",
            AUG_3,
            data={
                "supplier_id": base["supplier"]["id"],
                "commodity_slug": "rutf",
                "buyer_of_record": "programme_org",
                "buyer_org_id": base["us"]["id"],
                "reference": "PO-SPLIT",
                "quantity": "600",
                "quantity_unit": "carton",
                "status": "placed",
                "source": "we_recorded",
            },
        )
        shipment = {"contract_id": contract["id"], "dispatched_on": "2026-08-20", "source": "supplier_reported"}
        op(da, "shipment_record", AUG_20, data={**shipment, "reference": "SH-BARE"})
        assert ledger._lineless_in_transit(PROGRAM) == {"carton": 600}

        # 200 of the 600 on a second shipment that has its line: the bare one carries the other 400.
        op(
            da,
            "shipment_record",
            AUG_28,
            data={**shipment, "reference": "SH-PART", "lines": [{"quantity": "200", "quantity_unit": "carton"}]},
        )
        on_road = ledger.in_transit(PROGRAM)
        assert (on_road.amount, on_road.unit) == (600, "carton")
        assert ledger._lineless_in_transit(PROGRAM) == {"carton": 400}


# ---- 6. the timeline's lines and sources -----------------------------------


def _source_heading(body):
    """Who the first email event is from, as its head reads (unanswered round 1004 b3: every
    email in a history is one event, headed by its sender, with no "Email from ..." line)."""
    head = re.search(r'data-testid="email-event-head"[^>]*>(.*?)</span>\s*<span data-testid="actor-badge"', body, re.S)
    return " ".join(re.sub(r"<[^>]+>", " ", head.group(1)).split())


def _tender_page(client, tender_id):
    return client.get(reverse("supply_chain:procurement_tender_detail", args=[tender_id])).content.decode()


@pytest.mark.django_db
class TestTheTimeline:
    def test_created_and_changed_lines_read_one_way(self, da, base, order):
        lines = [e.line for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM)]
        assert lines[0] == "Shipment · SH-1 · ETA 5 Sep → 19 Sep"
        assert "Shipment · SH-1 · recorded: ETA 5 Sep" in lines
        # The order's own page is headed by its name; its lines about it leave it off.
        assert lines[-1] == "Order · recorded: 600 cartons"

    def test_a_correction_reads_the_same_way(self, da, base, ace):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        _correct_pack(da, quote, ace)
        lines = [e.line for e in timeline_for_tender(base["tender"]["id"], program_id=PROGRAM)]
        assert "Quote · Northwind Foods · sachets per carton not stated → 150" in lines
        assert any(line.startswith("Quote · Northwind Foods · recorded: USD 42.50 per carton") for line in lines)

    def test_the_page_renders_the_line_and_dates_with_their_year(self, da, base, order, client_in_program):
        body = client_in_program.get(
            reverse("supply_chain:order_detail", args=[order["contract"]["id"]])
        ).content.decode()
        texts = [
            " ".join(re.sub(r"<[^>]+>", "", t).split())
            for t in re.findall(r'data-testid="revision-text"[^>]*>(.*?)</div>', body, re.S)
        ]
        assert "Shipment · SH-1 · ETA 5 Sep → 19 Sep ETA moved +14 days" in texts
        times = re.findall(r"<time [^>]*>(.*?)</time>", body[body.index('id="history"') :])
        assert times and all(re.fullmatch(r"\d{1,2} [A-Z][a-z]{2} 2026", t) for t in times)

    def test_a_shipment_s_carrier_is_not_its_sender(self, da, base, ace, client_in_program, order):
        """The rehearsal's dispatch notice came from the supplier and read "Email
        from Crescent Freight" because a carrier was on the shipment. A carrier
        moves the goods; it did not write the email (2026-10-02 spec, ruling 9)."""
        from connect_labs.supply_chain.models import Shipment

        heading = _source_heading(
            client_in_program.get(
                reverse("supply_chain:order_detail", args=[order["contract"]["id"]])
            ).content.decode()
        )
        # No carrier yet: the shipment says its supplier reported it.
        assert heading == "Northwind Foods"

        Shipment.objects.filter(pk=order["shipment"]["id"]).update(carrier="Harmattan Haulage")
        body = client_in_program.get(
            reverse("supply_chain:order_detail", args=[order["contract"]["id"]])
        ).content.decode()
        assert _source_heading(body) == "Northwind Foods"
        excerpt = re.search(r'<blockquote data-testid="source-excerpt" class="([^"]*)">(.*?)</blockquote>', body, re.S)
        assert excerpt.group(2) == EMAIL
        classes = excerpt.group(1).split()
        assert "text-sm" in classes and "text-gray-900" in classes and "text-xs" not in classes

    def test_a_quote_s_source_names_its_supplier(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        _correct_pack(da, quote, ace)
        heading = _source_heading(_tender_page(client_in_program, base["tender"]["id"]))
        assert heading == "Northwind Foods"


# ---- 7. the ranked row stays one line; the correction says where from -------


def _ranked(body, quote_id):
    return re.search(rf'<tr data-testid="ranked-row" data-quote-id="{quote_id}"[^>]*>.*?</tr>', body, re.S).group(0)


def _detail(body, quote_id):
    return re.search(rf'<tr data-testid="ranked-row-detail" data-detail-for="{quote_id}">.*?</tr>', body, re.S).group(
        0
    )


# ---- 8. a blocked card ------------------------------------------------------


def _card(body, quote_id):
    """A blocked card, up to the next card (or the end of the page)."""
    start = body.index(f'<div data-quote-id="{quote_id}"')
    after = body.find("<div data-quote-id=", start + 1)
    return body[start : after if after != -1 else len(body)]


# ---- 9. the public listing -------------------------------------------------


def _listing(client, tender):
    return client.get(reverse("supply_chain:market_tender", args=[tender.pk])).content.decode()


@pytest.mark.django_db
class TestThePublicListing:
    def test_no_deadline_is_said_as_none(self, client, listed_tender):
        body = _listing(client, listed_tender)
        assert re.search(r'data-testid="replies-by"[^>]*>Open for bids<', body)
        assert re.search(r'data-testid="replies-by-caption"[^>]*>NO DEADLINE SET<', body)
        assert "Open until closed" not in body and "REPLIES BY" not in body

    def test_the_request_is_summarised_beside_the_products(self, client, listed_tender):
        Commodity.objects.filter(slug="rutf").update(
            spec_requirements=[
                {"field": "sachets_per_carton", "operator": "==", "value": 150},
                {"field": "shelf_life_months", "operator": ">=", "value": 18, "unit": "months"},
            ]
        )
        body = _listing(client, listed_tender)
        aside = re.search(r'<aside data-testid="request-summary".*?</aside>', body, re.S).group(0)
        # Since batch 9 the quantity is the hero's alone; a named store still adds to its city.
        assert re.search(r'data-testid="asked-for"[^>]*>500 cartons<', body)
        assert re.search(r'data-testid="delivery-point"[^>]*>.*?Central store.*?Lakeside', aside, re.S)
        assert re.findall(r'data-testid="spec-line"[^>]*>(.*?)<', aside) == [
            "Sachets per carton: 150 (exact)",
            "Shelf life: at least 18 months",
        ]
        assert "Where the goods go" not in body

    def test_the_market_counts_suppliers_registered_on_it(self, client, listed_tender):
        body = client.get(reverse("supply_chain:market")).content.decode()
        assert "REGISTERED ON THE MARKETPLACE" in body

    def test_a_requirement_line(self):
        assert requirement_line({"field": "moisture_pct", "operator": "<=", "value": 2.5, "unit": "%"}) == (
            "Moisture: at most 2.5 %"
        )


# ---- 10. an awarded tender is not open to anyone --------------------------


@pytest.mark.django_db
class TestAnAwardedTenderSaysClosed:
    def test_the_pill(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        assert "open to all suppliers" in _tender_page(client_in_program, tender_id).lower()
        Tender.objects.filter(pk=tender_id).update(status="awarded")
        body = _tender_page(client_in_program, tender_id)
        assert "open to all suppliers" not in body.lower()
        assert "closed to new quotes" in body.lower()
        assert re.search(r'data-testid="tender-state-pill"[^>]*>Awarded<', body)
