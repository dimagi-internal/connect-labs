import random

from commcare_connect.labs.synthetic.generator.fixtures.fields import _outlier, fill_form_json
from commcare_connect.labs.synthetic.generator.fixtures.manifest import (
    Anomaly,
    BeneficiaryCohort,
    BinaryDistribution,
    FlwPersona,
    MeanStddev,
    NormalDistribution,
    UniformDistribution,
)
from commcare_connect.labs.synthetic.generator.fixtures.schema_loader import FormSchema, QuestionSpec


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


def test_fill_form_json_emits_repeat_group_as_array():
    """A cohort repeat group materializes as a JSON array of sub-records — faithful
    to how CommCare submits repeats (form.X = [ {child: val}, ... ]) — issue #670 #6."""
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import CategoricalDistribution, RepeatGroupSpec

    rng = random.Random(7)
    cohort = BeneficiaryCohort(
        id="primary",
        size=50,
        field_distributions={"form.mother_age": NormalDistribution(mean=27, stddev=4, lo=15, hi=45)},
        progression="flat",
        repeat_groups={
            "form.children": RepeatGroupSpec(
                count={2: 1.0},  # always exactly two instances -> deterministic assert
                field_distributions={
                    "child_weight": NormalDistribution(mean=1800, stddev=200, lo=1000, hi=2500),
                    "sex": CategoricalDistribution(distribution="categorical", values={"m": 0.5, "f": 0.5}),
                },
            )
        },
    )
    schema = FormSchema(questions=[QuestionSpec("form.mother_age", "int")])
    out = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng)

    children = out["form"]["children"]
    assert isinstance(children, list), "a repeat group must serialize as a list, not a single object"
    assert len(children) == 2
    for el in children:
        assert isinstance(el, dict)
        assert 1000 <= el["child_weight"] <= 2500
        assert el["sex"] in ("m", "f")
    assert "mother_age" in out["form"], "scalar fields outside the repeat still fill"


def test_fill_form_json_repeat_array_wins_over_scalar_leaf():
    """If the schema also lists a flat leaf under the repeat base, the array wins —
    the base path must be a list, never a nested scalar dict."""
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import RepeatGroupSpec

    rng = random.Random(3)
    cohort = BeneficiaryCohort(
        id="p",
        size=10,
        field_distributions={},
        progression="flat",
        repeat_groups={
            "form.visits": RepeatGroupSpec(
                count={1: 1.0}, field_distributions={"weight": NormalDistribution(mean=10, stddev=1)}
            )
        },
    )
    schema = FormSchema(questions=[QuestionSpec("form.visits.weight", "decimal")])
    out = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng)
    assert isinstance(out["form"]["visits"], list)
    assert len(out["form"]["visits"]) == 1
    assert "weight" in out["form"]["visits"][0]


def test_fill_form_json_repeat_count_zero_yields_empty_list():
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import RepeatGroupSpec

    rng = random.Random(1)
    cohort = BeneficiaryCohort(
        id="p",
        size=10,
        field_distributions={},
        progression="flat",
        repeat_groups={
            "form.kids": RepeatGroupSpec(
                count={0: 1.0}, field_distributions={"w": NormalDistribution(mean=1, stddev=0.1)}
            )
        },
    )
    out = fill_form_json(schema=FormSchema(questions=[]), cohort=cohort, anomalies_for_visit=[], rng=rng)
    assert out["form"]["kids"] == []


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


def test_outlier_on_binary_field_returns_rare_outcome():
    # A field_outlier on a binary field used to raise TypeError (ace#762).
    # The outlier is the rare/failed outcome: opposite the expected majority.
    rng = random.Random(0)
    # Success is the majority (rate >= 0.5) -> outlier is the failure (0.0).
    majority_success = BinaryDistribution(distribution="binary", rate=0.8)
    assert _outlier(majority_success, rng) == 0.0
    # Failure is the majority (rate < 0.5) -> outlier is the rare success (1.0).
    majority_failure = BinaryDistribution(distribution="binary", rate=0.2)
    assert _outlier(majority_failure, rng) == 1.0
    # Boundary: rate == 0.5 counts as success-majority -> failure outlier.
    assert _outlier(BinaryDistribution(distribution="binary", rate=0.5), rng) == 0.0


def test_binary_select_field_honors_rate_within_tolerance():
    # ace#773: a binary distribution on a select question must render the choice
    # at the requested rate (not a 50/50 default), as the strings "yes"/"no".
    schema = FormSchema(questions=[QuestionSpec("form.slept_under_net", "select", choices=["yes", "no"])])
    cohort = BeneficiaryCohort(
        id="endemic_district",
        size=4000,
        field_distributions={
            "form.slept_under_net": BinaryDistribution(distribution="binary", rate=0.72),
        },
        progression="flat",
    )
    rng = random.Random(7)
    values = [
        fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng)["form"]["slept_under_net"]
        for _ in range(4000)
    ]
    # Values are the choice strings, never floats.
    assert all(v in ("yes", "no") for v in values), "binary select must render yes/no, not a float"
    yes_rate = sum(v == "yes" for v in values) / len(values)
    assert 0.69 <= yes_rate <= 0.75, f"expected ~0.72 yes; got {yes_rate}"


