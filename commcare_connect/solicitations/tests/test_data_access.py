"""
Tests for solicitations data access layer.

All tests mock LabsRecordAPIClient to avoid real API calls.
Mock returns LocalLabsRecord-compatible objects (proxy model instances).
"""

from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.labs.models import LocalLabsRecord
from commcare_connect.solicitations.data_access import (
    RESPONSE_TYPE,
    REVIEW_TYPE,
    SOLICITATION_TYPE,
    SolicitationsDataAccess,
)
from commcare_connect.solicitations.models import ResponseRecord, ReviewRecord, SolicitationRecord

# =========================================================================
# Fixtures
# =========================================================================


def _make_solicitation_record(**overrides):
    """Create a SolicitationRecord from dict data."""
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
    """Create a ResponseRecord from dict data."""
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


def _make_review_record(**overrides):
    """Create a ReviewRecord from dict data."""
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


@pytest.fixture
def mock_api_client():
    """Create a mock LabsRecordAPIClient."""
    with patch("commcare_connect.solicitations.data_access.LabsRecordAPIClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def data_access(mock_api_client):
    """Create a SolicitationsDataAccess with mocked API client."""
    da = SolicitationsDataAccess(
        program_id="42",
        access_token="test-token",
    )
    # Replace the labs_api with our mock (constructor already called LabsRecordAPIClient)
    da.labs_api = mock_api_client
    return da


# =========================================================================
# Constructor Tests
# =========================================================================


class TestConstructor:
    def test_requires_access_token(self):
        """Raises ValueError when no access token is provided."""
        with pytest.raises(ValueError, match="OAuth access token required"):
            SolicitationsDataAccess(program_id="42")

    @patch("commcare_connect.solicitations.data_access.LabsRecordAPIClient")
    def test_stores_program_id(self, MockClient):
        da = SolicitationsDataAccess(program_id="42", access_token="tok")
        assert da.program_id == "42"

    @patch("commcare_connect.solicitations.data_access.LabsRecordAPIClient")
    def test_creates_api_client_with_token(self, MockClient):
        SolicitationsDataAccess(program_id="42", access_token="tok")
        MockClient.assert_called_once_with("tok", program_id=42)

    @patch("commcare_connect.solicitations.data_access.LabsRecordAPIClient")
    def test_extracts_context_from_request(self, MockClient):
        request = MagicMock()
        request.labs_context = {"program_id": 99}
        request.session = {"labs_oauth": {"access_token": "req-tok", "expires_at": 9999999999}}
        da = SolicitationsDataAccess(request=request)
        assert da.program_id == "99"

    @patch("commcare_connect.solicitations.data_access.LabsRecordAPIClient")
    def test_explicit_program_id_overrides_context(self, MockClient):
        request = MagicMock()
        request.labs_context = {"program_id": 99}
        request.session = {"labs_oauth": {"access_token": "req-tok", "expires_at": 9999999999}}
        da = SolicitationsDataAccess(program_id="42", request=request)
        assert da.program_id == "42"


# =========================================================================
# Solicitation Tests
# =========================================================================


class TestGetSolicitations:
    def test_returns_solicitation_records(self, data_access, mock_api_client):
        records = [_make_solicitation_record(id=1), _make_solicitation_record(id=2)]
        mock_api_client.get_records.return_value = records

        result = data_access.get_solicitations()

        assert result == records
        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
        )

    def test_filters_by_status(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []

        data_access.get_solicitations(status="active")

        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
            status="active",
        )

    def test_filters_by_solicitation_type(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []

        data_access.get_solicitations(solicitation_type="rfp")

        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
            solicitation_type="rfp",
        )

    def test_filters_by_both(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []

        data_access.get_solicitations(status="draft", solicitation_type="eoi")

        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
            status="draft",
            solicitation_type="eoi",
        )


class TestGetPublicSolicitations:
    def test_passes_public_flag(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []

        data_access.get_public_solicitations()

        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            public=True,
            model_class=SolicitationRecord,
        )

    def test_filters_by_type(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []

        data_access.get_public_solicitations(solicitation_type="eoi")

        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            public=True,
            model_class=SolicitationRecord,
            solicitation_type="eoi",
        )


