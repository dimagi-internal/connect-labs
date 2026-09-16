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
from connect_labs.benchmarks.tests.test_publish import OPPS, SNAPSHOT, _history

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
        history=_history(),
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
    # One short of each cohort's membership: the reader's own row is excluded
    # from both (see test_a_reader_never_gets_its_own_row_back).
    assert len(peers_a) == len(OPPS) - 1
    assert len(peers_b) == 4, "the second cohort's own membership was not respected"
    # Each cohort's indices are its OWN -- drawn from its own publication's
    # range, ordered, with exactly the reader's index missing. That is only
    # expressible because the two are no longer merged into one list; merged,
    # cohort B's peer 0 overwrote cohort A's.
    idx_a = [p["peer_index"] for p in peers_a]
    idx_b = [p["peer_index"] for p in peers_b]
    assert idx_a == sorted(idx_a) and set(idx_a) < set(range(len(OPPS)))
    assert idx_b == sorted(idx_b) and set(idx_b) < set(range(5))
    assert len(set(range(len(OPPS))) - set(idx_a)) == 1, "exactly one row (the reader's) is absent"
    assert len(set(range(5)) - set(idx_b)) == 1
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


def test_a_reader_never_gets_its_own_row_back():
    """The report draws this opportunity from its own LIVE figures, so the
    published copy in the peer set would draw it twice — once at the published
    value, once at the current one — and a reader who can difference "the set
    including me" against "me" learns something about the remainder."""
    cohort = _published()
    peers = benchmarks_for_opportunity(_request(OPPS[0]), OPPS[0])["indicators"][str(cohort.pk)]["C"]["C15"]["peers"]
    assert len(peers) == len(OPPS) - 1, "the reader's own row is still in its peer set"


def test_the_excluded_row_is_the_readers_own_and_not_just_any_row():
    """A blanket `[:-1]` would also pass the count check above. Read the SAME
    cohort as two different members and confirm each is missing a DIFFERENT
    value — the one that member published."""
    cohort = _published()

    def peer_values(opp):
        out = benchmarks_for_opportunity(_request(opp), opp)
        return sorted(p["value"] for p in out["indicators"][str(cohort.pk)]["C"]["C15"]["peers"])

    first, second = peer_values(OPPS[0]), peer_values(OPPS[1])
    assert first != second, "both members saw the same peer set, so self was not what got dropped"
    assert len(first) == len(second) == len(OPPS) - 1


def test_a_non_member_with_access_still_reads_nothing():
    """Excluding self must not accidentally widen the read to outsiders."""
    _published()
    assert benchmarks_for_opportunity(_request(999), 999)["indicators"] == {}


def test_the_reader_gets_its_own_published_figures_back_separately():
    """Out of `peers`, but not thrown away — a trend chart draws its own line
    from the same publication as the peers', because the report's live figure is
    a different vintage and would not be comparable to the lines beside it."""
    cohort = _published()
    entry = benchmarks_for_opportunity(_request(OPPS[0]), OPPS[0])["indicators"][str(cohort.pk)]["C"]["C15"]
    assert entry["own"] is not None, "the reader's own published point value is missing"
    assert entry["ownSeries"], "the reader's own published series is missing"
    assert entry["own"] not in [p["value"] for p in entry["peers"]], "own value is ALSO in the peer set"


def test_each_member_gets_its_own_figures_not_a_shared_one():
    cohort = _published()

    def own(opp):
        return benchmarks_for_opportunity(_request(opp), opp)["indicators"][str(cohort.pk)]["C"]["C15"]["own"]

    assert own(OPPS[0]) != own(OPPS[1]), "two members were handed the same 'own' value"
