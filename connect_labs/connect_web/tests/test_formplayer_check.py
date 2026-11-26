import io
import json
import time

import httpx
import pytest
from django.core.management import CommandError, call_command

from connect_labs.connect_web import http
from connect_labs.connect_web.formplayer import sign_v2
from connect_labs.connect_web.management.commands.connect_web_formplayer_check import sign

FORMPLAYER = "http://formplayer.test:8080"
KEY = "the-shared-labs-key"


def _formplayer(server_key: str | None = KEY, enforce: bool = True, window: bool = True, allow_v1: bool = True):
    """A labs-mode formplayer's delete_application_dbs, as far as LabsAuthFilter goes (patch 0007)."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{FORMPLAYER}/delete_application_dbs"
        body = request.content
        token = request.headers["X-Labs-HQ-Token"]
        assert json.loads(body)["username"] and token
        if not enforce:
            return httpx.Response(200, json={"error": False, "message": "Successfully cleared"})
        v2 = request.headers.get("X-Labs-Signature-V2")
        v1 = request.headers.get("X-Labs-Signature")
        if v2 is not None:
            ts = int(request.headers["X-Labs-Timestamp"])
            if window and abs(time.time() - ts) > 300:
                return httpx.Response(401)
            if v2 != sign_v2(server_key, "POST", "/delete_application_dbs", ts, token, None, body):
                return httpx.Response(401)
        elif v1 is not None:
            if not allow_v1 or v1 != sign(server_key, body):
                return httpx.Response(401)
        else:
            return httpx.Response(403)  # what the real one answers: Spring, no credentials at all
        return httpx.Response(200, json={"error": False, "message": "Successfully cleared"})

    return httpx.MockTransport(handler)


@pytest.fixture
def configured(settings):
    settings.CONNECT_WEB_FORMPLAYER_URL = FORMPLAYER + "/"
    settings.CONNECT_WEB_FORMPLAYER_KEY = KEY


def test_healthy_formplayer_passes(configured, monkeypatch):
    monkeypatch.setattr(http, "HTTP_TRANSPORT", _formplayer())
    out = io.StringIO()
    call_command("connect_web_formplayer_check", stdout=out)
    assert "is healthy" in out.getvalue()
    assert KEY not in out.getvalue()


def test_key_mismatch_names_the_cause(configured, monkeypatch):
    monkeypatch.setattr(http, "HTTP_TRANSPORT", _formplayer(server_key="another-key"))
    with pytest.raises(CommandError, match="LABS_AUTH_KEY is not this key"):
        call_command("connect_web_formplayer_check", stdout=io.StringIO())


def test_unenforced_auth_fails(configured, monkeypatch):
    monkeypatch.setattr(http, "HTTP_TRANSPORT", _formplayer(enforce=False))
    with pytest.raises(CommandError, match="unsigned request: expected 401 or 403, got 200"):
        call_command("connect_web_formplayer_check", stdout=io.StringIO())


def test_unreachable_formplayer_fails(configured, monkeypatch):
    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(refuse))
    with pytest.raises(CommandError, match="ConnectError"):
        call_command("connect_web_formplayer_check", stdout=io.StringIO())


def test_unconfigured_fails_before_any_request(settings, monkeypatch):
    settings.CONNECT_WEB_FORMPLAYER_URL = ""
    settings.CONNECT_WEB_FORMPLAYER_KEY = ""
    monkeypatch.setattr(http, "HTTP_TRANSPORT", httpx.MockTransport(lambda r: pytest.fail("no request expected")))
    with pytest.raises(CommandError, match="must both be set"):
        call_command("connect_web_formplayer_check", stdout=io.StringIO())


def test_signature_matches_formplayer_docs():
    # formplayer/README.md: standard padded Base64 of HMAC-SHA256(key, body bytes).
    assert sign("k", b"{}") == "rdhTsQP7zJNqGU+esV4pxP8Ir25H1dG8pPICGOMeT/8="


def test_replay_window_not_enforced_fails(configured, monkeypatch):
    monkeypatch.setattr(http, "HTTP_TRANSPORT", _formplayer(window=False))
    with pytest.raises(CommandError, match="stale timestamp"):
        call_command("connect_web_formplayer_check", stdout=io.StringIO())


def test_expect_v1_closed(configured, monkeypatch):
    monkeypatch.setattr(http, "HTTP_TRANSPORT", _formplayer(allow_v1=False))
    out = io.StringIO()
    call_command("connect_web_formplayer_check", "--expect-v1-closed", stdout=out)
    assert "v1 is closed" in out.getvalue()
    monkeypatch.setattr(http, "HTTP_TRANSPORT", _formplayer(allow_v1=True))
    with pytest.raises(CommandError, match="v1-only request: expected 401"):
        call_command("connect_web_formplayer_check", "--expect-v1-closed", stdout=io.StringIO())
