"""The CommCare HQ token chain has one source of truth: UserCCHQToken.

CCHQ access tokens live 15 minutes, and HQ's django-oauth-toolkit rotates
refresh tokens: redeeming one revokes the old access and refresh tokens at once.
Two things broke live pulls, so users had to re-authorize after every one:

* A refresh made inside an SSE stream went only into ``request.session``, and
  SessionMiddleware had already saved the session before the stream ran. The
  stored session kept a pair HQ had just revoked.
* The session and the UserCCHQToken row were two copies of one chain, and each
  refreshed on its own. Whichever refreshed first revoked the other's tokens.

``FakeCCHQ`` below behaves like HQ's token endpoint, so these tests fail on a
revoked token the same way production did.
"""

import contextlib
import threading
import time
from collections import defaultdict
from datetime import timedelta
from importlib import import_module
from unittest.mock import MagicMock, patch

import pytest
from django.conf import settings as django_settings
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connection
from django.utils import timezone

from connect_labs.labs.analysis.sse_streaming import BaseSSEStreamView, send_sse_event
from connect_labs.labs.integrations.commcare.api_client import CommCareDataAccess
from connect_labs.labs.integrations.commcare.cchq_tokens import get_valid_cchq_access_token
from connect_labs.labs.models import UserCCHQToken
from connect_labs.users.models import User

SessionStore = import_module(django_settings.SESSION_ENGINE).SessionStore

LOCK = "connect_labs.labs.integrations.commcare.cchq_tokens.try_redis_lock"


class FakeCCHQ:
    """HQ's OAuth token endpoint and API auth, with django-oauth-toolkit's refresh-token rotation."""

    def __init__(self, access_token, refresh_token, exchange_delay=0.0):
        self._lock = threading.Lock()
        self.live_access = {access_token}
        self.live_refresh = {refresh_token: access_token}
        self.exchange_delay = exchange_delay
        self.exchanges = 0
        self.rejected_refreshes = 0

    def post(self, url, data, timeout):
        assert url.endswith("/oauth/token/")
        # The delay sits outside the lock so that two unserialized callers would
        # both redeem the same refresh_token, and the second would be rejected.
        time.sleep(self.exchange_delay)
        with self._lock:
            old_access = self.live_refresh.pop(data["refresh_token"], None)
            if old_access is None:
                self.rejected_refreshes += 1
                return _response(400, {"error": "invalid_grant"})
            self.live_access.discard(old_access)
            self.exchanges += 1
            access, refresh = f"access-{self.exchanges}", f"refresh-{self.exchanges}"
            self.live_access.add(access)
            self.live_refresh[refresh] = access
        return _response(200, {"access_token": access, "refresh_token": refresh, "expires_in": 900})

    def get(self, url, headers, timeout):
        token = headers["Authorization"].removeprefix("Bearer ")
        return _response(200 if token in self.live_access else 401, {"objects": []})

    @contextlib.contextmanager
    def serving(self):
        with (
            patch("connect_labs.labs.integrations.commcare.cchq_tokens.httpx.post", self.post),
            patch("connect_labs.labs.integrations.commcare.api_client.httpx.post", self.post),
            patch("connect_labs.labs.integrations.commcare.api_client.httpx.get", self.get),
        ):
            yield self


def _response(status_code, body):
    return MagicMock(status_code=status_code, json=lambda: body, text=str(body))


def _thread_lock_factory():
    """Stands in for try_redis_lock: a real per-key lock that works across threads."""
    locks = defaultdict(threading.Lock)

    @contextlib.contextmanager
    def try_lock(key, *, timeout, blocking_timeout, sleep=0.1):
        lock = locks[key]
        acquired = lock.acquire(timeout=blocking_timeout)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()

    return try_lock


@pytest.fixture(autouse=True)
def _oauth_client(settings):
    settings.COMMCARE_OAUTH_CLIENT_ID = "test-client"
    settings.COMMCARE_OAUTH_CLIENT_SECRET = "test-secret"


def _oauth(access, refresh, expires_in):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": timezone.now().timestamp() + expires_in,
        "token_type": "Bearer",
    }


