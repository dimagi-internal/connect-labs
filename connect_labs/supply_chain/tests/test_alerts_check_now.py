"""Check alerts now: the same pass as the beat, for this programme, on demand.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on chlorine_world.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import PROGRAM, op

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


class TestCheckAlertsNow:
    """A new alert could only be seen working five minutes later, at the next
    beat. "Check now" runs the same pass for this programme, on demand."""

    def _subscribe(self, da):
        # A commodity with no ration table: `commodity_course_undefined` is true of it.
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet"})
        return op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["commodity_course_undefined"], "recipient_email": "stores@example.org"},
        )

    def test_the_alerts_page_offers_it(self, client_in_programme):
        body = client_in_programme.get(reverse("supply_chain:alerts")).content.decode()
        assert reverse("supply_chain:alert_check_now") in body
        assert "Check now" in body

    def test_it_finds_what_is_new_logs_it_and_says_so(self, client_in_programme, da):
        from connect_labs.supply_chain.alerts.models import AlertNotice

        sub = self._subscribe(da)
        response = client_in_programme.post(reverse("supply_chain:alert_check_now"), follow=True)
        body = response.content.decode()
        assert AlertNotice.objects.filter(
            subscription_id=sub["id"], subject_kind="commodity_course_undefined"
        ).exists()
        assert "Checked now:" in body and "new notice" in body
        assert "No ration table" in body  # in the log, as words

    def test_asking_twice_reports_nothing_new_the_second_time(self, client_in_programme, da):
        from connect_labs.supply_chain.alerts.models import AlertNotice

        self._subscribe(da)
        client_in_programme.post(reverse("supply_chain:alert_check_now"))
        body = client_in_programme.post(reverse("supply_chain:alert_check_now"), follow=True).content.decode()
        assert "Checked now: nothing new" in body
        assert AlertNotice.objects.filter(program_id=PROGRAM).count() == 1

    def test_it_runs_only_this_programmes_subscriptions(self, client_in_programme, da):
        from connect_labs.supply_chain.alerts.models import AlertNotice

        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 7, caller=SYSTEM)
        other = self._subscribe(elsewhere)
        self._subscribe(da)
        client_in_programme.post(reverse("supply_chain:alert_check_now"))
        assert not AlertNotice.objects.filter(subscription_id=other["id"]).exists()

    def test_it_is_not_a_get(self, client_in_programme):
        assert client_in_programme.get(reverse("supply_chain:alert_check_now")).status_code == 405


class TestCheckNowSaysWhatIsAlreadyTrue:
    """ "Checked now: nothing new since the last check." sat directly above
    the sent-log rows it had found, and read as a contradiction."""

    def _subscribe(self, da):
        op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet"})
        return op(
            da,
            "alert_subscription_create",
            data={"check_kinds": ["commodity_course_undefined"], "recipient_email": "stores@example.org"},
        )

    def _twice(self, client_in_programme):
        client_in_programme.post(reverse("supply_chain:alert_check_now"))
        response = client_in_programme.post(reverse("supply_chain:alert_check_now"), follow=True)
        # The first press's message is still queued; the second's is last.
        return [str(m) for m in response.context["messages"]][-1:]

    def test_nothing_new_names_the_notices_already_sent_and_when(self, client_in_programme, da):
        from unittest.mock import patch

        from connect_labs.supply_chain.alerts import service
        from connect_labs.supply_chain.alerts.models import AlertNotice

        self._subscribe(da)
        with patch.object(service, "send_labs_email"), patch.object(service, "email_enabled", return_value=True):
            (said,) = self._twice(client_in_programme)
        sent_at = AlertNotice.objects.get(program_id=PROGRAM).sent_at
        assert said.startswith("Checked now: nothing new — the notice these alerts found was already sent")
        assert sent_at.strftime("%-d %b %Y, %H:%M") in said
        assert "since the last check" not in said

    def test_it_does_not_claim_sent_what_was_not(self, client_in_programme, da):
        # Email is off in tests: the notice was logged and not sent.
        self._subscribe(da)
        (said,) = self._twice(client_in_programme)
        assert said.startswith("Checked now: nothing new")
        assert "1 not sent — email is off" in said
        assert "already sent" not in said

    def test_it_says_so_when_these_alerts_have_found_nothing_at_all(self, client_in_programme, da):
        op(da, "alert_subscription_create", data={"check_kinds": ["stock_stockout"], "recipient_email": "a@b.org"})
        (said,) = self._twice(client_in_programme)
        assert said == "Checked now: nothing new — these alerts have not found anything yet."
