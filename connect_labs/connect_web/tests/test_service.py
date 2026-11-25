from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.connect_web import service
from connect_labs.connect_web.errors import (
    LEARNING_INCOMPLETE,
    ConnectAPIError,
    GuardRefused,
    IncorrectBackupCode,
    NotConfigured,
    WorkerUnavailable,
)
from connect_labs.connect_web.models import PoolWorker, WorkerSession

from . import fakes

pytestmark = pytest.mark.django_db


@pytest.fixture
def session(staff, pool_worker):
    return service.check_out(staff, pool_worker)


def _learning_done(servers):
    servers.learn_progress = {
        "completed_modules": [{"module": 1}, {"module": 2}],
        "assessments": [{"score": 80, "passing_score": 70, "passed": True, "date": "2026-11-03"}],
    }


# -- checking out ---------------------------------------------------------------------------


def test_check_out_and_release(staff, pool_worker):
    session = service.check_out(staff, pool_worker)
    assert service.current_session(staff) == session
    assert service.check_out(staff, pool_worker) == session  # idempotent
    session.access_token = "x"
    session.personalid_password = "y"
    session.save()
    service.release(session)
    session.refresh_from_db()
    assert session.ended_at and session.access_token == "" and session.personalid_password == ""
    assert service.current_session(staff) is None


def test_someone_elses_worker_is_refused_until_idle(staff, other_staff, pool_worker):
    theirs = service.check_out(other_staff, pool_worker)
    with pytest.raises(WorkerUnavailable, match="Col League"):
        service.check_out(staff, pool_worker)
    with pytest.raises(WorkerUnavailable):
        service.check_out(staff, pool_worker, take_over=True)  # not idle yet
    WorkerSession.objects.filter(pk=theirs.pk).update(last_used_at=timezone.now() - timedelta(hours=2))
    mine = service.check_out(staff, pool_worker, take_over=True)
    theirs.refresh_from_db()
    assert theirs.ended_at is not None and mine.user == staff


def test_checking_out_another_worker_ends_your_first(staff, pool_worker):
    first = service.check_out(staff, pool_worker)
    other = PoolWorker.objects.create(phone_number="+74260000002", display_name="Two", backup_code="x")
    second = service.check_out(staff, other)
    first.refresh_from_db()
    assert first.ended_at is not None and service.current_session(staff) == second


def test_inactive_worker_cannot_be_checked_out(staff, pool_worker):
    pool_worker.active = False
    pool_worker.save()
    with pytest.raises(WorkerUnavailable):
        service.check_out(staff, pool_worker)


# -- signing in -----------------------------------------------------------------------------


def test_first_sign_in_recovers_then_caches(servers, session):
    token = service.ensure_signed_in(session)
    assert [r.url.path for r in servers.requests] == [
        "/users/start_configuration",
        "/users/recover/complete_recovery",
        "/o/token/",
    ]
    start = servers.body(servers.calls("/users/start_configuration")[0])
    assert start["cc_device_id"] == str(session.pool_worker.device_id)
    session.refresh_from_db()
    assert session.access_token == token
    assert session.personalid_password == "rotated-password-1"
    assert session.pool_worker.personalid_username == fakes.USERNAME
    # Cached: no more calls.
    assert service.ensure_signed_in(WorkerSession.objects.get(pk=session.pk)) == token
    assert len(servers.requests) == 3


def test_expired_token_uses_the_password_grant_not_recovery(servers, session):
    service.ensure_signed_in(session)
    WorkerSession.objects.filter(pk=session.pk).update(access_token_expires_at=timezone.now() + timedelta(seconds=30))
    servers.requests.clear()
    fresh = WorkerSession.objects.get(pk=session.pk)
    token = service.ensure_signed_in(fresh)
    assert [r.url.path for r in servers.requests] == ["/o/token/"]
    assert token == fresh.access_token and fresh.access_token_expires_at > timezone.now() + timedelta(minutes=30)


def test_invalid_grant_falls_back_to_full_recovery(servers, session):
    service.ensure_signed_in(session)
    servers.passwords[fakes.USERNAME] = "rotated-elsewhere"  # someone signed in on a phone
    WorkerSession.objects.filter(pk=session.pk).update(access_token_expires_at=timezone.now())
    servers.requests.clear()
    fresh = WorkerSession.objects.get(pk=session.pk)
    service.ensure_signed_in(fresh)
    assert [r.url.path for r in servers.requests] == [
        "/o/token/",
        "/users/start_configuration",
        "/users/recover/complete_recovery",
        "/o/token/",
    ]
    fresh.refresh_from_db()
    assert fresh.personalid_password == "rotated-password-2"


def test_wrong_backup_code_blocks_sign_in_and_is_never_retried(servers, session):
    servers.backup_code = "999999"
    with pytest.raises(IncorrectBackupCode):
        service.ensure_signed_in(session)
    worker = PoolWorker.objects.get(pk=session.pool_worker_id)
    assert worker.sign_in_blocked_reason
    servers.requests.clear()
    with pytest.raises(WorkerUnavailable):
        service.ensure_signed_in(WorkerSession.objects.get(pk=session.pk))
    assert servers.calls("/users/recover/complete_recovery") == []
    assert servers.attempts_left == 2  # one attempt burned, not more


