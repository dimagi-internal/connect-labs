"""Sophie's RUTF walkthrough, judged a seventh time: each screen says one thing per line.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the earlier batches. What these pin: the overview names who
has not replied apart from whose quote is missing facts, keeps a silent
supplier's flag up for as long as it names them, gives a provisional award its
price, a caveat that counts quotes and silences, and its reason a row of its
own; the can't-compare flag is one line that folds open; a past date has no
flags column; the timeline's badge and its "View email" button are two things;
the supplier preview is a bar that counts what it hides; a blocked card links to
its supplier's drafted email and says its price on its own line; three equal
per-unit columns are one; and the tender's own page links to its listing.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.market.views import _hidden_from_suppliers
from connect_labs.supply_chain.models import Commodity, Tender
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests import test_sophie_judged_batch6 as judged6
from connect_labs.supply_chain.tests.test_history_timeline import AUG_3, AUG_20, AUG_28, PROGRAM, _quote_with, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _home, _page

registered_synthetic = batch6.registered_synthetic
da = batch6.da
sophie = batch6.sophie
ace = batch6.ace
base = batch6.base
order = batch6.order
client_in_program = batch6.client_in_program
home_client = batch6.home_client
listed_tender = batch6.listed_tender
owner = batch6.owner

_DELIVERED = batch6._DELIVERED
SEP_12 = datetime.date(2026, 9, 12)
WHY = "Complete and costed; the other two have not answered yet"


def _tender_row(today=SEP_12):
    (row,) = (r for r in standing_rows(PROGRAM, today) if r.kind == "tender")
    return row


def _provisional(da, base):
    """Northwind awarded at 42.50 a carton over Sahel (blocked), with Plateau invited and silent since 10 Aug."""
    blocked = batch6._supplier(da, "Sahel Nutrition")
    _quote_with(da, base["tender"]["id"], blocked["id"], AUG_20, {})
    silent = batch6._supplier(da, "Plateau Mills")
    judged6._outreach(da, base["tender"]["id"], silent["id"], datetime.date(2026, 8, 10))
    chosen = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
    op(da, "award_create", AUG_28, tender_id=base["tender"]["id"], quote_id=chosen["id"], rationale=WHY)
    return chosen


# ---- 1. waiting on: who has not replied, apart from whose quote lacks facts ---


@pytest.mark.django_db
class TestWaitingOnSaysTwoThings:
    def test_an_open_tender_names_the_silent_and_the_blocked_on_a_line_each(self, da, base, home_client):
        tender_id = base["tender"]["id"]
        op(da, "tender_open", AUG_20, tender_id=tender_id)
        judged6._outreach(da, tender_id, base["supplier"]["id"], datetime.date(2026, 8, 10), responded=True)
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {})  # blocked
        silent = batch6._supplier(da, "Plateau Mills")
        judged6._outreach(da, tender_id, silent["id"], datetime.date(2026, 8, 10))

        row = _tender_row()
        # Since batch 8 each blocked supplier carries what it is missing.
        missing = "Missing facts: Northwind Foods (sachets per carton, freight, duties)"
        assert row.waiting_lines == ("No reply: Plateau Mills", missing)
        assert row.waiting_detail == "1 of 2 replied"
        standing = batch6._standing(_home(home_client))
        lines = re.findall(r'data-testid="waiting-line"[^>]*>(.*?)</(?:span|div)>', standing)
        # Each line's kind in bold. Since unanswered-round 002 the silent
        # suppliers stack one a line under "No reply", each with the day asked.
        # (Read on today's page, the round may be past its deadline, which leads the
        # cell with "Us: extend or close the round"; that line is pinned elsewhere.)
        assert any(line.startswith("<strong>No reply</strong>:") for line in lines)
        assert re.findall(r'data-testid="silent-supplier">(.*?)</li>', standing) == [
            'Plateau Mills <span class="text-gray-600">(asked 10 Aug)</span>'
        ]
        # One labelled list per owner: "Missing facts" heads its suppliers as "No reply" does.
        assert any(line.startswith("<strong>Missing facts</strong>:") for line in lines)
        items = re.findall(r'data-testid="waiting-item">(.*?)</li>', standing)
        items = [re.sub(r"<[^>]+>", "", i) for i in items]
        assert [i for i in items if not i.startswith("extend or close the round")] == [missing[15:]]

    def test_after_a_provisional_award_the_awardee_is_not_missing_facts(self, da, base):
        _provisional(da, base)
        row = _tender_row()
        assert row.waiting_lines == (
            "No reply: Plateau Mills",
            "Missing facts: Sahel Nutrition (sachets per carton, freight, duties)",
        )
        assert "Northwind" not in row.waiting_on
        assert all("Northwind" not in line for flag in row.stale for line in flag.lines)

    def test_the_silent_supplier_s_flag_stays_while_waiting_on_names_them(self, da, base):
        _provisional(da, base)
        row = _tender_row()
        assert "No reply in 33 days: 1 supplier" in row.stale
        assert any(line.startswith("No reply: Plateau Mills") for line in row.waiting_lines)

    def test_both_go_once_the_order_is_placed(self, da, base):
        _provisional(da, base)
        op(
            da,
            "contract_create",
            AUG_28,
            data={
                "supplier_id": base["supplier"]["id"],
                "tender_id": base["tender"]["id"],
                "commodity_slug": "rutf",
                "buyer_of_record": "programme_org",
                "buyer_org_id": base["us"]["id"],
                "reference": "PO-B7",
                "quantity": "600",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        row = _tender_row()
        assert not any(flag.startswith("No reply") for flag in row.stale)
        assert row.waiting_lines == () and "Plateau" not in row.waiting_on


# ---- 2. the provisional caveat counts quotes and silences; the stage says the price


@pytest.mark.django_db
class TestTheProvisionalStage:
    def test_the_caveat_counts_quotes_and_who_has_not_replied(self, da, base):
        _provisional(da, base)
        # Since batch 8 the silent supplier is waiting-on's and the flags' to name, not the caveat's.
        assert _tender_row().provisional_caveat == "provisional — 1 of 2 quotes not yet comparable"

    def test_the_stage_line_carries_the_awarded_price(self, da, base, home_client):
        _provisional(da, base)
        row = _tender_row()
        # Since batch 8 with what the award commits, worded as the comparison's landed total.
        # Since the unanswered round's batch 4 the total says it is landed, and what it includes.
        price = "USD 42.50 per carton · USD 25,500.00 landed (incl. freight and duties) for 600 cartons"
        assert (row.stage, row.award_price) == ("awarded to Northwind Foods", price)
        standing = batch6._standing(_home(home_client))
        cell = re.search(r'<td class="px-4 py-2.5">\s*awarded to Northwind Foods(.*?)</td>', standing, re.S).group(1)
        # Since batch 9 a clause a line under the stage.
        lines = re.findall(r'data-testid="award-price-line"[^>]*>(.*?)</span>', cell)
        assert lines == price.split(" · ")


# ---- 3. the award's why is a full-width row ----------------------------------


@pytest.mark.django_db
class TestTheWhyRow:
    def test_a_row_of_its_own_across_every_column(self, da, base, home_client):
        _provisional(da, base)
        standing = batch6._standing(_home(home_client))
        row = re.search(r'<tr data-testid="award-why-row"[^>]*>(.*?)</tr>', standing, re.S).group(1)
        assert re.search(r'<td colspan="5"', row)
        why = re.search(r'<p data-testid="award-why" class="([^"]*)">(.*?)</p>', row, re.S)
        assert batch6._text(why.group(2)) == f"Why: {WHY}"
        assert "text-sm" in why.group(1).split()


# ---- 4. can't compare yet: one line, the suppliers folded under it -----------


@pytest.mark.django_db
class TestTheCantCompareMarker:
    def test_one_line_that_folds_open_on_the_suppliers(self, da, base, home_client):
        tender_id = base["tender"]["id"]
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "freight_basis": "not_specified"})
        _quote_with(
            da,
            tender_id,
            batch6._supplier(da, "Sahel Nutrition")["id"],
            AUG_20,
            {**_DELIVERED, "duties_basis": "excluded"},
        )
        standing = batch6._standing(_home(home_client))
        flag = re.search(r'<details data-testid="stale-flag"[^>]*>(.*?)</details>', standing, re.S).group(1)
        summary = re.search(r"<summary[^>]*>(.*?)</summary>", flag, re.S).group(1)
        assert batch6._text(summary) == "2 quotes missing facts"
        after = flag[flag.index("</summary>") :]
        # Folded open, the definition: who is missing what is the Waiting on cell's (DDD 002 batch 2).
        assert len(re.findall(r'data-testid="flag-line"[^>]*>(.*?)</span>', after)) == 1
        # Not the filled amber block any more.
        assert "bg-amber-50 px-1.5" not in summary


# ---- 5. a past date: no flags column -----------------------------------------


@pytest.mark.django_db
class TestAsOfHasNoFlagsColumn:
    def test_the_column_goes_and_one_note_says_why(self, da, base, home_client):
        past = _home(home_client, as_of="2026-08-20")
        standing = batch6._standing(past)
        assert ">Flags<" not in standing and 'data-col="flags"' not in standing
        assert 'data-testid="stale-flag"' not in standing
        assert past.count("Flags are worked out for today only") == 1
        assert ">Flags</th>" in _home(home_client)


# ---- 6. the timeline: the badge and the button are two things ---------------


@pytest.mark.django_db
class TestTheTimelineBadge:
    def test_the_badge_and_view_email_are_separate(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        # Since the unanswered round's batch 2 the badge is only a label; the source opens
        # from its own toggle beside it.
        badge = re.search(r'<span data-testid="actor-badge" data-ai .*?</span></span></span>', body, re.S).group(0)
        pill = re.search(r'<span data-testid="actor-pill"[^>]*>(.*?)</span></span>', badge, re.S).group(1)
        # Since batch 8 the AI marker is a glyph, not the word: "AI" was said twice.
        assert batch6._text(re.sub(r"<[^>]+>", "", pill)) == "ACE (agent)"
        assert 'aria-label="AI"' in pill
        assert "Source email" not in badge and "<summary" not in badge
        toggle = re.search(r'<summary data-testid="source-toggle".*?</summary>', body, re.S).group(0)
        button = re.search(r'<span data-testid="source-link" class="([^"]*)">.*?Source email</span>', toggle, re.S)
        assert {"border", "rounded"} <= set(button.group(1).split())

    def test_the_excerpt_runs_the_line_s_width(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        assert re.search(r'<details class="group text-xs open:w-full">\s*<summary data-testid="source-toggle"', body)
        quote = re.search(r'<blockquote data-testid="source-excerpt" class="([^"]*)"', body).group(1).split()
        assert "w-full" in quote and "max-w-2xl" not in quote


# ---- 7. the supplier preview -------------------------------------------------


@pytest.mark.django_db
class TestTheSupplierPreview:
    def test_a_tinted_bar_with_the_way_back(self, owner, listed_tender):
        body = batch6._listing(owner, listed_tender, as_supplier="1")
        bar = re.search(r'<div data-testid="supplier-preview-bar" class="([^"]*)">(.*?)</div>\s*</div>', body, re.S)
        assert "bg-indigo-50" in bar.group(1).split() and "border" in bar.group(1).split()
        assert "Supplier view — this is exactly what suppliers see" in bar.group(2)
        assert re.search(r'data-testid="exit-supplier-preview"[^>]*>Back to your view<', bar.group(2))
        assert "Previewing as a supplier would see it." not in body

    def test_what_is_hidden_is_counted(self, da, base, ace):
        tender = Tender.objects.get(pk=base["tender"]["id"])
        _quote_with(
            da,
            tender.pk,
            base["supplier"]["id"],
            AUG_20,
            {},
            channel="mcp",
            actor=ace,
            source={"ref": "<msg-b7@northwind.example>", "excerpt": "42.50 a carton, delivered."},
        )
        _quote_with(da, tender.pk, batch6._supplier(da, "Sahel Nutrition")["id"], AUG_20, {})
        hidden = _hidden_from_suppliers(tender)
        quotes, emails, history = hidden.split(" · ")
        assert quotes == "2 quotes and their prices"
        assert emails == "1 email excerpt"
        assert re.fullmatch(r"\d+ history entries", history)
        # Counts, never content.
        assert "42.50" not in hidden and "Northwind" not in hidden

    def test_the_lone_open_tenders_tab_goes_under_the_back_link(self, owner, listed_tender, client):
        for body in (batch6._listing(owner, listed_tender), batch6._listing(owner, listed_tender, as_supplier="1")):
            assert "← OPEN TENDERS" in body
            assert 'aria-label="Supply marketplace"' not in body
        # The market's own page keeps its tabs.
        assert 'aria-label="Supply marketplace"' in client.get(reverse("supply_chain:market")).content.decode()

    def test_the_hooks_the_walkthrough_uses_stay(self, owner, listed_tender):
        body = batch6._listing(owner, listed_tender)
        assert 'data-testid="preview-as-supplier"' in body and "This is the public listing" in body
        assert 'data-testid="supplier-preview-hidden"' in batch6._listing(owner, listed_tender, as_supplier="1")


# ---- 8. a blocked card -------------------------------------------------------


@pytest.mark.django_db
class TestABlockedCard:
    def test_it_links_to_the_supplier_s_drafted_email(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        op(da, "tender_open", AUG_20, tender_id=tender_id)
        judged6._outreach(da, tender_id, base["supplier"]["id"], datetime.date(2026, 8, 10), responded=True)
        quote = _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {})
        card = batch6._card(_page(client_in_program, tender_id), quote["id"])
        link = re.search(r'<a data-testid="draft-email-link" href="([^"]*)"[^>]*>(.*?)</a>', card)
        anchor = f"draft-supplier-{base['supplier']['id']}"
        assert link.group(2) == "Draft email to Northwind Foods"
        assert link.group(1) == reverse("supply_chain:procurement_tender_detail", args=[tender_id]) + f"#{anchor}"
        # The anchor is there, on that supplier's draft.
        page = batch6._tender_page(client_in_program, tender_id)
        assert re.search(rf'<div data-testid="draft" id="{anchor}" data-supplier-id="{base["supplier"]["id"]}"', page)
        assert page.count(f'id="{anchor}"') == 1

    def test_the_price_is_its_own_line_and_the_day_is_metadata(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        price = re.search(r'<p data-testid="as-quoted" class="([^"]*)">(.*?)</p>', card, re.S)
        assert batch6._text(price.group(2)) == "Quoted USD 42.50 per carton"
        assert "text-gray-600" not in price.group(1).split()
        assert re.search(r'data-testid="received-on"[^>]*>Received 20 Aug 2026<', card)
        assert card.index('data-testid="as-quoted"') < card.index('data-testid="received-on"')

    def test_a_per_sachet_price_says_it_is_converted(self, da, base, client_in_program):
        quote = _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {"as_quoted_amount": "0.28", "as_quoted_unit": "per_base_unit"},
        )
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        price = batch6._text(re.search(r'<p data-testid="as-quoted"[^>]*>(.*?)</p>', card, re.S).group(1))
        # Since batch 8: with the pack unknown, it says when the per-carton figure can be given.
        assert price == "Quoted USD 0.28 per sachet (per carton once sachets per carton is known)"

    def test_a_per_carton_price_says_nothing_more(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        assert 'data-testid="as-quoted-note"' not in card


# ---- 9. three equal per-unit columns are one --------------------------------


@pytest.mark.django_db
class TestOneUnitColumn:
    def test_collapsed_when_a_carton_is_a_course_is_a_child(self, da, base, client_in_program):
        Commodity.objects.filter(slug="rutf").update(
            base_per_pack=150, course_definition={"base_units_per_course": 150}
        )
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        body = _page(client_in_program, base["tender"]["id"])
        heads = re.findall(r"<th>(.*?)</th>", body)
        assert "USD per carton (one course)" in heads
        assert "USD per course" not in heads and "USD per child treated" not in heads
        # One cell per header still.
        row = re.search(rf'<tr data-testid="ranked-row" data-quote-id="{quote["id"]}"[^>]*>(.*?)</tr>', body, re.S)
        assert len(re.findall(r"<td", row.group(1))) == len(heads)

    def test_kept_apart_when_they_differ(self, da, base, client_in_program):
        Commodity.objects.filter(slug="rutf").update(
            base_per_pack=150, course_definition={"base_units_per_course": 300}
        )
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        heads = re.findall(r"<th>(.*?)</th>", _page(client_in_program, base["tender"]["id"]))
        assert "USD per carton" in heads and "USD per course" in heads


# ---- 10. the tender's own page links to its public listing ------------------


@pytest.mark.django_db
class TestTheListingLink:
    def test_an_open_tender_links_to_its_listing(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        op(da, "tender_open", AUG_3, tender_id=tender_id)
        page = batch6._tender_page(client_in_program, tender_id)
        link = re.search(r'<a data-testid="market-listing-link" href="([^"]*)"', page)
        assert link.group(1) == reverse("supply_chain:market_tender", args=[tender_id])

    def test_not_before_it_is_listed(self, da, base, client_in_program):
        page = batch6._tender_page(client_in_program, base["tender"]["id"])
        assert 'data-testid="market-listing-link"' not in page
