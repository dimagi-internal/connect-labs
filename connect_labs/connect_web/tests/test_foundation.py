"""The foundation under the pages and the player: how many outbound calls each view makes (cold and
warm), what invalidates what, form sessions across tabs, a cache outage, the error copy for an HQ
user Connect couldn't create, time budgets, pooled clients, signature v2, early 413s, and the
query count of the worker picker.
"""

import base64
import hashlib
import hmac
import json
import threading
import time

import httpx
import pytest
from django.contrib.messages import get_messages
from django.core.cache import cache
from django.db import connection
from django.test import Client
from django.urls import reverse

from connect_labs.connect_web import cache as lcache
from connect_labs.connect_web import formplayer, http, service
from connect_labs.connect_web.errors import (
    FAILED_USER_CREATE,
    SIGN_IN_BUSY,
    ConnectAPIError,
    HQUserNotCreated,
    PersonalIDError,
)
from connect_labs.connect_web.models import FormSession, PoolWorker, WorkerSession

from . import fake_formplayer, fakes

pytestmark = pytest.mark.django_db

OPP = fakes.OPP_UUID
SESSION_ID = fake_formplayer.fixture("form")["session_id"]
OTHER_SESSION_ID = "9d1e7c3a-2b4f-4a6e-8c0d-1f2e3a4b5c6d"


@pytest.fixture
def acting(client, staff, pool_worker):
    client.force_login(staff)
    service.check_out(staff, pool_worker)
    return client


def _session(staff) -> WorkerSession:
    """The checkout as a new request loads it (a fresh object: no per-request memo)."""
    return service.current_session(staff)


def _connect_paths(servers) -> list[str]:
    return [r.url.path for r in servers.connect_calls()]


def _claim(servers):
    servers.opportunities = [fakes.opportunity(claim={"max_payments": 20, "date_claimed": "2026-11-05"})]


def _learning_done(servers):
    servers.learn_progress = {
        "completed_modules": [{"module": 1}, {"module": 2}],
        "assessments": [{"score": 80, "passing_score": 70, "passed": True, "date": "2026-11-03"}],
    }


def _api(name, kind="learn"):
    return reverse(f"connect_web:api_{name}", args=[OPP, kind])


def _post(client, name, body=None, kind="learn"):
    return client.post(_api(name, kind), data=json.dumps(body or {}), content_type="application/json")


# -- outbound call counts -------------------------------------------------------------------------


def test_opportunity_detail_is_two_connect_calls_cold_and_none_warm(acting, servers):
    acting.get(reverse("connect_web:opportunity", args=[OPP]))
    assert sorted(_connect_paths(servers)) == sorted(["/api/opportunity/", f"/api/opportunity/{OPP}/learn_progress"])
    servers.requests.clear()
    response = acting.get(reverse("connect_web:opportunity", args=[OPP]))
    assert response.status_code == 200 and response.context["detail"]["learn"]["modules_done"] == 1
    assert servers.requests == []


def test_claimed_detail_reads_both_progresses_at_once(acting, servers):
    _claim(servers)
    started = []
    both = threading.Barrier(2, timeout=5)

    def handle(request):
        if request.url.path.endswith("_progress"):
            started.append(request.url.path)
            both.wait()  # only passes if learn and delivery progress are in flight together
        return servers.handle(request)

    http.HTTP_TRANSPORT = httpx.MockTransport(handle)
    response = acting.get(reverse("connect_web:opportunity", args=[OPP]))
    assert response.status_code == 200 and len(response.context["detail"]["deliveries"]) == 2
    assert sorted(started) == [f"/api/opportunity/{OPP}/delivery_progress", f"/api/opportunity/{OPP}/learn_progress"]
    assert len(servers.connect_calls()) == 3


def test_one_request_never_fetches_the_same_thing_twice(staff, pool_worker, servers):
    service.check_out(staff, pool_worker)
    session = _session(staff)
    lcache.django_cache = _BrokenCache()  # no shared cache at all: only the per-request memo
    try:
        service.opportunity_detail(session, OPP)
        service.get_opportunity(session, OPP)
        service.form_engine_context(session, OPP, "learn", with_token=False)
    finally:
        lcache.django_cache = cache
    assert _connect_paths(servers).count("/api/opportunity/") == 1


