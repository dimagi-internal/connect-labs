import random

from commcare_connect.labs.synthetic.generator.fields import fill_form_json
from commcare_connect.labs.synthetic.generator.manifest import (
    Anomaly,
    BeneficiaryCohort,
    FlwPersona,
    MeanStddev,
    NormalDistribution,
    UniformDistribution,
)
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


def _persona(field_overrides=None):
    return FlwPersona(
        id="amina",
        archetype="struggling",
        accuracy_distribution=MeanStddev(mean=0.8, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.9, stddev=0.03),
        flag_rate=0.1,
        field_overrides=field_overrides or {},
    )


def test_persona_field_overrides_replace_cohort_distribution():
    # Cohort weight ~ N(12.4, 0.5); persona overrides with a tight N(20, 0.1).
    # Drawing many samples should land near 20, not 12.4.
    persona = _persona({"form.weight_kg": NormalDistribution(mean=20.0, stddev=0.1)})
    rng = random.Random(0)
    samples = []
    for _ in range(50):
        out = fill_form_json(
            schema=_schema(),
            cohort=_cohort(),
            anomalies_for_visit=[],
            rng=rng,
            persona=persona,
        )
        samples.append(out["form"]["weight_kg"])
    avg = sum(samples) / len(samples)
    assert 19.5 < avg < 20.5, f"expected ~20 with override; got {avg}"


def test_persona_without_overrides_matches_cohort():
    persona = _persona()
    a = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[],
        rng=random.Random(7),
        persona=persona,
    )
    b = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[],
        rng=random.Random(7),
    )
    assert a == b, "empty field_overrides should be a no-op"


def test_persona_override_only_applies_to_named_path():
    # Override one field; the other still comes from the cohort.
    persona = _persona({"form.weight_kg": NormalDistribution(mean=99.0, stddev=0.01)})
    out = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[],
        rng=random.Random(7),
        persona=persona,
    )
    assert abs(out["form"]["weight_kg"] - 99.0) < 0.5
    # muac is not overridden — should be near cohort mean 13.2.
    assert 12.0 < out["form"]["muac_cm"] < 14.5


def test_persona_uniform_override_works_with_transform():
    # Skew gender on a single persona via a uniform distribution + transform.
    persona = _persona(
        {"form.gender": UniformDistribution(distribution="uniform", low=0, high=0.3, transform="gender")}
    )
    cohort = BeneficiaryCohort(
        id="primary",
        size=10,
        field_distributions={
            "form.gender": UniformDistribution(distribution="uniform", low=0, high=1.0, transform="gender")
        },
        progression="flat",
    )
    schema = FormSchema(questions=[QuestionSpec("form.gender", "select", choices=["male", "female"])])
    rng = random.Random(0)
    samples = [
        fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng, persona=persona)["form"][
            "gender"
        ]
        for _ in range(200)
    ]
    female_rate = sum(s == "female" for s in samples) / len(samples)
    # high=0.3 means values < 0.5 ~ always (so transform → "male"); female_rate ≈ 0.
    assert female_rate < 0.1, f"persona override should skew female rate low; got {female_rate}"