def _connect(user, access="access-0", refresh="refresh-0", expires_in=900) -> str:
    """What labs_commcare_callback leaves behind: the same pair in the session and the DB row."""
    UserCCHQToken.objects.update_or_create(
        user=user,
        defaults={
            "access_token": access,
            "refresh_token": refresh,
            "expires_at": timezone.now() + timedelta(seconds=expires_in),
        },
    )
    session = SessionStore()
    session["commcare_oauth"] = _oauth(access, refresh, expires_in)
    session.create()
    return session.session_key


def _expire_row(user):
    UserCCHQToken.objects.filter(user=user).update(expires_at=timezone.now() - timedelta(minutes=1))


def _web_client(rf, user, session_key, domain="demo") -> CommCareDataAccess:
    request = rf.get("/")
    request.user = user
    request.session = SessionStore(session_key=session_key)
    return CommCareDataAccess(request, domain)


def _stored_session_oauth(session_key) -> dict:
    return SessionStore(session_key=session_key)["commcare_oauth"]


class _LivePullStream(BaseSSEStreamView):
    """A pipeline SSE view reduced to its CCHQ calls, as cchq_fetcher makes them."""

    heartbeat_enabled = False  # keep the generator synchronous for the test

    def stream_data(self, request):
        client = CommCareDataAccess(request, "demo")
        assert client.check_token_valid()
        assert client.verify_hq_access()
        yield send_sse_event("pulled")


def _run_stream(rf, user, session_key):
    """Run the view through SessionMiddleware in Django's order: the response is returned first, then streamed."""
    request = rf.get("/labs/workflow/pipeline-rows/")
    request.COOKIES[django_settings.SESSION_COOKIE_NAME] = session_key
    request.user = user
    response = SessionMiddleware(_LivePullStream.as_view())(request)
    return b"".join(response.streaming_content)


@pytest.mark.django_db
class TestRefreshInsideStreamingResponse:
    def test_refresh_during_the_stream_reaches_the_stored_session_and_the_db(self, rf):
        user = User.objects.create(username="streamer")
        session_key = _connect(user, expires_in=-60)  # the 15 minutes are up

        with FakeCCHQ("access-0", "refresh-0").serving() as hq:
            assert b"pulled" in _run_stream(rf, user, session_key)

            assert hq.exchanges == 1
            assert _stored_session_oauth(session_key)["access_token"] == "access-1"
            assert _stored_session_oauth(session_key)["refresh_token"] == "refresh-1"
            row = UserCCHQToken.objects.get(user=user)
            assert (row.access_token, row.refresh_token) == ("access-1", "refresh-1")

            # The next live pull, a new request on the same session, has no re-authorize prompt.
            assert _web_client(rf, user, session_key).verify_hq_access()
            assert hq.exchanges == 1

    def test_refresh_written_to_the_store_leaves_other_session_keys_alone(self, rf):
        """The store write changes commcare_oauth only. It must not write back this
        request's stale copy of keys that another request changed meanwhile."""
        user = User.objects.create(username="neighbour")
        session_key = _connect(user, expires_in=-60)
        client = _web_client(rf, user, session_key)

        other_request = SessionStore(session_key=session_key)
        other_request["labs_oauth"] = {"access_token": "connect-refreshed-elsewhere"}
        other_request.save()

        with FakeCCHQ("access-0", "refresh-0").serving():
            assert client.check_token_valid()

        stored = SessionStore(session_key=session_key)
        assert stored["commcare_oauth"]["access_token"] == "access-1"
        assert stored["labs_oauth"] == {"access_token": "connect-refreshed-elsewhere"}

    def test_a_later_save_of_a_stale_session_does_not_bring_back_the_revoked_pair(self, rf):
        """SESSION_SAVE_EVERY_REQUEST: a request that loaded the session before the
        refresh saves its stale copy afterwards. The row still wins."""
        user = User.objects.create(username="clobbered")
        session_key = _connect(user, expires_in=-60)
        slow_request_session = SessionStore(session_key=session_key)
        assert slow_request_session["commcare_oauth"]["access_token"] == "access-0"

        with FakeCCHQ("access-0", "refresh-0").serving() as hq:
            assert b"pulled" in _run_stream(rf, user, session_key)
            slow_request_session.modified = True
            slow_request_session.save()
            assert _stored_session_oauth(session_key)["access_token"] == "access-0"

            client = _web_client(rf, user, session_key)
            assert client.access_token == "access-1"
            assert client.verify_hq_access()
            assert hq.exchanges == 1


