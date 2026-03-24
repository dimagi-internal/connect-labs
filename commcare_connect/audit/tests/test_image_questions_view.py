"""Tests for OpportunityImageTypesAPIView (streaming CSV image type discovery)."""
import csv
import io
import json
import time
from unittest.mock import MagicMock, patch

import pytest
from django.test import Client, override_settings

from commcare_connect.labs.tests.test_settings import LABS_SETTINGS

CSV_COLUMNS = ["id", "form_json", "images", "username"]


def _build_csv_line(fields):
    """Write a single CSV row using Python's csv module for correct quoting."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(fields)
    return buf.getvalue().rstrip("\r\n")


def _build_csv_lines(rows, use_python_repr=False):
    """Build properly-quoted CSV lines from a list of dicts.

    Args:
        rows: List of dicts with id/form_json/images/username.
        use_python_repr: If True, serialize with repr() (single quotes) to match
            the actual Connect production API format. If False, use json.dumps.
    """
    lines = [_build_csv_line(CSV_COLUMNS)]
    serialize = repr if use_python_repr else json.dumps
    for row in rows:
        fields = [
            row.get("id", ""),
            serialize(row.get("form_json", {})),
            serialize(row.get("images", [])),
            row.get("username", "user1"),
        ]
        lines.append(_build_csv_line(fields))
    return lines


def _mock_stream_context(lines):
    """Create a mock httpx.stream context manager that yields lines."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.iter_lines.return_value = iter(lines)

    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_response)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


@pytest.fixture
def labs_client(db):
    """Django test client with a valid labs session and authenticated user."""
    from commcare_connect.users.models import User

    user, _ = User.objects.update_or_create(
        username="testuser",
        defaults={"email": "testuser@example.com"},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    session = client.session
    session["labs_oauth"] = {
        "access_token": "test-token-abc",
        "expires_at": time.time() + 3600,
        "user_profile": {"username": "testuser", "id": 42, "email": "testuser@example.com"},
    }
    session.save()
    return client


@override_settings(**LABS_SETTINGS)
def test_image_types_returns_unique_question_ids(labs_client):
    """View returns unique question_ids discovered from streamed CSV rows."""
    rows = [
        {
            "id": 1,
            "form_json": {"form": {"group": {"photo_a": "img1.jpg"}}},
            "images": [{"blob_id": "b1", "name": "img1.jpg"}],
            "username": "user1",
        },
        {
            "id": 2,
            "form_json": {"form": {"group": {"photo_b": "img2.jpg"}}},
            "images": [{"blob_id": "b2", "name": "img2.jpg"}],
            "username": "user2",
        },
    ]
    lines = _build_csv_lines(rows)
    mock_cm = _mock_stream_context(lines)

    with patch("commcare_connect.audit.views.httpx.stream", return_value=mock_cm):
        response = labs_client.get("/audit/api/opportunity/42/image-questions/")

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 2
    ids = {item["id"] for item in data}
    assert "group/photo_a" in ids
    assert "group/photo_b" in ids


@override_settings(**LABS_SETTINGS)
def test_image_types_requires_auth(db):
    """Unauthenticated request redirects to login."""
    client = Client()
    response = client.get("/audit/api/opportunity/42/image-questions/")
    assert response.status_code in (302, 401)


@override_settings(**LABS_SETTINGS)
def test_image_types_empty_csv(labs_client):
    """Returns empty list when CSV has no header."""
    mock_cm = _mock_stream_context([])

    with patch("commcare_connect.audit.views.httpx.stream", return_value=mock_cm):
        response = labs_client.get("/audit/api/opportunity/42/image-questions/")

    assert response.status_code == 200
    assert response.json() == []


@override_settings(**LABS_SETTINGS)
def test_image_types_no_images_column(labs_client):
    """Returns empty list when CSV has no images column."""
    header_no_images = _build_csv_line(["id", "form_json", "username"])
    data_row = _build_csv_line([1, "{}", "user1"])
    lines = [header_no_images, data_row]
    mock_cm = _mock_stream_context(lines)

    with patch("commcare_connect.audit.views.httpx.stream", return_value=mock_cm):
        response = labs_client.get("/audit/api/opportunity/42/image-questions/")

    assert response.status_code == 200
    assert response.json() == []


@override_settings(**LABS_SETTINGS)
def test_image_types_python_repr_format(labs_client):
    """CSV from Connect uses Python repr (single quotes), not JSON. View must handle both."""
    rows = [
        {
            "id": 1,
            "form_json": {"form": {"group": {"photo_a": "img1.jpg"}}},
            "images": [{"blob_id": "b1", "name": "img1.jpg", "parent_id": "xf1"}],
            "username": "user1",
        },
    ]
    lines = _build_csv_lines(rows, use_python_repr=True)
    mock_cm = _mock_stream_context(lines)

    with patch("commcare_connect.audit.views.httpx.stream", return_value=mock_cm):
        response = labs_client.get("/audit/api/opportunity/42/image-questions/")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "group/photo_a"


@override_settings(**LABS_SETTINGS)
def test_image_types_skips_rows_without_images(labs_client):
    """Rows with empty images are skipped; only rows with images contribute."""
    rows = [
        {"id": 1, "form_json": {}, "images": [], "username": "user1"},
        {"id": 2, "form_json": {}, "images": [], "username": "user2"},
        {
            "id": 3,
            "form_json": {"form": {"group": {"photo_a": "img1.jpg"}}},
            "images": [{"blob_id": "b1", "name": "img1.jpg"}],
            "username": "user3",
        },
    ]
    lines = _build_csv_lines(rows)
    mock_cm = _mock_stream_context(lines)

    with patch("commcare_connect.audit.views.httpx.stream", return_value=mock_cm):
        response = labs_client.get("/audit/api/opportunity/42/image-questions/")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["id"] == "group/photo_a"
