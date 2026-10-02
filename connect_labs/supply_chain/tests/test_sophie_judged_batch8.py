"""Sophie's RUTF walkthrough, judged an eighth time: the same thing looks the same everywhere.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the earlier batches. What these pin: the "can't compare yet"
flag is the same boxed chip as every other flag and still opens; "waiting on"
names what each blocked supplier is missing; the provisional caveat is short
and the award line carries what it commits; the AI marker is a glyph, so the
agent badge reads "ACE (agent)" once; the timeline's source button says "Hide
email" while open and each change's new value is bold; the overview's AI pill
says on hover what was recorded; a past day sits inside the date field; the
listing's request spans the page and the preview's bid is visibly disabled; a
blocked per-sachet price says its per-carton figure, and the pack requirement
is a grey line; the ranked table reads a per-sachet price to three places.
"""

import datetime
import re

import pytest

from connect_labs.supply_chain.models import Commodity
from connect_labs.supply_chain.templatetags.supply_chain_extras import bold_after_arrow, money_text
from connect_labs.supply_chain.tests import test_sophie_batch6 as batch6
from connect_labs.supply_chain.tests import test_sophie_judged_batch6 as judged6
from connect_labs.supply_chain.tests import test_sophie_judged_batch7 as judged7
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, AUG_28, _quote_with, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _ALL_BUT_PACK, _home, _page, _with_spec

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
_text = batch6._text


def _bare(html):
    return _text(re.sub(r"<[^>]+>", "", html))


# ---- 1. the can't-compare flag is the same chip as the others ---------------


@pytest.mark.django_db
class TestTheCantCompareChip:
    def test_boxed_like_the_other_flags_and_still_opens(self, da, base, home_client):
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
        flag = re.search(r'<details data-testid="stale-flag"[^>]*>\s*<summary class="([^"]*)"', standing)
        classes = set(flag.group(1).split())
        assert {"rounded", "border", "border-amber-200", "bg-amber-50"} <= classes
        # Still folds open on the per-supplier lines.
        details = re.search(r'<details data-testid="stale-flag".*?</details>', standing, re.S).group(0)
        assert len(re.findall(r'data-testid="flag-line"', details)) == 2


# ---- 2. waiting on names what each blocked supplier is missing -------------


@pytest.mark.django_db
class TestMissingFactsSayWhich:
    def test_each_supplier_with_its_missing_facts(self, da, base, home_client):
        tender_id = base["tender"]["id"]
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "freight_basis": "not_specified"})
        _quote_with(
            da,
            tender_id,
            batch6._supplier(da, "Sahel Nutrition")["id"],
            AUG_20,
            {**_DELIVERED, "duties_basis": "excluded"},
        )
        row = judged7._tender_row()
        assert row.waiting_on == "Missing facts: Northwind Foods (freight), Sahel Nutrition (duties amount)"
        # The same words as the flag's per-supplier lines, from the same comparison.
        assert row.stale[0].lines == ("Northwind Foods — missing: freight", "Sahel Nutrition — missing: duties amount")


# ---- 3. the provisional stage: short caveat, price with what it commits ----


@pytest.mark.django_db
class TestTheProvisionalStage:
    def test_the_caveat_does_not_repeat_waiting_on(self, da, base, home_client):
        judged7._provisional(da, base)
        row = judged7._tender_row()
        assert row.provisional_caveat == "provisional — 1 of 2 quotes not yet comparable"
        assert "replied" not in row.provisional_caveat
        # Waiting-on still names the silent supplier.
        assert row.waiting_lines[0] == "No reply: Plateau Mills"

    def test_the_award_line_carries_the_committed_total(self, da, base, home_client):
        judged7._provisional(da, base)
        standing = batch6._standing(_home(home_client))
        # Since batch 9 a clause a line under the stage, with no leading "·".
        lines = re.findall(r'data-testid="award-price-line"[^>]*>(.*?)</span>', standing)
        assert lines == ["USD 42.50 per carton", "USD 25,500.00 for 600 cartons"]


# ---- 4. the AI marker is a glyph, so "AI" is not said twice ----------------


