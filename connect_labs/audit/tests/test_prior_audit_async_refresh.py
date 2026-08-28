"""Serving a stale projection while a worker rebuilds it, instead of rebuilding inline.

The change under test moves ONE thing off the request path: the full
fetch-and-rebuild that fires when a built projection passes STALE_AFTER. It is
behind PRIOR_AUDIT_ASYNC_STALE_REFRESH, default False, so the first thing these
tests pin is that the default really is today's behaviour -- an inert diff is a
claim, and this is what makes it checkable.

The rest cover the ways the async path could go wrong QUIETLY, which is the only
way it can go wrong at all: the reader always holds a correct-enough answer, so
a broken refresh does not raise, it just stops the projection from ever being
rebuilt. That would silently retire STALE_AFTER -- the retraction backstop --
and present a withdrawn verdict as standing.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.core.cache import cache
from django.utils import timezone as dj_timezone

from connect_labs.audit import prior_audit_projection as projection
from connect_labs.audit.data_access import AuditDataAccess
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditProjectionState
from connect_labs.audit.prior_audit_projection import rebuild_opportunity

OPP = 2157


def _dt(day):
    return datetime(2026, 5, day, tzinfo=dt_timezone.utc)


def _session(id, status, visit_results, completed_at=None, opportunity_id=OPP):
    data = {
        "status": status,
        "visit_results": visit_results,
        "title": "",
        "opportunity_id": opportunity_id,
    }
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": opportunity_id, "data": data}
    )


def _vr(**assessments):
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def _da(username="auditor"):
    da = AuditDataAccess.__new__(AuditDataAccess)
    # _requesting_username reads request.user.username; nothing here needs a real request.
    da.request = type("R", (), {"user": type("U", (), {"username": username})()})()
    return da


#: Stale (past STALE_AFTER = 24h) but still inside REFRESH_DEADLINE (48h), which
#: is the window the async path is actually for. Expressed in hours because both
#: bounds are compared with >=, so a value landing exactly on one takes the OTHER
#: branch -- 48h is past the deadline, not inside it.
STALE_BUT_REFRESHABLE_HOURS = 30
PAST_DEADLINE_HOURS = 72


def _build_then_age(hours):
    """A built projection whose last full build was `hours` ago."""
    rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
    state = PriorAuditProjectionState.objects.get(opportunity_id=OPP)
    PriorAuditProjectionState.objects.filter(pk=state.pk).update(built_at=dj_timezone.now() - timedelta(hours=hours))
    state.refresh_from_db()
    return state


@pytest.fixture(autouse=True)
def _clear_lock():
    cache.delete(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP))
    yield
    cache.delete(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP))


@pytest.mark.django_db
class TestDefaultIsTodaysBehaviour:
    """The flag is off by default, and off means: rebuild inline, exactly as before."""

    def test_stale_projection_is_rebuilt_inline_when_the_flag_is_off(self):
        _build_then_age(hours=STALE_BUT_REFRESHABLE_HOURS)
        fresh = _session(2, "completed", {"222": _vr(b2="fail")}, completed_at=_dt(2))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[fresh]) as spy:
            with patch.object(projection, "schedule_stale_refresh") as sched:
                index = _da().get_prior_audited_images(opportunity_id=OPP)
        # The full fetch is the tell: no completed_at__gt bound means a full rebuild.
        assert spy.call_args.kwargs == {"status": "completed"}
        sched.assert_not_called()
        assert set(index) == {"222:b2"}

    def test_targeted_reader_also_rebuilds_inline_when_the_flag_is_off(self):
        _build_then_age(hours=STALE_BUT_REFRESHABLE_HOURS)
        fresh = _session(2, "completed", {"222": _vr(b2="fail")}, completed_at=_dt(2))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[fresh]) as spy:
            with patch.object(projection, "schedule_stale_refresh") as sched:
                _da().get_prior_audited_images_for(opportunity_id=OPP, pairs=[("222", "b2")])
        assert spy.call_args.kwargs == {"status": "completed"}
        sched.assert_not_called()


@pytest.mark.django_db
class TestAsyncRefreshEnabled:
    @pytest.fixture(autouse=True)
    def _enable(self, settings):
        settings.PRIOR_AUDIT_ASYNC_STALE_REFRESH = True

    def test_a_stale_projection_is_served_and_the_rebuild_is_queued(self):
        _build_then_age(hours=STALE_BUT_REFRESHABLE_HOURS)
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]) as spy:
            with patch.object(projection, "schedule_stale_refresh") as sched:
                index = _da().get_prior_audited_images(opportunity_id=OPP)
        # The bounded watermark query, NOT the full fetch: that is the whole saving.
        assert "completed_at__gt" in spy.call_args.kwargs
        sched.assert_called_once_with(OPP, "auditor")
        assert index["111:b1"]["result"] == "pass", "must still answer from the projection"

    def test_both_readers_take_the_same_branch(self):
        """The two readers duplicated this decision; they must not diverge again."""
        _build_then_age(hours=STALE_BUT_REFRESHABLE_HOURS)
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]):
            with patch.object(projection, "schedule_stale_refresh") as sched:
                _da().get_prior_audited_images_for(opportunity_id=OPP, pairs=[("111", "b1")])
        sched.assert_called_once_with(OPP, "auditor")

    def test_a_fresh_projection_queues_nothing(self):
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]):
            with patch.object(projection, "schedule_stale_refresh") as sched:
                _da().get_prior_audited_images(opportunity_id=OPP)
        sched.assert_not_called()

    def test_an_unbuilt_opportunity_still_builds_inline(self):
        """The safety property of #1246 is untouched: never serve an unbuilt projection.

        A cold projection answered from the table would report "no prior
        verdicts" -- the silent under-fetch this module exists to prevent -- so
        the async path must not reach it.
        """
        s = _session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[s]) as spy:
            with patch.object(projection, "schedule_stale_refresh") as sched:
                index = _da().get_prior_audited_images(opportunity_id=OPP)
        assert spy.call_args.kwargs == {"status": "completed"}
        sched.assert_not_called()
        assert index["111:b1"]["result"] == "pass"

    def test_past_the_refresh_deadline_the_reader_stops_waiting(self):
        """A refresh that never lands must not silently retire the staleness floor.

        This is the failure the deadline exists for: every reader sees "stale but
        async is on", queues another doomed task, serves the projection, and the
        full rebuild never happens again.
        """
        _build_then_age(hours=PAST_DEADLINE_HOURS)  # REFRESH_DEADLINE is 2 * STALE_AFTER = 48h
        fresh = _session(2, "completed", {"222": _vr(b2="fail")}, completed_at=_dt(2))
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[fresh]) as spy:
            with patch.object(projection, "schedule_stale_refresh") as sched:
                _da().get_prior_audited_images(opportunity_id=OPP)
        assert spy.call_args.kwargs == {"status": "completed"}, "must rebuild inline past the deadline"
        sched.assert_not_called()

    def test_a_reader_with_no_username_does_not_queue_a_refresh(self):
        """Nothing to run as. Guessing an identity is how Pulse understated every figure 5x."""
        _build_then_age(hours=STALE_BUT_REFRESHABLE_HOURS)
        with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[]):
            with patch("connect_labs.audit.tasks.refresh_prior_audit_projection.delay") as delay:
                _da(username="").get_prior_audited_images(opportunity_id=OPP)
        delay.assert_not_called()


@pytest.mark.django_db
class TestSingleFlight:
    def test_only_one_refresh_is_queued_per_opportunity(self):
        """Losers serve the projection they already have -- they do not queue or wait.

        The lock is on the worker side on purpose: the expensive step is the
        outbound fetch, so an in-request lock would leave the losers blocking on
        it, which is the pile-up this change exists to avoid.
        """
        with patch("connect_labs.audit.tasks.refresh_prior_audit_projection.delay") as delay:
            assert projection.schedule_stale_refresh(OPP, "auditor") is True
            assert projection.schedule_stale_refresh(OPP, "auditor") is False
            assert projection.schedule_stale_refresh(OPP, "someone-else") is False
        assert delay.call_count == 1

    def test_the_lock_is_released_when_the_refresh_finishes(self):
        with patch("connect_labs.audit.tasks.refresh_prior_audit_projection.delay"):
            assert projection.schedule_stale_refresh(OPP, "auditor") is True
        projection.clear_refresh_lock(OPP)
        with patch("connect_labs.audit.tasks.refresh_prior_audit_projection.delay") as delay:
            assert projection.schedule_stale_refresh(OPP, "auditor") is True
        delay.assert_called_once()

    def test_a_broker_failure_releases_the_lock_rather_than_wedging_it(self):
        """A dead broker must not block retries for the lock's whole TTL."""
        with patch(
            "connect_labs.audit.tasks.refresh_prior_audit_projection.delay",
            side_effect=OSError("broker down"),
        ):
            assert projection.schedule_stale_refresh(OPP, "auditor") is False
        with patch("connect_labs.audit.tasks.refresh_prior_audit_projection.delay") as delay:
            assert projection.schedule_stale_refresh(OPP, "auditor") is True
        delay.assert_called_once()


