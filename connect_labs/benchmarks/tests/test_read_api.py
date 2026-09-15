"""Who may read a benchmark, and what a reader can never see."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from django.test import RequestFactory

from connect_labs.benchmarks.api_views import benchmarks_api
from connect_labs.benchmarks.data_access import benchmarks_for_opportunity
from connect_labs.benchmarks.models import BenchmarkCohort
from connect_labs.benchmarks.publish import publish_benchmark
from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT

pytestmark = pytest.mark.django_db


def _request(*accessible_opps):
    request = MagicMock()
    request.session = {"labs_oauth": {"organization_data": {"opportunities": [{"id": o} for o in accessible_opps]}}}
    request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
    return request


def _published(name="KMC", members=OPPS):
    cohort = BenchmarkCohort.objects.create(name=name, organization_id="dimagi-kmc")
    for opp in members:
        cohort.members.create(opportunity_id=opp)
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
    cohort = _published()
    out = benchmarks_for_opportunity(_request(OPPS[0]), OPPS[0])
    assert out["indicators"][str(cohort.pk)]["C"]["C15"]["peers"], "a cohort member got no benchmark"


def test_a_user_without_access_to_that_opportunity_reads_nothing():
    """Cohort membership is not enough -- the caller must hold the opportunity."""
    _published()
    out = benchmarks_for_opportunity(_request(999), OPPS[0])
    assert out["indicators"] == {}


def test_an_opportunity_in_no_cohort_reads_nothing():
    _published()
    out = benchmarks_for_opportunity(_request(999), 999)
    assert out["indicators"] == {}


def test_overlapping_cohorts_do_not_collide_on_peer_index():
    """An opportunity may sit in two cohorts, and `peer_index` is unique only
    within one publication of one cohort. Merged into a single map, cohort B's
    peer 0 silently overwrote (or interleaved with) cohort A's -- two different
    opportunities, one index, and a series line zig-zagging between them."""
    a = _published(name="All KMC", members=OPPS)
    b = _published(name="Uganda only", members=OPPS[:5] + [999])
    out = benchmarks_for_opportunity(_request(OPPS[0]), OPPS[0])

    assert set(out["indicators"]) == {str(a.pk), str(b.pk)}
    peers_a = out["indicators"][str(a.pk)]["C"]["C15"]["peers"]
    peers_b = out["indicators"][str(b.pk)]["C"]["C15"]["peers"]
    assert len(peers_a) == len(OPPS)
    assert len(peers_b) == 5, "the second cohort's own membership was not respected"
    # Each cohort's own indices, each complete and each starting at 0 -- which is
    # only expressible because the two are no longer in one list.
    assert [p["peer_index"] for p in peers_a] == list(range(len(OPPS)))
    assert [p["peer_index"] for p in peers_b] == list(range(5))
    assert out["cohorts"][str(a.pk)]["name"] == "All KMC"
    assert out["cohorts"][str(b.pk)]["name"] == "Uganda only"


def test_no_response_anywhere_carries_an_opportunity_id():
    """Contract test over the payload, not per-endpoint, so a new endpoint inherits it."""
    _published()
    out = benchmarks_for_opportunity(_request(OPPS[0]), OPPS[0])
    blob = json.dumps(out)
    assert "opportunity_id" not in blob
    for opp in OPPS:
        assert str(opp) not in blob, f"opportunity {opp} is recoverable from the response"


def test_an_anonymous_caller_gets_401_not_a_login_redirect():
    """This endpoint only ever answers JSON; a 302 to an HTML login page reads to
    a fetch() as a success with an unparseable body."""
    request = RequestFactory().get("/labs/benchmarks/api/500/")
    request.user = MagicMock(is_authenticated=False)
    response = benchmarks_api(request, OPPS[0])
    assert response.status_code == 401
    assert response.content == b""


def test_only_the_sanctioned_modules_can_see_identity():
    """Widening the set of code that can read identity must be a reviewed act."""
    app = Path(__file__).resolve().parents[1]
    allowed = {"models.py"}
    offenders = [
        path.name
        for path in app.rglob("*.py")
        if "tests" not in path.parts and path.name not in allowed and "with_source" in path.read_text()
    ]
    assert offenders == [], f"with_source() reached from unsanctioned modules: {offenders}"
