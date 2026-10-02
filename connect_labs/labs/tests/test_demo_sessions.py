"""Demo-persona sessions for the DDD inner loop are local-only, and provably off on labs."""

import importlib
import json
from importlib import import_module

import pytest
from django.conf import settings
from django.core.management import CommandError, call_command

from connect_labs.labs import demo_sessions
from connect_labs.users.models import User


def test_labs_aws_settings_can_never_mint():
    """The deployed settings module fails the guard on its own DEBUG, and even with DEBUG forced on."""
    labs_aws = importlib.import_module("config.settings.labs_aws")
    assert demo_sessions.allowed(labs_aws.DEBUG, "config.settings.labs_aws") is False
    assert demo_sessions.allowed(True, "config.settings.labs_aws") is False


def test_only_a_debug_local_build_is_allowed():
    assert demo_sessions.allowed(True, "config.settings.local") is True
    assert demo_sessions.allowed(False, "config.settings.local") is False
    assert demo_sessions.allowed(True, "config.settings.test") is False
    assert demo_sessions.allowed(True, None) is False


@pytest.mark.django_db
def test_mint_is_refused_outside_a_local_build():
    user = User.objects.create(username="demo-x", email="demo-x@example.invalid")
    with pytest.raises(demo_sessions.LocalOnly):
        demo_sessions.mint_session(user)
    with pytest.raises(CommandError, match="local DEBUG build"):
        call_command("ddd_demo_session", "--username", "demo-x", "--out", "/dev/null")


@pytest.fixture
def local_build(monkeypatch, settings):
    settings.DEBUG = True
    monkeypatch.setattr(settings._wrapped, "SETTINGS_MODULE", "config.settings.local", raising=False)
    return settings


@pytest.mark.django_db
def test_a_local_build_mints_a_session_that_is_the_user(local_build, tmp_path):
    user = User.objects.create(username="demo-sophie", email="demo-sophie@example.invalid")
    out = tmp_path / "state.json"
    call_command(
        "ddd_demo_session", "--username", "demo-sophie", "--base-url", "http://localhost:8010", "--out", str(out)
    )
    state = json.loads(out.read_text())
    cookie = state["cookies"][0]
    assert (cookie["domain"], cookie["secure"], cookie["name"]) == ("localhost", False, settings.SESSION_COOKIE_NAME)
    session = import_module(settings.SESSION_ENGINE).SessionStore(session_key=cookie["value"])
    assert session["_auth_user_id"] == str(user.pk)
    assert session["labs_oauth"]["access_token"] == "demo-persona-no-connect-account"
    assert out.stat().st_mode & 0o777 == 0o600


@pytest.mark.django_db
def test_a_cookie_is_never_written_for_a_deployed_host(local_build):
    user = User.objects.create(username="demo-y", email="demo-y@example.invalid")
    with pytest.raises(demo_sessions.LocalOnly, match="loopback"):
        demo_sessions.storage_state(user, "https://labs.connect.dimagi.com")
