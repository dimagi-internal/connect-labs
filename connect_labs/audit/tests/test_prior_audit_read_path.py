"""The read path must never serve an unbuilt projection as a clean history.

This is the safety property of #1246 step 2. Everything else in the switch-over
is a performance change; this one is the difference between "slow" and "tells an
auditor an image was never judged when it was".
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from django.utils import timezone as dj_timezone

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState, PriorAuditVerdict
from connect_labs.audit.prior_audit_projection import is_built, rebuild_opportunity, record_session, replace_session

OPP = 1973


def _dt(day):
    return datetime(2026, 5, day, tzinfo=timezone.utc)


def _session(id, status, visit_results, completed_at=None, title="", opportunity_id=OPP):
    data = {
        "status": status,
        "visit_results": visit_results,
        "title": title,
        "opportunity_id": opportunity_id,
    }
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
    return AuditSessionRecord(
        {
            "id": id,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": opportunity_id,
            "data": data,
        }
    )


def _vr(**assessments):
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def _da():
    # __init__ demands an OAuth token; nothing here reaches the network.
    return AuditDataAccess.__new__(AuditDataAccess)


@pytest.mark.django_db
class TestReadPathFallback:
    def test_unbuilt_opportunity_falls_back_to_live(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        # kwargs rather than assert_called_once_with: the mocks are autospec'd,
        # so `self` is part of the call and asserting on it adds nothing.
        assert spy.call_count == 1 and spy.call_args.kwargs == {"status": "completed"}
        assert index["111:b1"]["result"] == "pass"

    def test_a_built_opportunity_asks_only_the_cheap_question(self):
        """Not "no round trip" any more -- one WATERMARK query, not a full fetch.

        Since the watermark landed, a built opportunity asks the export API only
        for sessions later than what it has ingested. On production opp 2157 that
        is 0.11s against 1.79s for the full fetch, and it doubles as the
        incremental update.
        """
        s = _session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert spy.call_count == 1
        assert "completed_at__gt" in spy.call_args.kwargs, "must be the bounded query, not a full fetch"
        assert index["111:b1"]["result"] == "fail"

    def test_rows_without_a_state_row_are_not_trusted(self):
        """Dual-written rows accumulate before a backfill; a partial set is not a history.

        This is the case that makes `is_built` the gate rather than "are there
        any rows" -- serving a half-populated table would under-report priors.
        """
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        replace_session(s)  # writes rows, does NOT mark the opportunity built
        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).exists()
        assert not is_built(OPP)

        live_only = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[live_only]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        spy.assert_called_once()
        assert set(index) == {"222:b9"}, "must serve the live answer, not the partial table"

    def test_a_built_opportunity_with_no_verdicts_is_trusted_as_empty(self):
        """'Built and genuinely empty' must NOT fall back — otherwise nothing improves.

        The counterpart to the test above: these two states are identical in the
        rows table and only the state row tells them apart.
        """
        rebuild_opportunity(OPP, [_session(1, "in_progress", {"111": _vr(b1="pass")})])
        assert is_built(OPP)
        # No completed sessions means no watermark to bound a query with, so this
        # takes the full path -- which is cheap precisely because there is nothing
        # to return. What matters is that it does not report stale or wrong data.
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]):
            assert _da().get_prior_audited_images(opportunity_id=OPP) == {}

    def test_exclude_session_id_is_honoured_on_the_projection_path(self):
        s = _session(7, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        rebuild_opportunity(OPP, [s])
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]):
            assert _da().get_prior_audited_images(opportunity_id=OPP, exclude_session_id=7) == {}

    def test_other_opportunities_are_unaffected_by_a_build(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        assert not is_built(9999)


@pytest.mark.django_db
class TestBuildState:
    def test_rebuild_records_who_built_it_and_from_how_much(self):
        sessions = [
            _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1)),
            _session(2, "in_progress", {"111": _vr(b3="pass")}),
        ]
        rebuild_opportunity(OPP, sessions, built_by="poller@example.com")
        state = PriorAuditProjectionState.objects.get(opportunity_id=OPP)
        assert state.built_by == "poller@example.com"
        assert state.source_sessions == 2, "counts what it was handed, so a scope drop is visible"
        assert state.rows == 2


@pytest.mark.django_db
class TestRecordSessionIsBestEffort:
    def test_a_projection_failure_never_breaks_the_caller(self):
        """The audit was already saved; a cache write must not turn that into a 500."""
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch(
            "connect_labs.audit.prior_audit_projection.replace_session",
            side_effect=RuntimeError("db gone"),
        ):
            assert record_session(s) is None

    def test_a_successful_write_reports_its_row_count(self):
        s = _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1))
        assert record_session(s) == 2


@pytest.mark.django_db
class TestJustInTimePopulation:
    """The cache fills from the request that needs it, under that request's own auth.

    The alternative -- a scheduled backfill -- needs a credential to run under,
    meaning a service account or somebody's cached OAuth token driving a job.
    None of that is necessary: the caller has already authenticated and already
    proved access to the opportunity (get_audit_session 404s otherwise) before
    reaching here.
    """

    def test_a_miss_computes_live_and_stores_the_result(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        assert not is_built(OPP)
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        # kwargs rather than assert_called_once_with: the mocks are autospec'd,
        # so `self` is part of the call and asserting on it adds nothing.
        assert spy.call_count == 1 and spy.call_args.kwargs == {"status": "completed"}
        assert index["111:b1"]["result"] == "pass"
        assert is_built(OPP), "the miss must have populated the cache"

    def test_the_next_read_is_served_from_the_cache(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]):
            _da().get_prior_audited_images(opportunity_id=OPP)
        # Second read serves from cache after one bounded check -- no full fetch.
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert "completed_at__gt" in spy.call_args.kwargs
        assert index["111:b1"]["result"] == "pass"

    def test_a_stale_cache_is_recomputed_rather_than_served(self):
        """Bounds how long a missed completion dual-write can go unnoticed."""
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]):
            _da().get_prior_audited_images(opportunity_id=OPP)

        state = PriorAuditProjectionState.objects.get(opportunity_id=OPP)
        PriorAuditProjectionState.objects.filter(pk=state.pk).update(
            built_at=dj_timezone.now() - (projection.STALE_AFTER * 2)
        )
        assert is_built(OPP) and not projection.is_fresh(OPP)

        newer = _session(2, "completed", {"222": _vr(b9="fail")}, completed_at=_dt(2))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s, newer]) as spy:
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        spy.assert_called_once()
        assert set(index) == {"111:b1", "222:b9"}

    def test_a_failed_cache_write_still_returns_the_right_answer(self):
        """The reader holds a correct answer; a cache write must not 500 it."""
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with (
            patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]),
            patch(
                "connect_labs.audit.prior_audit_projection.rebuild_opportunity",
                side_effect=RuntimeError("db gone"),
            ),
        ):
            index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert index["111:b1"]["result"] == "pass"
        assert not is_built(OPP)

    def test_no_stored_credential_is_consulted_on_the_read_path(self):
        """Authorisation comes from the live request, never a standing grant.

        get_valid_access_token is how a background job reaches Connect. The read
        path must never call it -- if it did, the answer would depend on
        somebody's cached token rather than on the caller.
        """
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with (
            patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]),
            patch("connect_labs.labs.connect_tokens.get_valid_access_token") as tok,
        ):
            _da().get_prior_audited_images(opportunity_id=OPP)
        tok.assert_not_called()


@pytest.mark.django_db
class TestTargetedRead:
    """`prior_verdicts_for` must be `read_index` restricted to a key set -- nothing else.

    The targeted read exists because both callers already know the images they are
    asking about, while `read_index` materialised the whole opportunity: production
    opp 2154 held 17,653 verdict rows across 712 sessions to answer ~25 lookups
    (2026-08-27). That is only a safe swap if the two agree exactly, so these pin
    the agreement rather than the speed.
    """

    def test_agrees_with_read_index_on_every_requested_key(self):
        sessions = [
            _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1)),
            _session(2, "completed", {"222": _vr(b3="pass")}, completed_at=_dt(2)),
            _session(3, "completed", {"333": _vr(b4="fail")}, completed_at=_dt(3)),
        ]
        rebuild_opportunity(OPP, sessions)

        full = projection.read_index(OPP)
        pairs = [("111", "b1"), ("111", "b2"), ("222", "b3")]
        narrow = projection.prior_verdicts_for(OPP, pairs)

        assert set(narrow) == {"111:b1", "111:b2", "222:b3"}
        for key in narrow:
            assert narrow[key] == full[key], f"{key} disagrees with the full index"
        assert "333:b4" in full and "333:b4" not in narrow, "must not return unrequested images"

    def test_the_winner_rule_survives_narrowing(self):
        """Most-recent-completed-wins is decided on read, so it must hold per-key too.

        The whole-index path gets this from ordering the full scan. A targeted
        query orders a subset, and if the two disagreed here the narrow read would
        silently report an OLD verdict as current.
        """
        rebuild_opportunity(
            OPP,
            [
                _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1)),
                _session(2, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(5)),
                _session(3, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(3)),
            ],
        )
        narrow = projection.prior_verdicts_for(OPP, [("111", "b1")])
        assert narrow["111:b1"]["result"] == "fail", "the latest completed verdict must win"
        assert narrow["111:b1"] == projection.read_index(OPP)["111:b1"]

    def test_undated_never_displaces_dated_when_narrowed(self):
        rebuild_opportunity(
            OPP,
            [
                _session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(2)),
                _session(2, "completed", {"111": _vr(b1="pass")}),  # no completed_at
            ],
        )
        assert projection.prior_verdicts_for(OPP, [("111", "b1")])["111:b1"]["result"] == "fail"

    def test_exclude_session_id_is_honoured(self):
        rebuild_opportunity(OPP, [_session(7, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        assert projection.prior_verdicts_for(OPP, [("111", "b1")], exclude_session_id=7) == {}

    def test_does_not_leak_another_opportunity(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        rebuild_opportunity(
            4242, [_session(2, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(2), opportunity_id=4242)]
        )
        assert projection.prior_verdicts_for(OPP, [("111", "b1")])["111:b1"]["result"] == "pass"

    def test_empty_pairs_short_circuits_without_a_query(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            assert projection.prior_verdicts_for(OPP, []) == {}
        assert len(ctx) == 0, "no pairs means nothing to ask"

    def test_cost_tracks_the_pairs_not_the_history(self):
        """The regression this change exists to prevent -- pin the COST, not the behaviour.

        Ten sessions of unrelated history must not change what a one-image lookup
        reads. Before the targeted read, every one of those rows was fetched and
        turned into a dict entry on every single call.
        """
        history = [
            _session(i, "completed", {f"v{i}": _vr(**{f"b{i}": "pass"})}, completed_at=_dt(i)) for i in range(1, 11)
        ]
        target = _session(99, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(11))
        rebuild_opportunity(OPP, history + [target])

        assert PriorAuditVerdict.objects.filter(opportunity_id=OPP).count() == 11
        assert len(projection.read_index(OPP)) == 11, "the full index still reads everything"

        narrow = projection.prior_verdicts_for(OPP, [("111", "b1")])
        assert len(narrow) == 1, "the targeted read must not scale with unrelated history"
        assert narrow["111:b1"]["result"] == "fail"


@pytest.mark.django_db
class TestTargetedReadKeepsTheFreshnessGate:
    """Narrowing the READ must not make the BUILD lazy.

    A cold projection answered from a targeted query would return "nothing was
    judged before" instead of an error -- a silent under-fetch that re-presents
    already-judged images as new. These pin that `get_prior_audited_images_for`
    walks the same three paths as the full version.
    """

    def test_unbuilt_opportunity_still_builds_before_answering(self):
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]) as spy:
            index = _da().get_prior_audited_images_for(OPP, [("111", "b1")])
        assert spy.call_args.kwargs == {"status": "completed"}, "cold must take the full build"
        assert index["111:b1"]["result"] == "pass", "must not answer a cold projection as empty"
        assert is_built(OPP)

    def test_built_opportunity_asks_only_the_watermark_question(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="fail")}, completed_at=_dt(1))])
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]) as spy:
            index = _da().get_prior_audited_images_for(OPP, [("111", "b1")])
        assert "completed_at__gt" in spy.call_args.kwargs, "must be the bounded query, not a full fetch"
        assert index["111:b1"]["result"] == "fail"

    def test_matches_the_full_api_for_the_same_keys(self):
        rebuild_opportunity(
            OPP,
            [
                _session(1, "completed", {"111": _vr(b1="pass", b2="fail")}, completed_at=_dt(1)),
                _session(2, "completed", {"222": _vr(b3="pass")}, completed_at=_dt(2)),
            ],
        )
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]):
            full = _da().get_prior_audited_images(opportunity_id=OPP)
            narrow = _da().get_prior_audited_images_for(OPP, [("111", "b1"), ("222", "b3")])
        assert narrow == {k: full[k] for k in ("111:b1", "222:b3")}

    def test_empty_pairs_never_touches_the_api(self):
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True) as spy:
            assert _da().get_prior_audited_images_for(OPP, []) == {}
        assert spy.call_count == 0
