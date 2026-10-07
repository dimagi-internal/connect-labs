"""Sophie's RUTF walkthrough, judged: the third batch of what it found.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Built on the timeline tests' fixtures (a synthetic program, history recorded
through `call_operation` with `seed_overrides`), and read back through the
rendered pages rather than only their context.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import OperationCall
from connect_labs.supply_chain.models import Commodity, Item, Quote, Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.services.compliance import check_compliance
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.templatetags.supply_chain_extras import days_ago_and_day
from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests.test_history_timeline import (
    _COMPARABLE,
    AUG_3,
    AUG_20,
    AUG_28,
    PROGRAM,
    _correct_pack,
    _quote_with,
    op,
)

# The timeline tests' fixtures, shared rather than copied.
registered_synthetic = timeline.registered_synthetic
da = timeline.da
sophie = timeline.sophie
ace = timeline.ace
base = timeline.base
client_in_program = timeline.client_in_program

SPEC = [
    {"field": "sachets_per_carton", "operator": "==", "value": 150},
    {"field": "shelf_life_months", "operator": ">=", "value": 24, "unit": "months"},
]
# Everything a quote needs to be comparable except its pack.
_ALL_BUT_PACK = dict(freight_basis="included", duties_basis="included", base_unit_grams_stated=92)


def _with_spec(da):
    op(
        da,
        "commodity_upsert",
        AUG_3,
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "spec_requirements": SPEC},
    )


def _compare(da, tender_id):
    return call_operation("tender_compare", da, {"tender_id": tender_id, "commodity_slug": "rutf"})


def _row(comparison, quote_id):
    return next(r for r in comparison["all_rows"] if r["quote_id"] == quote_id)


def _page(client, tender_id, **params):
    url = reverse("supply_chain:procurement_comparison", args=[tender_id])
    return client.get(url, {"commodity": "rutf", **params}).content.decode()


def _fact(body, key):
    """One fact's column of the comparison grid: its cells, a quote to a row (2026-10-06)."""
    return "".join(re.findall(rf'<td [^>]*data-fact="{key}"[^>]*>.*?</td>', body, re.S))


def _card(body, quote_id):
    """A blocked quote's card on the comparison page."""
    start = body.index(f'<div data-quote-id="{quote_id}"')
    return body[start : body.index("</details>", start)]


# ---- 1. the specification reads what the quote states ---------------------


