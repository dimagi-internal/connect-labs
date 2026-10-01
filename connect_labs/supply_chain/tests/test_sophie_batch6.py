"""Sophie's RUTF walkthrough, judged again: the sixth batch of what it found.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the earlier batches: a synthetic program, history recorded
through `call_operation` with `seed_overrides`, and the pages read back as
rendered. These are the mechanical findings between a 3 and a 4: a caveat
said, a column held steady, a figure shown with its value.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.timeline import eta_moved, timeline_for_contract
from connect_labs.supply_chain.models import Commodity, Quote, Tender
from connect_labs.supply_chain.procurement.services.comparison import RANKING_RULE
from connect_labs.supply_chain.procurement.services.questions import missing_facts
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests import test_sophie_batch3 as batch3
from connect_labs.supply_chain.tests.test_history_timeline import (
    _COMPARABLE,
    AUG_3,
    AUG_20,
    AUG_28,
    PACK_EMAIL,
    PROGRAM,
    _correct_pack,
    _quote_with,
    op,
)
from connect_labs.supply_chain.tests.test_sophie_batch3 import (
    _ALL_BUT_PACK,
    MARKET_PROGRAM,
    _compare,
    _home,
    _page,
    _row,
    _sign_in,
    _with_spec,
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

_DELIVERED = {**_COMPARABLE, "freight_basis": "included", "duties_basis": "included"}


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _standing(body):
    start = body.index('id="supply-standing"')
    return body[start : body.index("</table>", start)]


def _supplier(da, name):
    return op(da, "supplier_create", AUG_3, data={"name": name})


def _tender_page(client, tender_id):
    return client.get(reverse("supply_chain:procurement_tender_detail", args=[tender_id])).content.decode()


def _order_page(client, contract_id):
    return client.get(reverse("supply_chain:order_detail", args=[contract_id])).content.decode()


# ---- 1. a provisional award: its reason in full, and why it is provisional ---


@pytest.mark.django_db
class TestAProvisionalAward:
    WHY = (
        "States everything needed to cost it and is the same manufacturer as the first round; "
        "provisional until the other two answer"
    )

    def _award(self, da, base):
        for name in ("Sahel Nutrition", "Plateau Mills"):
            _quote_with(da, base["tender"]["id"], _supplier(da, name)["id"], AUG_20, {})  # blocked
        chosen = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        op(da, "award_create", AUG_28, tender_id=base["tender"]["id"], quote_id=chosen["id"], rationale=self.WHY)

    def test_the_row_carries_the_caveat_and_the_whole_reason(self, da, base):
        self._award(da, base)
        (row,) = (r for r in standing_rows(PROGRAM, datetime.date(2026, 9, 12)) if r.kind == "tender")
        # Counted as quotes since batch 7 (nobody was invited and silent here).
        assert row.provisional_caveat == "provisional — 2 of 3 quotes not comparable"
        assert row.award_why == self.WHY

    def test_the_page_renders_both_under_the_stage(self, da, base, home_client):
        self._award(da, base)
        standing = _standing(_home(home_client))
        assert re.search(
            r'data-testid="provisional-caveat"[^>]*>provisional — 2 of 3 quotes not comparable<', standing
        )
        # A full-width row of its own under the tender's since batch 7.
        why = re.search(r'<p data-testid="award-why" class="([^"]*)"><span[^>]*>Why:</span> (.*?)</p>', standing)
        assert why.group(2) == self.WHY
        assert "whitespace-normal" in why.group(1).split()


# ---- 2. the overview's columns hold still ---------------------------------


def _colgroup(body):
    return re.search(r'<table data-testid="standing-table" class="([^"]*)">\s*<colgroup>(.*?)</colgroup>', body, re.S)


@pytest.mark.django_db
class TestTheOverviewColumns:
    def test_fixed_widths_the_same_today_and_as_of(self, da, base, home_client):
        today, past = _colgroup(_home(home_client)), _colgroup(_home(home_client, as_of="2026-08-20"))
        assert "table-fixed" in today.group(1).split() and "table-fixed" in past.group(1).split()
        widths = re.findall(r'data-col="([^"]+)" style="width: (\d+)%"', today.group(2))
        # A past date has no flags column since batch 7; its widths are fixed too.
        past_widths = dict(re.findall(r'data-col="([^"]+)" style="width: (\d+)%"', past.group(2)))
        assert list(past_widths) == ["title", "stage", "waiting", "last-change"]
        assert sum(int(w) for w in past_widths.values()) == 100
        by_col = {col: int(w) for col, w in widths}
        assert sum(by_col.values()) == 100
        # Stage is a few words; flags carry a line per supplier.
        assert by_col["stage"] < by_col["flags"] and by_col["flags"] == max(by_col.values())

    def test_headers_do_not_wrap(self, da, base, home_client):
        heads = re.findall(r"<th ([^>]*)>", _standing(_home(home_client)))
        assert len(heads) == 5 and all("whitespace-nowrap" in h for h in heads)
        heads = re.findall(r"<th ([^>]*)>", _standing(_home(home_client, as_of="2026-08-20")))
        assert len(heads) == 4 and all("whitespace-nowrap" in h for h in heads)


# ---- 3. can't compare yet: a line per supplier ------------------------------


@pytest.mark.django_db
class TestTheBlockedFlagReadsALineEach:
    def test_heading_then_one_line_per_supplier(self, da, base, home_client):
        tender_id = base["tender"]["id"]
        sahel = _supplier(da, "Sahel Nutrition")
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "freight_basis": "not_specified"})
        _quote_with(da, tender_id, sahel["id"], AUG_20, {**_DELIVERED, "duties_basis": "excluded"})

        (row,) = (r for r in standing_rows(PROGRAM, datetime.date(2026, 9, 12)) if r.kind == "tender")
        (flag,) = row.stale
        assert (flag.heading, flag.lines) == (
            "Can't compare yet — 2 quotes missing facts",
            ("Northwind Foods — missing: freight", "Sahel Nutrition — missing: duties amount"),
        )

        rendered = re.search(r'data-testid="stale-flag".*?</details>', _standing(_home(home_client)), re.S).group(0)
        assert re.findall(r'data-testid="flag-line"[^>]*>(.*?)</span>', rendered) == list(flag.lines)
        assert "Can&#x27;t compare yet — 2 quotes missing facts</span>" in rendered


# ---- 4. as of a past day ----------------------------------------------------


@pytest.mark.django_db
class TestAsOf:
    def test_the_last_change_is_the_day_alone(self, da, base, home_client):
        standing = _standing(_home(home_client, as_of="2026-08-20"))
        assert re.search(r'data-testid="last-change"[^>]*>3 Aug 2026<', standing)
        assert "days ago" not in standing and 'data-testid="last-change-day"' not in standing

    def test_the_applied_day_is_said_and_the_native_field_steps_back(self, da, base, home_client):
        past = _home(home_client, as_of="2026-08-20")
        control = re.search(r'data-testid="as-of-control".*?</form>', past, re.S).group(0)
        assert re.search(r'data-testid="as-of-date"[^>]*>20 Aug 2026<', control)
        field = re.search(r'<input id="supply-as-of" type="date"[^>]*>', control).group(0)
        assert "supply-as-of-applied" in field and 'value="2026-08-20"' in field
        assert "::-webkit-datetime-edit { display: none; }" in past
        # Still a working control: the walkthrough fills the date and presses the button.
        assert re.search(r'<button type="submit"[^>]*>Go</button>', control)

        live = re.search(r'data-testid="as-of-control".*?</form>', _home(home_client), re.S).group(0)
        assert "supply-as-of-applied" not in live and "opacity-60" not in live


# ---- 5. the chain says what it counts across -------------------------------


@pytest.mark.django_db
class TestTheChainSaysItsScope:
    def test_the_evaluation_is_across_the_program_s_tenders(self, da, base, home_client):
        second = op(
            da,
            "tender_create",
            AUG_3,
            data={
                "label": "Tender Two",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
            },
        )
        for tender_id in (base["tender"]["id"], second["id"]):
            op(da, "tender_open", AUG_3, tender_id=tender_id)
            _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, _DELIVERED)
        body = _home(home_client)
        chain = body[body.index("The chain") : body.index("Tenders</h2>")]
        assert "comparable across 2 tenders" in _text(chain)
        assert "live across 2 tenders" in _text(chain)


# ---- 6. the program is not named twice -------------------------------------


class _NamedProgramMiddleware:
    """The labs context as the real middleware gives it once a program is chosen: by name."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated:
            request.labs_context = {"program_id": PROGRAM, "program": {"id": PROGRAM, "name": "Connect-RUTF"}}
        return self.get_response(request)


