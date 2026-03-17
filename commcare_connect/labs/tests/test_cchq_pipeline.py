"""
Tests for CCHQ Form API data source in the pipeline.

Mocks CommCareDataAccess and verifies that CCHQ forms are normalized
and processed through the SQL backend correctly.
"""

import django
from django.conf import settings

# Minimal Django configuration so model imports in the analysis chain resolve.
if not settings.configured:
    settings.configure(
        INSTALLED_APPS=[
            "django.contrib.contenttypes",
            "django.contrib.auth",
        ],
        DATABASES={
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": ":memory:",
            }
        },
    )
    django.setup()

from commcare_connect.labs.analysis.backends.sql.cchq_fetcher import normalize_cchq_form_to_visit_dict  # noqa: E402


class TestNormalizeCCHQForm:
    """Test CCHQ form normalization to visit dict shape."""

    def test_basic_normalization(self):
        form = {
            "id": "abc-123",
            "received_on": "2026-01-15T10:30:00Z",
            "form": {
                "meta": {"username": "testuser", "userID": "user-uuid"},
                "mother_name": "Jane Doe",
                "expected_visits": "6",
            },
        }
        result = normalize_cchq_form_to_visit_dict(form, 0)

        assert result["id"] == "abc-123"
        assert result["username"] == "testuser"
        assert result["visit_date"] == "2026-01-15"
        assert result["form_json"] == form  # Full form is preserved
        assert result["status"] == "approved"

    def test_field_extraction_paths_work(self):
        """Verify that FieldComputation paths like 'form.mother_name' work on normalized dicts."""
        from commcare_connect.labs.analysis.utils import extract_json_path

        form = {
            "id": "test-1",
            "received_on": "2026-01-15T10:30:00Z",
            "form": {
                "meta": {"username": "user1"},
                "mother_name": "Jane",
                "expected_visits": "6",
            },
        }
        visit_dict = normalize_cchq_form_to_visit_dict(form, 0)

        # FieldComputation uses form_json as the source for path extraction
        form_json = visit_dict["form_json"]
        assert extract_json_path(form_json, "form.mother_name") == "Jane"
        assert extract_json_path(form_json, "form.expected_visits") == "6"

    def test_missing_username_falls_back_to_user_id(self):
        form = {
            "id": "test-2",
            "received_on": "2026-01-15T10:30:00Z",
            "form": {
                "meta": {"userID": "user-uuid-123"},
            },
        }
        result = normalize_cchq_form_to_visit_dict(form, 0)
        assert result["username"] == "user-uuid-123"