def test_missing_client_id_fails_before_recovery_rotates_anything(settings, servers, session):
    settings.CONNECT_WEB_PERSONALID_CLIENT_ID = ""
    with pytest.raises(NotConfigured):
        service.ensure_signed_in(session)
    assert servers.requests == []


def test_ended_session_cannot_sign_in(servers, session):
    service.release(session)
    with pytest.raises(WorkerUnavailable):
        service.ensure_signed_in(session)


def test_connect_401_drops_the_token_and_signs_in_again_once(servers, session):
    service.ensure_signed_in(session)
    servers.valid_access_tokens.clear()  # revoked on the server
    opps = service.list_opportunities(session)
    assert opps[0]["opp"]["opportunity_id"] == fakes.OPP_UUID
    paths = [r.url.path for r in servers.requests]
    assert paths.count("/api/opportunity/") == 2 and paths.count("/o/token/") == 2


# -- opportunities --------------------------------------------------------------------------


def test_list_summaries(servers, session):
    servers.opportunities = [
        fakes.opportunity(),
        fakes.opportunity(opportunity_id="u2", claim={"max_payments": 20}, deliver_progress=0),
        fakes.opportunity(opportunity_id="u3", claim={"max_payments": 20}, deliver_progress=5),
    ]
    items = service.list_opportunities(session)
    assert [(i["stage_label"], i["progress_pct"]) for i in items] == [("Learn", 50), ("Claimed", 0), ("Deliver", 25)]


def test_detail_merges_learn_progress_before_claim(servers, session):
    detail = service.opportunity_detail(session, fakes.OPP_UUID)
    assert detail["learn"]["modules_done"] == 1 and detail["learn"]["modules_total"] == 2
    assert [m["done"] for m in detail["learn"]["modules"]] == [True, False]
    assert detail["can_claim"] is False and detail["delivery"] is None
    assert servers.calls(f"/api/opportunity/{fakes.OPP_UUID}/delivery_progress") == []


def test_detail_merges_delivery_progress_after_claim(servers, session):
    servers.opportunities = [fakes.opportunity(claim={"max_payments": 20, "end_date": "2026-12-31"})]
    detail = service.opportunity_detail(session, fakes.OPP_UUID)
    assert len(detail["deliveries"]) == 2
    (payment,) = detail["payments"]
    assert payment["confirm_id"] == "9b0c-pay-uuid" and payment["state"] == "unconfirmed"


def test_unknown_opportunity_is_refused(servers, session):
    with pytest.raises(Exception, match="access"):
        service.opportunity_detail(session, "not-mine")


def test_start_learning_posts_the_uuid_from_connect(servers, session):
    service.start_learning(session, "77")  # by numeric id: the uuid sent is Connect's own
    assert servers.form(servers.calls("/users/start_learn_app/")[0]) == {"opportunity": fakes.OPP_UUID}


# -- the claim guard ------------------------------------------------------------------------


def test_claim_refused_until_modules_done_and_assessment_passed(servers, session):
    with pytest.raises(GuardRefused) as caught:
        service.claim(session, fakes.OPP_UUID)
    assert caught.value.error_code == LEARNING_INCOMPLETE
    assert servers.calls(f"/api/opportunity/{fakes.OPP_UUID}/claim") == []

    # All modules done but the assessment failed: still refused.
    servers.learn_progress = {
        "completed_modules": [{"module": 1}, {"module": 2}],
        "assessments": [{"score": 40, "passing_score": 70, "passed": False, "date": "2026-11-03"}],
    }
    with pytest.raises(GuardRefused):
        service.claim(session, fakes.OPP_UUID)

    # Assessment passed but a module missing: still refused.
    servers.learn_progress = {
        "completed_modules": [{"module": 1}],
        "assessments": [{"score": 90, "passing_score": 70, "passed": True}],
    }
    with pytest.raises(GuardRefused):
        service.claim(session, fakes.OPP_UUID)
    assert servers.calls(f"/api/opportunity/{fakes.OPP_UUID}/claim") == []


def test_claim_once_learning_is_complete(servers, session):
    _learning_done(servers)
    assert service.claim(session, fakes.OPP_UUID) is True
    assert len(servers.calls(f"/api/opportunity/{fakes.OPP_UUID}/claim")) == 1


def test_claim_already_claimed_is_a_no_op(servers, session):
    servers.opportunities = [fakes.opportunity(claim={"max_payments": 20})]
    assert service.claim(session, fakes.OPP_UUID) is False
    assert servers.calls(f"/api/opportunity/{fakes.OPP_UUID}/claim") == []


def test_claim_server_error_is_surfaced(servers, session):
    _learning_done(servers)
    servers.claim_error = "OPPORTUNITY_ENDED"
    with pytest.raises(ConnectAPIError) as caught:
        service.claim(session, fakes.OPP_UUID)
    assert "ended" in caught.value.user_message


