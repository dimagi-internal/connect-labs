"""A cohort is a named set of opportunities, and its membership IS the grant.

The twelve KMC opportunities span three programmes inside one organisation, so
no programme contains the cohort -- which is why the cohort is its own object
rather than a programme id.
"""

import pytest

from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkCohortMember

pytestmark = pytest.mark.django_db


def _cohort(**kwargs):
    defaults = {"name": "KMC", "organization_id": "dimagi-kmc"}
    return BenchmarkCohort.objects.create(**{**defaults, **kwargs})


def test_a_cohort_knows_its_opportunities():
    cohort = _cohort()
    for opp in (523, 874, 1487):
        BenchmarkCohortMember.objects.create(cohort=cohort, opportunity_id=opp)
    assert cohort.opportunity_ids == {523, 874, 1487}


def test_defaults_are_the_conservative_ones():
    """Publication is explicit, and the disclosure thresholds start strict."""
    cohort = _cohort()
    assert cohort.auto_publish_on_completion is False
    assert cohort.min_peers == 5
    assert cohort.min_denominator == 25


def test_thresholds_are_tunable_per_cohort_without_a_deploy():
    cohort = _cohort(min_peers=3, min_denominator=10)
    assert (cohort.min_peers, cohort.min_denominator) == (3, 10)


def test_for_opportunity_finds_every_cohort_the_opp_belongs_to():
    a, b, c = _cohort(name="All KMC"), _cohort(name="Uganda only"), _cohort(name="Unrelated")
    BenchmarkCohortMember.objects.create(cohort=a, opportunity_id=874)
    BenchmarkCohortMember.objects.create(cohort=b, opportunity_id=874)
    BenchmarkCohortMember.objects.create(cohort=c, opportunity_id=999)
    assert set(BenchmarkCohort.for_opportunity(874).values_list("name", flat=True)) == {
        "All KMC",
        "Uganda only",
    }


def test_an_opportunity_in_no_cohort_matches_nothing():
    _cohort()
    assert list(BenchmarkCohort.for_opportunity(874)) == []


def test_an_opportunity_cannot_join_the_same_cohort_twice():
    from django.db import IntegrityError

    cohort = _cohort()
    BenchmarkCohortMember.objects.create(cohort=cohort, opportunity_id=874)
    with pytest.raises(IntegrityError):
        BenchmarkCohortMember.objects.create(cohort=cohort, opportunity_id=874)


def test_a_cohort_cannot_be_configured_below_the_min_peers_floor():
    """0 would publish a figure no opportunity contributed. The DATABASE has to
    hold this: no code path here calls `full_clean()`."""
    from django.db import IntegrityError

    with pytest.raises(IntegrityError):
        _cohort(min_peers=0)


def test_a_cohort_may_turn_the_peer_floor_off():
    """1 is allowed: the floor is the cohort's setting (Jonathan, 2026-09-18)."""
    assert _cohort(min_peers=1, min_denominator=0).min_peers == 1


def test_the_floor_is_also_a_validator_so_a_form_says_so():
    from django.core.exceptions import ValidationError

    cohort = BenchmarkCohort(name="KMC", organization_id="dimagi-kmc", min_peers=0)
    with pytest.raises(ValidationError):
        cohort.full_clean()
