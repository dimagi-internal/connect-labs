import random

from commcare_connect.labs.synthetic.generator.fields import fill_form_json
from commcare_connect.labs.synthetic.generator.manifest import Anomaly, BeneficiaryCohort, NormalDistribution
from commcare_connect.labs.synthetic.generator.schema_loader import FormSchema, QuestionSpec


def _schema():
    return FormSchema(
        questions=[
            QuestionSpec("form.weight_kg", "decimal"),
            QuestionSpec("form.muac_cm", "decimal"),
            QuestionSpec("form.kmc_status", "select", choices=["active", "inactive"]),
        ]
    )


def _cohort():
    return BeneficiaryCohort(
        id="primary",
        size=100,
        field_distributions={
            "form.weight_kg": NormalDistribution(mean=12.4, stddev=0.5),
            "form.muac_cm": NormalDistribution(mean=13.2, stddev=0.3),
        },
        progression="flat",
    )


def test_fill_form_json_returns_a_value_for_every_question():
    rng = random.Random(7)
    out = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[],
        rng=rng,
    )
    # fill_form_json builds a nested dict from dotted question paths,
    # so "form.weight_kg" lands at out["form"]["weight_kg"].
    assert "weight_kg" in out["form"]
    assert "muac_cm" in out["form"]
    assert out["form"]["kmc_status"] in ("active", "inactive")


def test_fill_form_json_applies_anomaly_outlier():
    rng = random.Random(7)
    anomaly = Anomaly(
        id="weight_outlier",
        type="field_outlier",
        flw_ids=["ravi"],
        field_path="form.weight_kg",
        week=5,
    )
    out = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[anomaly],
        rng=rng,
    )
    # Anomaly outliers are >= 4 stddevs from cohort mean
    assert abs(out["form"]["weight_kg"] - 12.4) >= 4 * 0.5


def test_fill_form_json_is_deterministic():
    a = fill_form_json(schema=_schema(), cohort=_cohort(), anomalies_for_visit=[], rng=random.Random(7))
    b = fill_form_json(schema=_schema(), cohort=_cohort(), anomalies_for_visit=[], rng=random.Random(7))
    assert a == b