def test_binary_leaf_key_resolves_to_question():
    # ace#773 root case: the manifest keyed the distribution by the BARE LEAF
    # ("slept_under_net") while the schema question's json_path is the full dotted
    # path ("form.slept_under_net"). Leaf-resolution must still apply the rate.
    schema = FormSchema(questions=[QuestionSpec("form.slept_under_net", "select", choices=["yes", "no"])])
    cohort = BeneficiaryCohort(
        id="endemic_district",
        size=4000,
        field_distributions={
            "slept_under_net": BinaryDistribution(distribution="binary", rate=0.72),
        },
        progression="flat",
    )
    rng = random.Random(7)
    values = []
    for _ in range(4000):
        out = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng)
        values.append(out["form"]["slept_under_net"])
    assert all(v in ("yes", "no") for v in values), "leaf-keyed binary select must render yes/no"
    yes_rate = sum(v == "yes" for v in values) / len(values)
    assert 0.69 <= yes_rate <= 0.75, f"expected ~0.72 yes from leaf-keyed dist; got {yes_rate}"
    # The leaf key was CONSUMED by the schema question — it must not also be
    # orphan-written as a bare top-level "slept_under_net" float.
    assert "slept_under_net" not in out, "consumed leaf key must not be double-written as an orphan"


def test_ambiguous_leaf_key_does_not_guess():
    # Two questions share the leaf "status". An effective key keyed only by that
    # bare leaf is ambiguous, so leaf-resolution must NOT guess: both questions
    # fall through to their per-kind default, and the ambiguous key is left to
    # the orphan-write loop (documented behavior: emitted as a top-level float).
    schema = FormSchema(
        questions=[
            QuestionSpec("form.a.status", "select", choices=["yes", "no"]),
            QuestionSpec("form.b.status", "select", choices=["yes", "no"]),
        ]
    )
    cohort = BeneficiaryCohort(
        id="primary",
        size=10,
        field_distributions={
            "status": BinaryDistribution(distribution="binary", rate=0.9),
        },
        progression="flat",
    )
    rng = random.Random(0)
    out = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng)
    # No crash; both questions resolved to their select default (a valid choice).
    assert out["form"]["a"]["status"] in ("yes", "no")
    assert out["form"]["b"]["status"] in ("yes", "no")
    # The ambiguous key was NOT consumed, so it remains an orphan top-level float.
    assert out["status"] in (0.0, 1.0)


def test_categorical_value_respects_frequencies():
    from commcare_connect.labs.synthetic.generator.fixtures.fields import _categorical_value
    from commcare_connect.labs.synthetic.generator.fixtures.manifest import CategoricalDistribution

    rng = random.Random(0)
    d = CategoricalDistribution(distribution="categorical", values={"a": 0.9, "b": 0.1})
    draws = [_categorical_value(d, rng) for _ in range(2000)]
    assert 0.85 < draws.count("a") / len(draws) < 0.95


def test_null_rate_one_omits_field():
    from commcare_connect.labs.synthetic.generator.fixtures.fields import fill_form_json

    schema = FormSchema(questions=[QuestionSpec(json_path="form.w", kind="decimal")])
    cohort = BeneficiaryCohort(
        id="primary",
        size=10,
        progression="flat",
        field_distributions={"form.w": NormalDistribution(mean=1.0, stddev=0.1, null_rate=1.0)},
    )
    out = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=random.Random(1))
    assert "w" not in out.get("form", {})


def test_fill_form_json_uses_correlated_values():
    schema = FormSchema(
        questions=[
            QuestionSpec(json_path="form.a", kind="decimal"),
            QuestionSpec(json_path="form.b", kind="decimal"),
        ]
    )
    cohort = BeneficiaryCohort(
        id="primary",
        size=10,
        progression="flat",
        field_distributions={
            "form.a": NormalDistribution(mean=1.0, stddev=0.1),
            "form.b": NormalDistribution(mean=2.0, stddev=0.1),
        },
    )
    out = fill_form_json(
        schema=schema,
        cohort=cohort,
        anomalies_for_visit=[],
        rng=random.Random(1),
        correlated_values={"form.a": 42.0, "form.b": 99.0},
    )
    assert out["form"]["a"] == 42.0
    assert out["form"]["b"] == 99.0


