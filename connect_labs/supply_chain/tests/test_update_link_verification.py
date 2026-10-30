"""Proving that whoever opens an update link can read its recipient's PersonalID chat.

OCS is faked (test_update_link_send.FakeOCS): the code that would go to the
person's Connect chat is read off what labs asked OCS to send.
"""

import re
from datetime import timedelta
from unittest.mock import patch

import pytest
from django.urls import reverse
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.integrations.ocs.api_client import OCSAPIError
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Item, StockCount, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests.test_update_link_send import FakeOCS
from connect_labs.supply_chain.tests.test_update_links import _world
from connect_labs.supply_chain.update_links import delivery, verification
from connect_labs.supply_chain.update_links.models import (
    LinkDevice,
    LinkVerification,
    UpdateLink,
    UpdateLinkSubmission,
)
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PROGRAM = 10721


@pytest.fixture(autouse=True)
def fake_ocs():
    FakeOCS.instances, FakeOCS.valid, FakeOCS.refusal = [], True, None
    with patch.object(delivery, "OCSDataAccess", FakeOCS):
        yield FakeOCS


@pytest.fixture
def world():
    return _world(SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM))


@pytest.fixture
def issued(world):
    issuer = UserFactory()
    da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, user=issuer, caller=SYSTEM)
    return call_operation(
        "update_link_issue",
        da,
        {
            "data": {
                "org_id": world["eha"]["id"],
                "contract_ids": [world["contract"]["id"]],
                "supply_point_ids": [world["warehouse"]["id"]],
                "connect_username": "amina.bello",
                "require_verification": True,
                "send_via_bot": "bot-1",
            }
        },
    )


def _url(issued):
    return reverse("supply_chain:update_link_public", args=[issued["token"]])


def _code_sent():
    """The code in the last message labs asked OCS to deliver."""
    sent = [m for i in FakeOCS.instances for m in i.sent]
    return re.search(r"\b(\d{6})\b", sent[-1]["message_text"]).group(1)


def _ask_for_code(client, issued):
    return client.post(_url(issued), {"action": "send_code"})


class TestTheGate:
    def test_nothing_of_the_link_shows_until_the_code_is_typed(self, client, issued, world):
        page = client.get(_url(issued)).content.decode()
        assert "data-verify" in page
        assert "PO-1" not in page and "EHA warehouse" not in page  # nothing the link covers
        assert "amina.bello" not in page  # the name is masked for a stranger holding the URL
        assert "am•" in page

    def test_a_write_is_refused_before_the_code(self, client, issued, world):
        response = client.post(
            _url(issued),
            {
                "action": "record_stock_count",
                "record_stock_count-supply_point": world["warehouse"]["id"],
                "record_stock_count-quantity": "5",
            },
        )
        assert response.status_code == 403
        assert not StockCount.objects.exists()

    def test_a_link_that_does_not_ask_for_proof_is_open_as_before(self, client, world):
        da = SupplyDataAccess(access_token="unused", program_id=PROGRAM, user=UserFactory(), caller=SYSTEM)
        link = call_operation(
            "update_link_issue",
            da,
            {"data": {"org_id": world["eha"]["id"], "contract_ids": [world["contract"]["id"]]}},
        )
        assert b"data-verify" not in client.get(_url(link)).content
        assert client.post(_url(link), {"action": "send_code"}).status_code == 404


