"""Test opportunities leave every figure through a subquery, not a pasted id list.

The list made each card's SQL a different statement for every length of it
(~360 ids on prod), which crowded pg_stat_statements out of its 5,000 slots and
hid what was actually slow. The rows excluded are the same either way.
"""

import pytest
from django.test import RequestFactory

from connect_labs.pulse.api import _program_scope
from connect_labs.pulse.models import PulseOpportunity, PulseWork


def _req(params):
    request = RequestFactory().get("/pulse/api/summary/", params)
    request.user = type("U", (), {"is_authenticated": False, "is_superuser": False, "email": ""})()
    return request


@pytest.mark.django_db
def test_test_opportunities_are_excluded_by_a_subquery():
    for opp_id, is_test in ((71001, False), (71002, True), (71003, True)):
        PulseOpportunity.objects.create(opportunity_id=opp_id, name=f"o{opp_id}", is_test=is_test)

    sql = str(_program_scope(_req({}))["works"].query)

    assert "pulse_pulseopportunity" in sql, sql
    # Neither test id is pasted into the statement.
    assert "71002" not in sql and "71003" not in sql, sql


@pytest.mark.django_db
def test_the_same_rows_are_excluded():
    for opp_id, is_test in ((71011, False), (71012, True)):
        PulseOpportunity.objects.create(opportunity_id=opp_id, name=f"o{opp_id}", is_test=is_test)
    for opp_id in (71011, 71012):
        PulseWork.objects.create(
            work_key=f"k{opp_id}", opportunity_id=opp_id, status="approved", created_ts="2026-10-01T00:00:00Z"
        )

    works = _program_scope(_req({}))["works"].filter(opportunity_id__in=[71011, 71012])

    assert list(works.values_list("opportunity_id", flat=True)) == [71011]