def test_form_page_and_first_navigation_make_one_list_call(acting, servers):
    page = acting.get(reverse("connect_web:form_entry", args=[OPP, "learn"]))
    assert page.status_code == 200 and "player_config" in page.context
    response = _post(acting, "navigate", {"selections": [], "start": True})
    assert response.status_code == 200, response.content
    assert _connect_paths(servers).count("/api/opportunity/") == 1
    assert servers.profiles_served == 1


def test_progress_poll_after_a_submit_is_one_connect_call(acting, servers):
    acting.get(reverse("connect_web:opportunity", args=[OPP]))
    _post(acting, "navigate", {"selections": ["0", "1"]})
    assert _post(acting, "submit", {"session_id": SESSION_ID, "answers": {}}).json()["status"] == "success"
    page = acting.get(reverse("connect_web:opportunity", args=[OPP]) + "?tab=learn&submitted=learn")
    assert page.context["progress_url"].endswith("?kind=learn")
    servers.requests.clear()
    acting.get(page.context["progress_url"])
    assert _connect_paths(servers) == [f"/api/opportunity/{OPP}/learn_progress"]


# -- invalidation ---------------------------------------------------------------------------------


def test_a_submitted_form_is_awaited_so_progress_bypasses_the_cache(acting, servers, staff):
    acting.get(reverse("connect_web:opportunity", args=[OPP]))
    _post(acting, "navigate", {"selections": ["0", "1"]})
    _post(acting, "submit", {"session_id": SESSION_ID, "answers": {}})
    assert lcache.awaiting(_session(staff), OPP)
    servers.learn_progress["completed_modules"].append({"module": 2})  # Connect counts the form
    servers.requests.clear()
    for _ in range(2):  # every load while awaited reads Connect, not the cache
        detail = acting.get(reverse("connect_web:opportunity", args=[OPP])).context["detail"]
        assert detail["learn"]["modules_done"] == 2
    assert _connect_paths(servers).count(f"/api/opportunity/{OPP}/learn_progress") == 2


def test_start_learning_drops_the_list_and_learn_progress(acting, servers, staff):
    acting.get(reverse("connect_web:opportunity", args=[OPP]))
    acting.post(reverse("connect_web:start_learning", args=[OPP]))
    session = _session(staff)
    assert lcache.lookup(session, "opps") is None and lcache.lookup(session, lcache.learn_name(OPP)) is None


def test_claim_drops_list_progress_and_targets(acting, servers, staff):
    _learning_done(servers)
    acting.get(reverse("connect_web:form_entry", args=[OPP, "learn"]))
    session = _session(staff)
    assert lcache.lookup(session, lcache.target_name(OPP, "learn"))
    _claim(servers)  # what Connect will say after the claim
    acting.post(reverse("connect_web:claim", args=[OPP]))
    session = _session(staff)
    for name in ("opps", lcache.learn_name(OPP), lcache.delivery_name(OPP), lcache.target_name(OPP, "learn")):
        assert lcache.lookup(session, name) is None, name
    # The deliver tab is right at once (the claim is in the fresh list).
    assert acting.get(reverse("connect_web:opportunity", args=[OPP])).context["detail"]["claimed"] is True


def test_confirming_a_payment_drops_the_list_and_delivery_progress(acting, servers, staff):
    _claim(servers)
    acting.get(reverse("connect_web:opportunity", args=[OPP]))
    session = _session(staff)
    assert lcache.lookup(session, lcache.delivery_name(OPP)) is not None
    acting.post(
        reverse("connect_web:confirm_payment", args=[OPP]), {"payment_id": "9b0c-pay-uuid", "decision": "confirm"}
    )
    session = _session(staff)
    assert lcache.lookup(session, "opps") is None and lcache.lookup(session, lcache.delivery_name(OPP)) is None


