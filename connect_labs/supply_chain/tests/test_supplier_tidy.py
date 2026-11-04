"""Tidying imported suppliers (Sophie's RUTF feedback, Suppliers: imported data is messy).

Invented names and addresses only (public repo).
"""

from io import StringIO

import pytest
from django.core.management import call_command

from connect_labs.supply_chain.reference.tidy import tidy_city, tidy_contact, tidy_supplier
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import op

da = chlorine_world.da


class TestCity:
    def test_a_country_in_city_moves_to_country(self):
        assert tidy_city("Nigeria", "")[0] == {"city": "", "country": "NG"}
        assert tidy_city("nigeria", "NG")[0] == {"city": "", "country": "NG"}

    def test_a_city_with_its_country_keeps_the_city(self):
        assert tidy_city("Kano, Nigeria", "")[0] == {"city": "Kano", "country": "NG"}

    def test_a_real_city_is_untouched(self):
        assert tidy_city("Kano", "NG") == ({}, [], [])
        assert tidy_city("", "") == ({}, [], [])

    def test_a_different_country_is_reported_not_overwritten(self):
        patch, changes, left = tidy_city("Nigeria", "FR")
        assert patch == {} and changes == [] and "FR" in left[0]


class TestContacts:
    def test_a_name_in_brackets_becomes_the_name(self):
        fixed, changes, _ = tidy_contact({"email": "Jane Doe <Jane@Dabs.example.invalid>"})
        assert fixed == {"email": "jane@dabs.example.invalid", "name": "Jane Doe"}
        assert changes

    def test_a_note_in_the_cell_moves_to_the_note(self):
        fixed, _, _ = tidy_contact({"name": "Jane", "email": "jane@dabs.example.invalid - bounced in June"})
        assert fixed == {"name": "Jane", "email": "jane@dabs.example.invalid", "note": "bounced in June"}

    def test_an_existing_note_is_kept(self):
        fixed, _, _ = tidy_contact({"email": "jane@dabs.example.invalid (left the company)", "note": "sales"})
        assert fixed["note"] == "sales · left the company"

    def test_no_address_or_several_are_left_alone(self):
        for cell in ("ask by phone", "a@dabs.example.invalid; b@dabs.example.invalid"):
            contact = {"name": "X", "email": cell}
            fixed, changes, left = tidy_contact(contact)
            assert fixed == contact and changes == [] and left

    def test_a_clean_contact_is_untouched(self):
        contact = {"name": "Jane", "email": "jane@dabs.example.invalid", "use": "to"}
        assert tidy_contact(contact) == (contact, [], [])

    def test_running_twice_changes_nothing(self):
        once = tidy_supplier("Nigeria", "", [{"email": "Jane <j@dabs.example.invalid>"}])
        again = tidy_supplier(once.data["city"], once.data["country"], once.data["contacts"])
        assert not again.any


@pytest.mark.django_db
class TestCommand:
    def _messy(self, da):
        return op(
            da,
            "supplier_create",
            data={
                "name": "Dabs Foods",
                "city": "Nigeria",
                "contacts": [{"email": "Jane Doe <jane@dabs.example.invalid>"}],
            },
        )

    def test_it_shows_by_default_and_changes_nothing(self, da):
        supplier = self._messy(da)
        out = StringIO()
        call_command("supply_tidy_suppliers", program_id=chlorine_world.PROGRAM, stdout=out)
        assert "would change" in out.getvalue() and "--apply" in out.getvalue()
        assert op(da, "supplier_get", supplier_id=supplier["id"])["city"] == "Nigeria"

    def test_apply_writes_through_the_operation(self, da):
        supplier = self._messy(da)
        call_command("supply_tidy_suppliers", program_id=chlorine_world.PROGRAM, apply=True, stdout=StringIO())
        after = op(da, "supplier_get", supplier_id=supplier["id"])
        assert after["city"] == "" and after["country"] == "NG"
        assert after["contacts"] == [{"email": "jane@dabs.example.invalid", "name": "Jane Doe"}]
