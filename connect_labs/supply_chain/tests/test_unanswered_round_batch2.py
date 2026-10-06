"""The unanswered round, judged batch 2: chase from the draft, see what changed, read what we owe.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

Builds on test_tracking_reality's world: one open RUTF tender, Kanem invited,
Northgate a second supplier.
"""

import datetime
import html
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.models import Outreach
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.views import (
    _without_empty_tail,
    award_anyway,
    award_anyway_detail,
    not_stated,
)
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import (
    PROGRAM,
    _contract,
    _held_on_our_form_m,
    _kanem_quote,
    op,
)
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page

# That module's fixtures, shared rather than copied.
da = reality.da
world = reality.world
client_in_program = reality.client_in_program


@pytest.fixture
def web(client_in_program, da, monkeypatch):
    """The same signed-in client, able to submit the write screens too."""
    from connect_labs.supply_chain import form_views

    monkeypatch.setattr(form_views, "_access", lambda request: da)
    monkeypatch.setattr(form_views, "has_program_context", lambda request: True)
    return client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _comparable(world, **overrides):
    """Kanem's quote with everything the comparison needs stated."""
    return _kanem_quote(
        world,
        duties_basis="included",
        pack_spec_source="stated_on_quote",
        base_per_pack_stated=150,
        base_unit_grams_stated=92,
        quantity_basis=2000,
        quantity_basis_unit="carton",
        **overrides,
    )


def _answer_as_sophie(da, commitment_id, resolution):
    """Resolved on the web by Sophie (the page fixture's user), as the screen would."""
    from django.contrib.auth import get_user_model

    sophie = get_user_model().objects.get(username="sophie-reh")
    with seed_overrides(PROGRAM, actor=sophie, recorded_at=datetime.datetime(2026, 10, 2, 9, tzinfo=datetime.UTC)):
        call_operation(
            "commitment_resolve",
            da,
            {"commitment_id": commitment_id, "resolution": resolution, "resolved_on": "2026-10-02"},
            channel="web",
        )


def _question(da, world, text="Who is the importer of record?"):
    return op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": world["northgate"]["id"],
            "tender_id": world["tender"]["id"],
            "text": text,
            "raised_on": "2026-07-11",
            "source": "supplier_reported",
        },
    )


# ---- 1. chase from the reminder draft


class TestChaseFromTheDraft:
    def test_the_reminder_draft_carries_a_chase_form(self, da, world, web):
        body = _tender_page(web, world["tender"]["id"])
        drafts = body.split('id="drafts"', 1)[1]
        form = re.search(r'<form [^>]*data-testid="chase-form".*?</form>', drafts, re.S).group(0)
        url = reverse("supply_chain:procurement_outreach_chase", args=[world["outreach"]["id"]])
        assert f'action="{url}"' in form
        today = f"{datetime.date.today():%-d %b %Y}"
        assert re.search(rf'data-testid="chase-date" type="text" name="last_reminder_on"\s+value="{today}"', form)
        assert 'data-testid="mark-sent"' in form and ">Mark sent</button>" in form

    def test_recording_a_chase_sets_only_the_day_and_returns_to_the_row(self, da, world, web):
        outreach_id = world["outreach"]["id"]
        op(
            da,
            "outreach_update",
            outreach_id=outreach_id,
            data={"responded": True, "response_kind": "needs_info", "responded_on": "2026-07-09"},
        )
        response = web.post(
            reverse("supply_chain:procurement_outreach_chase", args=[outreach_id]), {"last_reminder_on": "2026-07-14"}
        )
        tender_url = reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        assert response.url == f"{tender_url}?changed=outreach-{outreach_id}&cell=last_chased#outreach-{outreach_id}"
        row = Outreach.objects.get(pk=outreach_id)
        assert row.last_reminder_on == datetime.date(2026, 7, 14)
        # The reply it held is untouched: the chase form posts the date alone.
        assert row.responded and row.response_kind == "needs_info"


# ---- 2. the changed row reads on a projector


