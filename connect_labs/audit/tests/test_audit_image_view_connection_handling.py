"""Regression tests for the audit image proxy's DB-connection and error handling.

Background — the 2026-07-30 500s on ``/audit/image/<opp>/<blob>/`` (#1060).
41 admin error mails overnight, all with this shape::

    psycopg2.OperationalError: connection to server at "labs-jj-postgres..."
    port 5432 failed: FATAL:  remaining connection slots are reserved for roles
    with the SUPERUSER attribute

raised from ``django/middleware/csrf.py`` ``process_request`` ->
``request.session.get(CSRF_SESSION_KEY)`` -> session load -> ``ensure_connection``.
The 500 happened in middleware, BEFORE the view ran, so the view's own
``except Exception`` never saw it.

Why the slots ran out: Django's ASGI handler opens a ``ThreadSensitiveContext``
per request (``django/core/handlers/asgi.py``), so every concurrent request gets
its own thread and therefore its own thread-local DB connection. With
``ATOMIC_REQUESTS = True`` that connection is pinned inside an open transaction
for the whole request. The bulk audit grid fires one image request per tile, so a
single page open put tens-to-hundreds of requests in flight, each holding a
connection slot for the duration of a multi-second fetch against
``connect.dimagi.com``. On a db.t3.small the server ran out of slots and the next
request in was the one that 500'd.

These tests pin the two properties that keep it from recurring:

1. the image proxy does not sit inside ATOMIC_REQUESTS, and hands its DB
   connection back before the slow upstream fetch;
2. failures are reported through the logger (so Sentry sees them) and a genuine
   fault is a 5xx, not a "404 Image not found" that hides it.
"""

import logging
from unittest.mock import Mock, patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory
from django.urls import resolve, reverse

from connect_labs.audit.data_access import ImageDownloadError
from connect_labs.audit.views import ExperimentAuditImageConnectView

OPP_ID = 1973  # the real prod opportunity from the incident (below the 10k labs-only floor)
BLOB_ID = "143477f0-ce1d-4d62-8419-106584c97bd4"  # a blob from a captured 500


def _request(user):
    request = RequestFactory().get(f"/audit/image/{OPP_ID}/{BLOB_ID}/")
    request.user = user
    return request


@pytest.fixture
def auth_user(django_user_model):
    """An authenticated user that costs no DB.

    Unsaved is deliberate: ``is_authenticated`` is True on any User instance, and
    BLOB_ID is a UUID so it never matches the ``^synth-muac-`` blob pattern —
    the synthetic branch short-circuits before its registry query. So this whole
    module runs without a database, and in particular without the table-
    truncating ``transaction=True`` mode, which drops the migration-seeded rows
    that other tests in the suite read.
    """
    return django_user_model(username="sagar", email="satre@dimagi.com")


# ---------------------------------------------------------------------------
# 1. Connection handling — the actual root cause of the 500s
# ---------------------------------------------------------------------------


def test_image_view_is_registered_non_atomic():
    """The proxy must be exempt from ATOMIC_REQUESTS.

    It writes nothing to the labs DB, so wrapping it in a transaction only buys
    a connection slot pinned in `idle in transaction` for the length of an
    upstream HTTP call. Being non-atomic is also the precondition for releasing
    the connection mid-request at all — closing inside an atomic block raises.
    """
    view = resolve(reverse("audit:audit_image_connect", args=[OPP_ID, BLOB_ID])).func
    assert "default" in getattr(view, "_non_atomic_requests", set())


def test_db_connection_released_before_upstream_fetch(auth_user):
    """The slow upstream fetch must not hold a DB connection slot.

    This is the regression that produced the incident: one connection per
    in-flight image request, held across a multi-second fetch, times a grid of
    images, times two users.

    Asserted as an ordering property (release happens, and happens *before* the
    fetch) rather than by inspecting a live connection: proving it against a real
    connection needs ``transaction=True``, whose table truncation wipes
    migration-seeded rows other tests depend on. The companion
    ``test_image_view_is_registered_non_atomic`` is what proves the release is
    legal at runtime — closing inside an ATOMIC_REQUESTS block would poison the
    transaction instead of freeing the slot.
    """
    calls = []

    data_access = Mock()
    data_access.download_image_from_connect.side_effect = lambda *_a, **_k: (calls.append("fetch"), b"JPEGDATA")[1]

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        with patch("connect_labs.audit.views.connection") as conn:
            conn.close.side_effect = lambda: calls.append("close")
            response = ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert response.status_code == 200
    assert response.content == b"JPEGDATA"
    assert calls == ["close", "fetch"], (
        f"expected the DB connection to be released before the upstream fetch, got {calls} — "
        "holding it across the fetch is what exhausted the server's connection slots"
    )


