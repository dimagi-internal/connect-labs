"""The one boundary that matters: rows know their source, displays never do.

Jonathan's call (2026-09-14) was to keep `opportunity_id` on BenchmarkValue,
because a figure that looks wrong is untraceable without it. That gives up the
structural guarantee -- a table with no identity column cannot leak it -- so
anonymity is enforced instead at a single choke point, and these are the tests
that hold that choke point closed.
"""

import pytest

from connect_labs.benchmarks.models import BenchmarkCohort, BenchmarkPublication, BenchmarkValue

pytestmark = pytest.mark.django_db


def _value(**kwargs):
    cohort = BenchmarkCohort.objects.create(name="KMC", organization_id="dimagi-kmc")
    pub = BenchmarkPublication.objects.create(
        cohort=cohort, source_workflow_id=19778, source_run_id=19783, registry_id=19784, as_of="2026-09-11"
    )
    defaults = {
        "publication": pub,
        "series": "N",
        "indicator_id": "N08",
        "period": None,
        "peer_index": 3,
        "value": 50.4,
        "opportunity_id": 874,
    }
    return BenchmarkValue.objects.create(**{**defaults, **kwargs})


def test_to_public_never_emits_the_source_opportunity():
    """The single projection any view may call."""
    public = _value().to_public()
    assert "opportunity_id" not in public
    # Nor under any other spelling, and not as a stray value.
    assert 874 not in public.values()
    assert public == {"series": "N", "indicator_id": "N08", "period": None, "peer_index": 3, "value": 50.4}


def test_to_public_is_clean_for_a_series_point_too():
    public = _value(period="2026-04").to_public()
    assert "opportunity_id" not in public
    assert public["period"] == "2026-04"


def test_with_source_is_the_named_way_in():
    """Identity is reachable, but only by asking for it by name."""
    _value()
    row = BenchmarkValue.objects.with_source()[0]
    assert row.opportunity_id == 874


def test_with_source_emits_an_audit_event(monkeypatch):
    events = []
    monkeypatch.setattr(
        "connect_labs.benchmarks.models.audit_record",
        lambda action, **kw: events.append((action, kw)),
    )
    _value()
    list(BenchmarkValue.objects.with_source())
    assert events, "an identified read left no audit trail"
    action, kw = events[0]
    assert action == "read"
    assert kw["resource_type"] == "benchmark_value_identified"
