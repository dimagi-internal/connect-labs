"""
Tests for solicitations JSON API views.

All tests mock SolicitationsDataAccess to avoid real API calls.
Uses Django RequestFactory for direct view function invocation.
"""

import json
from unittest.mock import MagicMock, patch

from commcare_connect.solicitations.api_views import (
    api_response_detail,
    api_responses_list,
    api_review_detail,
    api_reviews_create,
    api_solicitation_detail,
    api_solicitations_list,
)
from commcare_connect.solicitations.data_access import RESPONSE_TYPE, REVIEW_TYPE, SOLICITATION_TYPE
from commcare_connect.solicitations.models import ResponseRecord, ReviewRecord, SolicitationRecord

# =========================================================================
# Test record factories
# =========================================================================


def _make_solicitation(**overrides):
    data = {
        "title": "Test Solicitation",
        "description": "A test",
        "scope_of_work": "Do the work",
        "solicitation_type": "rfp",
        "status": "active",
        "is_public": True,
        "questions": [{"id": "q1", "text": "Why?", "type": "text", "required": True}],
        "application_deadline": "2026-06-01",
        "expected_start_date": "2026-07-01",
        "expected_end_date": "2026-12-31",
        "estimated_scale": "1000 beneficiaries",
        "contact_email": "test@example.com",
        "created_by": "testuser",
        "program_name": "Test Program",
    }
    data.update(overrides.pop("data", {}))
    defaults = {
        "id": 1,
        "experiment": "prog_42",
        "type": SOLICITATION_TYPE,
        "data": data,
        "opportunity_id": 0,
    }
    defaults.update(overrides)
    return SolicitationRecord(defaults)


def _make_response_record(**overrides):
    data = {
        "solicitation_id": 1,
        "llo_entity_id": "llo_entity_123",
        "llo_entity_name": "Test Org",
        "responses": {"q1": "Because"},
        "status": "submitted",
        "submitted_by_name": "Jane Doe",
        "submitted_by_email": "jane@example.com",
        "submission_date": "2026-05-15T10:00:00Z",
    }
    data.update(overrides.pop("data", {}))
    defaults = {
        "id": 10,
        "experiment": "llo_entity_123",
        "type": RESPONSE_TYPE,
        "data": data,
        "opportunity_id": 0,
    }
    defaults.update(overrides)
    return ResponseRecord(defaults)


def _make_review(**overrides):
    data = {
        "response_id": 10,
        "llo_entity_id": "llo_entity_123",
        "score": 85,
        "recommendation": "approved",
        "notes": "Looks good",
        "tags": "experienced,local",
        "reviewer_username": "reviewer1",
        "review_date": "2026-05-20T14:00:00Z",
    }
    data.update(overrides.pop("data", {}))
    defaults = {
        "id": 20,
        "experiment": "llo_entity_123",
        "type": REVIEW_TYPE,
        "data": data,
        "opportunity_id": 0,
    }
    defaults.update(overrides)
    return ReviewRecord(defaults)


# =========================================================================
# Request factory helpers
# =========================================================================

_DA_PATCH = "commcare_connect.solicitations.api_views._get_data_access"


def _make_get_request(path="/", query_params=None):
    """Create a minimal GET request object."""
    request = MagicMock()
    request.method = "GET"
    request.GET = query_params or {}
    request.path = path
    return request


def _make_post_request(path="/", body=None):
    """Create a minimal POST request object with JSON body."""
    request = MagicMock()
    request.method = "POST"
    request.GET = {}
    request.body = json.dumps(body or {}).encode("utf-8")
    request.path = path
    return request


def _make_put_request(path="/", body=None):
    """Create a minimal PUT request object with JSON body."""
    request = MagicMock()
    request.method = "PUT"
    request.GET = {}
    request.body = json.dumps(body or {}).encode("utf-8")
    request.path = path
    return request


def _parse_response(response):
    """Parse JsonResponse content."""
    return json.loads(response.content)


# =========================================================================
# Solicitation List / Create Tests
# =========================================================================