def test_a_new_invite_is_found_even_with_a_cached_list(acting, servers):
    acting.get(reverse("connect_web:opportunities"))
    servers.opportunities.append(fakes.opportunity(id=78, opportunity_id="new-opp-uuid", name="New one"))
    servers.requests.clear()
    response = acting.get(reverse("connect_web:form_entry", args=["new-opp-uuid", "learn"]))
    assert response.context["opp"]["name"] == "New one"
    assert _connect_paths(servers) == ["/api/opportunity/"]


def test_an_engine_error_forgets_the_target_and_build(acting, servers, staff):
    _post(acting, "navigate")
    session = _session(staff)
    assert lcache.lookup(session, lcache.target_name(OPP, "learn"))
    servers.formplayer.responses["navigate_menu"] = "engine_app_error"
    assert _post(acting, "navigate", {"selections": ["0"]}).status_code == 502
    session = _session(staff)
    assert lcache.lookup(session, lcache.target_name(OPP, "learn")) is None
    assert lcache.get_build("www.commcarehq.org", "nets-learn", "learnapp123") is None


def test_no_released_build_is_remembered_briefly(acting, servers):
    servers.profile_status = 404
    for _ in range(3):
        assert _post(acting, "navigate").json()["error"]["code"] == "NO_RELEASED_BUILD"
    assert servers.profiles_served == 1  # the 30-second negative entry
    servers.profile_status = 200
    cache.delete(lcache._build_key("www.commcarehq.org", "nets-learn", "learnapp123"))  # 30 s later
    assert _post(acting, "navigate").status_code == 200


# -- form sessions across tabs (P0-1) -------------------------------------------------------------


def test_two_tabs_keep_both_form_sessions(acting, servers):
    tab_two = Client()
    tab_two.cookies = acting.cookies  # the same browser: same labs session, same checkout
    first = _post(acting, "navigate", {"selections": ["0", "1"]})
    assert first.json()["session_id"] == SESSION_ID
    # Labs no longer writes form sessions into the Django session (each request saves it whole).
    assert first.wsgi_request.session.modified is False

    second_form = fake_formplayer.fixture("form") | {"session_id": OTHER_SESSION_ID}
    servers.formplayer.responses["navigate_menu"] = second_form
    servers.formplayer.open_sessions.add(OTHER_SESSION_ID)
    assert _post(tab_two, "navigate", {"selections": ["0", "2"]}).json()["session_id"] == OTHER_SESSION_ID
    del servers.formplayer.responses["navigate_menu"]

    for client, session_id in ((acting, SESSION_ID), (tab_two, OTHER_SESSION_ID)):
        response = _post(client, "answer", {"session_id": session_id, "ix": "1", "answer": "x"})
        assert response.status_code == 200, (session_id, response.content)
    assert FormSession.objects.count() == 2


def test_a_released_checkout_forgets_its_form_sessions(acting, servers, staff):
    _post(acting, "navigate", {"selections": ["0", "1"]})
    assert FormSession.objects.count() == 1
    acting.post(reverse("connect_web:release"))
    assert FormSession.objects.count() == 0


# -- a cache outage degrades to direct fetches ----------------------------------------------------