def test_connection_is_the_real_django_connection():
    """Guard the patch target above: the view must close Django's own connection.

    Without this, someone could rename or shadow the import and the ordering test
    would keep passing against a stand-in that frees nothing.
    """
    from django.db import connection as django_connection

    from connect_labs.audit import views

    assert views.connection is django_connection


def test_connection_released_even_when_fetch_fails(auth_user):
    """A failing fetch must not leave the slot pinned either."""
    calls = []

    data_access = Mock()
    data_access.download_image_from_connect.side_effect = RuntimeError("boom")

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        with patch("connect_labs.audit.views.connection") as conn:
            conn.close.side_effect = lambda: calls.append("close")
            response = ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert response.status_code == 502
    assert calls == ["close"]


def test_anonymous_request_is_not_served():
    """LoginRequiredMixin still gates the proxy — non-atomic must not mean open."""
    response = ExperimentAuditImageConnectView.as_view()(_request(AnonymousUser()), opp_id=OPP_ID, blob_id=BLOB_ID)
    assert response.status_code in (302, 403)


# ---------------------------------------------------------------------------
# 2. Error reporting — real errors must be real errors, and must reach Sentry
# ---------------------------------------------------------------------------


def test_upstream_not_found_is_a_404(auth_user):
    """A genuine upstream 404 is the one case that should be a 404."""
    data_access = Mock()
    data_access.download_image_from_connect.side_effect = ImageDownloadError(
        "Failed to download image (HTTP 404)", status_code=404
    )

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        response = ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert response.status_code == 404


def test_upstream_server_error_is_a_502_not_a_404(auth_user):
    """An upstream fault is a gateway error, not a missing image."""
    data_access = Mock()
    data_access.download_image_from_connect.side_effect = ImageDownloadError(
        "Failed to download image (HTTP 503)", status_code=503
    )

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        response = ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert response.status_code == 502


def test_connection_error_is_a_502_not_a_404(auth_user):
    """A transport failure that survives the retries is a fault, not a 404."""
    data_access = Mock()
    data_access.download_image_from_connect.side_effect = ImageDownloadError(
        "Failed to download image due to a connection error"
    )

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        response = ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert response.status_code == 502


def test_unexpected_failure_is_reported_as_server_error_not_404(auth_user, caplog):
    """An unexpected fault must not be laundered into "Image not found".

    The blanket ``except Exception -> 404`` is what kept this endpoint's real
    failures invisible: a 404 is a normal, uninteresting response, so nothing
    alerted and nothing was investigated.
    """
    data_access = Mock()
    data_access.download_image_from_connect.side_effect = RuntimeError("psycopg2 pool exhausted")

    with caplog.at_level(logging.ERROR, logger="connect_labs.audit.views"):
        with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
            response = ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert response.status_code == 502, "an unexpected fault is a server error, not a missing image"

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "the failure must be logged at ERROR so Sentry sees it"
    assert any(r.exc_info for r in errors), (
        "must use logger.exception (exc_info attached) — print()+format_exc() "
        "lands in stdout unstructured and never reaches Sentry"
    )


def test_failure_path_does_not_use_print(auth_user, capsys):
    """The traceback belongs in the logger, not on stdout."""
    data_access = Mock()
    data_access.download_image_from_connect.side_effect = RuntimeError("boom")

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    assert "[ERROR]" not in capsys.readouterr().out


def test_data_access_is_closed_on_failure(auth_user):
    """The httpx client is released even when the fetch blows up."""
    data_access = Mock()
    data_access.download_image_from_connect.side_effect = RuntimeError("boom")

    with patch("connect_labs.audit.views.AuditDataAccess", return_value=data_access):
        ExperimentAuditImageConnectView.as_view()(_request(auth_user), opp_id=OPP_ID, blob_id=BLOB_ID)

    data_access.close.assert_called_once()
