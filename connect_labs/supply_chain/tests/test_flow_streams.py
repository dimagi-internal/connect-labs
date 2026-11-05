# flake8: noqa: F811
"""Where it went: losses told apart (taken, expired, damaged, missing) and one partner's own flow.

THIS REPOSITORY IS PUBLIC. Invented names and figures; the world is test_worker_screens'.
"""

from datetime import timedelta

import pytest
from django.urls import reverse

from connect_labs.labs.models import LabsOrg
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests.test_worker_screens import (  # noqa: F401 -- fixtures
    PROGRAM,
    TODAY,
    client_in_program,
    da,
    get,
    world,
)

pytestmark = pytest.mark.django_db


def _report(da, world, kind, quantity):
    return call_operation(
        "stock_issue_report",
        da,
        {
            "data": {
                "supply_point_id": world["worker-acacia"].pk,
                "kind": kind,
                "occurred_on": (TODAY - timedelta(days=2)).isoformat(),
                "commodity_slug": "rutf",
                "item_id": world["item"].pk,
                "quantity": quantity,
                "quantity_unit": "sachet",
                "source": "we_recorded",
            }
        },
    )


def _flow(da, world, **extra):
    return call_operation("stock_flow", da, {"item_id": world["item"].pk, **extra})


def test_taken_expired_and_damaged_are_their_own_streams_not_one_lost_total(da, world):
    for kind, quantity in (("taken", 5), ("expired", 6), ("damaged", 7), ("missing", 8), ("other", 1)):
        _report(da, world, kind, quantity)
    stages = {s["key"]: s["amount"] for s in _flow(da, world)["account"]["stages"]}
    assert stages["taken"] == 5 and stages["expired"] == 6 and stages["damaged"] == 7
    assert stages["lost"] == 9  # missing and other stay "lost or missing"


def test_streams_with_nothing_in_them_are_not_shown_and_lost_always_is(da, world):
    keys = {s["key"] for s in _flow(da, world)["account"]["stages"]}
    assert "lost" in keys and not keys & {"taken", "expired", "damaged"}


def test_the_page_names_each_stream(client_in_program, da, world):
    _report(da, world, "taken", 5)
    _report(da, world, "expired", 6)
    body = get(client_in_program, "flow")
    assert "Taken" in body and "Expired" in body and "Lost or missing" in body
    assert "Lost or expired" not in body


def test_one_partners_flow_counts_only_the_places_it_runs(da, world):
    mine, other = LabsOrg.objects.create(slug="flow-a", name="Flow A"), LabsOrg.objects.create(
        slug="flow-b", name="Flow B"
    )
    world["store"].managed_by_org = mine
    world["store"].save()
    everyone = _flow(da, world)["account"]
    scoped = _flow(da, world, org_id=mine.pk)["account"]
    assert scoped["arrived"] == everyone["arrived"]
    empty = _flow(da, world, org_id=other.pk)
    assert empty["weeks"] == []


def test_the_page_filters_by_partner_and_keeps_the_switcher(client_in_program, da, world):
    org = LabsOrg.objects.create(slug="flow-c", name="Flow C")
    world["store"].managed_by_org = org
    world["store"].save()
    page = client_in_program.get(reverse("supply_chain:flow"), {"partner": org.pk})
    body = page.content.decode()
    assert page.status_code == 200 and 'data-testid="partner-switcher"' in body and "Flow C" in body
    assert 'data-testid="flow-account"' in body