@pytest.mark.django_db
class TestTheProgramHeading:
    HEADING = '<h2 class="text-lg font-semibold text-gray-900 mb-3">'

    def test_shown_when_neither_the_header_nor_the_banner_names_the_program(self, da, base, home_client):
        body = _home(home_client)
        # The program is named once: by the banner line when it can, else this heading.
        assert (self.HEADING in body) != ('data-testid="supply-program-line"' in body)

    def test_dropped_when_the_header_names_the_program(self, da, base, home_client, settings):
        # Before the first request: the test client builds its middleware chain once.
        settings.MIDDLEWARE = [*settings.MIDDLEWARE, f"{__name__}._NamedProgramMiddleware"]
        body = _home(home_client)
        assert 'data-testid="overview-row"' in body
        assert self.HEADING not in body


# ---- 7-9. the tender page and its timeline ----------------------------------


@pytest.mark.django_db
class TestTheTimelineReads:
    def test_dates_and_provenance_at_body_contrast(self, da, base, order, client_in_program):
        body = _order_page(client_in_program, order["contract"]["id"])
        history = body[body.index('id="history"') :]
        for classes in re.findall(r'<time class="([^"]*)"', history):
            assert "text-gray-900" in classes.split() and "text-gray-500" not in classes.split()
        heading = re.search(r'<p data-testid="source-heading" class="([^"]*)"', history).group(1).split()
        assert "text-gray-900" in heading and "text-gray-600" not in heading

    def test_sections_clear_the_fixed_header(self, da, base, client_in_program):
        body = _tender_page(client_in_program, base["tender"]["id"])
        assert re.search(r'<section id="history"[^>]*class="[^"]*scroll-mt-20', body)
        assert re.search(r'<h2 id="quotes" class="[^"]*scroll-mt-20[^"]*">Quotes</h2>', body)
        assert re.search(r'<h2 id="outreach" class="[^"]*scroll-mt-20[^"]*">Outreach</h2>', body)

    def test_an_eta_change_says_how_far_it_moved(self, da, base, order, client_in_program):
        (moved,) = (e for e in timeline_for_contract(order["contract"]["id"], program_id=PROGRAM) if e.eta_moved)
        assert moved.line == "Shipment · SH-1 · ETA 5 Sep → 19 Sep"
        assert moved.eta_moved == "ETA moved +14 days"

        body = _order_page(client_in_program, order["contract"]["id"])
        line = re.search(r'<li data-testid="revision-line" data-fields="[^"]*expected_on[^"]*".*?</li>', body, re.S)
        assert re.search(r'data-testid="eta-moved"[^>]*>ETA moved \+14 days<', line.group(0))
        assert body.count('data-testid="eta-moved"') == 1  # not on the shipment's create line

    def test_the_delta_is_signed(self):
        assert eta_moved({"expected_on": ["2026-09-19", "2026-09-16"]}) == "ETA moved -3 days"
        assert eta_moved({"expected_on": ["2026-09-05", "2026-09-06"]}) == "ETA moved +1 day"
        assert eta_moved({"expected_on": [None, "2026-09-06"]}) == ""
        assert eta_moved({"status": ["planned", "dispatched"]}) == ""

    def test_an_email_source_reads_view_email(self, da, base, order, client_in_program):
        body = _order_page(client_in_program, order["contract"]["id"])
        badge = re.search(r'<summary data-testid="actor-badge".*?</summary>', body, re.S).group(0)
        assert re.search(r'data-testid="source-link"[^>]*>View email<', badge)
        # The same excerpt still opens under it.
        details = body[body.index(badge) :]
        assert re.search(r'<blockquote data-testid="source-excerpt"', details[: details.index("</details>")])

    def test_a_document_source_still_reads_source(self, da, base, ace, client_in_program):
        quote = _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {},
            channel="mcp",
            actor=ace,
            source={"ref": "quotes/northwind-price-list.pdf", "excerpt": "42.50 a carton."},
        )
        assert quote
        body = _tender_page(client_in_program, base["tender"]["id"])
        assert re.search(r'data-testid="source-link"[^>]*>source<', body)
        assert "View email" not in body


