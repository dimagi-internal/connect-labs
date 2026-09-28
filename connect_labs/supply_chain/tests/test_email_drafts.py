"""The emails Sophie sends, as the product drafts them.

Sophie is the only person inside /supply/ for her procurement; suppliers never
log in, and every message goes from her own mailbox. Rehearsing her week found
that a draft lacked what any real email has -- a subject, a reply-by date, a
greeting to a person, a signature -- and that there was no draft at all for
the most common message of a round: the polite chase to a supplier who has
not answered.

These tests build unsaved records (see conftest.py): the renderer only reads.
Every supplier, person and price here is invented.
"""

from datetime import date
from decimal import Decimal

from connect_labs.supply_chain.models import Supplier, Tender
from connect_labs.supply_chain.procurement.services.render import (
    Sender,
    render_followup,
    render_initial_request,
    render_reminder,
)
from connect_labs.supply_chain.tests.conftest import quote, wrap

SOPHIE = Sender(name="Sophie Example", organisation="Example Relief")


def northwind(**overrides):
    data = {"name": "Northwind Nutrition", "country": "KE"}
    data.update(overrides)
    return wrap(Supplier, data)


def with_contact():
    return northwind(contacts=[{"name": "Ada Bello", "email": "ada@northwind.example.invalid"}])


