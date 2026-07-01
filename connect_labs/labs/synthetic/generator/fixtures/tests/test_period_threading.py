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
