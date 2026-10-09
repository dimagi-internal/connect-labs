"""Reading a case's visits from the visit cache: the case-coaching SQL, run on Postgres."""

import datetime as dt

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
from connect_labs.workflow import case_coaching as cc
from connect_labs.workflow import case_finder, case_visits

OPP = 10042


def _form(name, **form):
    return {"form": {"@name": name, **form}}


def _visit(n, day, form, status="approved", username="flw_001", entity="baby-1"):
    RawVisitCache.objects.create(
        opportunity_id=OPP,
        pipeline_id=USER_VISITS_RAW_SLOT,
        visit_count=10,
        expires_at=timezone.now() + dt.timedelta(hours=1),
        visit_id=str(n),
        username=username,
        entity_id=entity,
        entity_name="KMC Demo — Steady Gain",
        visit_date=dt.date.fromisoformat(day),
        status=status,
        form_json=form,
    )


@pytest.fixture
def cached(db):
    reg = "Child Registration Form"
    follow = "Record Visit Details"
    # Design B registration: form.case is the MOTHER, the baby is subcase_0.
    _visit(
        1,
        "2026-05-17",
        _form(
            reg,
            case={"@case_id": "mother-1"},
            subcase_0={"case": {"@case_id": "baby-1"}},
            child_weight_birth=1250,
            child_details={"birth_weight_reg": {"child_weight_reg": 1350}},
            danger_signs_checklist={"pus_grp": {"pus_in_eyes_skin_or_on_belly_button": "no"}},
        ),
    )
    for n, (day, w, h) in enumerate([("2026-05-25", 1635, 20), ("2026-06-01", 1915, 40), ("2026-06-08", 2285, 40)], 2):
        _visit(
            n,
            day,
            _form(
                follow,
                child_case_id="baby-1",
                anthropometric={"child_weight_visit": w},
                # The case's copy of the registration weight, on every follow-up.
                child_details={"birth_weight_reg": {"child_weight_reg": 1350}},
                **{"kmc_24-hour_recall": {"total_kmc_hours": h}},
                danger_signs_checklist={
                    "child_referred": "no",
                    "resp_rate_grp": {"child_breath_count": 40},
                    "noisy_breathing_grp": {"Noisy_breathing": "yes" if day == "2026-06-08" else "no"},
                    "danger_sign_label": {"high_breath_count": "OK"} if day == "2026-06-08" else {},
                },
            ),
        )
    # Rejected: never read. Another baby: not this case.
    _visit(
        9, "2026-06-09", _form(follow, child_case_id="baby-1", anthropometric={"child_weight_visit": 9999}), "rejected"
    )
    _visit(
        10,
        "2026-06-09",
        _form(follow, child_case_id="baby-2", anthropometric={"child_weight_visit": 2000}),
        entity="b2",
    )


def test_one_case_is_read_with_each_fact_from_its_own_path(cached):
    rows = case_visits.load_rows_for([OPP], cc.KMC_CASE_COACHING)
    cases = {c.case_id: c for c in cc.cases_from_rows(rows, cc.KMC_CASE_COACHING)}
    assert set(cases) == {"baby-1", "baby-2"}  # the mother's id never becomes a case
    case = cases["baby-1"]
    assert [v.date.isoformat() for v in case.visits] == ["2026-05-17", "2026-05-25", "2026-06-01", "2026-06-08"]
    assert [v.weight_g for v in case.visits] == [1350, 1635, 1915, 2285]
    assert [v.skin_to_skin_h for v in case.visits] == [None, 20, 40, 40]
    assert case.visits[-1].signs == ("fast_breathing", "noisy_breathing")
    assert [v.referred for v in case.visits] == [None, False, False, False]
    assert case.birth_weight_g == 1250
    # Not referred, with a danger sign: the case's story.
    assert cc.story_of(case).key == cc.DANGER


def test_as_of_cuts_the_visits_for_a_saved_run(cached):
    rows = case_visits.load_rows_for([OPP], cc.KMC_CASE_COACHING, as_of="2026-06-01")
    assert max(r["visit_date"] for r in rows) == dt.date(2026, 6, 1)


def test_the_snapshot_view_is_built_from_the_cache(cached):
    from types import SimpleNamespace

    definition = SimpleNamespace(data={"config": {"case_coaching": cc.KMC_CASE_COACHING}}, template_type=None)
    view = case_finder.snapshot_view(definition, [OPP], as_of=None, names={"10042::flw_001": "Asha"})
    [worker] = view["workers"]
    assert worker["name"] == "Asha"
    assert worker["eligible"][cc.DANGER]["best"][0]["case_id"] == "baby-1"
    assert case_finder.snapshot_view(SimpleNamespace(data={}, template_type=None), [OPP], as_of=None, names={}) is None
