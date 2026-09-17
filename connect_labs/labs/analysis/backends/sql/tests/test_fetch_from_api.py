"""Integration tests for the raw-visit FILL against the v2 paginated JSON API.

These used to drive `SQLBackend._fetch_from_api`, which paginated the whole export
into one list and handed it to `store_raw_visits`. That method is gone: it made a
request for N rows cost O(entire opportunity) — ~1.05 GB for a ~30k-visit
opportunity at ~35 KB a visit with `form_json`, on a 4096 MB task. The fill is now
`_stream_raw_visits_uncached`, which writes a page at a time and keeps nothing.

The HTTP contract they covered is unchanged and still worth pinning, so the same
three cases are kept and pointed at the path that actually runs: the pagination
chain with record conversion, `?images=true` reaching the wire, and ExportAPIError
being wrapped as RuntimeError. What each now asserts is what landed in the CACHE,
because the fill no longer returns rows.

See `test_raw_fill_is_bounded.py` for the memory bound itself.
"""

import pytest
from django.test import override_settings

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

# get_export_client queries SyntheticOpportunity to route real vs fixture reads,
# so these tests need DB access even though they mock HTTP.
pytestmark = pytest.mark.django_db


def _drain(backend, cache_manager, *, opportunity_id=42, include_images=False):
    """Run the fill to completion; it yields progress events and returns nothing."""
    for _event in backend._stream_raw_visits_uncached(
        opportunity_id,
        "test-token",
        cache_manager,
        expected_visit_count=None,
        user=None,
        accept_low_count=True,
        pipeline_id=None,
        include_images=include_images,
    ):
        pass


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_fill_paginates_and_converts_records(httpx_mock):
    """The full chain: ExportAPIClient pagination → record_to_visit_dict → cache."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?page_size=2500",
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
    cache_manager = SQLCacheManager(42)
    _drain(backend, cache_manager)

    visits = sorted(
        backend._load_from_cache(cache_manager, skip_form_json=False, filter_visit_ids=None),
        key=lambda v: str(v["id"]),
    )
    assert len(visits) == 2

    # First visit: full record, form_json preserved as dict, xform_id extracted
    assert str(visits[0]["id"]) == "1"
    assert visits[0]["username"] == "alice"
    assert visits[0]["form_json"] == {"id": "xform-abc-123", "form": {"q1": "v1"}}
    assert visits[0]["xform_id"] == "xform-abc-123"
    assert visits[0]["flagged"] is False

    # Second visit: from page 2, also converted correctly
    assert str(visits[1]["id"]) == "2"
    assert visits[1]["username"] == "bob"
    assert visits[1]["xform_id"] == "xform-def-456"
    assert visits[1]["flagged"] is True


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_fill_passes_images_param_when_requested(httpx_mock):
    """include_images=True must reach the WIRE, not just the slot's flag."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?images=true&page_size=2500",
        json={"next": None, "results": []},
    )

    backend = SQLBackend()
    _drain(backend, SQLCacheManager(42), include_images=True)

    request = httpx_mock.get_request()
    assert "images=true" in str(request.url)


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_fill_raises_runtime_error_on_export_api_failure(httpx_mock):
    """ExportAPIError is wrapped as RuntimeError for caller compatibility."""
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?page_size=2500",
        status_code=500,
    )

    backend = SQLBackend()
    with pytest.raises(RuntimeError, match="Connect export API error"):
        _drain(backend, SQLCacheManager(42))


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_xform_id_survives_the_cache_round_trip(httpx_mock):
    """A cache READ must carry xform_id, or the HQ form link silently disappears.

    RawVisitCache has no column for it, so before it was derived on read-back the
    same visit had xform_id on a MISS and no such key on a HIT.
    `audit.link_helpers` reads it with .get(), so a hit yielded None and
    build_hq_form_url returned "" -- a vanished link, only on the fast reads.
    """
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/42/user_visits/?page_size=2500",
        json={
            "next": None,
            "results": [{"id": 1, "opportunity_id": 42, "form_json": {"id": "xform-abc-123"}}],
        },
    )

    backend = SQLBackend()
    cache_manager = SQLCacheManager(42)
    _drain(backend, cache_manager)

    full = backend._load_from_cache(cache_manager, skip_form_json=False, filter_visit_ids=None)
    assert full[0]["xform_id"] == "xform-abc-123"

    # Slim mode does not load the form body, so it cannot derive the id -- and
    # record_to_visit_dict clears it for the same reason. The two must agree.
    slim = backend._load_from_cache(cache_manager, skip_form_json=True, filter_visit_ids=None)
    assert slim[0]["xform_id"] is None