def _ace_quote(da, base, ace, ref="<msg-b8@northwind.example>"):
    return _quote_with(
        da,
        base["tender"]["id"],
        base["supplier"]["id"],
        AUG_20,
        {},
        channel="mcp",
        actor=ace,
        source={"ref": ref, "excerpt": "Our price is 42.50 a carton."},
    )


@pytest.mark.django_db
class TestTheAgentBadge:
    def test_the_timeline_badge(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        badge = re.search(r'<span data-testid="actor-badge" data-ai .*?</span></span></span>', body, re.S).group(0)
        pill = re.search(r'<span data-testid="actor-pill"[^>]*>(.*?)</span></span>', badge, re.S).group(1)
        assert _bare(pill) == "ACE (agent)"
        assert re.search(r'<i data-testid="ai-glyph" [^>]*role="img" aria-label="AI"', pill)

    def test_the_comparison_card_badge(self, da, base, ace, client_in_program):
        quote = _ace_quote(da, base, ace)
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        badge = re.search(r'<span data-ai data-testid="ai-badge"[^>]*>(.*?)</span></span>', card, re.S).group(1)
        assert _bare(badge) == "ACE (agent)" and 'aria-label="AI"' in badge

    def test_the_overview_badge_says_what_was_recorded_on_hover(self, da, base, ace, home_client):
        _ace_quote(da, base, ace)
        standing = batch6._standing(_home(home_client))
        badge = re.search(
            r'<span data-ai data-testid="ai-badge" title="([^"]*)"[^>]*>(.*?)</span></span>', standing, re.S
        )
        assert _bare(badge.group(2)) == "ACE (agent)" and 'aria-label="AI"' in badge.group(2)
        assert badge.group(1) == "ACE recorded Quote · Northwind Foods from a forwarded email"

    def test_a_person_via_ai_gets_no_second_marker(self, da, base, sophie, home_client):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {}, channel="mcp", actor=sophie)
        standing = batch6._standing(_home(home_client))
        badge = re.search(
            r'<span data-ai data-testid="ai-badge" title="([^"]*)"[^>]*>(.*?)</span></span>', standing, re.S
        )
        assert _bare(badge.group(2)).startswith("via AI")
        assert "ai-glyph" not in badge.group(2)
        assert "via an AI assistant recorded Quote · Northwind Foods" in badge.group(1)


# ---- 5. the timeline: the button says what a click does; the new value is bold


