# flake8: noqa: F811
"""The Overview chase list shows an alert that could not be sent (it was only in the alert log).

THIS REPOSITORY IS PUBLIC. Invented names.
"""

import pytest
from django.utils import timezone

from connect_labs.supply_chain.alerts.models import AlertNotice, AlertSubscription
from connect_labs.supply_chain.tests.test_sophie_batch3 import _home
from connect_labs.supply_chain.tests.test_sophie_batch5 import (  # noqa: F401 -- fixtures
    ace,
    base,
    client_in_program,
    da,
    home_client,
    registered_synthetic,
    sophie,
)

pytestmark = pytest.mark.django_db


def test_a_failed_alert_is_on_the_overview_chase_list_and_clears_when_it_works(da, base, home_client):
    sub = AlertSubscription.objects.create(
        program_id=da.program_id,
        label="Partner stock alerts",
        check_kinds=["stock_stockout"],
        recipient_connect_username="someone",
        channel="connect_message",
    )
    AlertNotice.objects.create(
        subscription=sub,
        program_id=da.program_id,
        kind="check",
        subject_kind="stock_stockout",
        detected_at=timezone.now(),
        delivery="no_connection",
        delivery_note="Your Open Chat Studio connection has expired.",
    )
    body = _home(home_client)
    assert 'data-testid="chase-list"' in body
    assert "An alert could not be sent" in body and "Partner stock alerts" in body
    AlertNotice.objects.create(
        subscription=sub,
        program_id=da.program_id,
        kind="check",
        subject_kind="stock_stockout",
        detected_at=timezone.now(),
        delivery="queued",
    )
    assert "An alert could not be sent" not in _home(home_client)
