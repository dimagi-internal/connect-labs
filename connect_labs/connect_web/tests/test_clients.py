"""The request shapes each client sends, and how it reads each answer."""

import httpx
import pytest

from connect_labs.connect_web import connect_api, hq, http, personalid
from connect_labs.connect_web.errors import (
    START_LEARN_FAILED,
    ConnectAPIError,
    HQError,
    IncorrectBackupCode,
    NotConfigured,
    PersonalIDError,
)

from . import fakes


def test_start_configuration_shape(servers):
    session = personalid.start_configuration("+74260000001", "dev-uuid")
    (req,) = servers.calls("/users/start_configuration")
    assert req.method == "POST"
    assert req.headers["Accept"] == "application/json;version=3.0"
    assert "Authorization" not in req.headers
    assert servers.body(req) == {
        "phone_number": "+74260000001",
        "application_id": "org.commcare.dalvik",
        "cc_device_id": "dev-uuid",
        "device": "Connect Web (labs)",
    }
    assert session.token == "config-session-token" and session.demo_user is True
    assert "config-session-token" not in repr(session)


def test_start_configuration_sends_gps_when_given(servers):
    personalid.start_configuration("+74260000001", "dev-uuid", gps_location="0.1 0.2 0 0")
    assert servers.body(servers.calls("/users/start_configuration")[0])["gps_location"] == "0.1 0.2 0 0"


def test_complete_recovery_shape_and_success(servers):
    creds = personalid.complete_recovery("config-session-token", fakes.BACKUP_CODE)
    (req,) = servers.calls("/users/recover/complete_recovery")
    assert req.headers["Authorization"] == "Bearer config-session-token"
    assert req.headers["Accept"] == "application/json;version=3.0"
    assert servers.body(req) == {"method": "backup_code", "backup_code": fakes.BACKUP_CODE}
    assert creds.username == fakes.USERNAME and creds.password == "rotated-password-1"
    assert "rotated-password-1" not in repr(creds)


def test_wrong_backup_code_200_with_attempts_left_is_a_failure(servers):
    with pytest.raises(IncorrectBackupCode) as caught:
        personalid.complete_recovery("config-session-token", "000000")
    assert caught.value.attempts_left == 2
    assert caught.value.error_code == "INCORRECT_BACKUP_CODE"
    assert "000000" not in str(caught.value)
    # Exactly one attempt: a POST is never retried.
    assert len(servers.calls("/users/recover/complete_recovery")) == 1


def test_recovery_error_code_is_carried(servers):
    servers.force_status["/users/recover/complete_recovery"] = 401
    with pytest.raises(PersonalIDError) as caught:
        personalid.complete_recovery("config-session-token", fakes.BACKUP_CODE)
    assert caught.value.status == 401


def test_password_grant_shape(servers):
    servers.passwords[fakes.USERNAME] = "pw"
    token = personalid.password_grant(fakes.USERNAME, "pw")
    (req,) = servers.calls("/o/token/")
    assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert servers.form(req) == {
        "grant_type": "password",
        "client_id": "pid-client",
        "scope": "openid",
        "username": fakes.USERNAME,
        "password": "pw",
    }
    assert token.expires_in == 3600 and token.access_token.startswith("pid-access-")
    assert token.access_token not in repr(token)


def test_password_grant_invalid_grant_code(servers):
    with pytest.raises(PersonalIDError) as caught:
        personalid.password_grant(fakes.USERNAME, "wrong")
    assert caught.value.error_code == "invalid_grant"
    assert "wrong" not in str(caught.value)


def test_password_grant_needs_a_client_id(settings, servers):
    settings.CONNECT_WEB_PERSONALID_CLIENT_ID = ""
    with pytest.raises(NotConfigured):
        personalid.password_grant(fakes.USERNAME, "pw")
    assert servers.requests == []


@pytest.fixture
def connect(servers):
    servers.valid_access_tokens.add("bearer-xyz")
    return connect_api.ConnectClient("bearer-xyz")


def test_connect_headers_and_list(servers, connect):
    opps = connect.list_opportunities()
    (req,) = servers.calls("/api/opportunity/")
    assert req.headers["Authorization"] == "Bearer bearer-xyz"
    assert req.headers["Accept"] == "application/json;version=2.0"
    assert opps[0]["opportunity_id"] == fakes.OPP_UUID
    assert "bearer-xyz" not in repr(connect)


