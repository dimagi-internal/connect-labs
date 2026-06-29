"""
Tests for the CCHQ Case API v2 data source (cchq_cases) in the pipeline.

Verifies that work-area cases are normalized to visit-dict shape and that
FieldComputation dot-paths like "case.properties.expected_visit_count" and
"case.owner_id" resolve against the normalized form_json.
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

from commcare_connect.labs.analysis.backends.sql.cchq_cases_fetcher import (  # noqa: E402
    normalize_cchq_case_to_visit_dict,
)


def _sample_case():
    """A work-area case roughly as Case API v2 returns it."""
    return {
        "case_id": "wa-abc-123",
        "case_name": "Ward 7 / Area 3",
        "case_type": "work-area",
        "owner_id": "owner-uuid-xyz",
        "date_opened": "2026-01-10T08:00:00Z",
        "date_modified": "2026-02-20T12:34:56Z",
        "closed": False,
        "properties": {
            "status": "Visited",
            "expected_visit_count": "6",
            "building_count": "42",
        },
        "indices": {},
    }


class TestNormalizeCCHQCase:
    def test_basic_normalization(self):
        result = normalize_cchq_case_to_visit_dict(_sample_case(), opportunity_id=1973, index=0)

        assert result["id"] == "wa-abc-123"
        assert result["entity_id"] == "wa-abc-123"
        assert result["entity_name"] == "Ward 7 / Area 3"
        assert result["opportunity_id"] == 1973
        # date_modified wins over date_opened, truncated to the date.
        assert result["visit_date"] == "2026-02-20"
        assert result["date_created"] == "2026-01-10T08:00:00Z"
        assert result["status"] == "approved"
        # Whole case is nested under form_json["case"].
        assert result["form_json"]["case"]["case_id"] == "wa-abc-123"

    def test_field_extraction_paths_work(self):
        """case.properties.* and case.owner_id resolve via the dot-path extractor."""
        from commcare_connect.labs.analysis.utils import extract_json_path

        visit_dict = normalize_cchq_case_to_visit_dict(_sample_case(), opportunity_id=1973, index=0)
        form_json = visit_dict["form_json"]

        assert extract_json_path(form_json, "case.properties.status") == "Visited"
        assert extract_json_path(form_json, "case.properties.expected_visit_count") == "6"
        assert extract_json_path(form_json, "case.properties.building_count") == "42"
        assert extract_json_path(form_json, "case.owner_id") == "owner-uuid-xyz"

    def test_falls_back_to_date_opened_when_no_modified(self):
        case = _sample_case()
        del case["date_modified"]
        result = normalize_cchq_case_to_visit_dict(case, opportunity_id=1973, index=0)
        assert result["visit_date"] == "2026-01-10"

    def test_index_used_when_case_id_missing(self):
        result = normalize_cchq_case_to_visit_dict({}, opportunity_id=1973, index=7)
        assert result["id"] == 7
        assert result["entity_id"] == "7"


class TestFetcherGuards:
    def test_missing_case_type_raises(self):
        import pytest

        from commcare_connect.labs.analysis.backends.sql.cchq_cases_fetcher import fetch_cchq_cases_as_visit_dicts
        from commcare_connect.labs.analysis.config import DataSourceConfig

        ds = DataSourceConfig(type="cchq_cases", case_type="")
        with pytest.raises(ValueError, match="case_type"):
            fetch_cchq_cases_as_visit_dicts(
                request=None,
                data_source=ds,
                access_token="connect-token",
                opportunity_id=1973,
            )

    def test_headless_request_raises(self):
        """cchq_cases needs a web-session CCHQ OAuth token; a None request
        must fail loudly with a typed error, not a NoneType traceback."""
        import pytest

        from commcare_connect.labs.analysis.backends.sql.cchq_cases_fetcher import fetch_cchq_cases_as_visit_dicts
        from commcare_connect.labs.analysis.config import DataSourceConfig
        from commcare_connect.labs.integrations.commcare.api_client import CCHQHeadlessError

        ds = DataSourceConfig(type="cchq_cases", case_type="work-area")
        with pytest.raises(CCHQHeadlessError) as exc:
            fetch_cchq_cases_as_visit_dicts(
                request=None,
                data_source=ds,
                access_token="connect-token",
                opportunity_id=1973,
            )
        msg = str(exc.value)
        assert "cchq_cases" in msg
        assert "headless" in msg.lower() or "no request" in msg.lower()