def test_correlated_value_still_omitted_by_null_rate():
    # A correlated path whose distribution has null_rate=1.0 must still be omitted
    # even when the correlated_values dict supplies a concrete value.
    schema = FormSchema(questions=[QuestionSpec(json_path="form.x", kind="decimal")])
    cohort = BeneficiaryCohort(
        id="primary",
        size=10,
        progression="flat",
        field_distributions={"form.x": NormalDistribution(mean=5.0, stddev=0.5, null_rate=1.0)},
    )
    out = fill_form_json(
        schema=schema,
        cohort=cohort,
        anomalies_for_visit=[],
        rng=random.Random(1),
        correlated_values={"form.x": 123.0},
    )
    # null_rate=1.0 must cause the field to be omitted regardless of the correlated value.
    assert "x" not in out.get("form", {}), "correlated path with null_rate=1.0 must be omitted"


def test_fill_form_json_anomaly_on_binary_field_does_not_raise():
    # Regression for ace#762: routing a field_outlier through a binary-distributed
    # field used to crash fill_form_json with TypeError. It now yields the rare
    # outcome (opposite the expected majority) instead.
    schema = FormSchema(questions=[QuestionSpec("form.vitamin_a_given", "int")])
    cohort = BeneficiaryCohort(
        id="primary",
        size=10,
        field_distributions={
            # rate high -> success (1) is the majority outcome.
            "form.vitamin_a_given": BinaryDistribution(distribution="binary", rate=0.9),
        },
        progression="flat",
    )
    anomaly = Anomaly(
        id="vit_a_outlier",
        type="field_outlier",
        flw_ids=["ravi"],
        field_path="form.vitamin_a_given",
        week=5,
    )
    out = fill_form_json(
        schema=schema,
        cohort=cohort,
        anomalies_for_visit=[anomaly],
        rng=random.Random(7),
    )
    # Success (1) is the majority for rate=0.9, so the outlier is the rare
    # failure (0). The "int" question kind rounds the 0.0 draw to int 0.
    assert out["form"]["vitamin_a_given"] == 0


def test_normal_draw_is_clamped_to_bounds():
    """A bounded Normal never emits values outside [lo, hi] — kills negative ages."""
    from commcare_connect.labs.synthetic.generator.fixtures.fields import _draw

    # child_age-like: mean 13.5, big stddev that would otherwise go negative.
    dist = NormalDistribution(mean=13.5, stddev=12.9, lo=0.0, hi=60.0)
    rng = random.Random(1)
    draws = [_draw(dist, rng) for _ in range(2000)]
    assert min(draws) >= 0.0
    assert max(draws) <= 60.0
    # Without clamping this distribution produces negatives ~15% of the time.
    assert any(d == 0.0 for d in draws)


def test_outlier_never_flips_sign_on_nonnegative_field():
    """A seeded outlier on a non-negative field stays >= 0 (no negative-age outliers)."""
    dist = NormalDistribution(mean=2.0, stddev=3.0, lo=0.0, hi=20.0)
    rng = random.Random(3)
    assert all(_outlier(dist, rng) >= 0.0 for _ in range(500))


def test_unbounded_normal_still_works():
    """Distributions without lo/hi are unchanged (back-compat)."""
    from commcare_connect.labs.synthetic.generator.fixtures.fields import _draw

    dist = NormalDistribution(mean=100.0, stddev=1.0)
    rng = random.Random(5)
    vals = [_draw(dist, rng) for _ in range(100)]
    assert 90 < sum(vals) / len(vals) < 110


def test_text_fields_are_fabricated_not_stubbed():
    """Text fields with no distribution get plausible values, never 'sample-N'."""
    from commcare_connect.labs.synthetic.generator.fixtures.fields import _COUNTRIES, _fabricate_text

    rng = random.Random(11)
    schema = FormSchema(
        questions=[
            QuestionSpec("form.mother_name", "text"),
            QuestionSpec("form.location.country", "text"),
            QuestionSpec("form.location.village", "text"),
            QuestionSpec("form.phone_number", "text"),
            QuestionSpec("form.notes", "text"),
            QuestionSpec("form.kind_select", "select"),  # no choices
        ]
    )
    cohort = BeneficiaryCohort(id="primary", size=10, field_distributions={}, progression="flat")
    out = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=rng)

    def flat(d, p=""):
        for k, v in d.items():
            np = f"{p}.{k}" if p else k
            if isinstance(v, dict):
                yield from flat(v, np)
            else:
                yield np, v

    vals = dict(flat(out))
    assert not any(str(v).startswith("sample-") for v in vals.values())
    assert vals["form.location.country"] in _COUNTRIES
    assert " " in vals["form.mother_name"]  # first + last
    assert vals["form.kind_select"] in {"yes", "no"}

    # leaf heuristics
    assert _fabricate_text("mothers_phone_number", random.Random(1)).isdigit()
    assert _fabricate_text("country", random.Random(1)) in _COUNTRIES