# ---- 10. a question from what is already known ------------------------------


@pytest.mark.django_db
class TestQuestionsFromTheKnownState:
    def _facts(self, da, base, extra):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, extra)
        stored = Quote.objects.get(pk=quote["id"])
        return {f.key: f.question for f in missing_facts(stored, stored.commodity, stored.tender)}, quote

    def test_duties_excluded_without_an_amount_asks_for_the_amount(self, da, base):
        facts, quote = self._facts(da, base, {**_DELIVERED, "duties_basis": "excluded"})
        assert facts["duties_basis"] == "What are the import duties and taxes at Kano?"
        # And it is the blocker's one question.
        blocking = _row(_compare(da, base["tender"]["id"]), quote["id"])["blocking"]["question"]
        assert blocking["question"] == "What are the import duties and taxes at Kano?"

    def test_freight_excluded_without_an_amount_asks_for_the_charge(self, da, base):
        facts, _ = self._facts(da, base, {**_DELIVERED, "freight_basis": "excluded"})
        assert facts["freight_basis"] == "What is the freight charge to Kano?"

    def test_an_unstated_basis_still_asks_whether_it_is_included(self, da, base):
        facts, _ = self._facts(da, base, {**_DELIVERED, "duties_basis": "not_specified"})
        assert facts["duties_basis"].startswith("Does the price include import duties and taxes at Kano?")