@pytest.mark.django_db
class TestSessionAndDbShareOneChain:
    def test_headless_refresh_then_web_pull(self, rf):
        """A scheduled task refreshes the row while the browser session still holds
        the old pair. The old pair is revoked, and its timestamp still looks valid."""
        user = User.objects.create(username="scheduled")
        session_key = _connect(user)

        with FakeCCHQ("access-0", "refresh-0").serving() as hq:
            _expire_row(user)
            assert get_valid_cchq_access_token(user) == "access-1"  # workflow/tasks.py, mopup, MCP
            assert "access-0" not in hq.live_access

            client = _web_client(rf, user, session_key)
            assert client.access_token == "access-1"
            assert client.verify_hq_access()
            assert hq.exchanges == 1
            assert hq.rejected_refreshes == 0

    def test_web_refresh_then_headless_refresh(self, rf):
        """The browser refreshes first. The next scheduled run then redeems the
        refresh_token the browser received, not the one the browser just revoked."""
        user = User.objects.create(username="browser-first")
        session_key = _connect(user, expires_in=-60)
        _expire_row(user)

        with FakeCCHQ("access-0", "refresh-0").serving() as hq:
            assert _web_client(rf, user, session_key).check_token_valid()
            assert get_valid_cchq_access_token(user) == "access-1"  # fresh row, no second exchange

            _expire_row(user)
            assert get_valid_cchq_access_token(user) == "access-2"
            assert hq.rejected_refreshes == 0

            # The browser follows the row the scheduled run advanced.
            client = _web_client(rf, user, session_key)
            assert client.access_token == "access-2"
            assert client.verify_hq_access()

    def test_probe_401_after_another_holder_refreshed_picks_up_their_token(self, rf):
        """Another holder refreshes between this client reading the token and using
        it. The probe's 401 retry takes their token from the row and does not redeem again."""
        user = User.objects.create(username="raced")
        session_key = _connect(user)

        with FakeCCHQ("access-0", "refresh-0").serving() as hq:
            client = _web_client(rf, user, session_key)
            assert client.access_token == "access-0"

            _expire_row(user)
            get_valid_cchq_access_token(user)  # revokes access-0

            assert client.verify_hq_access()
            assert client.access_token == "access-1"
            assert hq.exchanges == 1
            assert _stored_session_oauth(session_key)["access_token"] == "access-1"

    def test_session_that_refreshed_on_its_own_before_this_fix_still_refreshes(self, rf):
        """Sessions in flight at deploy: the old code refreshed the session alone,
        which revoked the row's pair. The session's newer refresh_token is redeemed
        first, and the dead row is overwritten, not left for the next scheduled run."""
        user = User.objects.create(username="legacy")
        session_key = _connect(user, expires_in=-120)
        hq = FakeCCHQ("access-0", "refresh-0")
        with hq.serving():
            # Stand-in for the old session-only refresh: HQ rotated the pair, and only the session saw it.
            legacy = hq.post("/oauth/token/", {"refresh_token": "refresh-0"}, 30).json()
            session = SessionStore(session_key=session_key)
            session["commcare_oauth"] = _oauth(legacy["access_token"], legacy["refresh_token"], -60)
            session.save()

            assert _web_client(rf, user, session_key).check_token_valid()

            assert hq.rejected_refreshes == 0
            row = UserCCHQToken.objects.get(user=user)
            assert row.access_token == "access-2"
            assert row.access_token in hq.live_access

    def test_user_with_no_row_gets_one_on_first_refresh(self, rf):
        user = User.objects.create(username="pre-912")
        session_key = _connect(user, expires_in=-60)
        UserCCHQToken.objects.filter(user=user).delete()

        with FakeCCHQ("access-0", "refresh-0").serving():
            assert _web_client(rf, user, session_key).check_token_valid()

        assert UserCCHQToken.objects.get(user=user).access_token == "access-1"
        assert get_valid_cchq_access_token(user) == "access-1"

    def test_disconnected_session_stays_disconnected_even_though_the_row_remains(self, rf):
        """labs_commcare_logout clears only the session, and the row stays for
        schedules. The web path must not reconnect a user from the row alone."""
        user = User.objects.create(username="disconnected")
        session_key = _connect(user)
        session = SessionStore(session_key=session_key)
        session.pop("commcare_oauth")
        session.save()

        client = _web_client(rf, user, session_key)
        assert client.access_token is None
        assert client.check_token_valid() is False


