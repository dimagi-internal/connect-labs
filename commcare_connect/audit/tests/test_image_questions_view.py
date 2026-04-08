"""Tests for OpportunityImageTypesAPIView (v2 paginated JSON image type discovery)."""
import time

import pytest
from django.test import Client, override_settings

from commcare_connect.labs.tests.test_settings import LABS_SETTINGS

# URL the view is mounted at (config/urls.py: path("audit/", ...) + audit/urls.py)
ENDPOINT = "/audit/api/opportunity/42/image-questions/"

# The Connect API URL that ExportAPIClient will call
CONNECT_URL = "https://connect.example.com/export/opportunity/42/user_visits/?images=true"

# ---- Fixtures for form_json / images shapes -----
#
# extract_images_with_question_ids does:
#   form_data = form_json.get("form", form_json)
#   filename_map = _build_filename_map(form_data)
#   for each image: question_id = filename_map.get(image["name"])
#
# _build_filename_map builds path by joining keys with "/" starting from root of form_data.
# So {"group": {"photo_a": "img1.jpg"}} → filename_map = {"img1.jpg": "group/photo_a"}
#
# A record must have:
#   - form_json: {"form": {"group": {"photo_a": "img1.jpg"}}}
#   - images: [{"blob_id": "b1", "name": "img1.jpg"}]
# This produces question_id "group/photo_a".


def _make_record(record_id: int, form_json: dict, images: list, username: str = "user1") -> dict:
    """Build a single v2 user_visits record as returned by the Connect API."""
    return {
        "id": record_id,
        "username": username,
        "form_json": form_json,
        "images": images,
    }


def _page(records: list, next_url: str | None = None) -> dict:
    """Build a v2 paginated response payload."""
    return {"next": next_url, "results": records}


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


# ---- Sanity check: extract_images_with_question_ids produces IDs with our fixture ----


def test_extract_images_with_question_ids_sanity():
    """Verify our fixture shapes actually produce non-empty question_ids.

    This test catches fixture regressions before the integration tests run.
    """
    from commcare_connect.audit.analysis_config import extract_images_with_question_ids

    visit_data = {
        "form_json": {"form": {"group": {"photo_a": "img1.jpg"}}},
        "images": [{"blob_id": "b1", "name": "img1.jpg"}],
    }
    result = extract_images_with_question_ids(visit_data)
    assert len(result) == 1
    assert result[0]["question_id"] == "group/photo_a"


# ---- Integration tests ----


@override_settings(**LABS_SETTINGS)
def test_image_types_returns_unique_question_ids(labs_client, httpx_mock):
    """View returns unique question_ids from a single page of records."""
    records = [
        _make_record(
            1,
            form_json={"form": {"group": {"photo_a": "img1.jpg"}}},
            images=[{"blob_id": "b1", "name": "img1.jpg"}],
            username="user1",
        ),
        _make_record(
            2,
            form_json={"form": {"group": {"photo_b": "img2.jpg"}}},
            images=[{"blob_id": "b2", "name": "img2.jpg"}],
            username="user2",
        ),
    ]
    httpx_mock.add_response(
        url=CONNECT_URL,
        json=_page(records),
    )

    response = labs_client.get(ENDPOINT)

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    ids = {item["id"] for item in data}
    assert "group/photo_a" in ids
    assert "group/photo_b" in ids
    assert len(ids) == 2


@override_settings(**LABS_SETTINGS)
def test_image_types_paginates_across_multiple_pages(labs_client, httpx_mock):
    """View follows pagination and returns IDs found across all pages."""
    page1_url = CONNECT_URL
    page2_url = "https://connect.example.com/export/opportunity/42/user_visits/?images=true&last_id=1"

    page1_records = [
        _make_record(
            1,
            form_json={"form": {"section_a": {"photo_front": "front.jpg"}}},
            images=[{"blob_id": "ba1", "name": "front.jpg"}],
            username="user1",
        ),
    ]
    page2_records = [
        _make_record(
            2,
            form_json={"form": {"section_b": {"photo_back": "back.jpg"}}},
            images=[{"blob_id": "bb1", "name": "back.jpg"}],
            username="user2",
        ),
    ]

    httpx_mock.add_response(url=page1_url, json=_page(page1_records, next_url=page2_url))
    httpx_mock.add_response(url=page2_url, json=_page(page2_records, next_url=None))

    response = labs_client.get(ENDPOINT)

    assert response.status_code == 200
    data = response.json()
    ids = {item["id"] for item in data}
    assert "section_a/photo_front" in ids, f"Expected section_a/photo_front in {ids}"
    assert "section_b/photo_back" in ids, f"Expected section_b/photo_back in {ids}"
    assert len(ids) == 2


@override_settings(**LABS_SETTINGS)
def test_image_types_returns_empty_list_when_no_images(labs_client, httpx_mock):
    """Records with empty image lists produce an empty question_id response."""
    records = [
        _make_record(1, form_json={}, images=[], username="user1"),
        _make_record(2, form_json={}, images=[], username="user2"),
    ]
    httpx_mock.add_response(url=CONNECT_URL, json=_page(records))

    response = labs_client.get(ENDPOINT)

    assert response.status_code == 200
    assert response.json() == []


@override_settings(**LABS_SETTINGS)
def test_image_types_returns_502_on_api_error(labs_client, httpx_mock):
    """Returns 502 when the Connect API returns a 5xx error."""
    httpx_mock.add_response(url=CONNECT_URL, status_code=500)

    response = labs_client.get(ENDPOINT)

    assert response.status_code == 502
    data = response.json()
    assert "error" in data


@override_settings(**LABS_SETTINGS)
def test_image_types_returns_401_when_no_oauth_token(db):
    """Returns 401 when no labs_oauth token is in the session."""
    from commcare_connect.users.models import User

    user, _ = User.objects.update_or_create(
        username="testuser_notoken",
        defaults={"email": "testuser_notoken@example.com"},
    )
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    # Intentionally do NOT set labs_oauth in the session

    response = client.get(ENDPOINT)

    assert response.status_code == 401
    data = response.json()
    assert "error" in data
