"""Publication reads a completed run's frozen snapshot and recomputes nothing."""

import pytest

from connect_labs.benchmarks import publish as publish_module
from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkPublication, BenchmarkValue
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

# Two indicators, EACH with both point AND month data, so a test can prove
# tie_salt varies per indicator on BOTH the anonymise_point and the
# anonymise_series call sites -- not just the point one.
SNAPSHOT_TWO_INDICATORS_WITH_SERIES = {
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
            "opportunity_month": {
                "N08": {
                    "2026-01": [
                        {"opportunity_id": 500 + i, "value": 40.0 + i, "denominator": 100, "suppressed": False}
                        for i in range(6)
                    ],
                },
                "N09": {
                    "2026-01": [
                        {"opportunity_id": 500 + i, "value": 5.0 + i, "denominator": 100, "suppressed": False}
                        for i in range(6)
                    ],
                },
            },
        }
    },
}

# opportunity_month only -- no opportunity (point) scope at all for this
# indicator. Reproduces I1: an indicator with only series data must still
# publish its series rows, not be silently skipped.
SNAPSHOT_MONTH_ONLY_INDICATOR = {
    "as_of": "2026-09-11",
    "series": {
        "N": {
            "opportunity": {},
            "opportunity_month": {
                "C05": {
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

# Second indicator's opportunity rows contain a duplicate opportunity_id, which
# disclosure.py raises ValueError on. Used to force a mid-publication failure
# and prove atomicity: the first indicator's rows must never survive either.
SNAPSHOT_DUPLICATE_IN_SECOND_INDICATOR = {
    "as_of": "2026-09-11",
    "series": {
        "N": {
            "opportunity": {
                "N08": [
                    {"opportunity_id": 500 + i, "value": 50.0 + i, "denominator": 100, "suppressed": False}
                    for i in range(6)
                ],
                "N09": [
                    {"opportunity_id": 500, "value": 10.0, "denominator": 100, "suppressed": False},
                    {"opportunity_id": 500, "value": 11.0, "denominator": 100, "suppressed": False},
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


def test_republishing_leaves_the_first_publications_values_untouched():
    """Immutability means re-publishing creates a SEPARATE, unmodified record --
    not merely that Django handed back a different pk (which `objects.create`
    guarantees unconditionally and can't fail to do)."""
    cohort = _cohort()
    kwargs = dict(
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    first = publish_benchmark(cohort, **kwargs)
    first_values_before = set(
        BenchmarkValue.objects.filter(publication=first).values_list(
            "series", "indicator_id", "period", "peer_index", "value", "opportunity_id"
        )
    )
    second = publish_benchmark(cohort, **kwargs)

    assert first.pk != second.pk
    assert BenchmarkPublication.objects.count() == 2
    first_values_after = set(
        BenchmarkValue.objects.filter(publication=first).values_list(
            "series", "indicator_id", "period", "peer_index", "value", "opportunity_id"
        )
    )
    assert first_values_after == first_values_before
    assert BenchmarkValue.objects.filter(publication=first).count() == 6 + 12
    assert BenchmarkValue.objects.filter(publication=second).count() == 6 + 12


def test_tie_salt_varies_per_indicator(monkeypatch):
    """tie_salt is what stops tied peers holding the same index in every indicator.

    disclosure.py can only reject an EMPTY salt -- it has no way to know whether
    the salt actually differs across indicators within one publication. That
    contract belongs to the publisher, so it must be tested here: capture every
    tie_salt the publisher actually passes to anonymise_point AND
    anonymise_series, and assert there is one distinct value per indicator per
    call site -- not one constant value reused.

    Both call sites are spied on: a constant salt on the SERIES call
    (anonymise_series) reinstates the cross-indicator linkage attack on peer
    LINES, which disclosure.py documents as the higher residual re-identification
    risk -- a spy on anonymise_point alone would never see that regression.
    """
    point_salts = []
    series_salts = []
    real_anonymise_point = publish_module.anonymise_point
    real_anonymise_series = publish_module.anonymise_series

    def _point_spy(observations, *, min_peers, min_denominator, tie_salt):
        point_salts.append(tie_salt)
        return real_anonymise_point(
            observations, min_peers=min_peers, min_denominator=min_denominator, tie_salt=tie_salt
        )

    def _series_spy(observations_by_period, *, min_peers, min_denominator, tie_salt):
        series_salts.append(tie_salt)
        return real_anonymise_series(
            observations_by_period, min_peers=min_peers, min_denominator=min_denominator, tie_salt=tie_salt
        )

    monkeypatch.setattr(publish_module, "anonymise_point", _point_spy)
    monkeypatch.setattr(publish_module, "anonymise_series", _series_spy)

    publish_benchmark(
        _cohort(),
        snapshot=SNAPSHOT_TWO_INDICATORS_WITH_SERIES,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )

    # Two indicators (N08, N09), each with both point and month data -- each
    # call site must have received one distinct salt per indicator. A constant
    # salt on either call site would collapse that site's set to size 1.
    assert len(point_salts) == 2
    assert len(set(point_salts)) == 2
    assert len(series_salts) == 2
    assert len(set(series_salts)) == 2


def test_an_indicator_present_only_in_the_series_scope_is_still_published():
    """An indicator can exist only under opportunity_month (no point-in-time
    equivalent). Iterating just POINT_SCOPE's keys would silently skip it --
    zero rows, no exception, no log."""
    pub = publish_benchmark(
        _cohort(),
        snapshot=SNAPSHOT_MONTH_ONLY_INDICATOR,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    series = BenchmarkValue.objects.filter(publication=pub, indicator_id="C05", period__isnull=False)
    assert series.count() == 12
    assert set(series.values_list("period", flat=True)) == {"2026-01", "2026-02"}


def test_a_failure_mid_publication_leaves_nothing_committed():
    """publish_benchmark is @transaction.atomic: a publication is complete or
    absent, never half-written. Force the ValueError disclosure.py raises on a
    duplicate opportunity inside the SECOND indicator's rows -- the first
    indicator's rows must not survive either."""
    cohort = _cohort()
    with pytest.raises(ValueError):
        publish_benchmark(
            cohort,
            snapshot=SNAPSHOT_DUPLICATE_IN_SECOND_INDICATOR,
            source_workflow_id=19778,
            source_run_id=19783,
            registry_id=19784,
            as_of="2026-09-11",
        )
    assert BenchmarkPublication.objects.count() == 0
    assert BenchmarkValue.objects.count() == 0
