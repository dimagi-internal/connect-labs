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
from connect_labs.supply_chain.tests.test_history_timeline import AUG_20, AUG_28, _quote_with, op
from connect_labs.supply_chain.tests.test_sophie_batch3 import _home

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


# ---- 2. waiting on names what each blocked supplier is missing -------------


# ---- 3. the provisional stage: short caveat, price with what it commits ----


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
        assert _bare(pill) == "AI assistant"  # since DDD 003 batch 7
        assert re.search(r'<i data-testid="ai-glyph" [^>]*role="img" aria-label="AI"', pill)

    def test_the_overview_badge_says_what_was_recorded_on_hover(self, da, base, ace, home_client):
        _ace_quote(da, base, ace)
        standing = batch6._standing(_home(home_client))
        badge = re.search(r'data-ai data-testid="ai-badge" title="([^"]*)">(<span[^>]*></span>[^<]*)</span>', standing)
        assert _bare(badge.group(2)) == "AI assistant" and 'data-src="ai"' in badge.group(2)
        assert badge.group(1) == "ACE recorded Quote · Northwind Foods from a forwarded email"

    def test_a_person_via_ai_gets_no_second_marker(self, da, base, sophie, home_client):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {}, channel="mcp", actor=sophie)
        standing = batch6._standing(_home(home_client))
        badge = re.search(r'data-ai data-testid="ai-badge" title="([^"]*)">(<span[^>]*></span>[^<]*)</span>', standing)
        assert _bare(badge.group(2)).startswith("via AI")
        assert "ai-glyph" not in badge.group(2)
        assert "via an AI assistant recorded Quote · Northwind Foods" in badge.group(1)


# ---- 5. the timeline: the button says what a click does; the new value is bold


@pytest.mark.django_db
class TestTheTimeline:
    def test_the_source_toggle_keeps_one_label_and_turns_a_caret(self, da, base, order, client_in_program):
        body = batch6._order_page(client_in_program, order["contract"]["id"])
        # An AI-recorded email is an email event (unanswered round 1004 b3), the toggle its own.
        assert re.search(r'<details class="group mt-1 w-full text-xs">', body)
        button = re.search(r'<span data-testid="source-link"[^>]*>(.*?)</summary>', body, re.S).group(1)
        assert "<span>Source email</span>" in button
        assert "Hide email" not in button
        assert 'data-testid="source-caret"' in button and "group-open:rotate-90" in button

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
        field = re.search(r'<input id="supply-as-of"[^>]*>', past).group(0)
        assert 'value="20 Aug 2026"' in field
        live_field = re.search(r'<input id="supply-as-of"[^>]*>', live).group(0)
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


# ---- 10. the ranked table reads a per-sachet price to three places ---------


@pytest.mark.django_db
class TestTheRankedTable:

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
