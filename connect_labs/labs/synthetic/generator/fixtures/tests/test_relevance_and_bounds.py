"""#1181: the generator produced records the app could never have accepted.

Reported live on labs-only opp 10037 (216 visits):

    "meeting_conducted": "yes",
    "community_meeting":      {"male_attendance": 32.213, "members_with_disability": -0.776},
    "meeting_did_not_happen": {"reason": "conflicting_event"}

Both mutually-exclusive relevance groups populated, a count of people with a
fractional part, and a negative headcount despite the manifest declaring bounds.
"""

import random

import pytest
from pydantic import ValidationError

from connect_labs.labs.synthetic.generator.fixtures.fields import fill_form_json
from connect_labs.labs.synthetic.generator.fixtures.manifest import (
    Anomaly,
    BeneficiaryCohort,
    CategoricalDistribution,
    NormalDistribution,
    RelevanceRule,
)
from connect_labs.labs.synthetic.generator.fixtures.schema_loader import FormSchema, QuestionSpec


def _spark_schema():
    """The shape from the report: one controller, two exclusive groups under it."""
    return FormSchema(
        questions=[
            QuestionSpec("form.meeting_conducted", "select", choices=["yes", "no"]),
            QuestionSpec("form.community_meeting.meeting_type", "select", choices=["community_meeting"]),
            QuestionSpec("form.community_meeting.male_attendance", "int"),
            QuestionSpec("form.meeting_did_not_happen.reason", "select", choices=["conflicting_event"]),
        ]
    )


def _spark_cohort(*, held: bool, relevance=True):
    groups = {}
    if relevance:
        groups = {
            "form.community_meeting": RelevanceRule(when="form.meeting_conducted", equals="yes"),
            "form.meeting_did_not_happen": RelevanceRule(when="form.meeting_conducted", equals="no"),
        }
    return BeneficiaryCohort(
        id="cbf",
        size=10,
        field_distributions={
            "form.meeting_conducted": CategoricalDistribution(
                distribution="categorical", values={"yes": 1.0} if held else {"no": 1.0}
            ),
            "form.community_meeting.meeting_type": CategoricalDistribution(
                distribution="categorical", values={"community_meeting": 1.0}
            ),
            "form.community_meeting.male_attendance": NormalDistribution(mean=32.0, stddev=3.0),
            "form.meeting_did_not_happen.reason": CategoricalDistribution(
                distribution="categorical", values={"conflicting_event": 1.0}
            ),
        },
        progression="flat",
        relevance_groups=groups,
    )


def _fill(cohort, seed=7):
    return fill_form_json(
        schema=_spark_schema(),
        cohort=cohort,
        anomalies_for_visit=[],
        rng=random.Random(seed),
    )


class TestRelevanceGating:
    def test_a_not_held_meeting_carries_no_meeting_type(self):
        """The defect that made the demo's whole point unreproducible: a payability
        rule ANDing `meeting_conducted='yes'` with `meeting_type='community_meeting'`
        overcounted by the entire non-occurrence rate, because meeting_type was
        present on not-held records too."""
        out = _fill(_spark_cohort(held=False))

        assert out["form"]["meeting_conducted"] == "no"
        assert "community_meeting" not in out["form"]
        assert out["form"]["meeting_did_not_happen"]["reason"] == "conflicting_event"

    def test_a_held_meeting_carries_no_did_not_happen_block(self):
        out = _fill(_spark_cohort(held=True))

        assert out["form"]["meeting_conducted"] == "yes"
        assert out["form"]["community_meeting"]["meeting_type"] == "community_meeting"
        assert "meeting_did_not_happen" not in out["form"]

    def test_without_relevance_groups_both_groups_still_fill(self):
        """The old behaviour, kept explicit: a cohort that declares no relevance is
        unchanged, so this is opt-in and no existing manifest shifts under it."""
        out = _fill(_spark_cohort(held=False, relevance=False))

        assert "community_meeting" in out["form"]
        assert "meeting_did_not_happen" in out["form"]

    def test_a_list_of_accepted_values_works(self):
        cohort = _spark_cohort(held=True)
        cohort.relevance_groups["form.community_meeting"] = RelevanceRule(
            when="form.meeting_conducted", equals=["yes", "partially"]
        )
        assert "community_meeting" in _fill(cohort)["form"]

    def test_an_unanswered_controller_leaves_the_group_relevant(self):
        """Fail toward the old behaviour: a misdeclared rule can over-fill a record,
        never silently empty one."""
        cohort = _spark_cohort(held=True)
        cohort.relevance_groups["form.community_meeting"] = RelevanceRule(
            when="form.a_question_that_is_never_asked", equals="yes"
        )
        assert "community_meeting" in _fill(cohort)["form"]

    def test_gating_holds_across_seeds(self):
        for seed in range(25):
            out = _fill(_spark_cohort(held=False), seed=seed)
            assert "community_meeting" not in out["form"], seed