@pytest.mark.django_db
class TestRefreshTask:
    """Every failure here is silent by design -- the reader already answered.

    Which is exactly why each one must release the lock and say why in the log:
    a refresh that fails quietly and holds its lock stops the projection being
    rebuilt at all, and the only thing standing between that and a stale verdict
    served forever is REFRESH_DEADLINE.
    """

    def _task(self):
        from connect_labs.audit.tasks import refresh_prior_audit_projection

        return refresh_prior_audit_projection

    def test_a_missing_user_is_logged_and_releases_the_lock(self):
        cache.add(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP), "ghost", 60)
        assert self._task()(OPP, "ghost") is None
        assert cache.get(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP)) is None

    def test_an_unusable_connect_token_is_logged_and_releases_the_lock(self, django_user_model):
        """An expired refresh token is the failure most easily mistaken for 'nothing to do'."""
        from connect_labs.labs.connect_tokens import ConnectTokenError

        django_user_model.objects.create(username="auditor")
        cache.add(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP), "auditor", 60)
        with patch(
            "connect_labs.labs.connect_tokens.get_valid_access_token",
            side_effect=ConnectTokenError("refresh expired"),
        ):
            assert self._task()(OPP, "auditor") is None
        assert cache.get(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP)) is None

    def test_a_successful_refresh_rebuilds_and_releases_the_lock(self, django_user_model):
        django_user_model.objects.create(username="auditor")
        rebuild_opportunity(OPP, [_session(1, "completed", {"111": _vr(b1="pass")}, completed_at=_dt(1))])
        cache.add(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP), "auditor", 60)

        newer = _session(2, "completed", {"222": _vr(b2="fail")}, completed_at=_dt(2))
        with patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="tok"):
            with patch.object(AuditDataAccess, "__init__", return_value=None):
                with patch.object(AuditDataAccess, "get_audit_sessions", autospec=True, return_value=[newer]):
                    with patch.object(AuditDataAccess, "close", autospec=True, return_value=None):
                        result = self._task()(OPP, "auditor")

        assert result["rows_total"] == 2, "the session the reader had not seen must now be in the projection"
        assert PriorAuditProjectionState.objects.get(opportunity_id=OPP).built_by == "auditor"
        assert cache.get(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP)) is None

    def test_an_exception_mid_rebuild_still_releases_the_lock(self, django_user_model):
        django_user_model.objects.create(username="auditor")
        cache.add(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP), "auditor", 60)
        with patch("connect_labs.labs.connect_tokens.get_valid_access_token", return_value="tok"):
            with patch.object(AuditDataAccess, "__init__", return_value=None):
                with patch.object(
                    AuditDataAccess, "get_audit_sessions", autospec=True, side_effect=RuntimeError("connect down")
                ):
                    with patch.object(AuditDataAccess, "close", autospec=True, return_value=None):
                        assert self._task()(OPP, "auditor") is None
        assert cache.get(projection._REFRESH_LOCK_KEY.format(opportunity_id=OPP)) is None