def tender(**overrides):
    fields = dict(
        id=1,
        program_id=10501,
        label="Tender 2",
        status="open",
        lines=[{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        delivery_point={
            "name": "Central store",
            "city": "Kano",
            "country": "NG",
            "country_name": "Nigeria",
            "incoterm_requested": "DDP",
        },
        shelf_life_months_minimum=18,
    )
    fields.update(overrides)
    return Tender(**fields)


def _last_lines(text, count):
    return [line for line in text.splitlines() if line.strip()][-count:]


# ---- every draft is an email --------------------------------------------


class TestTheRequest:
    def test_it_has_a_subject_naming_quantity_product_place_and_tender(self, rutf):
        draft = render_initial_request(rutf, tender(), northwind(), sender=SOPHIE)
        assert draft.subject == (
            "Quotation request: 2,000 cartons of Ready-to-use therapeutic food, delivered Kano — Tender 2"
        )

    def test_a_tender_that_only_collects_says_so_in_the_subject(self, rutf):
        draft = render_initial_request(
            rutf, tender(delivery_point=None, pickup_accepted=True), northwind(), sender=SOPHIE
        )
        assert "for collection" in draft.subject

    def test_it_greets_the_named_contact(self, rutf):
        draft = render_initial_request(rutf, tender(), with_contact(), sender=SOPHIE)
        assert draft.text.startswith("Dear Ada Bello,")
        assert draft.to == "ada@northwind.example.invalid"

    def test_with_no_named_contact_it_greets_the_organisation(self, rutf):
        draft = render_initial_request(rutf, tender(), northwind(contacts=[{"email": "x@example.invalid"}]))
        assert draft.text.startswith("Dear Northwind Nutrition,")
        assert draft.to == "x@example.invalid"

    def test_it_signs_off_with_the_senders_name_and_organisation(self, rutf):
        draft = render_initial_request(rutf, tender(), northwind(), sender=SOPHIE)
        assert _last_lines(draft.text, 3) == ["With thanks,", "Sophie Example", "Example Relief"]

    def test_an_unknown_sender_leaves_a_bracketed_placeholder_not_a_bare_sign_off(self, rutf):
        draft = render_initial_request(rutf, tender(), northwind())
        assert _last_lines(draft.text, 3) == ["With thanks,", "[your name]", "[your organisation]"]
        assert not draft.text.rstrip().endswith("With thanks,")

    def test_it_asks_for_a_reply_by_the_deadline_as_a_person_writes_a_date(self, rutf):
        draft = render_initial_request(
            rutf, tender(response_deadline=date(2026, 10, 9)), northwind(), today=date(2026, 9, 28)
        )
        assert "Please reply by 9 Oct 2026." in draft.text
        assert "2026-10-09" not in draft.text

    def test_with_no_deadline_it_never_invents_one(self, rutf):
        draft = render_initial_request(rutf, tender(), northwind())
        assert "reply by" not in draft.text.lower()

    def test_a_buyers_note_that_restates_the_questions_is_not_repeated(self, rutf):
        note = "Please state the price per carton, the shelf life and your minimum order quantity."
        draft = render_initial_request(rutf, tender(notes_to_supplier=note), northwind())
        assert note not in draft.text
        assert "minimum order quantity" in draft.text  # still asked, once, as a question

    def test_a_buyers_note_that_says_something_new_is_kept(self, rutf):
        note = (
            "We are a small programme buying for a pilot, so our volumes are below a typical "
            "institutional contract. Please state the price per carton."
        )
        draft = render_initial_request(rutf, tender(notes_to_supplier=note), northwind())
        assert "We are a small programme buying for a pilot" in draft.text
        # The sentence that only restated a question went; the one that did not stayed.
        assert "Please state the price per carton." not in draft.text


class TestTheFollowUp:
    def _open_quote(self, **overrides):
        fields = dict(
            pack_spec_source="not_stated",
            base_per_pack_stated=None,
            shelf_life_months_stated=24,
            received_on=date(2026, 9, 10),
        )
        fields.update(overrides)
        return quote(**fields)

    def test_it_names_the_quote_it_is_about(self, rutf):
        draft = render_followup(self._open_quote(), rutf, tender(), northwind(), sender=SOPHIE)
        assert "your quotation of 10 Sep 2026, USD 50.00 per carton" in draft.text
        assert draft.subject == "Follow-up on your quotation of 10 Sep 2026 — Tender 2"

    def test_a_quote_with_no_received_date_is_named_by_its_price_alone(self, rutf):
        draft = render_followup(self._open_quote(received_on=None), rutf, tender(), northwind())
        assert "your quotation, USD 50.00 per carton" in draft.text
        assert draft.subject == "Follow-up on your quotation — Tender 2"

    def test_it_is_signed(self, rutf):
        draft = render_followup(self._open_quote(), rutf, tender(), northwind(), sender=SOPHIE)
        assert _last_lines(draft.text, 3) == ["With thanks,", "Sophie Example", "Example Relief"]

    def test_it_carries_the_reply_by_date_when_there_is_one(self, rutf):
        draft = render_followup(
            self._open_quote(),
            rutf,
            tender(response_deadline=date(2026, 10, 9)),
            northwind(),
            today=date(2026, 9, 28),
        )
        assert "Please reply by 9 Oct 2026." in draft.text

    def test_a_per_sachet_price_is_said_per_sachet(self, rutf):
        q = self._open_quote(as_quoted_unit="per_base_unit", as_quoted_amount=Decimal("0.35"))
        draft = render_followup(q, rutf, tender(), northwind())
        assert "USD 0.35 per sachet" in draft.text


class TestTheReminder:
    def test_it_names_the_date_of_the_request_and_what_was_asked(self, rutf):
        draft = render_reminder(
            rutf, tender(), with_contact(), sent_on=date(2026, 9, 9), sender=SOPHIE, today=date(2026, 9, 28)
        )
        assert draft.subject == "Reminder: quotation request of 9 Sep 2026 — Tender 2"
        assert draft.text.startswith("Dear Ada Bello,")
        assert "On 9 Sep 2026 we asked for a quotation for 2,000 cartons of Ready-to-use therapeutic food" in (
            draft.text
        )
        assert "Central store, Kano, Nigeria" in draft.text
        # The questions, in case the original went astray.
        assert "What is your minimum order quantity?" in draft.text
        assert _last_lines(draft.text, 3) == ["With thanks,", "Sophie Example", "Example Relief"]

    def test_a_second_reminder_says_when_we_last_wrote(self, rutf):
        draft = render_reminder(
            rutf,
            tender(),
            northwind(),
            sent_on=date(2026, 9, 9),
            last_reminder_on=date(2026, 9, 16),
            today=date(2026, 9, 28),
        )
        assert "We last wrote about this on 16 Sep 2026." in draft.text

    def test_a_deadline_still_ahead_is_the_reply_by_date(self, rutf):
        draft = render_reminder(
            rutf,
            tender(response_deadline=date(2026, 10, 9)),
            northwind(),
            sent_on=date(2026, 9, 9),
            today=date(2026, 9, 28),
        )
        assert "Please reply by 9 Oct 2026." in draft.text

    def test_a_deadline_already_past_is_said_honestly_not_moved(self, rutf):
        draft = render_reminder(
            rutf,
            tender(response_deadline=date(2026, 9, 20)),
            northwind(),
            sent_on=date(2026, 9, 9),
            today=date(2026, 9, 28),
        )
        assert "We asked for replies by 20 Sep 2026" in draft.text
        assert "Please reply by" not in draft.text

    def test_with_no_deadline_it_never_invents_one(self, rutf):
        draft = render_reminder(rutf, tender(), northwind(), sent_on=date(2026, 9, 9), today=date(2026, 9, 28))
        assert "reply by" not in draft.text.lower()