class TestBoundsAndIntegers:
    def test_min_and_max_are_accepted_as_aliases_for_lo_hi(self):
        """They were silently swallowed by pydantic's default extra="ignore", so a
        manifest that carefully bounded a field produced unbounded draws."""
        d = NormalDistribution(mean=2.0, stddev=5.0, min=0.0, max=10.0)
        assert (d.lo, d.hi) == (0.0, 10.0)

    def test_an_unknown_key_now_raises_instead_of_vanishing(self):
        with pytest.raises(ValidationError):
            NormalDistribution(mean=2.0, stddev=1.0, maximum=10.0)

    def test_bounds_actually_clamp_a_wild_draw(self):
        schema = FormSchema(questions=[QuestionSpec("form.n", "decimal")])
        cohort = BeneficiaryCohort(
            id="c",
            size=1,
            field_distributions={"form.n": NormalDistribution(mean=1.0, stddev=50.0, min=0.0, max=10.0)},
            progression="flat",
        )
        for seed in range(40):
            v = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=random.Random(seed))["form"][
                "n"
            ]
            assert 0.0 <= v <= 10.0, (seed, v)

    def test_integer_rounds_a_headcount_on_an_orphan_path(self):
        """The HQ schema's `int` kind only reaches questions the app declares, so a
        manifest-only path fell through to round(float, 3) — 32.213 people."""
        schema = FormSchema(questions=[])
        cohort = BeneficiaryCohort(
            id="c",
            size=1,
            field_distributions={
                "form.community_meeting.male_attendance": NormalDistribution(
                    mean=32.0, stddev=3.0, min=0, integer=True
                )
            },
            progression="flat",
        )
        for seed in range(20):
            v = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=random.Random(seed))["form"][
                "community_meeting"
            ]["male_attendance"]
            assert isinstance(v, int), (seed, v)

    def test_without_integer_the_old_rounding_is_unchanged(self):
        schema = FormSchema(questions=[])
        cohort = BeneficiaryCohort(
            id="c",
            size=1,
            field_distributions={"form.x": NormalDistribution(mean=32.0, stddev=3.0)},
            progression="flat",
        )
        v = fill_form_json(schema=schema, cohort=cohort, anomalies_for_visit=[], rng=random.Random(1))["form"]["x"]
        assert isinstance(v, float)


class TestAnomaliesThatUsedToNoOp:
    def _manifest(self, **anomaly_kwargs):
        from connect_labs.labs.synthetic.generator.fixtures.engine import _anomalies_at

        class _M:
            anomalies = [Anomaly(id="a1", type="field_outlier", flw_ids=["flw_a"], **anomaly_kwargs)]

        return _M, _anomalies_at

    def test_no_week_means_every_week_not_no_week(self):
        """It used to mean none: the loop fell through and returned [], so an
        anomaly declared with only flw_ids silently no-opped and the demo shipped
        without the defect it existed to showcase."""
        M, _anomalies_at = self._manifest(field_path="form.x")
        assert [_anomalies_at(w, "flw_a", M) != [] for w in range(4)] == [True] * 4

    def test_an_explicit_week_still_narrows(self):
        M, _anomalies_at = self._manifest(field_path="form.x", week=2)
        assert [_anomalies_at(w, "flw_a", M) != [] for w in range(4)] == [False, False, True, False]

    def test_week_zero_is_not_treated_as_unset(self):
        """`if a.week and ...` made week 0 fall through to the else branch — the
        classic falsy-zero bug, now `is not None`."""
        M, _anomalies_at = self._manifest(field_path="form.x", week=0)
        assert _anomalies_at(0, "flw_a", M) != []
        assert _anomalies_at(1, "flw_a", M) == []

    def test_an_explicit_weeks_list_still_narrows(self):
        M, _anomalies_at = self._manifest(field_path="form.x", weeks=[1, 3])
        assert [_anomalies_at(w, "flw_a", M) != [] for w in range(4)] == [False, True, False, True]

    def test_a_stale_flw_id_is_reported_rather_than_vanishing(self):
        from connect_labs.labs.synthetic.generator.fixtures.engine import warn_on_anomalies_that_match_nothing

        M, _ = self._manifest(field_path="form.x")
        problems = warn_on_anomalies_that_match_nothing(M, ["flw_b", "flw_c"])
        assert any("flw_a" in p for p in problems)

    def test_a_matching_id_reports_nothing(self):
        from connect_labs.labs.synthetic.generator.fixtures.engine import warn_on_anomalies_that_match_nothing

        M, _ = self._manifest(field_path="form.x")
        assert warn_on_anomalies_that_match_nothing(M, ["flw_a"]) == []