def test_start_learn_app_is_form_encoded(servers, connect):
    connect.start_learn_app(fakes.OPP_UUID)
    (req,) = servers.calls("/users/start_learn_app/")
    assert req.method == "POST"
    assert req.headers["Content-Type"] == "application/x-www-form-urlencoded"
    assert servers.form(req) == {"opportunity": fakes.OPP_UUID}


def test_start_learn_app_bare_500_is_surfaced_as_no_invite(servers, connect):
    servers.start_learn_status = 500
    with pytest.raises(ConnectAPIError) as caught:
        connect.start_learn_app(fakes.OPP_UUID)
    assert caught.value.error_code == START_LEARN_FAILED
    assert "invite" in caught.value.user_message
    assert len(servers.calls("/users/start_learn_app/")) == 1  # never retried


def test_claim_sends_empty_json_and_reads_201_vs_200(servers, connect):
    assert connect.claim(fakes.OPP_UUID) is True
    req = servers.calls(f"/api/opportunity/{fakes.OPP_UUID}/claim")[0]
    assert req.headers["Content-Type"] == "application/json"
    assert servers.body(req) == {}
    servers.claim_status = 200
    assert connect.claim(fakes.OPP_UUID) is False


def test_claim_error_code(servers, connect):
    servers.claim_error = "OPPORTUNITY_FULL"
    with pytest.raises(ConnectAPIError) as caught:
        connect.claim(fakes.OPP_UUID)
    assert caught.value.error_code == "OPPORTUNITY_FULL"
    assert "full" in caught.value.user_message


def test_confirm_payments_sends_string_flags(servers, connect):
    connect.confirm_payments([("9b0c-pay-uuid", True), (5, False)])
    (req,) = servers.calls("/api/payment/confirm")
    assert servers.body(req) == {
        "payments": [{"id": "9b0c-pay-uuid", "confirmed": "true"}, {"id": 5, "confirmed": "false"}]
    }


def test_gets_retry_on_transient_errors_but_posts_do_not(monkeypatch, connect):
    seen = []

    def flaky(request):
        seen.append(request.method)
        if len(seen) < 3:
            raise httpx.ConnectError("boom")
        return httpx.Response(200, json=[])

    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(flaky))
    assert connect.list_opportunities() == []
    assert seen == ["GET", "GET", "GET"]

    seen.clear()
    with pytest.raises(ConnectAPIError) as caught:
        connect.confirm_payments([(1, True)])
    assert seen == ["POST"]
    assert caught.value.status is None and "reach" in caught.value.user_message


def test_get_retries_on_503_then_gives_up(monkeypatch, connect):
    seen = []

    def down(request):
        seen.append(1)
        return httpx.Response(503)

    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(down))
    with pytest.raises(ConnectAPIError) as caught:
        connect.learn_progress(fakes.OPP_UUID)
    assert caught.value.status == 503 and len(seen) == http.GET_ATTEMPTS


def test_hq_username_and_base():
    assert hq.hq_username("PoolUserOne", "nets-learn") == "pooluserone@nets-learn.commcarehq.org"
    assert hq.hq_base_for("https://www.commcarehq.org/a/x/apps/download/y/media_profile.ccpr") == fakes.HQ
    assert hq.hq_base_for(None) == fakes.HQ


@pytest.mark.parametrize(
    "url", ["http://www.commcarehq.org/a/x", "https://evil.example.com/a/x", "https://www.commcarehq.org.evil.com/"]
)
def test_hq_base_refuses_hosts_off_the_allow_list(url):
    with pytest.raises(HQError):
        hq.hq_base_for(url)


def test_hq_token_shape(servers):
    servers.valid_access_tokens.add("pid-tok")
    token = hq.request_token(fakes.HQ, "PoolUserOne", "nets-learn", "pid-tok")
    (req,) = servers.calls("/oauth/token/")
    assert req.url.host == "www.commcarehq.org"
    assert servers.form(req) == {
        "grant_type": "password",
        "client_id": "hq-client",
        "scope": "mobile_access sync",
        "username": "pooluserone@nets-learn.commcarehq.org",
        "password": "pid-tok",
    }
    assert token.access_token == "hq-1" and token.expires_in == 900


def test_hq_base_with_a_malformed_port_is_an_hq_error():
    with pytest.raises(HQError):
        hq.hq_base_for("https://www.commcarehq.org:99999/a/x")


def test_non_numeric_expires_in_falls_back(servers):
    assert http.seconds("soon") == 0 and http.seconds(None) == 0 and http.seconds("900") == 900