class _BrokenCache:
    """A cache backend whose every call fails, as Redis does when it is down."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise ConnectionError("redis down")

        return fail


class _SilentCache:
    """django_redis with IGNORE_EXCEPTIONS while Redis is down: every call answers None."""

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.mark.parametrize("backend", [_BrokenCache, _SilentCache])
def test_the_pages_and_player_work_with_the_cache_down(acting, servers, monkeypatch, backend):
    monkeypatch.setattr(lcache, "django_cache", backend())
    assert acting.get(reverse("connect_web:opportunities")).status_code == 200
    detail = acting.get(reverse("connect_web:opportunity", args=[OPP])).context["detail"]
    assert detail["learn"]["modules_done"] == 1
    assert acting.get(reverse("connect_web:form_entry", args=[OPP, "learn"])).context["player_config"]
    assert _post(acting, "navigate", {"start": True}).status_code == 200
    _post(acting, "navigate", {"selections": ["0", "1"]})
    assert _post(acting, "answer", {"session_id": SESSION_ID, "ix": "1", "answer": "x"}).status_code == 200
    assert _post(acting, "submit", {"session_id": SESSION_ID, "answers": {}}).json()["status"] == "success"
    # Signed in once (the row-lock fallback), not once per request.
    assert len(servers.calls("/users/recover/complete_recovery")) == 1


# -- the HQ user Connect couldn't create (P0-3) ---------------------------------------------------


def test_failed_user_create_on_start_learning_names_the_learn_domain(acting, servers):
    servers.start_learn_failure = (502, FAILED_USER_CREATE)
    response = acting.post(reverse("connect_web:start_learning", args=[OPP]), {"open_player": "1"})
    assert response["Location"].endswith("?tab=learn")  # not on to the player
    (message,) = (str(m) for m in get_messages(response.wsgi_request))
    assert message == (
        "CommCare HQ refused to create this worker's mobile user in the project space nets-learn, so learning "
        "can't start. Trying again won't help until it's fixed on HQ: ask the opportunity's admin to check that "
        "(1) the project space has the COMMCARE_CONNECT feature flag, and (2) the API key Connect uses for this "
        "opportunity belongs to an HQ web user who can edit mobile workers in nets-learn. The phone app would "
        "fail the same way."
    )
    assert "try again" not in message.lower().replace("trying again won't help", "")


def test_failed_user_create_on_claim_names_the_deliver_domain(staff, pool_worker, servers):
    _learning_done(servers)
    servers.claim_failure = (400, FAILED_USER_CREATE)
    with pytest.raises(HQUserNotCreated) as caught:
        service.claim(service.check_out(staff, pool_worker), OPP)
    text = caught.value.user_message
    assert "project space nets-deliver, so this opportunity can't be claimed" in text
    assert "edit mobile workers in nets-deliver" in text
    assert caught.value.error_code == FAILED_USER_CREATE and isinstance(caught.value, ConnectAPIError)


def test_failed_user_create_is_not_retryable_in_the_api():
    from connect_labs.connect_web.api import _status_for

    assert _status_for(HQUserNotCreated(status=502, domain="d", action="learn")) == (502, False)


# -- concurrency (separate connections, real commits) ---------------------------------------------


def _in_threads(*targets):
    errors = []

    def run(target):
        try:
            target()
        except Exception as exc:  # noqa: BLE001 — reported below
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=run, args=(t,)) for t in targets]
    for t in threads:
        t.start()
    for t in threads:
        t.join(20)
    assert not errors, errors


@pytest.mark.django_db(transaction=True)
def test_two_domains_hq_tokens_refreshed_at_once_both_survive(staff, pool_worker, servers):
    session = service.check_out(staff, pool_worker)
    service.ensure_signed_in(session)
    both = threading.Barrier(2, timeout=10)
    servers.hq_token_hook = lambda domain: both.wait()  # both fetched before either is saved

    _in_threads(
        lambda: service.hq_token(_session(staff), "nets-learn"),
        lambda: service.hq_token(_session(staff), "nets-deliver"),
    )
    tokens = PoolWorker.objects.get(pk=pool_worker.pk).get_hq_tokens()
    assert set(tokens) == {"www.commcarehq.org/nets-learn", "www.commcarehq.org/nets-deliver"}
    assert servers.hq_issued == 2


def test_a_sign_in_while_another_is_in_flight_never_recovers_twice(staff, pool_worker, servers, monkeypatch):
    """Single flight without a transaction held across the calls: while one request is recovering,
    another for the same worker waits on the lock (here: gives up quickly) instead of recovering too."""
    service.check_out(staff, pool_worker)
    monkeypatch.setattr(service, "SIGN_IN_WAIT_SECONDS", 0.3)
    nested = []

    def handle(request):
        if request.url.path == "/users/recover/complete_recovery" and not nested:
            # No transaction (savepoint) is open across the call: only the test's own.
            assert connection.savepoint_ids == outer_savepoints
            with pytest.raises(PersonalIDError) as caught:  # the second request, mid-recovery
                service.ensure_signed_in(_session(staff))
            nested.append(caught.value.error_code)
        return servers.handle(request)

    outer_savepoints = list(connection.savepoint_ids)
    http.HTTP_TRANSPORT = httpx.MockTransport(handle)
    token = service.ensure_signed_in(_session(staff))
    assert nested == [SIGN_IN_BUSY]
    assert len(servers.calls("/users/recover/complete_recovery")) == 1
    # Once the first is done, the next request just uses its token.
    assert service.ensure_signed_in(_session(staff)) == token
    assert retryable_status(SIGN_IN_BUSY)


def retryable_status(code):
    from connect_labs.connect_web.api import _status_for

    return _status_for(PersonalIDError(error_code=code))[1]


# -- restore freshness: sync-db when the player starts --------------------------------------------


def test_the_player_start_syncs_when_labs_has_not_recently(acting, servers):
    _post(acting, "navigate", {"start": True})
    assert [e for e, _ in servers.formplayer.bodies] == ["sync-db", "navigate_menu_start"]
    _post(acting, "navigate", {"start": True})  # a reload a minute later: no second sync
    _post(acting, "navigate")  # the menu again (Back): never a sync
    assert [e for e, _ in servers.formplayer.bodies].count("sync-db") == 1


def test_a_stale_sync_runs_again_and_a_failed_one_does_not_stop_the_player(acting, servers, staff, monkeypatch):
    _post(acting, "navigate", {"start": True})
    monkeypatch.setattr(time, "time", lambda real=time.time: real() + formplayer.SYNC_EVERY_SECONDS + 1)
    servers.formplayer.responses["sync-db"] = (500, {"status": "error", "exception": "restore failed"})
    response = _post(acting, "navigate", {"start": True})
    assert response.status_code == 200 and response.json()["type"] == "commands"
    assert [e for e, _ in servers.formplayer.bodies].count("sync-db") == 2
    del servers.formplayer.responses["sync-db"]
    _post(acting, "navigate", {"start": True})  # a failed sync isn't recorded: tried again next start
    assert [e for e, _ in servers.formplayer.bodies].count("sync-db") == 3


# -- time budgets and pooled clients --------------------------------------------------------------


def test_one_pooled_client_is_reused(servers):
    servers.valid_access_tokens.add("tok")
    from connect_labs.connect_web.connect_api import ConnectClient

    first = http.client()
    ConnectClient("tok").list_opportunities()
    ConnectClient("tok").learn_progress(OPP)
    assert http.client() is first
    assert http.shared_client("formplayer") is not first


def test_a_retry_is_not_started_past_the_budget(monkeypatch):
    seen = []
    clock = [0.0]
    monkeypatch.setattr(http.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(http.time, "sleep", lambda s: clock.__setitem__(0, clock[0] + s))

    def slow_503(request):
        seen.append(request.extensions["timeout"]["read"])
        clock[0] += 12.0  # each attempt takes 12 s of the 20 s budget
        return httpx.Response(503)

    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(slow_503))
    with pytest.raises(ConnectAPIError) as caught:
        http.request("GET", "https://connect.test/x", ConnectAPIError, log_name="t")
    # The second attempt gets only what is left (8 s); there is no time for a third.
    assert seen == [15.0, 8.0] and caught.value.status == 503


def test_a_read_timeout_on_the_list_is_not_repeated(monkeypatch, servers):
    seen = []

    def hang(request):
        seen.append(request)
        raise httpx.ReadTimeout("slow", request=request)

    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(hang))
    from connect_labs.connect_web.connect_api import ConnectClient

    with pytest.raises(ConnectAPIError) as caught:
        ConnectClient("tok").list_opportunities()
    assert len(seen) == 1 and "too long" in str(caught.value)
    seen.clear()
    with pytest.raises(ConnectAPIError):
        ConnectClient("tok").learn_progress(OPP)  # a quick read may be retried
    assert len(seen) == http.GET_ATTEMPTS


def test_each_engine_call_has_its_own_time_budget(servers):
    timeouts = {}

    def handle(request):
        timeouts[request.url.path] = request.extensions["timeout"]["read"]
        return servers.handle(request)

    http.HTTP_TRANSPORT = httpx.MockTransport(handle)
    fp = formplayer.Formplayer(_TARGET, "hq-token-abc")
    formplayer.navigate(fp, [])
    formplayer.answer(fp, SESSION_ID, "1", "x")
    assert timeouts == {"/navigate_menu_start": 75.0, "/answer": 30.0}


# -- signature v2 ---------------------------------------------------------------------------------

_TARGET = formplayer.AppTarget(
    opportunity_id=OPP,
    app_kind="learn",
    hq_base=fakes.HQ,
    domain="nets-learn",
    cc_app_id="learnapp123",
    build_id=fakes.BUILD_IDS["learnapp123"],
    hq_username="pooluserone@nets-learn.commcarehq.org",
)


def test_signature_v2_test_vector():
    body = b'{"username":"u","domain":"d"}'
    canonical = formplayer.canonical_v2(
        "POST", "/navigate_menu", 1764000000, "hq-token-abc", "https://www.commcarehq.org", body
    )
    assert canonical == (
        b"v2\nPOST\n/navigate_menu\n1764000000\n"
        + hashlib.sha256(b"hq-token-abc").hexdigest().encode()
        + b"\nhttps://www.commcarehq.org\n"
        + body
    )
    expected = base64.b64encode(hmac.new(b"secret", canonical, hashlib.sha256).digest()).decode()
    signature = formplayer.sign_v2(
        "secret", "POST", "/navigate_menu", 1764000000, "hq-token-abc", "https://www.commcarehq.org", body
    )
    assert signature == expected
    # Pinned for Formplayer's LabsAuthFilter tests: the same inputs must give the same signature there.
    assert signature == "+JY/F05Pq0QN1CTixn8r8nzKZuDxRILSlI656rh2US8="
    # An empty HQ host is signed as an empty line.
    assert formplayer.canonical_v2("post", "/answer", 1, "t", None, b"").startswith(b"v2\nPOST\n/answer\n1\n")


def test_every_engine_call_carries_v1_and_v2(servers, monkeypatch):
    monkeypatch.setattr(formplayer, "_now", lambda: 1764000123)
    servers.formplayer.require_v2 = True
    servers.formplayer.open_sessions.add(SESSION_ID)
    fp = formplayer.Formplayer(_TARGET, "hq-token-abc")
    monkeypatch.setattr(fake_formplayer.time, "time", lambda: 1764000100)
    formplayer.navigate(fp, [])
    formplayer.answer_media(fp, SESSION_ID, "16", content=b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    request = servers.formplayer.requests[0]
    assert request.headers["X-Labs-Timestamp"] == "1764000123"
    assert request.headers["X-Labs-Signature"] == formplayer.sign(fake_formplayer.KEY, request.content)
    assert request.headers["X-Labs-Signature-V2"] == formplayer.sign_v2(
        fake_formplayer.KEY,
        "POST",
        "/navigate_menu_start",
        1764000123,
        "hq-token-abc",
        request.headers["X-Labs-HQ-Host"],
        request.content,
    )
    assert servers.formplayer.v2_verified == 2  # the multipart answer_media signs its JSON part too


def test_a_replayed_request_fails_v2(servers, monkeypatch):
    monkeypatch.setattr(formplayer, "_now", lambda: 1764000000)
    monkeypatch.setattr(fake_formplayer.time, "time", lambda: 1764000000 + 301)
    from connect_labs.connect_web.errors import FormplayerError

    with pytest.raises(FormplayerError) as caught:
        formplayer.navigate(formplayer.Formplayer(_TARGET, "hq-token-abc"), [])
    assert caught.value.error_code == "ENGINE_AUTH_FAILED"


# -- early 413s (P1-8) ----------------------------------------------------------------------------


def test_an_oversized_json_body_is_refused_from_its_length(acting, servers):
    response = acting.post(
        _api("answer"), data=b"{}", content_type="application/json", CONTENT_LENGTH=str(3 * 1024 * 1024)
    )
    assert response.status_code == 413 and response.json()["error"]["code"] == "TOO_LARGE"
    assert servers.requests == []  # refused before anything else, even the opportunity list


def test_an_oversized_upload_is_refused_before_it_is_spooled(staff, pool_worker, servers, monkeypatch):
    from django.http import request as django_request

    def never(*args, **kwargs):
        raise AssertionError("the upload was parsed")

    strict = Client(enforce_csrf_checks=True)  # the CSRF check must not read the body first either
    strict.force_login(staff)
    service.check_out(staff, pool_worker)
    monkeypatch.setattr(django_request.HttpRequest, "_load_post_and_files", never)
    response = strict.post(
        _api("answer_media"),
        data=b"x",
        content_type="multipart/form-data; boundary=zzz",
        CONTENT_LENGTH=str(formplayer.media_max_bytes() + 1024 * 1024),
    )
    assert response.status_code == 413 and "photo" in response.json()["error"]["message"]


def test_a_bad_content_length_is_a_400(acting):
    response = acting.post(_api("answer"), data=b"{}", content_type="application/json", CONTENT_LENGTH="lots")
    assert response.status_code == 400


# -- the picker and last-used ---------------------------------------------------------------------


def test_the_worker_picker_makes_a_fixed_number_of_queries(client, staff, other_staff, django_assert_max_num_queries):
    from connect_labs.users.models import User

    for i in range(2, 12):
        worker = PoolWorker.objects.create(phone_number=f"+7426000000{i:02}", display_name=f"W{i}", backup_code="1")
        user = User.objects.create(username=f"u{i}", email=f"u{i}@dimagi.com")
        service.check_out(user, worker)
    client.force_login(staff)
    client.get(reverse("connect_web:workers"))  # warm-up (session, user)
    # Session, user, my checkout, every open checkout with its user, the workers — plus labs' own
    # per-request queries; none of it per worker (it was ~4 a worker).
    with django_assert_max_num_queries(10):
        response = client.get(reverse("connect_web:workers"))
    assert response.status_code == 200 and response.content.decode().count("Checked out by") == 10


def test_player_calls_keep_the_checkout_from_looking_idle(acting, staff, servers):
    from datetime import timedelta

    from django.utils import timezone

    old = timezone.now() - timedelta(minutes=30)
    WorkerSession.objects.filter(user=staff).update(last_used_at=old)
    _post(acting, "navigate")
    assert _session(staff).last_used_at > old + timedelta(minutes=29)
    # Throttled: at most one write a minute.
    counter = _UpdateCounter()
    with connection.execute_wrapper(counter):
        _post(acting, "navigate")
    assert counter.last_used == 0


class _UpdateCounter:
    def __init__(self):
        self.last_used = 0

    def __call__(self, execute, sql, params, many, context):
        if sql.startswith("UPDATE") and "last_used_at" in sql:
            self.last_used += 1
        return execute(sql, params, many, context)


# -- the data migration ---------------------------------------------------------------------------


def test_the_migration_moves_an_open_sessions_sign_in_to_its_worker(staff):
    """Run inside the test's transaction (Postgres DDL is transactional): nothing outlives the test."""
    from django.db.migrations.executor import MigrationExecutor

    before = [("connect_web", "0001_initial")]
    after = [("connect_web", "0002_sign_in_on_pool_worker_and_form_sessions")]
    executor = MigrationExecutor(connection)
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    Worker, Session = old.get_model("connect_web", "PoolWorker"), old.get_model("connect_web", "WorkerSession")
    worker = Worker.objects.create(phone_number="+74260000009", display_name="M", backup_code="1")
    idle = Worker.objects.create(phone_number="+74260000008", display_name="N", backup_code="1")
    Session.objects.create(
        pool_worker=worker,
        user_id=staff.pk,
        personalid_password="pw",
        access_token="tok",
        hq_tokens='{"h/d": {"token": "hq", "expires_at": "2030-01-01T00:00:00+00:00"}}',
    )
    with connection.cursor() as cursor:
        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")  # the deferred FK checks, before ALTER TABLE
    MigrationExecutor(connection).migrate(after)
    moved = PoolWorker.objects.get(pk=worker.pk)
    assert (moved.personalid_password, moved.access_token) == ("pw", "tok")
    assert moved.get_hq_tokens()["h/d"]["token"] == "hq"
    assert PoolWorker.objects.get(pk=idle.pk).personalid_password == ""