# ---- 11. a blocked card -----------------------------------------------------


def _card(body, quote_id):
    start = body.index(f'<div data-quote-id="{quote_id}"')
    after = body.find("<div data-quote-id=", start + 1)
    return body[start : after if after != -1 else len(body)]


@pytest.mark.django_db
class TestABlockedCard:
    def test_the_spec_chip_is_not_repeated_under_the_question(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        card = _card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        assert re.search(r'data-testid="spec-chip"[^>]*>Spec: 150 sachets per carton<', card)
        assert "Our specification: exactly 150" not in card

    def test_not_blocking_says_the_rule(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        card = _card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        rule = re.search(r'data-testid="not-blocking-rule" title="([^"]*)"[^>]*>not blocking<', card).group(1)
        assert rule == RANKING_RULE.replace("'", "&#x27;")
        for gate in ("price", "pack size", "quantity", "freight", "duties"):
            assert gate in RANKING_RULE

    def test_the_blocker_outweighs_the_metadata(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        card = _card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        meta = re.search(r'data-testid="received-on" class="([^"]*)"', card).group(1).split()
        blocking = re.search(r'data-testid="blocking" class="([^"]*)"', card).group(1).split()
        assert "font-semibold" in blocking and "font-semibold" not in meta
        assert "text-gray-900" in blocking and "text-gray-600" in meta

    def test_the_banner_says_each_is_missing_one_fact(self, da, base, client_in_program):
        tender_id = base["tender"]["id"]
        _quote_with(da, tender_id, base["supplier"]["id"], AUG_20, {**_DELIVERED, "freight_basis": "not_specified"})
        _quote_with(
            da, tender_id, _supplier(da, "Sahel Nutrition")["id"], AUG_20, {**_DELIVERED, "duties_basis": "excluded"}
        )
        _quote_with(da, tender_id, _supplier(da, "Lakeside Foods")["id"], AUG_20, _ALL_BUT_PACK)
        banner = _text(
            re.search(r'<div class="bg-amber-50[^"]*">(.*?)</div>', _page(client_in_program, tender_id), re.S).group(1)
        )
        assert banner == "0 of 3 comparable — each quote below is missing one fact"

    def test_a_quote_missing_two_facts_is_not_said_to_miss_one(self, da, base, client_in_program):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})
        body = _page(client_in_program, base["tender"]["id"])
        assert "each quote below is missing at least one fact" in body


@pytest.mark.django_db
class TestStatedOnTheQuoteShowsItsValue:
    def test_the_figure_beside_its_name(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(
            da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {**_DELIVERED, "shelf_life_months_stated": 24}
        )
        row = _row(_compare(da, base["tender"]["id"]), quote["id"])
        assert [v["text"] for v in row["specification"]["stated_values"]] == [
            "Sachets per carton: 150",
            "Shelf life: 24 months",
        ]
        body = _page(client_in_program, base["tender"]["id"])
        stated = re.findall(r'data-spec-origin="quote">(.*?)</div>', body)
        assert stated == [
            "Sachets per carton: 150 (stated on the quote)",
            "Shelf life: 24 months (stated on the quote)",
        ]


# ---- 12. a ranked row -------------------------------------------------------


def _detail(body, quote_id):
    return re.search(rf'<tr data-testid="ranked-row-detail" data-detail-for="{quote_id}">.*?</tr>', body, re.S).group(
        0
    )


@pytest.mark.django_db
class TestARankedRow:
    def test_provenance_is_one_line_opening_on_the_quote(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        corrected = _correct_pack(da, quote, ace)
        detail = _detail(_page(client_in_program, base["tender"]["id"]), corrected["id"])
        source = re.search(r'<details data-testid="correction-source".*?</details>', detail, re.S).group(0)
        summary = _text(re.search(r"<summary[^>]*>(.*?)</summary>", source, re.S).group(1))
        assert summary == "Corrected 28 Aug by ACE (agent) from Northwind Foods email:"
        after_summary = source[source.index("</summary>") :]
        # The quote comes first under it; no second "recorded by" heading.
        assert after_summary.index('data-testid="source-excerpt"') < after_summary.index("Changed:")
        assert re.search(r'data-testid="source-excerpt"[^>]*>(.*?)</blockquote>', source).group(1) == PACK_EMAIL
        assert "recorded by" not in source

    def test_a_figure_the_correction_supplied_is_not_said_to_be_on_the_quote(self, da, base, ace, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        corrected = _correct_pack(da, quote, ace)
        detail = _detail(_page(client_in_program, base["tender"]["id"]), corrected["id"])
        # Not said twice: the correction's source line names the email.
        assert "from supplier email" not in detail
        assert "(stated on the quote)" not in detail

    def test_the_landed_basis(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        detail = _detail(_page(client_in_program, base["tender"]["id"]), quote["id"])
        assert re.search(
            r'data-testid="landed-basis"[^>]*>Delivered to Kano · freight included · duties included, per quote<',
            detail,
        )

    def test_the_landed_basis_says_an_added_amount_and_an_incoterm(self, da, base):
        quote = _quote_with(
            da,
            base["tender"]["id"],
            base["supplier"]["id"],
            AUG_20,
            {
                **_DELIVERED,
                "freight_basis": "excluded",
                "freight_amount": "300",
                "duties_basis": "not_specified",
                "incoterm": "DDP Kano",
            },
        )
        row = _row(_compare(da, base["tender"]["id"]), quote["id"])
        assert row["landed_basis"] == (
            "Delivered to Kano · freight 300.00 USD added · duties included, per quote and Incoterm DDP Kano"
        )

    def test_equal_per_carton_course_and_child_are_said_once(self, da, base, client_in_program):
        Commodity.objects.filter(slug="rutf").update(
            base_per_pack=150, course_definition={"base_units_per_course": 150}
        )
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        body = _page(client_in_program, base["tender"]["id"])
        # Since batch 7 the three equal columns are one, its header saying why.
        assert 'data-testid="unit-equivalence"' not in body
        assert "<th>USD per carton (= 1 course = 1 child treated)</th>" in body
        assert "<th>USD per child treated</th>" not in body and "<th>USD per course</th>" not in body

    def test_not_said_when_they_differ(self, da, base, client_in_program):
        Commodity.objects.filter(slug="rutf").update(
            base_per_pack=150, course_definition={"base_units_per_course": 300}
        )
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _DELIVERED)
        assert 'data-testid="unit-equivalence"' not in _page(client_in_program, base["tender"]["id"])


# ---- 13. preview as a supplier ---------------------------------------------


def _listing(client, tender, **params):
    return client.get(reverse("supply_chain:market_tender", args=[tender.pk]), params).content.decode()


@pytest.fixture
def owner(client, django_user_model):
    user = django_user_model.objects.create_user(username="sophie6", password="x", email="s6@example.org")
    return _sign_in(client, user, [{"id": MARKET_PROGRAM, "name": "Lakeside RUTF"}])


@pytest.mark.django_db
class TestPreviewAsASupplier:
    def test_the_owner_is_offered_the_preview(self, owner, listed_tender):
        body = _listing(owner, listed_tender)
        link = re.search(r'<a data-testid="preview-as-supplier" href="([^"]*)"[^>]*>Preview as a supplier</a>', body)
        assert link.group(1) == reverse("supply_chain:market_tender", args=[listed_tender.pk]) + "?as_supplier=1"

    def test_the_preview_is_the_supplier_s_page(self, owner, listed_tender):
        body = _listing(owner, listed_tender, as_supplier="1")
        assert "This is the public listing" not in body and 'data-testid="public-listing-note"' not in body
        assert "Your program&#x27;s tender" not in body and "Your program's tender" not in body
        assert reverse("supply_chain:procurement_tender_detail", args=[listed_tender.pk]) not in body
        assert "Register as a supplier" not in body
        # The Bid action is drawn, and goes nowhere.
        assert re.search(r'<span data-testid="preview-bid" aria-disabled="true"[^>]*>Bid →</span>', body)
        assert reverse("supply_chain:market_bid", args=[listed_tender.pk, "rutf"]) not in body
        assert re.search(r'data-testid="exit-supplier-preview" href="([^"]*)"', body).group(1) == reverse(
            "supply_chain:market_tender", args=[listed_tender.pk]
        )

    def test_a_non_member_is_unaffected(self, client, django_user_model, listed_tender):
        user = django_user_model.objects.create_user(username="plateau6", password="x", email="p6@example.org")
        _sign_in(client, user, [{"id": MARKET_PROGRAM + 1, "name": "Someone else's"}])
        plain, asked = _listing(client, listed_tender), _listing(client, listed_tender, as_supplier="1")
        for body in (plain, asked):
            assert 'data-testid="preview-as-supplier"' not in body
            assert 'data-testid="supplier-preview-bar"' not in body
            assert 'data-testid="preview-bid"' not in body
        assert "Register to bid" in asked


# ---- 14. the listing's hero and request -------------------------------------


@pytest.mark.django_db
class TestTheListingLayout:
    def test_one_product_is_one_panel_with_its_action(self, client, listed_tender):
        body = _listing(client, listed_tender)
        panel = re.search(r'<aside data-testid="request-summary".*?</aside>', body, re.S).group(0)
        assert re.search(r'data-testid="request-product"[^>]*>RUTF<', panel)
        assert "500 cartons" in panel and "Sign in to bid" in panel
        assert "<article" not in body and "THE REQUEST" not in body
        # The heading sits above the panel, not beside it in a column.
        assert body.index("What they are asking for") < body.index('data-testid="request-summary"')

    def test_several_products_keep_their_cards_beside_the_request(self, client, listed_tender):
        Commodity.objects.create(slug="rusf", name="RUSF", base_unit="sachet", pack_unit="carton")
        Tender.objects.filter(pk=listed_tender.pk).update(
            lines=[
                {"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"},
                {"commodity_slug": "rusf", "quantity": "200", "quantity_unit": "carton"},
            ]
        )
        body = _listing(client, listed_tender)
        assert body.count("<article") == 2
        grid = body.index('<div class="grid gap-6 lg:grid-cols')
        assert body.index("What they are asking for") < grid < body.index("<article")