class TestApiSolicitationsList:
    @patch(_DA_PATCH)
    def test_get_returns_json_with_solicitations_key(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_solicitations.return_value = [
            _make_solicitation(id=1),
            _make_solicitation(id=2),
        ]
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_solicitations_list(request)

        assert response.status_code == 200
        body = _parse_response(response)
        assert "solicitations" in body
        assert len(body["solicitations"]) == 2
        assert body["solicitations"][0]["id"] == 1
        assert body["solicitations"][1]["id"] == 2

    @patch(_DA_PATCH)
    def test_get_with_status_filter(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_solicitations.return_value = []
        mock_get_da.return_value = mock_da

        request = _make_get_request(query_params={"status": "active", "type": "rfp"})
        api_solicitations_list(request)

        mock_da.get_solicitations.assert_called_once_with(
            status="active",
            solicitation_type="rfp",
        )

    @patch(_DA_PATCH)
    def test_get_with_is_public_filter(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_public_solicitations.return_value = []
        mock_get_da.return_value = mock_da

        request = _make_get_request(query_params={"is_public": "true"})
        api_solicitations_list(request)

        mock_da.get_public_solicitations.assert_called_once_with(solicitation_type=None)

    @patch(_DA_PATCH)
    def test_get_serializes_solicitation_fields(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_solicitations.return_value = [_make_solicitation()]
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_solicitations_list(request)

        body = _parse_response(response)
        s = body["solicitations"][0]
        assert s["title"] == "Test Solicitation"
        assert s["solicitation_type"] == "rfp"
        assert s["status"] == "active"
        assert s["is_public"] is True
        assert s["application_deadline"] == "2026-06-01"
        assert s["contact_email"] == "test@example.com"
        assert s["created_by"] == "testuser"
        assert s["program_name"] == "Test Program"

    @patch(_DA_PATCH)
    def test_post_creates_and_returns_201(self, mock_get_da):
        mock_da = MagicMock()
        created = _make_solicitation(id=99, data={"title": "New One"})
        mock_da.create_solicitation.return_value = created
        mock_get_da.return_value = mock_da

        request = _make_post_request(body={"title": "New One", "status": "draft"})
        response = api_solicitations_list(request)

        assert response.status_code == 201
        body = _parse_response(response)
        assert "solicitation" in body
        assert body["solicitation"]["id"] == 99
        mock_da.create_solicitation.assert_called_once_with({"title": "New One", "status": "draft"})

    @patch(_DA_PATCH)
    def test_post_invalid_json_returns_400(self, mock_get_da):
        mock_get_da.return_value = MagicMock()

        request = MagicMock()
        request.method = "POST"
        request.body = b"not json"
        response = api_solicitations_list(request)

        assert response.status_code == 400
        body = _parse_response(response)
        assert "error" in body

    @patch(_DA_PATCH)
    def test_get_empty_list(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_solicitations.return_value = []
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_solicitations_list(request)

        assert response.status_code == 200
        body = _parse_response(response)
        assert body["solicitations"] == []


# =========================================================================
# Solicitation Detail / Update Tests
# =========================================================================


class TestApiSolicitationDetail:
    @patch(_DA_PATCH)
    def test_get_returns_solicitation_json(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_solicitation_by_id.return_value = _make_solicitation(id=5)
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_solicitation_detail(request, pk=5)

        assert response.status_code == 200
        body = _parse_response(response)
        assert "solicitation" in body
        assert body["solicitation"]["id"] == 5
        mock_da.get_solicitation_by_id.assert_called_once_with(5)

    @patch(_DA_PATCH)
    def test_get_returns_404_when_not_found(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_solicitation_by_id.return_value = None
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_solicitation_detail(request, pk=999)

        assert response.status_code == 404
        body = _parse_response(response)
        assert "error" in body

    @patch(_DA_PATCH)
    def test_put_updates_solicitation(self, mock_get_da):
        mock_da = MagicMock()
        updated = _make_solicitation(id=5, data={"title": "Updated"})
        mock_da.update_solicitation.return_value = updated
        mock_get_da.return_value = mock_da

        request = _make_put_request(body={"title": "Updated"})
        response = api_solicitation_detail(request, pk=5)

        assert response.status_code == 200
        body = _parse_response(response)
        assert body["solicitation"]["id"] == 5
        mock_da.update_solicitation.assert_called_once_with(5, {"title": "Updated"})

    @patch(_DA_PATCH)
    def test_put_invalid_json_returns_400(self, mock_get_da):
        mock_get_da.return_value = MagicMock()

        request = MagicMock()
        request.method = "PUT"
        request.body = b"bad json"
        response = api_solicitation_detail(request, pk=5)

        assert response.status_code == 400


# =========================================================================
# Responses List / Create Tests
# =========================================================================


class TestApiResponsesList:
    @patch(_DA_PATCH)
    def test_get_with_solicitation_id_filter(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_responses_for_solicitation.return_value = [
            _make_response_record(id=10),
            _make_response_record(id=11),
        ]
        mock_get_da.return_value = mock_da

        request = _make_get_request(query_params={"solicitation_id": "1"})
        response = api_responses_list(request)

        assert response.status_code == 200
        body = _parse_response(response)
        assert "responses" in body
        assert len(body["responses"]) == 2
        mock_da.get_responses_for_solicitation.assert_called_once_with(1)

    @patch(_DA_PATCH)
    def test_get_without_solicitation_id_returns_400(self, mock_get_da):
        mock_get_da.return_value = MagicMock()

        request = _make_get_request()
        response = api_responses_list(request)

        assert response.status_code == 400
        body = _parse_response(response)
        assert "solicitation_id" in body["error"]

    @patch(_DA_PATCH)
    def test_get_with_invalid_solicitation_id_returns_400(self, mock_get_da):
        mock_get_da.return_value = MagicMock()

        request = _make_get_request(query_params={"solicitation_id": "not_a_number"})
        response = api_responses_list(request)

        assert response.status_code == 400
        body = _parse_response(response)
        assert "integer" in body["error"]

    @patch(_DA_PATCH)
    def test_get_serializes_response_fields(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_responses_for_solicitation.return_value = [_make_response_record()]
        mock_get_da.return_value = mock_da

        request = _make_get_request(query_params={"solicitation_id": "1"})
        response = api_responses_list(request)

        body = _parse_response(response)
        r = body["responses"][0]
        assert r["solicitation_id"] == 1
        assert r["llo_entity_id"] == "llo_entity_123"
        assert r["llo_entity_name"] == "Test Org"
        assert r["status"] == "submitted"
        assert r["submitted_by_name"] == "Jane Doe"
        assert r["submitted_by_email"] == "jane@example.com"
        assert r["submission_date"] is not None

    @patch(_DA_PATCH)
    def test_post_creates_response_and_returns_201(self, mock_get_da):
        mock_da = MagicMock()
        created = _make_response_record(id=50)
        mock_da.create_response.return_value = created
        mock_get_da.return_value = mock_da

        body_data = {
            "solicitation_id": 1,
            "llo_entity_id": "llo_entity_123",
            "responses": {"q1": "Answer"},
        }
        request = _make_post_request(body=body_data)
        response = api_responses_list(request)

        assert response.status_code == 201
        body = _parse_response(response)
        assert "response" in body
        assert body["response"]["id"] == 50
        mock_da.create_response.assert_called_once_with(
            solicitation_id=1,
            llo_entity_id="llo_entity_123",
            data=body_data,
        )

    @patch(_DA_PATCH)
    def test_post_without_solicitation_id_returns_400(self, mock_get_da):
        mock_get_da.return_value = MagicMock()

        request = _make_post_request(body={"llo_entity_id": "123"})
        response = api_responses_list(request)

        assert response.status_code == 400
        body = _parse_response(response)
        assert "solicitation_id" in body["error"]


# =========================================================================
# Response Detail / Update Tests
# =========================================================================


class TestApiResponseDetail:
    @patch(_DA_PATCH)
    def test_get_returns_response_json(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_response_by_id.return_value = _make_response_record(id=10)
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_response_detail(request, pk=10)

        assert response.status_code == 200
        body = _parse_response(response)
        assert "response" in body
        assert body["response"]["id"] == 10

    @patch(_DA_PATCH)
    def test_get_returns_404_when_not_found(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_response_by_id.return_value = None
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_response_detail(request, pk=999)

        assert response.status_code == 404

    @patch(_DA_PATCH)
    def test_put_updates_response(self, mock_get_da):
        mock_da = MagicMock()
        updated = _make_response_record(id=10, data={"status": "approved"})
        mock_da.update_response.return_value = updated
        mock_get_da.return_value = mock_da

        request = _make_put_request(body={"status": "approved"})
        response = api_response_detail(request, pk=10)

        assert response.status_code == 200
        body = _parse_response(response)
        assert body["response"]["id"] == 10
        mock_da.update_response.assert_called_once_with(10, {"status": "approved"})


# =========================================================================
# Reviews Create Tests
# =========================================================================


class TestApiReviewsCreate:
    @patch(_DA_PATCH)
    def test_post_creates_review_and_returns_201(self, mock_get_da):
        mock_da = MagicMock()
        created = _make_review(id=60)
        mock_da.create_review.return_value = created
        mock_get_da.return_value = mock_da

        body_data = {
            "response_id": 10,
            "llo_entity_id": "llo_entity_123",
            "score": 90,
            "recommendation": "approved",
        }
        request = _make_post_request(body=body_data)
        response = api_reviews_create(request)

        assert response.status_code == 201
        body = _parse_response(response)
        assert "review" in body
        assert body["review"]["id"] == 60
        mock_da.create_review.assert_called_once_with(
            response_id=10,
            data=body_data,
        )

    @patch(_DA_PATCH)
    def test_post_without_response_id_returns_400(self, mock_get_da):
        mock_get_da.return_value = MagicMock()

        request = _make_post_request(body={"score": 85})
        response = api_reviews_create(request)

        assert response.status_code == 400
        body = _parse_response(response)
        assert "response_id" in body["error"]

    @patch(_DA_PATCH)
    def test_post_serializes_review_fields(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.create_review.return_value = _make_review()
        mock_get_da.return_value = mock_da

        body_data = {
            "response_id": 10,
            "score": 85,
            "recommendation": "approved",
        }
        request = _make_post_request(body=body_data)
        response = api_reviews_create(request)

        body = _parse_response(response)
        rv = body["review"]
        assert rv["score"] == 85
        assert rv["recommendation"] == "approved"
        assert rv["notes"] == "Looks good"
        assert rv["tags"] == "experienced,local"
        assert rv["reviewer_username"] == "reviewer1"
        assert rv["review_date"] is not None


# =========================================================================
# Review Detail / Update Tests
# =========================================================================


class TestApiReviewDetail:
    @patch(_DA_PATCH)
    def test_get_returns_review_json(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_review_by_id.return_value = _make_review(id=20)
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_review_detail(request, pk=20)

        assert response.status_code == 200
        body = _parse_response(response)
        assert "review" in body
        assert body["review"]["id"] == 20

    @patch(_DA_PATCH)
    def test_get_returns_404_when_not_found(self, mock_get_da):
        mock_da = MagicMock()
        mock_da.get_review_by_id.return_value = None
        mock_get_da.return_value = mock_da

        request = _make_get_request()
        response = api_review_detail(request, pk=999)

        assert response.status_code == 404

    @patch(_DA_PATCH)
    def test_put_updates_review(self, mock_get_da):
        mock_da = MagicMock()
        updated = _make_review(id=20, data={"score": 95})
        mock_da.update_review.return_value = updated
        mock_get_da.return_value = mock_da

        request = _make_put_request(body={"score": 95})
        response = api_review_detail(request, pk=20)

        assert response.status_code == 200
        body = _parse_response(response)
        assert body["review"]["id"] == 20
        mock_da.update_review.assert_called_once_with(20, {"score": 95})


# =========================================================================
# Auth Error Tests
# =========================================================================


class TestAuthErrors:
    @patch(_DA_PATCH, side_effect=ValueError("OAuth access token required"))
    def test_solicitations_list_returns_401_on_auth_error(self, mock_get_da):
        request = _make_get_request()
        response = api_solicitations_list(request)
        assert response.status_code == 401

    @patch(_DA_PATCH, side_effect=ValueError("OAuth access token required"))
    def test_solicitation_detail_returns_401_on_auth_error(self, mock_get_da):
        request = _make_get_request()
        response = api_solicitation_detail(request, pk=1)
        assert response.status_code == 401

    @patch(_DA_PATCH, side_effect=ValueError("OAuth access token required"))
    def test_responses_list_returns_401_on_auth_error(self, mock_get_da):
        request = _make_get_request()
        response = api_responses_list(request)
        assert response.status_code == 401

    @patch(_DA_PATCH, side_effect=ValueError("OAuth access token required"))
    def test_reviews_create_returns_401_on_auth_error(self, mock_get_da):
        request = _make_post_request(body={"response_id": 1})
        response = api_reviews_create(request)
        assert response.status_code == 401
