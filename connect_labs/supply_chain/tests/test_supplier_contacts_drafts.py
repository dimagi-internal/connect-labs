"""Who a drafted email goes to, and how it greets them (Sophie's RUTF feedback, Sourcing).

THIS REPOSITORY IS PUBLIC. Every name and address here is invented.
"""

from datetime import date

from connect_labs.supply_chain.models import Supplier
from connect_labs.supply_chain.procurement.operations import (
    _noted_not_contacted,
    stale_after_days,
)
from connect_labs.supply_chain.procurement.services.contacts import clean_email, recipients, split_names
from connect_labs.supply_chain.procurement.services.render import (
    render_initial_request,
    render_reminder,
)
from connect_labs.supply_chain.tests.conftest import wrap
from connect_labs.supply_chain.tests.test_email_drafts import SOPHIE, tender


def supplier(contacts, **extra):
    return wrap(Supplier, {"name": "Dabs Foods", "country": "NG", "contacts": contacts, **extra})


class TestCleanAddresses:
    def test_a_name_in_brackets_is_not_part_of_the_address(self):
        assert clean_email("Jane Doe <Jane@Dabs.example.invalid>") == "jane@dabs.example.invalid"
        assert clean_email("jane@dabs.example.invalid (Jane)") == "jane@dabs.example.invalid"

    def test_a_note_inside_the_cell_is_dropped(self):
        assert clean_email("jane@dabs.example.invalid - bounced in June") == "jane@dabs.example.invalid"
        assert clean_email("no reply, ask by phone") == ""

    def test_the_draft_goes_to_the_clean_address(self, rutf):
        s = supplier([{"name": "Jane Doe", "email": "Jane Doe <jane@dabs.example.invalid>"}])
        assert render_initial_request(rutf, tender(), s, sender=SOPHIE).to == "jane@dabs.example.invalid"


class TestGreeting:
    def test_five_names_in_one_cell_are_not_strung_together(self, rutf):
        names = "Ann Ade / Bo Bello; Cy Chi; Di Dada; Ed Eze"
        s = supplier([{"name": names, "role": "sales", "note": "names not matched to addresses"}])
        text = render_initial_request(rutf, tender(), s, sender=SOPHIE).text
        assert text.startswith("Dear Dabs Foods team,")
        assert ";" not in text.splitlines()[0] and "/" not in text.splitlines()[0]

    def test_two_names_are_joined_in_words(self, rutf):
        s = supplier([{"name": "Ann Ade / Bo Bello"}])
        assert render_initial_request(rutf, tender(), s, sender=SOPHIE).text.startswith("Dear Ann Ade and Bo Bello,")

    def test_a_person_with_their_own_address_is_preferred(self):
        s = supplier(
            [
                {"name": "Ann Ade; Bo Bello"},
                {"name": "Cy Chi", "email": "cy@dabs.example.invalid"},
            ]
        )
        assert recipients(s).names == ["Cy Chi"]

    def test_split_names(self):
        assert split_names("A B / C D; E F") == ["A B", "C D", "E F"]


class TestToAndCc:
    CONTACTS = [
        {"name": "Old Contact", "email": "old@dabs.example.invalid", "use": "history"},
        {"name": "Cy Chi", "email": "cy@dabs.example.invalid", "use": "cc"},
        {"name": "Ann Ade", "email": "ann@dabs.example.invalid", "use": "to"},
    ]

    def test_one_main_contact_and_the_people_copied(self, rutf):
        draft = render_initial_request(rutf, tender(), supplier(self.CONTACTS), sender=SOPHIE)
        assert draft.to == "ann@dabs.example.invalid"
        assert draft.cc == ("cy@dabs.example.invalid",)
        assert draft.text.startswith("Dear Ann Ade,")
        assert draft.as_dict()["cc"] == ["cy@dabs.example.invalid"]

    def test_an_old_address_is_kept_on_file_but_never_used(self):
        got = recipients(supplier(self.CONTACTS))
        assert "old@dabs.example.invalid" not in [got.to, *got.cc]

    def test_without_markers_the_first_address_is_the_main_one_and_nobody_is_copied(self):
        s = supplier([{"name": "Ann Ade", "email": "ann@dabs.example.invalid"}, {"email": "b@dabs.example.invalid"}])
        got = recipients(s)
        assert got.to == "ann@dabs.example.invalid" and got.cc == []


class TestStaleSilence:
    def test_a_young_reminder_is_the_usual_one(self, rutf):
        draft = render_reminder(rutf, tender(), supplier([]), sent_on=date(2026, 9, 1), sender=SOPHIE)
        assert draft.subject.startswith("Reminder:")

    def test_long_silence_asks_whether_they_still_wish_to_quote(self, rutf):
        draft = render_reminder(rutf, tender(), supplier([]), sent_on=date(2026, 9, 1), stale_days=54, sender=SOPHIE)
        assert "54 days" in draft.text
        assert "whether you still wish to quote" in draft.text
        assert not draft.subject.startswith("Reminder:")

    def test_what_counts_as_long_scales_with_the_interval(self):
        assert stale_after_days(7) == 28
        assert stale_after_days(14) == 56


class FakeRow:
    def __init__(self, notes=""):
        self.notes = notes


def test_a_supplier_noted_not_yet_contacted_is_not_chased():
    assert _noted_not_contacted(supplier([], notes="Not yet contacted"), [FakeRow()])
    assert _noted_not_contacted(supplier([]), [FakeRow("not yet contacted - waiting for approval")])
    assert not _noted_not_contacted(supplier([], notes="Called twice"), [FakeRow()])
