"""Tests for the exports download page."""
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings

from commcare_connect.users.models import User

LABS_MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "commcare_connect.labs.context.LabsContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "commcare_connect.utils.middleware.CustomErrorHandlingMiddleware",
    "commcare_connect.utils.middleware.CurrentVersionMiddleware",
]

LABS_SETTINGS = dict(
    IS_LABS_ENVIRONMENT=True,
    MIDDLEWARE=LABS_MIDDLEWARE,
    LOGIN_URL="/labs/login/",
)


@pytest.fixture
def dimagi_client(db):
    """Authenticated client with a @dimagi.com username."""
    user, _ = User.objects.update_or_create(
        username="reviewer@dimagi.com",
        defaults={"email": ""},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token",
        "expires_at": time.time() + 3600,
    }
    session.save()
    return client


@pytest.fixture
def non_dimagi_client(db):
    """Authenticated client without @dimagi.com username."""
    user, _ = User.objects.update_or_create(
        username="regularuser",
        defaults={"email": ""},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    return client


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET="test-bucket")
@patch("commcare_connect.custom_analysis.exports.views.boto3")
def test_exports_page_lists_files(mock_boto3, dimagi_client):
    """Page renders with file metadata from S3 head_object."""
    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.head_object.return_value = {
        "ContentLength": 4096,
        "Metadata": {"row-count": "42", "last-updated": "2026-03-16T10:00:00+00:00"},
    }

    response = dimagi_client.get("/custom_analysis/exports/")

    assert response.status_code == 200
    assert b"workflow_runs.csv" in response.content
    assert b"audit_sessions.csv" in response.content
    assert b"42" in response.content  # row count visible


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET=None)
def test_exports_page_shows_unconfigured_state(dimagi_client):
    """When no bucket is configured, page renders the not-configured message."""
    response = dimagi_client.get("/custom_analysis/exports/")
    assert response.status_code == 200
    assert b"not configured" in response.content.lower()
    assert b"workflow_runs.csv" not in response.content


@override_settings(**LABS_SETTINGS)
def test_exports_page_requires_dimagi_user(non_dimagi_client):
    """Non-Dimagi users receive 403."""
    response = non_dimagi_client.get("/custom_analysis/exports/")
    assert response.status_code == 403


@override_settings(**LABS_SETTINGS)
def test_exports_page_requires_login(db):
    """Unauthenticated requests are redirected to login."""
    client = Client()
    response = client.get("/custom_analysis/exports/")
    assert response.status_code == 302
    assert "/labs/login/" in response["Location"]


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET="test-bucket")
@patch("commcare_connect.custom_analysis.exports.views.boto3")
def test_download_redirects_to_presigned_url(mock_boto3, dimagi_client):
    """Download endpoint generates pre-signed URL and redirects."""
    from commcare_connect.labs.s3_export import WORKFLOW_RUNS_KEY

    mock_s3 = MagicMock()
    mock_boto3.client.return_value = mock_s3
    mock_s3.generate_presigned_url.return_value = "https://s3.example.com/presigned"

    response = dimagi_client.get(f"/custom_analysis/exports/download/?key={WORKFLOW_RUNS_KEY}")

    assert response.status_code == 302
    assert response["Location"] == "https://s3.example.com/presigned"
    mock_s3.generate_presigned_url.assert_called_once_with(
        "get_object",
        Params={"Bucket": "test-bucket", "Key": WORKFLOW_RUNS_KEY},
        ExpiresIn=900,
    )


@override_settings(**LABS_SETTINGS, LABS_EXPORTS_BUCKET="test-bucket")
def test_download_rejects_unknown_key(dimagi_client):
    """Download with an arbitrary key returns 400."""
    response = dimagi_client.get("/custom_analysis/exports/download/?key=../../etc/passwd")
    assert response.status_code == 400
