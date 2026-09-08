"""Integration tests for SQLBackend.stream_raw_visits (v2 paginated JSON)."""
import pytest
from django.test import override_settings

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache


@pytest.mark.django_db
@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_stream_raw_visits_yields_progress_per_page_and_complete(httpx_mock):
    """Verifies the producer yields ('progress', rows, total) per page and ("complete", row_count) at the end."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?page_size=2500",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
            "results": [
                {
                    "id": 1,
                    "opportunity_id": 42,
                    "username": "alice",
                    "form_json": {"id": "xform-1"},
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
                    "form_json": {"id": "xform-2"},
                    "images": [],
                },
            ],
        },
    )

    backend = SQLBackend()
    events = list(backend.stream_raw_visits(opportunity_id=42, access_token="t", expected_visit_count=2))

    # Two progress events (one per page) + one complete event
    progress_events = [e for e in events if e[0] == "progress"]
    complete_events = [e for e in events if e[0] == "complete"]

    assert len(progress_events) == 2
    assert progress_events[0] == ("progress", 1, 2)
    assert progress_events[1] == ("progress", 2, 2)

    assert len(complete_events) == 1
    # The payload is a COUNT, not the rows. Nothing downstream reads the rows --
    # they are read back out of Postgres -- so retaining them only bought memory
    # proportional to the whole opportunity. See #1575.
    assert complete_events[0][1] == 2
    assert not isinstance(complete_events[0][1], list)

    # Cache was finalized — rows should be visible to readers
    assert RawVisitCache.objects.filter(opportunity_id=42, visit_count=2).count() == 2

    # ...and form_json must still have been PERSISTED. This is the half that
    # matters now that nothing is retained in memory: the extraction step reads
    # form_json from these rows, so a "stream to SQL" that dropped it would be
    # silently lossy rather than merely leaner.
    stored = RawVisitCache.objects.filter(opportunity_id=42).order_by("visit_id")
    assert [r.form_json for r in stored] == [{"id": "xform-1"}, {"id": "xform-2"}]


@pytest.mark.django_db
@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_stream_raw_visits_aborts_cache_on_export_api_error(httpx_mock):
    """Verifies that on mid-stream HTTP error, sentinel rows are cleaned up."""
    # Page 1 succeeds
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?page_size=2500",
        json={
            "next": "https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
            "results": [
                {"id": 1, "opportunity_id": 42, "username": "alice", "form_json": {}, "images": []},
            ],
        },
    )
    # Page 2 returns 500
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?last_id=1",
        status_code=500,
    )

    backend = SQLBackend()
    with pytest.raises(RuntimeError, match="Connect export API error"):
        list(backend.stream_raw_visits(opportunity_id=42, access_token="t", expected_visit_count=2))

    # No sentinel rows should remain — abort cleaned them up.
    # (We can't easily query negative-count rows without knowing the sentinel value,
    # but we can assert there are NO rows at all for this opportunity since no
    # previous run finalized any.)
    assert RawVisitCache.objects.filter(opportunity_id=42).count() == 0