@pytest.mark.django_db
class TestRefreshLocking:
    """Refreshes are serialized per user, across the web and headless paths, by cchq_refresh_lock."""

    def test_lock_loser_takes_the_winners_token_from_the_db_not_its_own_session(self, rf):
        user = User.objects.create(username="loser")
        session_key = _connect(user, expires_in=-60)
        _expire_row(user)
        client = _web_client(rf, user, session_key)

        @contextlib.contextmanager
        def winner_lands_while_we_wait(key, **kwargs):
            # The winning request refreshed while we waited. Its write reached the
            # row, but not this request's in-memory session.
            UserCCHQToken.objects.filter(user=user).update(
                access_token="winners-access",
                refresh_token="winners-refresh",
                expires_at=timezone.now() + timedelta(minutes=15),
            )
            yield True

        with (
            patch(LOCK, winner_lands_while_we_wait),
            patch("connect_labs.labs.integrations.commcare.cchq_tokens._exchange_refresh_token") as exchange,
        ):
            assert client._refresh_token() is True

        exchange.assert_not_called()
        assert client.access_token == "winners-access"
        assert _stored_session_oauth(session_key)["access_token"] == "winners-access"

    def test_lock_busy_and_no_fresh_token_fails_without_exchanging(self, rf):
        user = User.objects.create(username="busy")
        session_key = _connect(user, expires_in=-60)
        _expire_row(user)
        client = _web_client(rf, user, session_key)

        with (
            patch(LOCK, return_value=contextlib.nullcontext(False)),
            patch("connect_labs.labs.integrations.commcare.cchq_tokens._exchange_refresh_token") as exchange,
        ):
            assert client._refresh_token() is False

        exchange.assert_not_called()

    def test_broken_lock_backend_degrades_to_an_unlocked_refresh(self, rf):
        user = User.objects.create(username="redis-down")
        session_key = _connect(user, expires_in=-60)
        _expire_row(user)

        with patch(LOCK, side_effect=RuntimeError("redis is down")), FakeCCHQ("access-0", "refresh-0").serving():
            assert _web_client(rf, user, session_key).check_token_valid()

        assert UserCCHQToken.objects.get(user=user).access_token == "access-1"

    def test_no_request_context_fails_without_locking(self):
        client = CommCareDataAccess(None, domain="demo")
        client.commcare_oauth = {"refresh_token": "whatever"}

        with patch(LOCK) as lock:
            assert client._refresh_token() is False

        lock.assert_not_called()


@pytest.mark.django_db(transaction=True)
def test_concurrent_refreshes_redeem_the_refresh_token_once(rf):
    """One page load's auth-status check and two SSE streams, plus a scheduled run,
    all find the token expired at once. HQ must see exactly one redemption, and
    every caller must end up with the token it issued."""
    user = User.objects.create(username="stampede")
    session_key = _connect(user, expires_in=-60)
    _expire_row(user)

    hq = FakeCCHQ("access-0", "refresh-0", exchange_delay=0.2)
    barrier = threading.Barrier(4)
    results, errors = {}, []

    def web(name):
        try:
            client = _web_client(rf, user, session_key)
            barrier.wait()
            results[name] = client.access_token if client.check_token_valid() else None
        except Exception as exc:  # surfaced below; a thread's exception would otherwise vanish
            errors.append(exc)
        finally:
            connection.close()

    def headless():
        try:
            barrier.wait()
            results["schedule"] = get_valid_cchq_access_token(user)
        except Exception as exc:
            errors.append(exc)
        finally:
            connection.close()

    with patch(LOCK, _thread_lock_factory()), hq.serving():
        threads = [threading.Thread(target=web, args=(f"web-{i}",)) for i in range(3)]
        threads.append(threading.Thread(target=headless))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

    assert not errors, errors
    assert hq.exchanges == 1
    assert hq.rejected_refreshes == 0
    assert set(results.values()) == {"access-1"}, results
    assert UserCCHQToken.objects.get(user=user).access_token == "access-1"
    assert _stored_session_oauth(session_key)["access_token"] == "access-1"
