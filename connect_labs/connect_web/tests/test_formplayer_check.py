import io
import json

import httpx
import pytest
from django.core.management import CommandError, call_command

from connect_labs.connect_web import http
from connect_labs.connect_web.management.commands.connect_web_formplayer_check import sign

FORMPLAYER = "http://formplayer.test:8080"
KEY = "the-shared-labs-key"


def _formplayer(server_key: str | None = KEY, enforce: bool = True):
    """A labs-mode formplayer's delete_application_dbs, as far as its auth filter goes."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{FORMPLAYER}/delete_application_dbs"
        body = request.content
        assert json.loads(body)["username"] and request.headers["X-Labs-HQ-Token"]
        signature = request.headers.get("X-Labs-Signature")
        if enforce and (signature is None or signature != sign(server_key, body)):
            return httpx.Response(401)
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
    with pytest.raises(CommandError, match="unsigned request: expected 401, got 200"):
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