class TestGetSolicitationById:
    def test_returns_record(self, data_access, mock_api_client):
        record = _make_solicitation_record(id=5)
        mock_api_client.get_record_by_id.return_value = record

        result = data_access.get_solicitation_by_id(5)

        assert result is record
        mock_api_client.get_record_by_id.assert_called_once_with(
            record_id=5,
            experiment="42",
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
        )

    def test_returns_none_when_not_found(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None

        result = data_access.get_solicitation_by_id(999)

        assert result is None


class TestCreateSolicitation:
    def test_creates_record(self, data_access, mock_api_client):
        input_data = {"title": "New Solicitation", "status": "draft"}
        api_return = LocalLabsRecord(
            {
                "id": 100,
                "experiment": "42",
                "type": SOLICITATION_TYPE,
                "data": input_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.create_record.return_value = api_return

        result = data_access.create_solicitation(input_data)

        assert isinstance(result, SolicitationRecord)
        assert result.id == 100
        mock_api_client.create_record.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            data=input_data,
            program_id=42,
            public=False,
        )

    def test_sets_public_flag_from_data(self, data_access, mock_api_client):
        input_data = {"title": "Public Solicitation", "is_public": True}
        api_return = LocalLabsRecord(
            {
                "id": 101,
                "experiment": "42",
                "type": SOLICITATION_TYPE,
                "data": input_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.create_record.return_value = api_return

        data_access.create_solicitation(input_data)

        mock_api_client.create_record.assert_called_once_with(
            experiment="42",
            type=SOLICITATION_TYPE,
            data=input_data,
            program_id=42,
            public=True,
        )


class TestUpdateSolicitation:
    def test_updates_record(self, data_access, mock_api_client):
        updated_data = {"title": "Updated Title", "status": "closed"}
        api_return = LocalLabsRecord(
            {
                "id": 5,
                "experiment": "42",
                "type": SOLICITATION_TYPE,
                "data": updated_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.update_record.return_value = api_return

        result = data_access.update_solicitation(5, updated_data)

        assert isinstance(result, SolicitationRecord)
        assert result.id == 5
        mock_api_client.update_record.assert_called_once_with(
            record_id=5,
            experiment="42",
            type=SOLICITATION_TYPE,
            data=updated_data,
        )


# =========================================================================
# Response Tests
# =========================================================================


class TestGetResponsesForSolicitation:
    def test_returns_response_records(self, data_access, mock_api_client):
        records = [_make_response_record(id=10), _make_response_record(id=11)]
        mock_api_client.get_records.return_value = records

        result = data_access.get_responses_for_solicitation(solicitation_id=1)

        assert result == records
        mock_api_client.get_records.assert_called_once_with(
            type=RESPONSE_TYPE,
            labs_record_id=1,
            model_class=ResponseRecord,
        )


class TestGetResponseById:
    def test_returns_record(self, data_access, mock_api_client):
        record = _make_response_record(id=10)
        mock_api_client.get_record_by_id.return_value = record

        result = data_access.get_response_by_id(10)

        assert result is record
        mock_api_client.get_record_by_id.assert_called_once_with(
            record_id=10,
            type=RESPONSE_TYPE,
            model_class=ResponseRecord,
        )

    def test_returns_none_when_not_found(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None

        result = data_access.get_response_by_id(999)

        assert result is None


class TestCreateResponse:
    def test_creates_record_with_llo_entity_id_as_experiment(self, data_access, mock_api_client):
        input_data = {
            "solicitation_id": 1,
            "llo_entity_id": "llo_entity_123",
            "responses": {"q1": "Answer"},
        }
        api_return = LocalLabsRecord(
            {
                "id": 50,
                "experiment": "llo_entity_123",
                "type": RESPONSE_TYPE,
                "data": input_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.create_record.return_value = api_return

        result = data_access.create_response(
            solicitation_id=1,
            llo_entity_id="llo_entity_123",
            data=input_data,
        )

        assert isinstance(result, ResponseRecord)
        assert result.id == 50
        mock_api_client.create_record.assert_called_once_with(
            experiment="llo_entity_123",
            type=RESPONSE_TYPE,
            data=input_data,
            labs_record_id=1,
        )


class TestAwardResponse:
    def test_awards_response(self, data_access, mock_api_client):
        current_record = _make_response_record(id=10)
        mock_api_client.get_record_by_id.return_value = current_record

        updated_data = dict(current_record.data)
        updated_data["status"] = "awarded"
        updated_data["reward_budget"] = 500000
        updated_data["org_id"] = "org_99"
        api_return = LocalLabsRecord(
            {
                "id": 10,
                "experiment": "llo_entity_123",
                "type": RESPONSE_TYPE,
                "data": updated_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.update_record.return_value = api_return

        result = data_access.award_response(10, reward_budget=500000, org_id="org_99")

        assert isinstance(result, ResponseRecord)
        mock_api_client.update_record.assert_called_once()
        call_data = mock_api_client.update_record.call_args[1]["data"]
        assert call_data["status"] == "awarded"
        assert call_data["reward_budget"] == 500000
        assert call_data["org_id"] == "org_99"

    def test_raises_for_missing_response(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None
        with pytest.raises(ValueError, match="Response 999 not found"):
            data_access.award_response(999, reward_budget=500000, org_id="org_99")


class TestUpdateResponse:
    def test_updates_record(self, data_access, mock_api_client):
        updated_data = {
            "llo_entity_id": "llo_entity_123",
            "responses": {"q1": "Updated answer"},
            "status": "submitted",
        }
        api_return = LocalLabsRecord(
            {
                "id": 10,
                "experiment": "llo_entity_123",
                "type": RESPONSE_TYPE,
                "data": updated_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.update_record.return_value = api_return

        result = data_access.update_response(10, updated_data)

        assert isinstance(result, ResponseRecord)
        assert result.id == 10
        mock_api_client.update_record.assert_called_once_with(
            record_id=10,
            experiment="llo_entity_123",
            type=RESPONSE_TYPE,
            data=updated_data,
        )


# =========================================================================
# Review Tests
# =========================================================================


class TestGetReviewsForResponse:
    def test_returns_review_records(self, data_access, mock_api_client):
        records = [_make_review_record(id=20), _make_review_record(id=21)]
        mock_api_client.get_records.return_value = records

        result = data_access.get_reviews_for_response(response_id=10)

        assert result == records
        mock_api_client.get_records.assert_called_once_with(
            type=REVIEW_TYPE,
            labs_record_id=10,
            model_class=ReviewRecord,
        )


class TestGetReviewById:
    def test_returns_record(self, data_access, mock_api_client):
        record = _make_review_record(id=20)
        mock_api_client.get_record_by_id.return_value = record

        result = data_access.get_review_by_id(20)

        assert result is record
        mock_api_client.get_record_by_id.assert_called_once_with(
            record_id=20,
            type=REVIEW_TYPE,
            model_class=ReviewRecord,
        )

    def test_returns_none_when_not_found(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None

        result = data_access.get_review_by_id(999)

        assert result is None


class TestCreateReview:
    def test_creates_record_with_llo_entity_id_as_experiment(self, data_access, mock_api_client):
        input_data = {
            "response_id": 10,
            "llo_entity_id": "llo_entity_123",
            "score": 90,
            "recommendation": "approved",
            "reviewer_username": "reviewer1",
        }
        api_return = LocalLabsRecord(
            {
                "id": 60,
                "experiment": "llo_entity_123",
                "type": REVIEW_TYPE,
                "data": input_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.create_record.return_value = api_return

        result = data_access.create_review(response_id=10, data=input_data)

        assert isinstance(result, ReviewRecord)
        assert result.id == 60
        mock_api_client.create_record.assert_called_once_with(
            experiment="llo_entity_123",
            type=REVIEW_TYPE,
            data=input_data,
            labs_record_id=10,
        )


class TestAwardResponseAutoAllocation:
    def test_award_creates_fund_allocation(self):
        """When the solicitation has a fund_id, awarding auto-creates a fund allocation."""
        response_data = {
            "solicitation_id": 100,
            "status": "submitted",
            "llo_entity_id": "org1",
            "llo_entity_name": "Partner Org",
        }
        mock_response = ResponseRecord(
            {
                "id": 10,
                "experiment": "org1",
                "type": "solicitation_new_response",
                "opportunity_id": None,
                "data": response_data,
            }
        )
        awarded_data = dict(response_data)
        awarded_data.update({"status": "awarded", "reward_budget": 50000, "org_id": "42"})
        mock_awarded = ResponseRecord(
            {
                "id": 10,
                "experiment": "org1",
                "type": "solicitation_new_response",
                "opportunity_id": None,
                "data": awarded_data,
            }
        )

        solicitation_data = {"title": "Test RFP", "fund_id": 5}
        mock_solicitation = SolicitationRecord(
            {
                "id": 100,
                "experiment": "1",
                "type": "solicitation_new",
                "opportunity_id": None,
                "data": solicitation_data,
            }
        )

        da = SolicitationsDataAccess(program_id="1", access_token="tok")
        with (
            patch.object(da, "get_response_by_id", return_value=mock_response),
            patch.object(da, "update_response", return_value=mock_awarded),
            patch.object(da, "get_solicitation_by_id", return_value=mock_solicitation),
            patch("commcare_connect.funder_dashboard.data_access.FunderDashboardDataAccess") as MockFDA,
        ):
            mock_fda_instance = MockFDA.return_value
            da.award_response(10, reward_budget=50000, org_id="42")
            mock_fda_instance.add_allocation.assert_called_once()
            alloc = mock_fda_instance.add_allocation.call_args[1]["allocation"]
            assert alloc["amount"] == 50000
            assert alloc["type"] == "award"
            assert alloc["response_id"] == 10
            assert alloc["solicitation_id"] == 100


class TestUpdateReview:
    def test_updates_record(self, data_access, mock_api_client):
        updated_data = {
            "llo_entity_id": "llo_entity_123",
            "score": 95,
            "recommendation": "approved",
            "notes": "Updated notes",
        }
        api_return = LocalLabsRecord(
            {
                "id": 20,
                "experiment": "llo_entity_123",
                "type": REVIEW_TYPE,
                "data": updated_data,
                "opportunity_id": 0,
            }
        )
        mock_api_client.update_record.return_value = api_return

        result = data_access.update_review(20, updated_data)

        assert isinstance(result, ReviewRecord)
        assert result.id == 20
        mock_api_client.update_record.assert_called_once_with(
            record_id=20,
            experiment="llo_entity_123",
            type=REVIEW_TYPE,
            data=updated_data,
        )
