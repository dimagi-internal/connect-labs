"""A delivery point reads as a place, whatever parts of it were filled in.

THIS REPOSITORY IS PUBLIC. Every place name here is invented.

Found on the supplier marketplace, which is the one supply page people
outside the programme read: a round asking for goods in Kano rendered
"Deliver to (not stated), Kano". The name was not stated, the city was, and
three templates each hand-rolled the same join and got it wrong in their own
way -- one printed a leading comma, one printed "(not stated)" beside a city
it did know, one dropped the country.

The rule is one sentence: say the parts you have, in order, separated by
commas, and say "not stated" only when you have none of them.
"""

import pytest

from connect_labs.supply_chain.templatetags.supply_chain_extras import place_text

pytestmark = pytest.mark.django_db


class TestWhatIsKnownIsSaid:
    def test_every_part_present_reads_as_an_address(self):
        assert (
            place_text({"name": "A placeholder depot", "city": "A placeholder city", "country_name": "Placeholderia"})
            == "A placeholder depot, A placeholder city, Placeholderia"
        )

    def test_a_city_with_no_name_does_not_lead_with_a_comma(self):
        """The defect this was written for."""
        assert place_text({"city": "A placeholder city"}) == "A placeholder city"

    def test_a_name_with_no_city_stands_alone(self):
        assert place_text({"name": "A placeholder depot"}) == "A placeholder depot"

    def test_the_two_letter_country_is_used_only_when_the_name_is_missing(self):
        """A reader wants "Placeholderia", not "PL" -- but "PL" beats nothing."""
        assert place_text({"city": "A placeholder city", "country": "PL"}) == "A placeholder city, PL"
        assert (
            place_text({"city": "A placeholder city", "country": "PL", "country_name": "Placeholderia"})
            == "A placeholder city, Placeholderia"
        )


class TestWhatIsNotKnownIsNotInvented:
    def test_an_empty_point_says_so_rather_than_rendering_blank(self):
        """A blank cell is indistinguishable from a rendering failure."""
        assert place_text({}) == "not stated"
        assert place_text(None) == "not stated"

    def test_blank_strings_count_as_absent(self):
        """A stored "" is how a form records a field somebody left alone."""
        assert place_text({"name": "", "city": "  ", "country_name": ""}) == "not stated"