class TestTheCode:
    def test_a_code_goes_to_the_recipients_chat_as_the_issuer(self, client, issued):
        response = _ask_for_code(client, issued)
        assert b"data-code-sent" in response.content
        message = [m for i in FakeOCS.instances for m in i.sent][-1]  # after the one that carried the link
        assert message["identifier"] == "amina.bello" and message["experiment_id"] == "bot-1"
        link = UpdateLink.objects.get(pk=issued["id"])
        assert FakeOCS.instances[0].user == link.issued_by
        assert LinkVerification.objects.get().code_hash != _code_sent()  # only a keyed hash is kept

    def test_the_right_code_unlocks_the_page_for_that_browser_only(self, client, issued, django_assert_num_queries):
        _ask_for_code(client, issued)
        response = client.post(_url(issued), {"action": "check_code", "code": _code_sent()})
        assert response.status_code == 302
        assert verification.cookie_name(UpdateLink.objects.get(pk=issued["id"])) in response.cookies
        page = client.get(_url(issued)).content.decode()
        assert "data-verify" not in page and "EHA warehouse" in page and "data-verified" in page
        # Another browser with the same URL is still shut out.
        other = type(client)()
        assert b"data-verify" in other.get(_url(issued)).content

    def test_a_wrong_code_is_refused_and_five_wrong_ones_use_it_up(self, client, issued):
        _ask_for_code(client, issued)
        good = _code_sent()
        wrong = "000000" if good != "000000" else "111111"
        for _ in range(verification.MAX_ATTEMPTS):
            assert client.post(_url(issued), {"action": "check_code", "code": wrong}).status_code == 400
        # Even the right code no longer works: ask for a new one.
        assert client.post(_url(issued), {"action": "check_code", "code": good}).status_code == 400
        assert not LinkDevice.objects.exists()

    def test_a_code_works_once(self, client, issued):
        _ask_for_code(client, issued)
        code = _code_sent()
        assert client.post(_url(issued), {"action": "check_code", "code": code}).status_code == 302
        assert type(client)().post(_url(issued), {"action": "check_code", "code": code}).status_code == 400

    def test_an_expired_code_is_refused(self, client, issued):
        _ask_for_code(client, issued)
        LinkVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        assert client.post(_url(issued), {"action": "check_code", "code": _code_sent()}).status_code == 400

    def test_codes_are_rationed(self, client, issued):
        assert _ask_for_code(client, issued).status_code == 200
        assert _ask_for_code(client, issued).status_code == 429  # too soon
        for _ in range(verification.MAX_SENDS_PER_HOUR):
            LinkVerification.objects.update(created_at=timezone.now() - timedelta(minutes=5))
            _ask_for_code(client, issued)
        LinkVerification.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        assert _ask_for_code(client, issued).status_code == 429  # too many this hour

    def test_a_failed_send_tells_the_person_plainly_and_leaves_no_live_code(self, client, issued, fake_ocs):
        fake_ocs.refusal = OCSAPIError("x", detail="User has not given consent")
        response = client.post(_url(issued), {"action": "send_code"})
        assert response.status_code == 429
        body = response.content.decode()
        assert "could not send a code" in body and "consent" not in body  # OCS's reason is not for them
        assert not LinkVerification.objects.exists()


class TestWhatIsRecorded:
    def test_a_write_from_a_verified_browser_is_marked_verified(self, client, issued, world):
        _ask_for_code(client, issued)
        client.post(_url(issued), {"action": "check_code", "code": _code_sent()})
        point = SupplyPoint.objects.get(pk=world["warehouse"]["id"])
        item = Item.objects.get(pk=world["item"]["id"])
        response = client.post(
            _url(issued),
            {
                "action": "record_stock_count",
                "record_stock_count-supply_point": point.pk,
                "record_stock_count-item": item.pk,
                "record_stock_count-quantity": "5",
                "record_stock_count-unit_basis": "pack",
                "record_stock_count-counted_on": timezone.localdate().isoformat(),
            },
        )
        assert response.status_code == 302, response.content.decode()[:500]
        submission = UpdateLinkSubmission.objects.get()
        assert submission.verified is True and submission.connect_username == "amina.bello"

    def test_a_write_by_service_without_a_device_is_not_marked_verified(self, issued, world):
        from connect_labs.supply_chain.update_links import service

        service.submit(
            UpdateLink.objects.get(pk=issued["id"]),
            "record_stock_count",
            {
                "supply_point": SupplyPoint.objects.get(pk=world["warehouse"]["id"]),
                "item": Item.objects.get(pk=world["item"]["id"]),
                "quantity": "5",
                "unit_basis": "pack",
            },
        )
        assert UpdateLinkSubmission.objects.get().verified is False


class TestIssuing:
    def _da(self):
        return SupplyDataAccess(access_token="unused", program_id=PROGRAM, user=UserFactory(), caller=SYSTEM)

    def test_verification_needs_a_username(self, world):
        with pytest.raises(ValueError, match="PersonalID user"):
            call_operation(
                "update_link_issue",
                self._da(),
                {
                    "data": {
                        "org_id": world["eha"]["id"],
                        "contract_ids": [world["contract"]["id"]],
                        "require_verification": True,
                        "send_via_bot": "bot-1",
                    }
                },
            )

    def test_verification_needs_a_chatbot_to_send_codes_through(self, world, settings):
        settings.SUPPLY_LINK_OCS_BOT = ""
        with pytest.raises(ValueError, match="chatbot"):
            call_operation(
                "update_link_issue",
                self._da(),
                {
                    "data": {
                        "org_id": world["eha"]["id"],
                        "contract_ids": [world["contract"]["id"]],
                        "connect_username": "amina.bello",
                        "require_verification": True,
                    }
                },
            )
