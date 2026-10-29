"""An update link can name the PersonalID user it was sent to.

Not authentication: the link still works for whoever holds it. What it adds is
attribution -- the work done through it is credited to the person we sent it
to. So these tests pin both halves: that the name travels onto what is
recorded, and that it never changes who may use the link.
"""

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Item, StockCount, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests.test_update_links import _world
from connect_labs.supply_chain.update_links import forms, service
from connect_labs.supply_chain.update_links.messaging import link_message
from connect_labs.supply_chain.update_links.models import UpdateLink, UpdateLinkSubmission

pytestmark = pytest.mark.django_db

PROGRAM = 10701
OTHER_PROGRAM = 10702


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world(da):
    return _world(da)


def _worker_point(da, username, slug):
    return op(
        da,
        "supply_point_upsert",
        data={
            "slug": slug,
            "name": f"{username}'s bag",
            "kind": "user_held",
            "connect_username": username,
            "source": "we_recorded",
        },
    )


def _issue(da, world, **extra):
    return op(
        da,
        "update_link_issue",
        data={
            "org_id": world["eha"]["id"],
            "contract_ids": [world["contract"]["id"]],
            "supply_point_ids": [world["warehouse"]["id"]],
            **extra,
        },
    )


def _count(link, point_row, world, quantity="7"):
    return service.submit(
        UpdateLink.objects.get(pk=link["id"]),
        "record_stock_count",
        {
            "supply_point": SupplyPoint.objects.get(pk=point_row["id"]),
            "item": Item.objects.get(pk=world["item"]["id"]),
            "counted_on": timezone.now().date(),
            "quantity": quantity,
            "unit_basis": "pack",
        },
    )


class TestIssuing:
    def test_the_username_is_kept_and_listed(self, da, world):
        issued = _issue(da, world, connect_username="  amina.bello  ")
        assert issued["connect_username"] == "amina.bello"
        assert UpdateLink.objects.get(pk=issued["id"]).connect_username == "amina.bello"
        assert op(da, "update_link_list")[0]["connect_username"] == "amina.bello"

    def test_a_link_to_an_organisation_has_no_recipient(self, da, world):
        assert _issue(da, world)["connect_username"] == ""

    def test_a_listed_link_to_a_worker_also_covers_that_workers_own_stock_point(self, da, world):
        mine = _worker_point(da, "amina.bello", "amina-bag")
        _worker_point(da, "someone.else", "other-bag")
        issued = _issue(da, world, connect_username="amina.bello")
        assert mine["id"] in issued["supply_point_ids"]
        assert len(issued["supply_point_ids"]) == 2  # the warehouse and her bag, nobody else's

    def test_a_worker_point_in_another_programme_is_not_reached_by_the_name(self, da, world):
        other = SupplyDataAccess(access_token="unused", program_id=OTHER_PROGRAM, caller=SYSTEM)
        theirs = _worker_point(other, "amina.bello", "amina-bag-elsewhere")
        issued = _issue(da, world, connect_username="amina.bello")
        assert theirs["id"] not in issued["supply_point_ids"]

    def test_a_name_with_no_point_still_needs_something_to_cover(self, da, world):
        with pytest.raises(ValueError, match="at least one"):
            op(da, "update_link_issue", data={"org_id": world["eha"]["id"], "connect_username": "nobody"})


class TestAttribution:
    def test_a_count_through_the_link_is_credited_to_the_person_it_was_sent_to(self, da, world):
        bag = _worker_point(da, "amina.bello", "amina-bag")
        issued = _issue(da, world, connect_username="amina.bello")
        result = _count(issued, bag, world)
        count = StockCount.objects.get(pk=result["id"])
        assert count.connect_username == "amina.bello"
        assert count.recorded_by_org_id == world["eha"]["id"]
        submission = UpdateLinkSubmission.objects.get(link_id=issued["id"])
        assert submission.connect_username == "amina.bello"

    def test_the_credit_survives_the_link_being_revoked(self, da, world):
        bag = _worker_point(da, "amina.bello", "amina-bag")
        issued = _issue(da, world, connect_username="amina.bello")
        _count(issued, bag, world)
        op(da, "update_link_revoke", link_id=issued["id"])
        assert UpdateLinkSubmission.objects.get(link_id=issued["id"]).connect_username == "amina.bello"

    def test_a_link_to_an_organisation_credits_no_person(self, da, world):
        issued = _issue(da, world)
        result = _count(issued, world["warehouse"], world)
        assert StockCount.objects.get(pk=result["id"]).connect_username == ""
        assert UpdateLinkSubmission.objects.get(link_id=issued["id"]).connect_username == ""

    def test_naming_a_person_does_not_change_who_may_use_the_link(self, client, da, world):
        # No login, no check on who opens it: the name is a label, not a gate.
        issued = _issue(da, world, connect_username="amina.bello")
        response = client.get(reverse("supply_chain:update_link_public", args=[issued["token"]]))
        assert response.status_code == 200
        assert b"amina.bello" in response.content


class TestTheIssueForm:
    def test_a_name_alone_is_enough_to_cover(self):
        form = forms.UpdateLinkIssueForm(data={"coverage": "listed", "connect_username": "amina.bello"})
        form.is_valid()
        assert "cover something" not in " ".join(form.non_field_errors())


class TestTheMessage:
    def test_the_link_is_a_markdown_link_because_the_app_does_not_auto_link_bare_urls(self):
        text = link_message("https://labs.example/supply/u/abc/", org_name="EHA")
        assert "[Update EHA stock](https://labs.example/supply/u/abc/)" in text
