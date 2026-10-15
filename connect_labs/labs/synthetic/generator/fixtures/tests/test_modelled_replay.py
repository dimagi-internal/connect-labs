"""A modelled pool is written out exactly as the case model sampled it.

The replay layer was built for the retired mirror mode, whose pool was the real cases:
it jittered every value, stamped each case's constants onto every visit, and filled any
select the case left blank from its marginal. On a modelled pool each of those undoes
something the case model learned from the source -- on KMC EHA (2026-10-15) the visit
weight sat on whole hundreds 99% of the time in the source and 18% in the clone, the
registration date appeared on 91% of registration forms against none, and the
follow-up's danger-sign checklist on three in four.
"""

from __future__ import annotations

import random

from connect_labs.labs.synthetic.generator.fixtures.entities import plan_mirror_visits
from connect_labs.labs.synthetic.generator.fixtures.fields import fill_form_json
from connect_labs.labs.synthetic.generator.fixtures.manifest import (
    BeneficiaryCohort,
    CategoricalDistribution,
    LongitudinalSpec,
)
from connect_labs.labs.synthetic.generator.fixtures.schema_loader import FormSchema, QuestionSpec

POOL = [
    {
        "owner": "flw_001",
        "start_date": "2026-03-02",
        "visits": [
            {"day": 0, "form": "Register KMC Beneficiary", "values": {"form.w": 1500.0, "form.bw": 1400.0}},
            {
                "day": 7,
                "form": "Record Visit Details",
                "values": {"form.w": 1600.0},
                "dates": {"form.reg_date": 0},
                "cats": {"form.ds.jaundice": "no"},
            },
            {"day": 14, "form": "Record Visit Details", "values": {"form.w": 1700.0}, "dates": {"form.reg_date": 0}},
        ],
    }
]


def test_a_modelled_pool_is_replayed_verbatim():
    planned = plan_mirror_visits(LongitudinalSpec(mode="modelled", transplant_pool=POOL), seed=3)

    assert [p.forced_values.get("form.w") for p in planned] == [1500.0, 1600.0, 1700.0]
    reg, first, second = (p.forced_values for p in planned)
    assert "form.bw" not in first, "a registration-only constant stays on the registration"
    assert "form.reg_date" not in reg, "a follow-up field stays off the registration"
    assert "form.ds.jaundice" not in second, "an answer the case left blank stays blank"


def test_a_field_the_pool_owns_is_never_filled_from_its_marginal():
    cohort = BeneficiaryCohort(
        id="c",
        size=1,
        progression="flat",
        field_distributions={
            "form.ds.jaundice": CategoricalDistribution(distribution="categorical", values={"no": 0.9, "yes": 0.1}),
            "form.other": CategoricalDistribution(distribution="categorical", values={"a": 1.0}),
        },
    )
    schema = FormSchema(
        questions=[
            QuestionSpec(json_path="form.ds.jaundice", kind="select", choices=["no", "yes"]),
            QuestionSpec(json_path="form.other", kind="select", choices=["a"]),
        ]
    )
    out = fill_form_json(
        schema=schema,
        cohort=cohort,
        anomalies_for_visit=[],
        rng=random.Random(1),
        forced_values={},
        mirror=True,
        owned_paths=frozenset({"form.ds.jaundice"}),
    )
    assert "jaundice" not in (out["form"].get("ds") or {})
    assert out["form"]["other"] == "a", "a field the pool does not model still fills"


def test_a_whole_number_is_written_whole():
    """CommCare submits "2", never "2.0"; a visit counter replayed as 2.0 read "2.0" and
    matched none of the source's "1".."5" (KMC GHI, 2026-10-15)."""
    schema = FormSchema(questions=[QuestionSpec(json_path="form.visit_number", kind="text")])
    cohort = BeneficiaryCohort(id="c", size=1, progression="flat", field_distributions={})
    out = fill_form_json(
        schema=schema,
        cohort=cohort,
        anomalies_for_visit=[],
        rng=random.Random(1),
        forced_values={"form.visit_number": 2.0, "form.w": 2512.5},
        mirror=True,
    )
    assert out["form"]["visit_number"] == 2 and isinstance(out["form"]["visit_number"], int)
    assert out["form"]["w"] == 2512.5


def test_a_field_is_filled_only_on_the_forms_the_source_records_it_on():
    """A registration question filled at its overall rate landed on follow-ups the app
    never shows it on (cause of premature birth on 100% of KMC follow-ups, 2026-10-15).
    The source's own per-form rate decides it."""
    from connect_labs.labs.synthetic.generator.fixtures.profiler import _profile_presence_by_form

    source = [
        {"form_json": {"form": {"@name": "Register", "cause": "infection" if i % 2 else "unknown"}}} for i in range(40)
    ] + [{"form_json": {"form": {"@name": "Visit", "x": "1"}}} for _ in range(60)]
    by_form = _profile_presence_by_form(source)
    assert by_form["form.cause"] == {"Register": 1.0}
    cohort = BeneficiaryCohort(
        id="c",
        size=1,
        progression="flat",
        presence_by_form=by_form,
        field_distributions={
            "form.cause": CategoricalDistribution(
                distribution="categorical", values={"infection": 0.5, "unknown": 0.5}
            )
        },
    )
    schema = FormSchema(
        questions=[QuestionSpec(json_path="form.cause", kind="select", choices=["infection", "unknown"])]
    )
    rng = random.Random(3)

    def filled(form):
        out = fill_form_json(
            schema=schema,
            cohort=cohort,
            anomalies_for_visit=[],
            rng=rng,
            forced_values={"form.@name": form},
            mirror=True,
        )
        return "cause" in out["form"]

    assert not any(filled("Visit") for _ in range(200))
    assert all(filled("Register") for _ in range(50))
