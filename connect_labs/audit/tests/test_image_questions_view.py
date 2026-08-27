"""Tests for OpportunityImageTypesAPIView (v2 paginated JSON image type discovery)."""
import time

import pytest
from django.test import Client, override_settings

from connect_labs.labs.tests.test_settings import LABS_SETTINGS

# URL the view is mounted at (config/urls.py: path("audit/", ...) + audit/urls.py)
ENDPOINT = "/audit/api/opportunity/42/image-questions/"

# The Connect API URL that ExportAPIClient will call.
# page_size is the view's MAX_ROWS, not the client's 2500 default -- see
# test_sampler_never_requests_more_rows_than_it_can_read.
CONNECT_URL = "https://connect.example.com/export/opportunity/42/user_visits/?images=true&page_size=200"

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
    from connect_labs.users.models import User

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
    from connect_labs.audit.analysis_config import extract_images_with_question_ids

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
def test_sampler_stopping_early_audits_a_successful_partial_export(labs_client, httpx_mock):
    """The sampler's early exit must not log a failed bulk-PHI export.

    This endpoint stops as soon as the question-id set goes stable, abandoning
    the page generator mid-stream. That raises GeneratorExit inside
    ExportAPIClient.paginate, which used to be audited as outcome=failure — so
    these endpoints logged 100% failure on every call they ever served.
    """
    from connect_labs.audit_trail.models import Action, AuditEvent, Outcome

    # Every record carries the same question id, so after STABLE_THRESHOLD rows
    # with nothing new the view breaks out — page 2 is never requested.
    records = [
        _make_record(
            i,
            form_json={"form": {"group": {"photo_a": f"img{i}.jpg"}}},
            images=[{"blob_id": f"b{i}", "name": f"img{i}.jpg"}],
            username=f"user{i}",
        )
        for i in range(1, 61)
    ]
    next_url = "https://connect.example.com/export/opportunity/42/user_visits/?images=true&last_id=60"
    httpx_mock.add_response(url=CONNECT_URL, json=_page(records, next_url=next_url))

    response = labs_client.get(ENDPOINT)
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == ["group/photo_a"]

    event = AuditEvent.objects.get(action=Action.EXPORT)
    assert event.outcome == Outcome.SUCCESS
    assert "error" not in event.metadata
    assert event.metadata["terminated"] == "early"
    # record_count is PHI actually transferred, not rows inspected: page 1
    # arrived whole. The early stop is why page 2 was never requested — and
    # httpx_mock has no response registered for it, so this test would error
    # if the sampler ran on.
    assert event.record_count == len(records)


@override_settings(**LABS_SETTINGS)
def test_image_types_returns_401_when_no_oauth_token(db):
    """Returns 401 when no labs_oauth token is in the session."""
    from connect_labs.users.models import User

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


@override_settings(**LABS_SETTINGS)
def test_sampler_never_requests_more_rows_than_it_can_read(labs_client, httpx_mock):
    """The cost property, not the response shape.

    This is a SAMPLER: it reads at most MAX_ROWS rows and stops. But the export
    client defaults to page_size=2500, and `partial_ok` only avoids fetching the
    SECOND page -- the first is transferred in full before a single row is read.
    So the endpoint asked Connect to build and ship 12x more image-bearing visits
    than it could ever look at, and then waited for them.

    Measured on 2026-08-26, after per-request telemetry could finally see outbound
    calls at all: 6.9-8.9s per request, of which 4.8-5.2s was outbound wait on ONE
    call -- fired four times concurrently, one per opportunity, by the audit wizard.

    Asserting on the rendered question ids cannot catch this; the ids are correct
    either way. Assert on the page_size actually requested.
    """
    from connect_labs.audit.views import OpportunityImageTypesAPIView

    httpx_mock.add_response(url=CONNECT_URL, json=_page([]))

    resp = labs_client.get(ENDPOINT)
    assert resp.status_code == 200

    (request,) = httpx_mock.get_requests()
    requested = int(request.url.params["page_size"])
    assert requested <= OpportunityImageTypesAPIView.MAX_ROWS, (
        f"sampler requested {requested} rows but can only ever read " f"{OpportunityImageTypesAPIView.MAX_ROWS}"
    )


