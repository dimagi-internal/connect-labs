"""Who may read a benchmark, and what a reader can never see."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from connect_labs.benchmarks.data_access import benchmarks_for_opportunity
from connect_labs.benchmarks.models import BenchmarkCohort
from connect_labs.benchmarks.publish import publish_benchmark
from connect_labs.benchmarks.tests.test_publish import SNAPSHOT

pytestmark = pytest.mark.django_db


def _request(*accessible_opps):
    request = MagicMock()
    request.session = {"labs_oauth": {"organization_data": {"opportunities": [{"id": o} for o in accessible_opps]}}}
    request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
    return request


def _published():
    cohort = BenchmarkCohort.objects.create(name="KMC", organization_id="dimagi-kmc")
    for i in range(6):
        cohort.members.create(opportunity_id=500 + i)
    publish_benchmark(
        cohort,
        snapshot=SNAPSHOT,
        source_workflow_id=19778,
        source_run_id=19783,
        registry_id=19784,
        as_of="2026-09-11",
    )
    return cohort


def test_a_member_reads_its_cohorts_benchmarks():
    _published()
    out = benchmarks_for_opportunity(_request(500), 500)
    assert out["indicators"]["N"]["N08"]["peers"], "a cohort member got no benchmark"


def test_a_user_without_access_to_that_opportunity_reads_nothing():
    """Cohort membership is not enough -- the caller must hold the opportunity."""
    _published()
    out = benchmarks_for_opportunity(_request(999), 500)
    assert out["indicators"] == {}


def test_an_opportunity_in_no_cohort_reads_nothing():
    _published()
    out = benchmarks_for_opportunity(_request(999), 999)
    assert out["indicators"] == {}


def test_no_response_anywhere_carries_an_opportunity_id():
    """Contract test over the payload, not per-endpoint, so a new endpoint inherits it."""
    _published()
    out = benchmarks_for_opportunity(_request(500), 500)
    blob = json.dumps(out)
    assert "opportunity_id" not in blob
    for opp in (500, 501, 502, 503, 504, 505):
        assert str(opp) not in blob, f"opportunity {opp} is recoverable from the response"


def test_only_the_sanctioned_modules_can_see_identity():
    """Widening the set of code that can read identity must be a reviewed act."""
    app = Path(__file__).resolve().parents[1]
    allowed = {"publish.py", "models.py"}
    offenders = [
        path.name
        for path in app.rglob("*.py")
        if "tests" not in path.parts and path.name not in allowed and "with_source" in path.read_text()
    ]
    assert offenders == [], f"with_source() reached from unsanctioned modules: {offenders}"