class TestTheSpecificationReadsWhatTheQuoteStates:
    def test_a_quote_origin_and_a_trade_item_origin_are_told_apart(self):
        commodity = Commodity(slug="rutf", base_unit="sachet", pack_unit="carton", spec_requirements=SPEC)
        stated = Quote(pack_spec_source="stated_on_quote", base_per_pack_stated=150, shelf_life_months_stated=24)
        results = {r.field: r for r in check_compliance(stated, commodity)}
        assert {f: (r.outcome, r.spec_origin) for f, r in results.items()} == {
            "sachets_per_carton": ("pass", "quote"),
            "shelf_life_months": ("pass", "quote"),
        }
        assert results["sachets_per_carton"].message.endswith("(stated on the quote)")

        by_item = Quote(pack_spec_source="trade_item_confirmed")
        item = Item(base_per_pack=150, shelf_life_months=36)
        results = {r.field: r for r in check_compliance(by_item, commodity, item=item)}
        assert {r.spec_origin for r in results.values()} == {"item"}

    def test_a_pack_figure_not_said_to_be_stated_on_the_quote_does_not_count(self):
        commodity = Commodity(slug="rutf", base_unit="sachet", pack_unit="carton", spec_requirements=SPEC[:1])
        (result,) = check_compliance(Quote(pack_spec_source="not_stated", base_per_pack_stated=150), commodity)
        assert result.outcome == "not_stated"

    @pytest.mark.django_db
    def test_stated_on_first_entry_the_offer_meets_the_specification_and_nothing_is_asked(
        self, da, base, client_in_program
    ):
        _with_spec(da)
        quote = _quote_with(
            da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {**_COMPARABLE, "shelf_life_months_stated": 24}
        )
        row = _row(_compare(da, base["tender"]["id"]), quote["id"])

        assert row["specification"]["summary"] == "Meets all 2"
        assert row["specification"]["stated_on_quote"] == ["sachets per carton", "shelf life"]
        assert not [q for q in row["questions"] if q["key"].startswith("spec:") or q["key"] == "shelf_life"]

        body = _page(client_in_program, base["tender"]["id"])
        # On the grid: its landed price, nothing about it shown as a gap, and no verdict chip.
        landed = _fact(body, "landed")
        assert "landed-needs" not in landed and re.search(r"USD [\d,]+\.\d\d", landed)
        assert 'data-testid="grid-status"' not in body
        assert "data-gap" not in _fact(body, "pack")

    @pytest.mark.django_db
    def test_after_a_correction_states_the_pack_its_question_is_gone(self, da, base, ace, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        before = _row(_compare(da, base["tender"]["id"]), quote["id"])
        assert before["specification"]["summary"] == "Not stated: sachets per carton, shelf life"

        corrected = _correct_pack(da, quote, ace)
        after = _row(_compare(da, base["tender"]["id"]), corrected["id"])

        assert after["specification"]["summary"] == "Not stated: shelf life"
        assert after["specification"]["stated_on_quote"] == ["sachets per carton"]
        keys = [q["key"] for q in after["questions"]]
        assert "spec:sachets_per_carton" not in keys and "pack_spec" not in keys
        body = _page(client_in_program, base["tender"]["id"])
        assert "How many sachets" not in body
        assert "sachets per carton of the item" not in body


# ---- 3. a blocked card leads with what blocks it --------------------------


@pytest.mark.django_db
class TestABlockedCardLeadsWithWhatBlocksIt:
    def test_one_blocking_line_and_the_one_question_that_clears_it(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        row = _row(_compare(da, base["tender"]["id"]), quote["id"])

        assert row["blocking"]["fact"] == "Sachets per carton not stated on the quote"
        assert row["blocking"]["question"]["key"] == "pack_spec"

        body = _page(client_in_program, base["tender"]["id"])
        # The grid's pack cell is the gap, its reason on hover; the quote's one action asks for it.
        pack = _fact(body, "pack")
        assert pack.count("data-gap") == 1
        assert 'title="Sachets per carton not stated on the quote">' in pack
        assert 'data-testid="grid-status">sachets per carton · to ask<' in body

    def test_questions_about_one_figure_are_asked_once_as_a_sentence(self, da, base):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        rows = _row(_compare(da, base["tender"]["id"]), quote["id"])["questions"]
        questions = {q["key"]: q["question"] for q in rows}
        requirements = {q["key"]: q["requirement"] for q in rows}

        assert "spec:sachets_per_carton" not in questions
        # Asked neutrally; the requirement is carried beside it (batch 4).
        assert questions["pack_spec"] == "How many sachets are in one carton?"
        assert requirements["pack_spec"] == "exactly 150"
        assert "shelf_life" not in questions
        assert questions["spec:shelf_life_months"].startswith("What is the shelf life from the date of manufacture")
        assert requirements["spec:shelf_life_months"] == "at least 24 months"
        assert not any("of the item you would supply" in q for q in questions.values())

    def test_a_spec_question_nothing_else_asks_reads_as_a_sentence(self, da, base):
        op(
            da,
            "commodity_upsert",
            AUG_3,
            data={
                "slug": "rutf",
                "name": "RUTF",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "spec_requirements": [{"field": "moisture_pct", "operator": "<=", "value": 2.5, "unit": "%"}],
            },
        )
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        (fact,) = (
            q
            for q in _row(_compare(da, base["tender"]["id"]), quote["id"])["questions"]
            if q["key"] == "spec:moisture_pct"
        )
        assert fact["question"] == "What is the moisture of what you would supply?"
        assert fact["requirement"] == "no more than 2.5 %"

    def test_quantities_read_with_separators_and_no_storage_decimals(self, da, base, client_in_program):
        Tender.objects.filter(pk=base["tender"]["id"]).update(
            lines=[{"commodity_slug": "rutf", "quantity": "2400.0000", "quantity_unit": "carton"}]
        )
        quote = _quote_with(
            da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {**_COMPARABLE, "quantity_basis": "1200"}
        )
        assert quote["id"]
        body = _page(client_in_program, base["tender"]["id"])
        assert ">Ask for quantity<" in body
        assert "2400.0000" not in body and "1200.0000" not in body


# ---- 4. why a corrected offer joined the ranking --------------------------


@pytest.mark.django_db
class TestACorrectedOfferSaysWhyItJoined:

    def test_an_offer_entered_complete_carries_no_note(self, da, base, client_in_program):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        assert 'data-testid="correction-note"' not in _page(client_in_program, base["tender"]["id"])


# ---- 8. the source says where it came from --------------------------------


_SHIPMENT_EMAIL = "Your cartons left the plant this morning. Northwind dispatch"


def _order_with_emailed_shipment(da, base, ace, when=AUG_20):
    contract = op(
        da,
        "contract_create",
        AUG_3,
        data={
            "supplier_id": base["supplier"]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": base["us"]["id"],
            "reference": "PO-SOURCE",
            "quantity": "600",
            "quantity_unit": "carton",
            "source": "we_recorded",
        },
    )
    shipment = {
        "contract_id": contract["id"],
        "reference": "SH-SRC",
        "expected_on": "2026-09-05",
        "source": "supplier_reported",
    }
    source = {"ref": "<msg-77@northwind.example>", "excerpt": _SHIPMENT_EMAIL}
    op(da, "shipment_record", when, channel="mcp", actor=ace, source=source, data=shipment)
    return contract, shipment, source


def _event_head(body):
    """The email event's head, as read: the sender, then who recorded it (unanswered round 1004 b3)."""
    head = re.search(r'<li data-testid="email-event".*?</details>', body, re.S).group(0)
    sender = re.search(
        r'data-testid="email-event-head"[^>]*>(.*?)</span>\s*<span data-testid="actor-badge"', head, re.S
    )
    return " ".join(re.sub(r"<[^>]+>", " ", sender.group(1)).split()), head


def _replayed(head):
    found = re.search(r'data-testid="source-replayed"[^>]*>(.*?)</p>', head, re.S)
    return " ".join(found.group(1).split()) if found else ""


@pytest.mark.django_db
class TestTheSourceSaysWhereItCameFrom:
    def test_the_excerpt_is_headed_by_its_kind_who_recorded_it_and_when(self, da, base, ace, client_in_program):
        contract, _shipment, _source = _order_with_emailed_shipment(da, base, ace)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        # The shipment says its supplier reported it, so the email is the supplier's: one
        # email event headed by its sender and the AI pill, its day the day heading.
        sender, head = _event_head(body)
        assert sender == "Northwind Foods"
        assert "data-ai" in head and "AI assistant" in head
        assert 'datetime="2026-08-20"' in body

    def test_the_same_email_again_is_answered_once_and_says_so(self, da, base, ace, client_in_program):
        contract, shipment, source = _order_with_emailed_shipment(da, base, ace)
        replayed = op(da, "shipment_record", AUG_28, channel="mcp", actor=ace, source=source, data=shipment)

        assert replayed["replayed"] is True
        call = OperationCall.objects.get(operation="shipment_record", source_ref=source["ref"])
        assert call.replay_count == 1 and call.last_replayed_at == AUG_28
        url = reverse("supply_chain:order_detail", args=[contract["id"]])
        live = client_in_program.get(url).content.decode()
        assert _replayed(_event_head(live)[1]) == "Forwarded again 28 Aug 2026 — recorded once"
        # Before it arrived again, it had not.
        past = client_in_program.get(url, {"as_of": "2026-08-25"}).content.decode()
        assert _replayed(_event_head(past)[1]) == ""


# ---- 5 and 6. the overview ------------------------------------------------


def _home(client, **params):
    return client.get(reverse("supply_chain:home"), params).content.decode()


@pytest.fixture
def home_client(client_in_program, monkeypatch):
    from connect_labs.supply_chain import views

    monkeypatch.setattr(views, "resolve_org", lambda access: None)
    return client_in_program


def _outreach(da, tender_id, supplier_id, sent_on):
    op(da, "outreach_log", AUG_3, data={"tender_id": tender_id, "supplier_id": supplier_id, "sent_on": sent_on})


def _open(da, tender_id):
    op(da, "tender_open", AUG_3, tender_id=tender_id)


@pytest.mark.django_db
class TestTheOverview:
    def test_the_first_column_is_headed_where_it_can_be_read(self, da, base, home_client):
        body = _home(home_client)
        assert re.search(r'<th scope="col"[^>]*>Procurement</th>', body)
        assert 'sr-only">Procurement' not in body

    def test_the_no_reply_flag_names_the_silent_suppliers(self, da, base, home_client):
        _open(da, base["tender"]["id"])
        _outreach(da, base["tender"]["id"], base["supplier"]["id"], "2026-08-01")

        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 8, 18)) if r.kind == "tender")
        assert [(m.rule, m.text) for m in row.theirs] == [("no reply", "Northwind Foods: reply (silent 17 days)")]
        assert row.theirs[0].detail.startswith("asked 1 Aug")

    def test_the_last_change_gives_the_day_beside_the_count_from_the_as_of_date(self, da, base, home_client):
        past = _home(home_client, as_of="2026-08-10")
        # Since batch 6 a past date shows the day alone: a count from the chosen
        # day read as a count from today.
        count = re.search(r'data-testid="last-change"[^>]*>(.*?)</span>', past).group(1)
        assert count == "3 Aug 2026"
        assert 'data-testid="last-change-day"' not in past

    def test_a_count_and_its_day(self):
        now = datetime.datetime(2026, 9, 26, 18, tzinfo=datetime.UTC)
        assert days_ago_and_day(datetime.datetime(2026, 9, 26, 9, tzinfo=datetime.UTC), now) == "today · 26 Sep"
        assert days_ago_and_day(datetime.datetime(2026, 9, 19, 9, tzinfo=datetime.UTC), now) == "7 days ago · 19 Sep"
        # Past a month the count is already the day: said once.
        assert days_ago_and_day(datetime.datetime(2026, 7, 1, 9, tzinfo=datetime.UTC), now) == "1 Jul 2026"

    def test_a_complete_award_has_no_why_and_reads_awarded(self, da, base, home_client):
        chosen = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        op(da, "award_create", AUG_28, tender_id=base["tender"]["id"], quote_id=chosen["id"], rationale="cheapest")
        body = _home(home_client)
        assert 'data-testid="award-why"' not in body
        assert "awarded, provisional" not in body

    def test_on_a_past_date_the_checks_are_not_shown_as_if_they_were_then(self, da, base, home_client):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})  # a check: not comparable
        live = _home(home_client)
        past = _home(home_client, as_of="2026-08-25")

        assert 'data-testid="checks-as-of-note"' not in live
        assert "Checks are worked out for today; back to today to see them." in past