class TestTheChangedRow:
    def test_the_saved_outreach_row_has_an_accent_bar_and_says_so(self, da, world, client_in_program):
        outreach_id = world["outreach"]["id"]
        body = _tender_page(client_in_program, world["tender"]["id"], f"?changed=outreach-{outreach_id}")
        row = re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', body, re.S).group(0)
        assert "border-l-4" in row and 'data-testid="changed-chip"' in row
        # The marker sits in the cell that changed (DDD 002 batch 1): a reply moves "Replied",
        # not beside the supplier's name.
        # The day a reply came in has its own Replied cell again (2026-10-05): the marker goes there.
        replied = re.search(r'<td [^>]*data-testid="replied-on"[^>]*>.*?</td>', row, re.S).group(0)
        assert 'data-testid="changed-chip"' in replied
        chased = _tender_page(
            client_in_program, world["tender"]["id"], f"?changed=outreach-{outreach_id}&cell=last_chased"
        )
        row = re.search(rf'<tr data-outreach-id="{outreach_id}".*?</tr>', chased, re.S).group(0)
        cell = re.search(r'<td [^>]*data-testid="supplier-chased"[^>]*>.*?</td>', row, re.S).group(0)
        assert 'data-testid="changed-chip"' in cell and "font-semibold" in cell
        assert row.count('data-testid="changed-chip"') == 1
        plain = _tender_page(client_in_program, world["tender"]["id"])
        assert 'data-testid="changed-chip"' not in plain

    def test_the_resolved_owed_row_has_them_too(self, da, world, client_in_program):
        asked = _question(da, world)
        op(da, "commitment_resolve", channel="web", commitment_id=asked["id"], resolution="We are.")
        body = _tender_page(client_in_program, world["tender"]["id"], f"?changed=commitment-{asked['id']}")
        # One pill on an answered row since batch 4: its "Answered" chip says "just now".
        row = re.search(rf'<div [^>]*data-commitment-id="{asked["id"]}".*?\(just now\)', body, re.S)
        assert row is not None and "border-l-4" in row.group(0) and "updated just now" not in row.group(0)


# ---- 3. what we owe: open first, the answered apart, in the history


class TestWhatWeOweReads:
    def test_open_first_answered_apart_and_the_heading_counts_the_open(self, da, world, web):
        answered = _question(da, world, "One warehouse, or several?")
        _question(da, world, "Who is the importer of record?")
        _answer_as_sophie(da, answered["id"], "One warehouse in Kano.")
        body = _tender_page(web, world["tender"]["id"])
        assert (
            _text(re.search(r'<summary id="owed".*?</summary>', body, re.S).group(0))
            == "Questions and promises 1 open"
        )
        open_list = re.search(
            r'<div [^>]*data-testid="owed">.*?</div>\s*</div>\s*(?=<details|<div class="mb)', body, re.S
        )
        assert "Who is the importer of record?" in open_list.group(0)
        assert "One warehouse, or several?" not in open_list.group(0)
        group = re.search(r'<details data-testid="owed-answered"[^>]*>.*?</details>', body, re.S).group(0)
        assert " open>" not in group.split(">", 1)[0] + ">"  # shut unless it holds the row just saved
        assert "Answered by Sophie · 2 Oct 2026: One warehouse in Kano." in _text(group)
        assert 'data-testid="owed-status"' in group

        arrived = _tender_page(web, world["tender"]["id"], f"?changed=commitment-{answered['id']}")
        opening = re.search(r'<details data-testid="owed-answered"[^>]*>', arrived).group(0)
        assert opening.endswith(" open>")

    def test_answering_is_in_the_tenders_history(self, da, world, web):
        asked = _question(da, world)
        _answer_as_sophie(da, asked["id"], "We are the importer.")
        history = _text(_tender_page(web, world["tender"]["id"]).split('data-testid="timeline"', 1)[1])
        assert "To do · Northgate Rehearsal Commodities · answered: We are the importer. Sophie" in history
        assert (
            "To do · Northgate Rehearsal Commodities · recorded: they asked: Who is the importer of record?" in history
        )


# ---- 4. the comparison


