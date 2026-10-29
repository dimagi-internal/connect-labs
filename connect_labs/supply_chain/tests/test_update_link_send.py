"""Sending an update link to its recipient's PersonalID chat, through Open Chat Studio.

OCS is faked here: these tests pin what labs ASKS OCS to do (who, which channel,
what text, as whom) and what it does with each answer -- not OCS itself.
"""

from unittest.mock import patch

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.labs.integrations.ocs.api_client import OCSAPIError
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests.test_update_links import _world
from connect_labs.supply_chain.update_links import delivery
from connect_labs.supply_chain.update_links.models import UpdateLink
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db

PROGRAM = 10711


class FakeOCS:
    """Stands in for OCSDataAccess; records what it was asked to send."""

    instances = []

    def __init__(self, request=None, *, user=None):
        self.user = user
        self.sent = []
        self.valid = FakeOCS.valid
        self.refusal = FakeOCS.refusal
        FakeOCS.instances.append(self)

    valid = True
    refusal = None

    def check_token_valid(self):
        return self.valid

    def trigger_bot(self, **kwargs):
        if self.refusal:
            raise self.refusal
        self.sent.append(kwargs)
        return {"session_id": "s1"}

    def list_experiments(self):
        return [{"public_id": "bot-1", "name": "Supply helper"}]

    def close(self):
        pass


@pytest.fixture(autouse=True)
def fake_ocs():
    FakeOCS.instances, FakeOCS.valid, FakeOCS.refusal = [], True, None
    with patch.object(delivery, "OCSDataAccess", FakeOCS):
        yield FakeOCS


@pytest.fixture
def user():
    return UserFactory()


@pytest.fixture
def da(user):
    # An unattended caller that still names the person acting, which is all the send needs:
    # who issued the link. The standing checks are the data access's business, not this file's.
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, user=user, caller=SYSTEM)


@pytest.fixture
def world():
    # Seeded unattended: an unattended write needs no organisation to speak for.
    return _world(SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM))


def _issue(da, world, **extra):
    return call_operation(
        "update_link_issue",
        da,
        {
            "data": {
                "org_id": world["eha"]["id"],
                "contract_ids": [world["contract"]["id"]],
                "label": "April stock",
                **extra,
            }
        },
    )


class TestSending:
    def test_the_link_goes_to_the_recipients_chat_as_a_markdown_link_through_the_chosen_bot(self, da, world, user):
        result = _issue(da, world, connect_username="amina.bello", send_via_bot="bot-1")
        assert result["delivery"] == {"sent": True}
        (sent,) = (m for i in FakeOCS.instances for m in i.sent)
        assert (sent["identifier"], sent["platform"], sent["experiment_id"]) == (
            "amina.bello",
            "commcare_connect",
            "bot-1",
        )
        assert f"]({result['url']})" in sent["message_text"]
        assert FakeOCS.instances[0].user == user  # as the person who issued it
        link = UpdateLink.objects.get(pk=result["id"])
        assert link.sent_at is not None and link.sent_via_bot == "bot-1" and link.send_error == ""

    def test_no_send_is_made_unless_asked_for(self, da, world):
        result = _issue(da, world, connect_username="amina.bello")
        assert "delivery" not in result
        assert FakeOCS.instances == []

    def test_a_send_needs_to_know_who_it_is_for(self, da, world):
        with pytest.raises(ValueError, match="PersonalID user"):
            _issue(da, world, send_via_bot="bot-1")

    def test_a_refusal_keeps_the_link_and_says_why(self, da, world, fake_ocs):
        fake_ocs.refusal = OCSAPIError("x", detail="User has not given consent")
        result = _issue(da, world, connect_username="amina.bello", send_via_bot="bot-1")
        assert result["delivery"]["sent"] is False
        assert "not given consent" in result["delivery"]["reason"]
        link = UpdateLink.objects.get(pk=result["id"])
        assert link.sent_at is None and "consent" in link.send_error
        assert result["url"]  # the issuer still has the link to pass on

    def test_an_unconnected_ocs_is_reported_not_raised(self, da, world, fake_ocs):
        fake_ocs.valid = False
        result = _issue(da, world, connect_username="amina.bello", send_via_bot="bot-1")
        assert "not connected" in result["delivery"]["reason"]

    def test_a_synthetic_programme_sends_nothing(self, da, world):
        with patch("connect_labs.supply_chain.scopes.is_synthetic", return_value=True):
            result = _issue(da, world, connect_username="amina.bello", send_via_bot="bot-1")
        assert result["delivery"]["synthetic"] is True
        assert FakeOCS.instances == []


class TestTheIssueForm:
    def test_the_issuer_is_offered_their_bots_and_the_default_is_preselected(self, da, settings):
        from connect_labs.supply_chain.update_links.forms import UpdateLinkIssueForm

        settings.SUPPLY_LINK_OCS_BOT = "bot-1"
        form = UpdateLinkIssueForm(access=da)
        assert ("bot-1", "Supply helper") in form.fields["send_via_bot"].choices
        assert form.fields["send_via_bot"].initial == "bot-1"

    def test_without_an_ocs_connection_there_is_no_empty_dropdown_and_the_help_says_how_to_get_one(self, da, fake_ocs):
        from connect_labs.supply_chain.update_links.forms import UpdateLinkIssueForm

        fake_ocs.valid = False
        form = UpdateLinkIssueForm(access=da)
        assert "send_via_bot" not in form.fields
        assert "connect Open Chat Studio" in str(form.fields["connect_username"].help_text)

    def test_choosing_a_bot_needs_a_username(self, da, world):
        from connect_labs.supply_chain.update_links.forms import UpdateLinkIssueForm

        form = UpdateLinkIssueForm(
            data={
                "org": world["eha"]["id"],
                "coverage": "listed",
                "contracts": [world["contract"]["id"]],
                "expires_in_days": 30,
                "send_via_bot": "bot-1",
            },
            access=da,
        )
        assert not form.is_valid()
        assert "PersonalID username" in " ".join(form.non_field_errors())
