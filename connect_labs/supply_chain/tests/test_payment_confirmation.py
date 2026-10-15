"""The supplier confirming that a payment arrived.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on field_gaps_world.
"""

from datetime import timedelta

import pytest
from django.urls import reverse

from connect_labs.supply_chain.checks import KIND_CATEGORIES, PAYMENT_CONFIRMATION_GRACE_DAYS
from connect_labs.supply_chain.models import Payment
from connect_labs.supply_chain.tests import field_gaps_world
from connect_labs.supply_chain.tests.field_gaps_world import TODAY, _contract, _paid, op
from connect_labs.supply_chain.values import day_text

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = field_gaps_world.da
world = field_gaps_world.world
dispensers = field_gaps_world.dispensers
scoped = field_gaps_world.scoped


def _unconfirmed(da):
    return [c for c in op(da, "checks_list")["checks"] if c["kind"] == "payment_unconfirmed"]


class TestThePayeeConfirmsAPayment:
    def test_it_is_a_missing_fact(self):
        assert KIND_CATEGORIES["payment_unconfirmed"] == "missing"

    def test_a_payment_starts_unconfirmed(self, da, world):
        payment = _paid(da, _contract(da, world, world["main"], "700"), days_ago=1)
        assert payment["confirmed_by_payee_on"] is None

    def test_an_old_payment_with_no_confirmation_is_a_check_aged_from_payment(self, da, world):
        contract = _contract(da, world, world["main"], "700")
        payment = _paid(da, contract, days_ago=PAYMENT_CONFIRMATION_GRACE_DAYS + 6)
        (check,) = _unconfirmed(da)
        assert check["subject"] == {"type": "payment", "id": payment["id"], "label": check["subject"]["label"]}
        assert check["audience"] == "supplier"
        assert check["days_open"] == PAYMENT_CONFIRMATION_GRACE_DAYS + 6
        assert check["facts"]["contract_id"] == contract["id"]
        assert check["facts"]["amount"] == "900"

    def test_a_recent_one_is_not_yet(self, da, world):
        _paid(da, _contract(da, world, world["main"], "700"), days_ago=2)
        assert _unconfirmed(da) == []

    def test_confirming_it_clears_it(self, da, world):
        payment = _paid(da, _contract(da, world, world["main"], "700"), days_ago=30)
        confirmed = op(da, "payment_confirm", payment_id=payment["id"], confirmed_on=TODAY.isoformat())
        assert confirmed["confirmed_by_payee_on"] == TODAY.isoformat()
        assert _unconfirmed(da) == []

    def test_confirmation_cannot_predate_the_payment(self, da, world):
        payment = _paid(da, _contract(da, world, world["main"], "700"), days_ago=3)
        with pytest.raises(ValueError, match="before"):
            op(da, "payment_confirm", payment_id=payment["id"], confirmed_on=(TODAY - timedelta(days=10)).isoformat())


def test_the_order_page_shows_each_payment_and_whether_the_payee_confirmed_it(scoped, da, world):
    contract = _contract(da, world, world["main"], "700")
    payment = _paid(da, contract, days_ago=30)
    body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    assert "not confirmed by the payee" in body
    assert reverse("supply_chain:payment_confirm", args=[payment["id"]]) in body


def test_recording_the_confirmation(scoped, da, world):
    contract = _contract(da, world, world["main"], "700")
    payment = _paid(da, contract, days_ago=30)
    response = scoped.post(
        reverse("supply_chain:payment_confirm", args=[payment["id"]]), {"confirmed_by_payee_on": TODAY.isoformat()}
    )
    assert response.status_code == 302, response.content.decode()[:2000]
    assert Payment.objects.get(pk=payment["id"]).confirmed_by_payee_on == TODAY
    body = scoped.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    assert f"confirmed by the payee {day_text(TODAY)}" in body