class TestTheComparison:
    def test_one_offer_is_not_numbered_or_ranked(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        # One offer is one column of facts with its status, and no rank.
        assert body.count('data-testid="grid-quote"') == 1
        assert 'data-testid="grid-status">Comparable<' in body
        assert 'data-testid="ranked-table"' not in body

    def test_award_anyway_names_who_is_still_missing_what(self):
        comparison = {
            "blocked": [
                {
                    "supplier_name": "Northgate",
                    "blockers": [{"fact": "Pack not stated", "label": "sachets per carton"}],
                },
                {"supplier_name": "Sahel", "blockers": [{"fact": "ETA", "label": "ETA"}]},
                {"supplier_name": "Lakeside", "blockers": []},
            ]
        }
        # Since the 002 run's batch 1: the button names its own award and why it is
        # early; the other suppliers' gaps are its tooltip, never its label.
        assert award_anyway(comparison) == "2 other quotes can't be compared yet"
        assert award_anyway_detail(comparison) == (
            "Northgate has not stated sachets per carton. Sahel has not stated ETA"
        )
        assert award_anyway({"blocked": []}) == ""

    def test_a_gap_the_buyer_records_is_not_blamed_on_the_supplier(self):
        row = {
            "supplier_name": "Sahel",
            "blockers": [
                {"fact": "Quote is in EUR and no exchange rate was recorded", "label": "exchange rate"},
                {"fact": "Freight excluded", "label": "freight amount"},
            ],
        }
        assert not_stated(row) == "Sahel has not stated freight amount; no exchange rate recorded for the EUR quote"
        only_ours = {"supplier_name": "Sahel", "blockers": [row["blockers"][0]]}
        assert not_stated(only_ours) == "Sahel: no exchange rate recorded for the EUR quote"

    def test_the_award_button_steps_down_while_a_quote_is_blocked(self, da, world, client_in_program):
        op(da, "quote_record", data=_comparable(world))
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        clear = client_in_program.get(url).content.decode()
        assert "data-anyway" not in clear
        # Northgate quotes without saying what freight it includes: blocked.
        op(
            da,
            "quote_record",
            data={
                **_comparable(world),
                "supplier_id": world["northgate"]["id"],
                "freight_basis": "not_specified",
                "duties_basis": "not_specified",
            },
        )
        body = client_in_program.get(url).content.decode()
        # The award opens from its fold, whose summary says the others are not comparable yet.
        button = re.search(r'<summary data-testid="award-open"[^>]*>(.*?)</summary>', body, re.S)
        assert button is not None and "data-anyway" in button.group(1)
        label = " ".join(html.unescape(_text(button.group(1))).split())
        assert label.startswith("Award Kanem ") and label.endswith("· 1 other quote not comparable yet")
        assert 'title="Northgate Rehearsal Commodities has not stated ' in button.group(1)

    def test_an_empty_trailing_column_is_dropped_but_not_one_between_figures(self):
        rows = [{"figures": {"a": {"amount": "1"}, "b": {"amount": None}, "c": {"amount": "2"}, "d": {}}}]
        columns = [{"key": k} for k in "abcd"]
        assert [c["key"] for c in _without_empty_tail(rows, columns)] == ["a", "b", "c"]


# ---- 5. the history: a label, a toggle, bookkeeping set back


class TestTheHistoryLine:
    def test_the_source_has_its_own_toggle_and_the_badge_is_a_label(self, da, world, client_in_program):
        op(
            da,
            "quote_record",
            source={"ref": "<q-1@kanem.example.invalid>", "excerpt": "USD 54.50 per carton, CPT Kano."},
            data=_kanem_quote(world),
        )
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        # Since unanswered round 1004 b3 an AI-recorded email is always an email event: the
        # badge in its head, the toggle its own, the quote a record under it.
        event = timeline.split('data-testid="email-event"', 1)[1]
        assert "Quote" in event and 'data-testid="revision-line"' in event
        assert re.search(r'<span data-testid="actor-badge" data-ai ', event)
        toggle = re.search(r'<summary data-testid="source-toggle".*?</summary>', event, re.S).group(0)
        assert "Source email" in toggle and "actor-badge" not in toggle
        assert 'data-testid="source-excerpt"' in event and 'data-testid="source-heading"' not in event

    def test_an_invitation_sent_and_a_chase_are_set_back(self, da, world, client_in_program):
        with seed_overrides(PROGRAM, recorded_at=datetime.datetime(2026, 10, 1, 10, tzinfo=datetime.UTC)):
            call_operation(
                "outreach_update",
                da,
                {"outreach_id": world["outreach"]["id"], "data": {"last_reminder_on": "2026-10-01"}},
                channel="web",
            )
        op(da, "quote_record", data=_kanem_quote(world))
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        texts = re.findall(r'<div data-testid="revision-text"([^>]*)>(.*?)</div>', timeline, re.S)
        kept = {_text(t): attrs for attrs, t in texts}
        sent = next(attrs for text, attrs in kept.items() if text.startswith("Invitation") or " sent " in text)
        chased = next(attrs for text, attrs in kept.items() if "Last chased" in text)
        quote = next(attrs for text, attrs in kept.items() if text.startswith("Quote"))
        assert "data-bookkeeping" in sent and "text-xs" in sent
        assert "data-bookkeeping" in chased
        assert "data-bookkeeping" not in quote and "text-sm" in quote

    def test_a_voided_ai_quote_says_why_it_offers_no_fix(self, da, world, client_in_program):
        quote = op(da, "quote_record", data=_kanem_quote(world))
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        fixes = re.search(r'data-testid="quote-fixes"[^>]*>(.*?)</div>', timeline, re.S).group(1)
        assert "Correct" in fixes and "Void" in fixes
        op(da, "quote_void", quote_id=quote["id"], reason="sent twice")
        timeline = _tender_page(client_in_program, world["tender"]["id"]).split('data-testid="timeline"', 1)[1]
        fixes = re.findall(r'data-testid="quote-fixes"[^>]*>(.*?)</div>', timeline, re.S)
        assert fixes and all(_text(f) == "voided" for f in fixes)


# ---- 6. the line that put a shipment on hold for us


class TestTheHoldInTheHistory:
    def test_the_line_that_asked_for_our_document_says_we_are_waited_on(self, da, world, client_in_program):
        contract, _ = _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        holds = re.findall(r'data-testid="revision-hold"[^>]*>([^<]*)<', body)
        assert holds == ["Waiting on us: import permit"]

    def test_once_it_is_on_file_the_marker_goes(self, da, world, client_in_program):
        from connect_labs.supply_chain.models import Shipment

        contract, shipment = _held_on_our_form_m(da, world)
        op(
            da,
            "document_attach",
            channel="web",
            data={
                "shipment_id": shipment["id"],
                "kind": "import_permit",
                "title": "Form M",
                "external_url": "https://example.invalid/form-m.pdf",
                "source": "we_recorded",
            },
        )
        assert Shipment.objects.get(pk=shipment["id"]).documents.exists()
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert 'data-testid="revision-hold"' not in body


# ---- 7. the order page's headings and the variance


def test_the_order_sections_share_one_heading_style(da, world, client_in_program):
    contract = _contract(da, world)
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    styles = {}
    for name in ("Shipments", "Received", "Questions and promises", "Invoices", "Evidence", "History"):
        match = re.search(rf'<h[23][^>]*class="([^"]*)"[^>]*>\s*{name}', body)
        assert match, name
        styles[name] = {c for c in match.group(1).split() if c.startswith("text-") or c.startswith("font-")}
    assert len({frozenset(s) for s in styles.values()}) == 1, styles


# ---- 8. the overview: a document we owe makes the order's move ours


def test_a_held_document_makes_the_orders_move_ours(da, world, client_in_program):
    _held_on_our_form_m(da, world)
    body = client_in_program.get(reverse("supply_chain:home")).content.decode()
    assert 'data-testid="whose-us" data-count="1">To do 1<' in body
    assert "Provide 1 document to " in body


# ---- 9. the tender page header


def test_the_compare_button_says_compare_quotes(da, world, client_in_program):
    body = _tender_page(client_in_program, world["tender"]["id"])
    assert re.search(r">\s*Compare quotes\s*</a>", body)