@pytest.mark.django_db
class TestTheTimeline:
    def test_view_email_becomes_hide_email_while_open(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        assert re.search(r'<details class="group text-xs open:w-full">', body)
        button = re.search(r'<span data-testid="source-link"[^>]*>(.*?)</summary>', body, re.S).group(1)
        assert '<span class="group-open:hidden">Source email</span>' in button
        assert '<span data-testid="source-hide" class="hidden group-open:inline">Hide email</span>' in button

    def test_the_value_after_the_arrow_is_bold(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        assert 'ETA 5 Sep → <strong class="font-semibold">19 Sep</strong>' in body

    def test_the_filter_escapes_and_bolds_each_change(self):
        out = bold_after_arrow("Status draft → open; Note <b> → a & b")
        assert out == (
            'Status draft → <strong class="font-semibold">open</strong>; '
            'Note &lt;b&gt; → <strong class="font-semibold">a &amp; b</strong>'
        )
        assert bold_after_arrow("Quote recorded: 42.50 USD") == "Quote recorded: 42.50 USD"


# ---- 7. a past day sits inside the date field ------------------------------


@pytest.mark.django_db
class TestTheAsOfField:
    def test_the_day_is_the_field_s_value_and_the_field_looks_as_today(self, da, base, home_client):
        past = re.search(r'data-testid="as-of-control".*?</form>', _home(home_client, as_of="2026-08-20"), re.S).group(
            0
        )
        live = re.search(r'data-testid="as-of-control".*?</form>', _home(home_client), re.S).group(0)
        field = re.search(r'<input id="supply-as-of" type="date"[^>]*>', past).group(0)
        assert 'value="2026-08-20"' in field
        live_field = re.search(r'<input id="supply-as-of" type="date"[^>]*>', live).group(0)
        cls = re.search(r'class="([^"]*)"', field).group(1)
        assert cls == re.search(r'class="([^"]*)"', live_field).group(1)
        # No bare day beside a lone calendar button.
        assert "<span" not in past and "opacity-60" not in past


# ---- 8. the listing spans the page; the preview's bid is disabled -----------


@pytest.mark.django_db
class TestTheListing:
    def test_the_request_spans_the_banner_s_width(self, client, listed_tender):
        Commodity.objects.filter(slug="rutf").update(
            spec_requirements=[{"field": "sachets_per_carton", "operator": "==", "value": 150}]
        )
        body = batch6._listing(client, listed_tender)
        aside = re.search(r'<aside data-testid="request-summary" class="([^"]*)".*?</aside>', body, re.S)
        assert "max-w-3xl" not in aside.group(1).split()
        columns = re.search(r'data-testid="request-columns" class="([^"]*)"', aside.group(0)).group(1).split()
        assert "md:grid-cols-2" in columns
        # The specification is the right-hand column, after the quantity and the place.
        panel = aside.group(0)
        assert panel.index("Delivered to") < panel.index('data-testid="spec-line"')

    def test_the_preview_bid_is_disabled_and_says_where_suppliers_bid(self, owner, listed_tender):
        body = batch6._listing(owner, listed_tender, as_supplier="1")
        # Since batch 9 plain words, not a greyed button.
        bid = re.search(r'<p data-testid="preview-bid" class="([^"]*)">(.*?)</p>', body)
        assert bid.group(2) == "Suppliers place their bid from this page."
        assert "pg-link" not in bid.group(1).split() and "border" not in bid.group(1).split()
        assert "Bid →" not in body


# ---- 9. a blocked card: the per-carton figure; the pack requirement a line --


@pytest.mark.django_db
class TestABlockedCard:
    def test_a_per_sachet_price_says_its_per_carton_figure(self, da, base, client_in_program):
        quote = _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {
                **_DELIVERED,
                "freight_basis": "not_specified",
                "as_quoted_amount": "0.29",
                "as_quoted_unit": "per_base_unit",
            },
        )
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        price = _text(re.search(r'<p data-testid="as-quoted"[^>]*>(.*?)</p>', card, re.S).group(1))
        assert price == "Quoted USD 0.29 per sachet = USD 43.50 per carton"

    def test_the_pack_requirement_is_a_grey_line_not_a_chip(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        card = batch6._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        line = re.search(r'<p data-testid="blocker-spec-line" class="([^"]*)">(.*?)</p>', card)
        assert line.group(2) == "Sachets per carton: not stated (tender requires 150)"
        assert {"text-xs", "text-gray-600"} <= set(line.group(1).split())
        assert 'data-testid="spec-chip"' not in card


# ---- 10. the ranked table reads a per-sachet price to three places ---------


@pytest.mark.django_db
class TestTheRankedTable:
    def test_usd_per_sachet_to_three_places(self, da, base, client_in_program):
        quote = _quote_with(
            da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {**_DELIVERED, "as_quoted_amount": "41.00"}
        )
        body = _page(client_in_program, base["tender"]["id"])
        row = re.search(
            rf'<tr data-testid="ranked-row" data-quote-id="{quote["id"]}"[^>]*>(.*?)</tr>', body, re.S
        ).group(1)
        assert "USD 0.273" in row and "0.2733" not in row
        assert "USD 41.00" in row

    def test_the_cap_only_shortens(self):
        assert money_text({"amount": "0.27333", "currency": "USD"}, 3) == "USD 0.273"
        assert money_text({"amount": "41", "currency": "USD"}, 3) == "USD 41.00"
        assert money_text({"amount": "0.27333", "currency": "USD"}) == "USD 0.2733"


# The walkthrough's hooks stay where they were.
@pytest.mark.django_db
def test_the_hooks_stay(da, base, order, client_in_program, home_client):
    judged6._outreach(da, base["tender"]["id"], base["supplier"]["id"], datetime.date(2026, 8, 10))
    op(da, "tender_open", AUG_28, tender_id=base["tender"]["id"])
    body = batch6._order_page(client_in_program, order["contract"]["id"])
    for hook in ("timeline", "revision-line", "actor-badge", "source-excerpt"):
        assert f'data-testid="{hook}"' in body
    home = _home(home_client)
    for hook in ("overview-row", "as-of-control"):
        assert f'data-testid="{hook}"' in home