# ---------------------------------------------------------------------------
# Discovery caching (#1315)
# ---------------------------------------------------------------------------


@override_settings(**LABS_SETTINGS)
def test_second_call_serves_from_cache_without_touching_connect(labs_client, httpx_mock):
    """The cost property. One Connect round-trip, not one per request.

    This endpoint's ~7s is a SINGLE outbound call that Connect is slow to generate
    — capping page_size (#1311) cut the payload 12.5x and bought only ~20%, so the
    only remaining move is to stop asking. The audit wizard fires four of these
    concurrently (one per opportunity) on every load, and the answer is form schema
    that changes when someone edits the app, i.e. approximately never.

    Asserting on the returned question ids cannot catch a regression here — they're
    identical whether the cache works or not. Assert that Connect was called once.
    """
    records = [
        _make_record(1, {"form": {"group": {"photo_a": "img1.jpg"}}}, [{"blob_id": "b1", "name": "img1.jpg"}]),
    ]
    httpx_mock.add_response(url=CONNECT_URL, json=_page(records))

    first = labs_client.get(ENDPOINT)
    assert first.status_code == 200
    assert first.json() == [{"id": "group/photo_a", "label": "photo_a", "path": "group/photo_a"}]

    # No second mock is registered: a second outbound call would fail the test.
    second = labs_client.get(ENDPOINT)
    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(httpx_mock.get_requests()) == 1


@override_settings(**LABS_SETTINGS)
def test_cache_is_scoped_per_opportunity(labs_client, httpx_mock):
    """Two opportunities must not share a discovery result — that would show one
    opp's image questions while auditing another."""
    other_url = CONNECT_URL.replace("/opportunity/42/", "/opportunity/99/")
    httpx_mock.add_response(
        url=CONNECT_URL,
        json=_page([_make_record(1, {"form": {"a": "x.jpg"}}, [{"blob_id": "b", "name": "x.jpg"}])]),
    )
    httpx_mock.add_response(
        url=other_url,
        json=_page([_make_record(2, {"form": {"b": "y.jpg"}}, [{"blob_id": "c", "name": "y.jpg"}])]),
    )

    assert labs_client.get(ENDPOINT).json() == [{"id": "a", "label": "a", "path": "a"}]
    other = labs_client.get("/audit/api/opportunity/99/image-questions/")
    assert other.json() == [{"id": "b", "label": "b", "path": "b"}]


@override_settings(**LABS_SETTINGS)
def test_unavailable_cache_degrades_to_calling_connect(labs_client, httpx_mock, monkeypatch):
    """Redis down must cost speed, never correctness."""
    from connect_labs.audit import views as audit_views

    def boom(*_a, **_k):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(audit_views.cache, "get", boom)
    monkeypatch.setattr(audit_views.cache, "set", boom)

    httpx_mock.add_response(url=CONNECT_URL, json=_page([]))
    httpx_mock.add_response(url=CONNECT_URL, json=_page([]))

    assert labs_client.get(ENDPOINT).status_code == 200
    assert labs_client.get(ENDPOINT).status_code == 200
    assert len(httpx_mock.get_requests()) == 2


@override_settings(**LABS_SETTINGS)
def test_a_failed_discovery_is_not_cached(labs_client, httpx_mock):
    """A 502 must not poison the cache for 15 minutes — the next call retries."""
    httpx_mock.add_response(url=CONNECT_URL, status_code=500)
    assert labs_client.get(ENDPOINT).status_code == 502

    httpx_mock.add_response(url=CONNECT_URL, json=_page([]))
    assert labs_client.get(ENDPOINT).status_code == 200