def test_confirm_payment(servers, session):
    service.confirm_payment(session, "9b0c-pay-uuid", False)
    assert servers.body(servers.calls("/api/payment/confirm")[0]) == {
        "payments": [{"id": "9b0c-pay-uuid", "confirmed": "false"}]
    }


# -- HQ tokens and the form engine seam -------------------------------------------------------


def test_hq_token_is_cached_per_domain_until_expiry(servers, session):
    first = service.hq_token(session, "nets-learn")
    assert service.hq_token(session, "nets-learn") == first
    assert servers.hq_issued == 1
    other = service.hq_token(session, "nets-deliver")
    assert other != first and servers.hq_issued == 2
    (req,) = [r for r in servers.calls("/oauth/token/") if r.url.host == "www.commcarehq.org"][:1]
    assert servers.form(req)["username"] == "pooluserone@nets-learn.commcarehq.org"

    # Persisted (encrypted) on the session, and re-fetched once expired.
    stored = WorkerSession.objects.get(pk=session.pk)
    assert service.hq_token(stored, "nets-learn") == first
    key = "www.commcarehq.org/nets-learn"
    stored.set_hq_token(key, first, timezone.now() + timedelta(seconds=10))
    stored.save()
    assert service.hq_token(stored, "nets-learn") != first
    assert servers.hq_issued == 3


def test_hq_refused_grant_re_signs_in_once(servers, session):
    service.ensure_signed_in(session)
    servers.valid_access_tokens.clear()
    token = service.hq_token(session, "nets-learn")
    assert token.startswith("hq-")
    assert len([r for r in servers.requests if r.url.host == "www.commcarehq.org"]) == 2


def test_form_engine_context_learn(servers, session):
    ctx = service.form_engine_context(session, fakes.OPP_UUID, "learn")
    assert ctx == {
        "hq_base": fakes.HQ,
        "domain": "nets-learn",
        "app_id": "learnapp123",
        "install_url": f"{fakes.HQ}/a/nets-learn/apps/download/learnapp123/media_profile.ccpr",
        "hq_username": "pooluserone@nets-learn.commcarehq.org",
        "hq_token": "hq-1",
    }


def test_form_engine_context_deliver_needs_a_claim(servers, session):
    with pytest.raises(GuardRefused):
        service.form_engine_context(session, fakes.OPP_UUID, "deliver")
    servers.opportunities = [fakes.opportunity(claim={"max_payments": 20})]
    ctx = service.form_engine_context(session, fakes.OPP_UUID, "deliver")
    assert ctx["domain"] == "nets-deliver" and ctx["hq_token"]


def test_form_engine_context_without_token_makes_no_hq_call(servers, session):
    opp = fakes.opportunity()
    ctx = service.form_engine_context(session, opp, "learn", with_token=False)
    assert ctx["hq_token"] is None and ctx["domain"] == "nets-learn"
    assert servers.requests == []


def test_form_engine_context_refuses_an_unlisted_hq_host(servers, session):
    opp = fakes.opportunity()
    opp["learn_app"]["install_url"] = "https://evil.example.com/a/x/apps/download/y/profile.ccpr"
    with pytest.raises(Exception, match="allowed"):
        service.form_engine_context(session, opp, "learn")
    assert [r for r in servers.requests if r.url.host == "evil.example.com"] == []


def test_a_failed_grant_after_recovery_keeps_the_rotated_password(servers, session):
    servers.force_status["/o/token/"] = 503
    with pytest.raises(Exception):
        service.ensure_signed_in(session)
    stored = WorkerSession.objects.get(pk=session.pk)
    assert stored.personalid_password == "rotated-password-1"
    assert stored.pool_worker.personalid_username == fakes.USERNAME
    # Next time: the password grant alone, no second recovery.
    del servers.force_status["/o/token/"]
    servers.requests.clear()
    service.ensure_signed_in(stored)
    assert [r.url.path for r in servers.requests] == ["/o/token/"]


def test_hq_token_checks_a_caller_supplied_base(servers, session):
    with pytest.raises(Exception, match="allowed"):
        service.hq_token(session, "nets-learn", hq_base="https://evil.example.com")
    assert servers.requests == []


def test_hq_token_refuses_a_released_session(servers, session):
    service.release(session)
    with pytest.raises(WorkerUnavailable):
        service.hq_token(session, "nets-learn")


def test_hq_token_is_stored_encrypted(servers, session):
    from django.db import connection

    token = service.hq_token(session, "nets-learn")
    with connection.cursor() as cursor:
        cursor.execute("SELECT hq_tokens FROM connect_web_workersession WHERE id = %s", [session.pk])
        (raw,) = cursor.fetchone()
    assert token not in raw and raw.startswith("fernet1:")
    assert WorkerSession.objects.get(pk=session.pk).get_hq_tokens()["www.commcarehq.org/nets-learn"]["token"] == token
