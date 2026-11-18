"""A failed Connect sign-in must land on the login page, never loop.

On 2026-11-18 Connect began refusing labs' `openid` scope at /o/authorize/
(commcare-connect#1765: an application with no ID-token signing algorithm may not
ask for openid). Every callback arrived carrying error=invalid_scope, and every
failure branch redirected to /labs/initiate/, which went straight back to Connect:
an endless redirect loop the browser reported as a generic error.
"""

import ast
from pathlib import Path

from django.conf import settings
from django.test import RequestFactory

from connect_labs.labs.integrations.connect.oauth_views import labs_oauth_callback


def _callback(params, session):
    request = RequestFactory().get("/labs/callback/", params)
    request.session = session
    request._messages = _Messages()
    return labs_oauth_callback(request)


class _Messages:
    def __init__(self):
        self.added = []

    def add(self, level, message, extra_tags=""):
        self.added.append(message)


def test_connect_error_lands_on_the_login_page_not_initiate():
    session = {"oauth_state": "s1", "oauth_code_verifier": "v"}
    resp = _callback({"state": "s1", "error": "invalid_scope"}, session)
    assert resp.url == "/labs/login/"


def test_bad_state_lands_on_the_login_page_not_initiate():
    resp = _callback({"state": "forged", "code": "c"}, {"oauth_state": "s1"})
    assert resp.url == "/labs/login/"


def test_missing_verifier_lands_on_the_login_page_not_initiate():
    resp = _callback({"state": "s1", "code": "c"}, {"oauth_state": "s1"})
    assert resp.url == "/labs/login/"


def test_production_scopes_are_export_plus_openid():
    # openid works only because labs' Connect application is RS256 (see labs_aws.py).
    # Read from the production settings module itself: the test settings never set
    # LABS_OAUTH_SCOPES, so asserting on django.conf.settings would pass vacuously.
    source = (Path(settings.BASE_DIR) / "config" / "settings" / "labs_aws.py").read_text()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "LABS_OAUTH_SCOPES" for t in node.targets
        ):
            scopes = ast.literal_eval(node.value)
            break
    else:
        raise AssertionError("labs_aws.py no longer sets LABS_OAUTH_SCOPES")
    assert set(scopes) == {"openid", "export"}
