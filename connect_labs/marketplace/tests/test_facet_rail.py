"""The filter rail: every value's size before you click, and facets that combine.

All data invented. The point of a rail over a dropdown is that you can see how
big a filter is without opening it, and that picking two values widens the
answer rather than replacing it.
"""
import pytest
from django.http import QueryDict
from django.urls import reverse

from connect_labs.marketplace import queries
from connect_labs.marketplace.testing import make_partner
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


@pytest.fixture
def user(db, django_user_model):
    return django_user_model.objects.create_user(username="staff", password="x")


@pytest.fixture
def network(db):
    ug = make_partner("Northlake Maternal Health Network", "NMHN", countries=["Uganda"], sectors=["Health"])
    mw = make_partner("Serrano Child Nutrition Foundation", "SCNF", countries=["Malawi"], sectors=["Nutrition"])
    ke = make_partner("Fenwick Community Trust", "FCT", countries=["Kenya"], sectors=["Health", "WASH"])

    chc = Solicitation.objects.create(slug="chc-2025", title="CHC", status="closed", sa_access_state="ok")
    for i, org in enumerate([ug, ke], start=2):
        SolicitationResponse.objects.create(
            solicitation=chc, llo_entity=org, source_row=i, org_name=org.name, match_state="name"
        )
    return {"ug": ug, "mw": mw, "ke": ke, "chc": chc}


def _names(response):
    return {row["org"].name for row in response.context["listed"]}


@pytest.mark.django_db
class TestTheRail:
    def test_every_value_carries_its_count(self, client, user, network):
        client.force_login(user)
        rail = {s["param"]: s for s in client.get(reverse("marketplace:network")).context["rail"]}
        countries = {r["label"]: r["count"] for r in rail["country"]["rows"]}
        assert countries == {"Uganda": 1, "Malawi": 1, "Kenya": 1}

    def test_a_round_facet_is_labelled_by_title_not_slug(self, client, user, network):
        client.force_login(user)
        rail = {s["param"]: s for s in client.get(reverse("marketplace:network")).context["rail"]}
        assert rail["applied"]["rows"][0]["label"] == "CHC"
        assert rail["applied"]["rows"][0]["value"] == "chc-2025"

    def test_counts_narrow_with_the_filters_already_applied(self, client, user, network):
        """A facet count answers 'how many would this add to what I have',
        which means counting over the rows in scope, not the whole registry."""
        client.force_login(user)
        rail = {
            s["param"]: s for s in client.get(reverse("marketplace:network"), {"applied": "chc-2025"}).context["rail"]
        }
        countries = {r["label"] for r in rail["country"]["rows"]}
        assert countries == {"Uganda", "Kenya"}


@pytest.mark.django_db
class TestFacetsCombine:
    def test_two_countries_widen_the_answer_rather_than_replacing_it(self, client, user, network):
        """The whole reason a rail beats a dropdown."""
        client.force_login(user)
        response = client.get(reverse("marketplace:network"), {"country": ["Uganda", "Malawi"]})
        assert _names(response) == {"Northlake Maternal Health Network", "Serrano Child Nutrition Foundation"}

    def test_different_facets_narrow_each_other(self, client, user, network):
        client.force_login(user)
        response = client.get(reverse("marketplace:network"), {"country": ["Uganda", "Kenya"], "sector": ["WASH"]})
        assert _names(response) == {"Fenwick Community Trust"}

    def test_a_segment_still_applies_on_top_of_the_facets(self, client, user, network):
        client.force_login(user)
        response = client.get(
            reverse("marketplace:network"), {"country": ["Uganda", "Malawi", "Kenya"], "segment": "nocontact"}
        )
        assert len(response.context["listed"]) == 3


@pytest.mark.django_db
class TestToggleLinks:
    def test_an_unselected_value_links_to_adding_it(self, client, user, network):
        client.force_login(user)
        rail = {s["param"]: s for s in client.get(reverse("marketplace:network")).context["rail"]}
        row = next(r for r in rail["country"]["rows"] if r["label"] == "Uganda")
        assert not row["selected"]
        assert "country=Uganda" in row["url"]

    def test_a_selected_value_links_to_removing_itself(self, client, user, network):
        """A checked box has to uncheck, which a plain link cannot express
        unless the URL is built for it."""
        client.force_login(user)
        rail = {
            s["param"]: s for s in client.get(reverse("marketplace:network"), {"country": "Uganda"}).context["rail"]
        }
        row = next(r for r in rail["country"]["rows"] if r["label"] == "Uganda")
        assert row["selected"]
        assert "country=Uganda" not in row["url"]

    def test_toggling_one_facet_preserves_every_other_control(self, client, user, network):
        """A facet that dropped the search box or the segment when clicked would
        be worse than no facet at all."""
        client.force_login(user)
        response = client.get(
            reverse("marketplace:network"), {"segment": "bench", "sector": "Health", "country": "Kenya"}
        )
        rail = {s["param"]: s for s in response.context["rail"]}
        row = next(r for r in rail["country"]["rows"] if r["label"] == "Uganda")
        assert "segment=bench" in row["url"]
        assert "sector=Health" in row["url"]
        assert "country=Kenya" in row["url"]  # the one already chosen survives

    def test_the_rail_builder_needs_no_request(self, network):
        """Built from a QueryDict so it is testable without a view."""
        facets = queries.facet_counts(queries.all_rows_with_rounds(), set())
        rail = queries.facet_rail(facets, {"countries": [], "sectors": [], "applied": []}, QueryDict(""))
        assert {s["param"] for s in rail} == {"country", "sector", "applied"}


@pytest.mark.django_db
class TestTheGlobeFollowsTheRail:
    def test_the_points_endpoint_honours_multi_select(self, client, user, network):
        client.force_login(user)
        data = client.get(reverse("marketplace:network_points"), {"country": ["Uganda", "Malawi"]}).json()
        assert len(data["points"]) == 0  # none of these fixtures carry coordinates


@pytest.mark.django_db
class TestASecondValueStaysReachable:
    """A facet must be counted with its OWN filter excluded. Otherwise choosing
    Uganda removes Malawi from the country list, and the multi-select the rail
    exists for can never be reached through the UI.
    """

    def test_choosing_one_country_leaves_the_others_offerable(self, client, user, network):
        client.force_login(user)
        rail = {
            s["param"]: s for s in client.get(reverse("marketplace:network"), {"country": "Uganda"}).context["rail"]
        }
        offered = {r["label"] for r in rail["country"]["rows"]}
        assert {"Uganda", "Malawi", "Kenya"} <= offered

    def test_the_other_dimensions_still_narrow_normally(self, client, user, network):
        """Excluding a facet's own filter must not exclude everyone else's."""
        client.force_login(user)
        rail = {
            s["param"]: s for s in client.get(reverse("marketplace:network"), {"sector": "Nutrition"}).context["rail"]
        }
        assert {r["label"] for r in rail["country"]["rows"]} == {"Malawi"}
