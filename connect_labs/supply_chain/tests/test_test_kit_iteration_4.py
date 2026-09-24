"""What the test-kit walkthrough's third render asked for.

THIS REPOSITORY IS PUBLIC. Every organisation, product and number is invented.

  - a supplier link stops telling somebody an action is unavailable on the
    same screen that records them doing it;
  - the award states what was awarded in figures, and that the awarded kit
    met the specification it was chosen against;
  - "Decided by" is not prefilled with the account's login handle;
  - the approval screen keeps its nav tab highlighted.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.tests import test_test_kit_iteration_2 as base
from connect_labs.supply_chain.tests import test_update_links as links

pytestmark = pytest.mark.django_db

op = base.op
da = base.da
scoped = base.scoped
world = base.world


class TestTheAwardStatesWhatWasAwarded:
    """The record of the decision can be checked against the offer it came from.

    The award named a kit, a decider and a reason, and stated no quantity,
    price or total directly above its own "Place order" button -- so the order
    that followed was taken on trust from a prefill. It also never said the
    awarded kit met the specification, though meeting it was the whole reason
    it was chosen.
    """

    def _page(self, scoped, world):
        url = reverse("supply_chain:award_detail", args=[world["award"]["id"]])
        response = scoped.get(url)
        assert response.status_code == 200
        return response.content.decode()

    def test_it_states_how_many_the_money_is_for(self, scoped, world):
        assert "20 kits" in self._page(scoped, world)

    def test_it_states_the_offer_s_own_money(self, scoped, world):
        body = self._page(scoped, world)
        # The quote was recorded at USD 38.00 the kit, and the award now says
        # so under the same column label the comparison ranked it by.
        assert "USD 38.00" in body
        assert "USD per kit" in body

    def test_it_says_the_awarded_kit_meets_the_specification(self, scoped, world):
        body = self._page(scoped, world)
        assert "Meets all 2" in body


class TestDecidedByIsNotTheAccount:
    """A purchasing decision is made by a person, not by the login recording it.

    `User.name` is free text and on a shared or service account it holds the
    handle itself, which was then prefilled into the one field that exists to
    name who decided -- so the award form opened reading "Decided by: ace".
    """

    @pytest.mark.parametrize(
        "name, username, email, prefilled",
        [
            # The handle in the name field is not a name.
            ("ace", "ace", "ace@dimagi-ai.com", ""),
            ("ACE", "ace", "ace@dimagi-ai.com", ""),
            # Nor is the email's local part, which is how the handle usually
            # gets into `name` in the first place.
            ("ace", "someone-else", "ace@dimagi-ai.com", ""),
            # A person's name is.
            ("Hauwa Bello", "ace", "ace@dimagi-ai.com", "Hauwa Bello"),
            # Nothing on file prefills nothing, rather than falling back to
            # the login the way `get_display_name` does.
            ("", "ace", "ace@dimagi-ai.com", ""),
        ],
    )
    def test_only_a_person_s_own_name_is_offered(self, django_user_model, name, username, email, prefilled):
        from connect_labs.supply_chain.procurement.views import _person_name

        user = django_user_model(name=name, username=username, email=email)
        assert _person_name(user) == prefilled

    def test_the_comparison_offers_that_and_not_the_handle(self, scoped, django_user_model, world):
        user = django_user_model.objects.get()
        user.name = user.username
        user.save()
        url = reverse("supply_chain:procurement_comparison", args=[world["award"]["round_id"]])
        response = scoped.get(url + "?commodity=test-kit")
        assert response.status_code == 200
        assert response.context["decider"] == ""

    def test_the_comparison_offers_a_real_name(self, scoped, django_user_model, world):
        user = django_user_model.objects.get()
        user.name = "Hauwa Bello"
        user.save()
        url = reverse("supply_chain:procurement_comparison", args=[world["award"]["round_id"]])
        response = scoped.get(url + "?commodity=test-kit")
        assert response.context["decider"] == "Hauwa Bello"


class TestAnActionAlreadyDoneIsNotCalledUnavailable:
    """Unavailable-because-out-of-scope and unavailable-because-done are opposites.

    `is_available()` is False for both, so the page printed "Not available on
    this link right now: confirm an order" on the same screen that recorded
    the supplier confirming that order. The approver half of this was fixed by
    excluding approver forms; this fixes it by cause, for everyone.
    """

    def test_out_of_scope_actions_are_still_named(self, client, links_issued, links_world):
        body = client.get(links._url(links_issued["token"])).content.decode()
        assert "Not available on this link right now" in body

    def test_a_confirmed_order_is_not_listed_as_unavailable(self, client, links_issued, links_world):
        client.post(
            links._url(links_issued["token"]),
            {"action": "confirm_order", "confirm_order-contract": links_world["contract"]["id"]},
        )
        assert Contract.objects.get(pk=links_world["contract"]["id"]).status == "confirmed"
        body = client.get(links._url(links_issued["token"])).content.decode()
        unavailable = body.split("Not available on this link right now")
        assert len(unavailable) == 1 or "confirm an order" not in unavailable[1].split("</p>")[0]


@pytest.fixture
def links_world(links_da):
    return links._world(links_da)


@pytest.fixture
def links_da():
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.data_access import SupplyDataAccess

    return SupplyDataAccess(access_token="unused", program_id=links.PROGRAM, caller=SYSTEM)


@pytest.fixture
def links_issued(links_da, links_world):
    return links.issued.__wrapped__(links_da, links_world)


class TestTheApprovalScreenKeepsItsTab:
    def test_asking_for_an_approval_still_highlights_sourcing(self, scoped, world):
        url = reverse("supply_chain:approval_request", args=[world["award"]["id"]])
        response = scoped.get(url)
        assert response.status_code == 200
        body = response.content.decode()
        from connect_labs.supply_chain.navigation import TAB_FOR_VIEW

        assert TAB_FOR_VIEW["supply_chain:approval_request"] == "supply_chain:procurement_round_board"
        assert reverse("supply_chain:procurement_round_board") in body
