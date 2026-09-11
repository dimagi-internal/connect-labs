"""fill_form_json must apply a binary field's per-period rate for the visit's period."""

from __future__ import annotations

import random

from connect_labs.labs.synthetic.generator.fixtures.fields import fill_form_json
from connect_labs.labs.synthetic.generator.fixtures.manifest import BeneficiaryCohort, BinaryDistribution
from connect_labs.labs.synthetic.generator.fixtures.schema_loader import FormSchema


def _outcome_rate(period: int, n: int = 3000) -> float:
    cohort = BeneficiaryCohort(
        id="c",
        size=10,
        field_distributions={
            "form.va_confirmed": BinaryDistribution(distribution="binary", rate=0.5, period_rates={6: 0.9, 1: 0.1})
        },
        progression="flat",
    )
    schema = FormSchema(questions=[])
    rng = random.Random(0)
    hits = 0
    for _ in range(n):
        fj = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng, period=period)
        hits += int(fj["form"]["va_confirmed"] == 1.0)
    return hits / n


def test_period_6_uses_high_rate():
    assert 0.87 <= _outcome_rate(6) <= 0.93


def test_period_1_uses_low_rate():
    assert 0.07 <= _outcome_rate(1) <= 0.13


def test_mirror_visits_carry_a_case_block_and_form_name():
    """Clones must be structurally shaped like their source.

    They carried no case block and no form name at all — form.@name,
    form.case.@case_id and form.subcase_0.case.@case_id were null on every
    synthetic row — so a whole class of pipeline logic (entity joins, form-name
    filters) was untestable on synthetic data. Every entity-join defect this cohort
    hit therefore had to be found against production (connect-labs#1224).
    """
    from connect_labs.labs.synthetic.generator.fixtures.entities import plan_mirror_visits
    from connect_labs.labs.synthetic.generator.fixtures.manifest import LongitudinalSpec

    spec = LongitudinalSpec(
        mode="mirror",
        jitter_frac=0.0,
        transplant_pool=[
            {
                "owner": "flw_001",
                "start_date": "2026-01-01",
                "visits": [
                    {"day": 0, "values": {"form.w": 1000.0}, "form": "Child Registration Form"},
                    {"day": 7, "values": {"form.w": 1100.0}, "form": "Record Visit Details"},
                ],
            }
        ],
    )

    planned = plan_mirror_visits(spec, seed=1)

    assert len(planned) == 2
    # "Child Registration Form" is design B: form.case is the MOTHER, stable
    # across the series and never the baby's own id ...
    case_ids = {p.forced_values["form.case.@case_id"] for p in planned}
    assert len(case_ids) == 1
    assert case_ids != {planned[0].entity_id}
    # ... the baby lives on subcase_0 on every form, and the visit names it
    # again as child_case_id (test_conditional_paths.py, observed on 1487/1790)
    subs = {p.forced_values["form.subcase_0.case.@case_id"] for p in planned}
    assert subs == {planned[0].entity_id}
    assert "form.child_case_id" not in planned[0].forced_values, "a registration carries no child_case_id"
    assert planned[1].forced_values["form.child_case_id"] == planned[0].entity_id
    assert "form.kmc_beneficiary_case_id" not in planned[1].forced_values
    # and the form name is replayed
    assert [p.forced_values["form.@name"] for p in planned] == [
        "Child Registration Form",
        "Record Visit Details",
    ]


def test_design_a_sources_keep_the_baby_on_form_case():
    """ "Register KMC Beneficiary" is design A: the baby is form.case on every
    form, subcase_0 is a fresh throwaway case per visit, and visits name the
    baby again as kmc_beneficiary_case_id. Writing every source as one shape
    split design-B babies in two under the real key (BERI 613 -> 1,182)."""
    from connect_labs.labs.synthetic.generator.fixtures.entities import plan_mirror_visits
    from connect_labs.labs.synthetic.generator.fixtures.manifest import LongitudinalSpec

    spec = LongitudinalSpec(
        mode="mirror",
        jitter_frac=0.0,
        transplant_pool=[
            {
                "owner": "flw_001",
                "start_date": "2026-01-01",
                "visits": [
                    {"day": 0, "values": {"form.w": 1000.0}, "form": "Register KMC Beneficiary"},
                    {"day": 7, "values": {"form.w": 1100.0}, "form": "Record Visit Details"},
                    {"day": 14, "values": {"form.w": 1200.0}, "form": "Record Visit Details"},
                ],
            }
        ],
    )
    planned = plan_mirror_visits(spec, seed=1)
    baby = planned[0].entity_id
    assert {p.forced_values["form.case.@case_id"] for p in planned} == {baby}
    subs = [p.forced_values["form.subcase_0.case.@case_id"] for p in planned]
    assert len(set(subs)) == 3 and baby not in subs
    assert "form.kmc_beneficiary_case_id" not in planned[0].forced_values
    assert all(p.forced_values["form.kmc_beneficiary_case_id"] == baby for p in planned[1:])
    assert all("form.child_case_id" not in p.forced_values for p in planned)


def test_design_is_inferred_from_where_the_case_updates_live_when_names_are_unknown():
    from connect_labs.labs.synthetic.generator.fixtures.entities import _registration_design

    b_pool = [
        {
            "owner": "f",
            "start_date": "2026-01-01",
            "visits": [
                {"day": 0, "values": {"form.subcase_0.case.update.child_weight_birth": 1000.0}, "form": "Enrolment"},
                {"day": 7, "values": {"form.anthropometric.child_weight_visit": 1100.0}, "form": "Follow-up"},
            ],
        }
    ]
    a_pool = [
        {
            "owner": "f",
            "start_date": "2026-01-01",
            "visits": [
                {"day": 0, "values": {"form.case.update.child_weight_birth": 1000.0}, "form": "Enrolment"},
                {"day": 7, "values": {"form.anthropometric.child_weight_visit": 1100.0}, "form": "Follow-up"},
            ],
        }
    ]
    assert _registration_design(b_pool) == "B"
    assert _registration_design(a_pool) == "A"


def test_app_calculated_values_are_replayed_exactly_not_jittered():
    """Jitter is for measurements. A computed value encodes an identity.

    child_age must keep equalling visit_date - dob, and a visit counter must keep
    matching its position in the series. Jittering them was the largest remaining
    parity gap on every KMC opportunity — visit-counter median gap 1.0, child_age
    0.10-0.17. Regression for connect-labs#1225.
    """
    from connect_labs.labs.synthetic.generator.fixtures.entities import plan_mirror_visits
    from connect_labs.labs.synthetic.generator.fixtures.manifest import LongitudinalSpec

    spec = LongitudinalSpec(
        mode="mirror",
        jitter_frac=0.5,  # aggressive, so an un-protected field would visibly move
        transplant_pool=[
            {
                "owner": "flw_001",
                "start_date": "2026-01-01",
                "visits": [
                    {"day": 0, "values": {"form.visit_number": 1.0, "form.weight": 1000.0}},
                    {"day": 7, "values": {"form.visit_number": 2.0, "form.weight": 2000.0}},
                ],
            }
        ],
    )

    planned = plan_mirror_visits(spec, seed=7, no_jitter_paths={"form.visit_number"})

    counters = [p.forced_values["form.visit_number"] for p in planned]
    assert counters == [1.0, 2.0], "a computed counter must survive replay exactly"
    # the measured field is still free to move
    weights = [p.forced_values["form.weight"] for p in planned]
    assert weights != [1000.0, 2000.0] or spec.jitter_frac == 0
