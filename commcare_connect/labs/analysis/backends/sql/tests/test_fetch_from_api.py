"""Integration tests for SQLBackend._fetch_from_api (v2 paginated JSON)."""
import pytest
from django.test import override_settings

from commcare_connect.labs.analysis.backends.sql.backend import SQLBackend


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_fetch_from_api_paginates_and_converts_records(httpx_mock):
    """Verifies the full chain: ExportAPIClient pagination → record_to_visit_dict conversion."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
            "results": [
                {
                    "id": 1,
                    "opportunity_id": 42,
                    "username": "alice",
                    "deliver_unit": "DU1",
                    "deliver_unit_id": 7,
                    "entity_id": "ent-1",
                    "entity_name": "Household 1",
                    "visit_date": "2026-04-01",
                    "status": "approved",
                    "flagged": False,
                    "form_json": {"id": "xform-abc-123", "form": {"q1": "v1"}},
                    "completed_work_id": 9,
                    "images": [],
                },
            ],
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
        json={
            "next": None,
            "results": [
                {
                    "id": 2,
                    "opportunity_id": 42,
                    "username": "bob",
                    "deliver_unit": "DU2",
                    "form_json": {"id": "xform-def-456"},
                    "flagged": True,
                    "images": [],
                },
            ],
        },
    )

    backend = SQLBackend()
    visits = backend._fetch_from_api(opportunity_id=42, access_token="test-token")

    assert len(visits) == 2

    # First visit: full record, form_json preserved as dict, xform_id extracted
    assert visits[0]["id"] == 1
    assert visits[0]["username"] == "alice"
    assert visits[0]["form_json"] == {"id": "xform-abc-123", "form": {"q1": "v1"}}
    assert visits[0]["xform_id"] == "xform-abc-123"
    assert visits[0]["flagged"] is False

    # Second visit: from page 2, also converted correctly
    assert visits[1]["id"] == 2
    assert visits[1]["username"] == "bob"
    assert visits[1]["xform_id"] == "xform-def-456"
    assert visits[1]["flagged"] is True


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_fetch_from_api_passes_images_param_when_requested(httpx_mock):
    """Verifies include_images=True adds ?images=true to the request URL."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?images=true",
        json={"next": None, "results": []},
    )

    backend = SQLBackend()
    visits = backend._fetch_from_api(opportunity_id=42, access_token="test-token", include_images=True)

    assert visits == []
    request = httpx_mock.get_request()
    assert "images=true" in str(request.url)


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_fetch_from_api_raises_runtime_error_on_export_api_failure(httpx_mock):
    """Verifies ExportAPIError is wrapped as RuntimeError for caller compatibility."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/",
        status_code=500,
    )

    backend = SQLBackend()
    with pytest.raises(RuntimeError, match="Connect export API error"):
        backend._fetch_from_api(opportunity_id=42, access_token="test-token")
