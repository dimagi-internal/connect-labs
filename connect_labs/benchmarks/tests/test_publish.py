"""Publication reads a completed run's frozen snapshot and recomputes nothing."""

import pytest

from connect_labs.benchmarks import publish as publish_module
from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkValue
from connect_labs.benchmarks.publish import publish_benchmark

pytestmark = pytest.mark.django_db

SNAPSHOT = {
    "as_of": "2026-09-11",
    "series": {
        "N": {
            "opportunity": {
                "N08": [
                    {"opportunity_id": 500 + i, "value": 50.0 + i, "denominator": 100, "suppressed": False}
                    for i in range(6)
                ]
            },
            "opportunity_month": {
                "N08": {
                    "2026-01": [
                        {"opportunity_id": 500 + i, "value": 40.0 + i, "denominator": 100, "suppressed": False}
                        for i in range(6)
                    ],
                    "2026-02": [
                        {"opportunity_id": 500 + i, "value": 45.0 + i, "denominator": 100, "suppressed": False}
                        for i in range(6)
                    ],
                }
            },
        }
    },
}

# A second point indicator, so a test can prove tie_salt varies BETWEEN indicators.
SNAPSHOT_TWO_INDICATORS = {
    "as_of": "2026-09-11",
    "series": {
        "N": {
            "opportunity": {
                "N08": [
                    {"opportunity_id": 500 + i, "value": 50.0 + i, "denominator": 100, "suppressed": False}
                    for i in range(6)
                ],
                "N09": [
                    {"opportunity_id": 500 + i, "value": 10.0 + i, "denominator": 100, "suppressed": False}
                    for i in range(6)
                ],
            },
            "opportunity_month": {},
        }
    },
}


def _cohort():
    cohort = BenchmarkCohort.objects.create(name="KMC", organization_id="dimagi-kmc")
    for i in range(6):
        cohort.members.create(opportunity_id=500 + i)
    return cohort


def test_it_writes_point_and_series_values():
    pub = publish_benchmark(
        _cohort(),
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    points = BenchmarkValue.objects.filter(publication=pub, period__isnull=True)
    series = BenchmarkValue.objects.filter(publication=pub, period__isnull=False)
    assert points.count() == 6
    assert series.count() == 12
    assert set(series.values_list("period", flat=True)) == {"2026-01", "2026-02"}


def test_it_stores_provenance_while_the_public_projection_stays_clean():
    pub = publish_benchmark(
        _cohort(),
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    identified = BenchmarkValue.objects.filter(publication=pub).with_source()
    assert {r.opportunity_id for r in identified} == {500, 501, 502, 503, 504, 505}
    assert all("opportunity_id" not in r.to_public() for r in BenchmarkValue.objects.filter(publication=pub))


def test_an_opportunity_outside_the_cohort_is_never_published():
    """The snapshot spans the whole report; only cohort members may be benchmarked."""
    cohort = BenchmarkCohort.objects.create(name="Partial", organization_id="dimagi-kmc")
    for i in range(5):
        cohort.members.create(opportunity_id=500 + i)
    pub = publish_benchmark(
        cohort,
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    published = {r.opportunity_id for r in BenchmarkValue.objects.filter(publication=pub).with_source()}
    assert 505 not in published, "a non-member opportunity was benchmarked"


def test_a_cohort_too_small_after_the_rules_publishes_nothing():
    cohort = BenchmarkCohort.objects.create(name="Tiny", organization_id="dimagi-kmc")
    for i in range(3):
        cohort.members.create(opportunity_id=500 + i)
    pub = publish_benchmark(
        cohort,
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    assert BenchmarkValue.objects.filter(publication=pub).count() == 0


def test_publication_is_immutable_so_republishing_makes_a_new_one():
    cohort = _cohort()
    kwargs = dict(
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    first = publish_benchmark(cohort, **kwargs)
    second = publish_benchmark(cohort, **kwargs)
    assert first.pk != second.pk
    assert BenchmarkValue.objects.filter(publication=first).count() == 6 + 12


def test_tie_salt_varies_per_indicator(monkeypatch):
    """tie_salt is what stops tied peers holding the same index in every indicator.

    disclosure.py can only reject an EMPTY salt -- it has no way to know whether
    the salt actually differs across indicators within one publication. That
    contract belongs to the publisher, so it must be tested here: capture every
    tie_salt the publisher actually passes to anonymise_point and assert there
    is one distinct value per indicator, not one constant value reused.
    """
    captured_salts = []
    real_anonymise_point = publish_module.anonymise_point

    def _spy(observations, *, min_peers, min_denominator, tie_salt):
        captured_salts.append(tie_salt)
        return real_anonymise_point(
            observations, min_peers=min_peers, min_denominator=min_denominator, tie_salt=tie_salt
        )

    monkeypatch.setattr(publish_module, "anonymise_point", _spy)

    publish_benchmark(
        _cohort(),
        snapshot=SNAPSHOT_TWO_INDICATORS,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )

    # Two point indicators (N08, N09) were published -- each must have received
    # its own tie_salt. A constant salt would collapse this set to size 1.
    assert len(captured_salts) == 2
    assert len(set(captured_salts)) == 2
