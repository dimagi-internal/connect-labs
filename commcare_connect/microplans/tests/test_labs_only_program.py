"""A microplans program can be labs-only (synthetic): its plans live in the labs
DB, not production Connect.

A labs-only program is a synthetic opportunity surfaced in ``user_programs`` as a
negative id (``= -opportunity_id``) by ``labs.context._merge_labs_only_opps``.
``ProgramPlanDataAccess`` recognises the negative id, carries the backing
synthetic opp, and the LabsRecord API client short-circuits to the local backend
— no prod round-trip, no membership check. Real (positive-PK) programs are
untouched.
"""

from __future__ import annotations

import pytest

from commcare_connect.labs.synthetic.models import LabsLocalRecord, SyntheticOpportunity
from commcare_connect.microplans.core.data_access import TYPE_PLAN, ProgramPlanDataAccess

_HULLS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[[8.0, 9.0], [8.1, 9.0], [8.1, 9.1], [8.0, 9.1], [8.0, 9.0]]],
            },
            "properties": {
                "cluster": "C0",
                "building_count": 10,
                "expected_visit_count": 10,
            },
        }
    ],
}
_EMPTY = {"type": "FeatureCollection", "features": []}


@pytest.fixture
def synthetic_program(db):
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=10_007,
        label="Study Design",
        program_name="Study Design Synthetic",
        gdrive_folder_id="f",
        labs_only=True,
    )
    return opp


@pytest.mark.django_db
def test_negative_program_id_carries_backing_synthetic_opp(synthetic_program):
    da = ProgramPlanDataAccess(-10_007, access_token="labs-local")
    # The DA auto-resolved the backing synthetic opp so the client short-circuits local.
    assert da.opportunity_id == 10_007
    assert da.program_id == -10_007


@pytest.mark.django_db
def test_plan_round_trips_through_the_labs_db_not_production(synthetic_program):
    da = ProgramPlanDataAccess(-10_007, access_token="labs-local")
    plan = da.create_plan(region="Attakar", name="Attakar", mode="coverage", pins=_EMPTY, hulls=_HULLS)

    # Stored as a local record scoped to the negative program id + backing opp...
    row = LabsLocalRecord.objects.get(id=plan.id)
    assert row.type == TYPE_PLAN
    assert row.opportunity_id == 10_007
    assert row.program_id == -10_007
    assert row.experiment == "-10007"
    # ...and it reads back through the same labs-local path (no prod call).
    assert [p.id for p in da.list_plans()] == [plan.id]
    assert da.get_plan(plan.id).data["region"] == "Attakar"


@pytest.mark.django_db
def test_unregistered_negative_id_does_not_short_circuit():
    # A negative id whose backing opp isn't a registered labs-only synthetic opp
    # must NOT be treated as labs-local (no opportunity carried → falls through to
    # the normal production path).
    da = ProgramPlanDataAccess(-999_999, access_token="labs-local")
    assert da.opportunity_id is None


@pytest.mark.django_db
def test_positive_program_id_is_untouched(synthetic_program):
    da = ProgramPlanDataAccess(133, access_token="labs-local")
    assert da.opportunity_id is None  # real program → production path, no opp carried