# ---- 7. the public listing, seen by its own program -----------------------


MARKET_PROGRAM = 10777


def _market_op(name, **payload):
    return call_operation(name, SupplyDataAccess(program_id=MARKET_PROGRAM, caller=SYSTEM), payload)


@pytest.fixture
def listed_tender(db):
    _market_op("commodity_upsert", data={"slug": "rutf", "name": "RUTF", "category": "therapeutic_food"})
    made = _market_op(
        "tender_create",
        data={
            "label": "Lakeside RUTF tender",
            "delivery_point": {"name": "Central store", "city": "Lakeside"},
            "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
        },
    )
    _market_op("tender_open", tender_id=made["id"])
    return Tender.objects.get(pk=made["id"])


def _sign_in(client, user, programs):
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {"organization_data": {"programs": programs}}
    session.save()
    return client


@pytest.mark.django_db
class TestThePublicListing:
    def test_no_deadline_reads_as_none(self, client, listed_tender):
        body = client.get(reverse("supply_chain:market_tender", args=[listed_tender.pk])).content.decode()
        # "Open for bids", captioned "no deadline set"; never a bare dash.
        assert "Open for bids" in body and "NO DEADLINE SET" in body and ">—<" not in body

    def test_the_owning_program_is_told_this_is_the_public_view(self, client, django_user_model, listed_tender):
        user = django_user_model.objects.create_user(username="sophie2", password="x", email="s2@example.org")
        _sign_in(client, user, [{"id": MARKET_PROGRAM, "name": "Lakeside RUTF"}])
        body = client.get(reverse("supply_chain:market_tender", args=[listed_tender.pk])).content.decode()

        note = re.search(r'data-testid="public-listing-note".*?</div>', body, re.S).group(0)
        assert "This is the public listing, as every supplier sees it." in note
        assert "Quotes, sources and history stay in your program." in note
        assert reverse("supply_chain:procurement_tender_detail", args=[listed_tender.pk]) in note

    def test_a_supplier_is_not_told_anything_about_a_program(self, client, django_user_model, listed_tender):
        user = django_user_model.objects.create_user(username="plateau", password="x", email="p@example.org")
        _sign_in(client, user, [{"id": MARKET_PROGRAM + 1, "name": "Someone else's"}])
        body = client.get(reverse("supply_chain:market_tender", args=[listed_tender.pk])).content.decode()
        assert 'data-testid="public-listing-note"' not in body
        assert reverse("supply_chain:procurement_tender_detail", args=[listed_tender.pk]) not in body
