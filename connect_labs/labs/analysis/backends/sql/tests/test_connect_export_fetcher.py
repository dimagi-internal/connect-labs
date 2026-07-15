"""Tests for fetch_connect_export_as_visit_dicts' redirect handling.

Observed live against production: the .../export/opportunity/<id>/work_areas/
endpoint 301-redirects for some opportunities (3 of 4 CHC PRE-RCT LLOs) but not
others. httpx.Client does not follow redirects unless told to, so a plain
raise_for_status() treats that 301 as a hard failure -- the fetch never even
reaches the real (redirected) URL. This locks in follow_redirects=True.
"""

from django.test import override_settings

from connect_labs.labs.analysis.backends.sql.connect_export_fetcher import fetch_connect_export_as_visit_dicts
from connect_labs.labs.analysis.config import DataSourceConfig


def _data_source():
    return DataSourceConfig(type="connect_export", endpoint="work_areas")


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_follows_redirect_instead_of_raising(httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/1973/work_areas/?page_size=500",
        status_code=301,
        headers={"Location": "https://connect.example.com/export/opportunity/1973/work_areas?page_size=500"},
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/1973/work_areas?page_size=500",
        json={"results": [{"id": 7097, "ward": "Unguwar Gabas", "status": "NOT_VISITED"}], "next": None},
    )

    records = fetch_connect_export_as_visit_dicts(
        request=None,
        data_source=_data_source(),
        access_token="tok",
        opportunity_id=1973,
    )

    assert len(records) == 1
    assert records[0]["form_json"]["work_area"]["ward"] == "Unguwar Gabas"
    assert records[0]["entity_id"] == "7097"


@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_paginates_across_multiple_pages(httpx_mock):
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/1978/work_areas/?page_size=500",
        json={
            "results": [{"id": 1, "ward": "Unguwar Gabas"}],
            "next": "https://connect.example.com/export/opportunity/1978/work_areas/?page=2",
        },
    )
    httpx_mock.add_response(
        url="https://connect.example.com/export/opportunity/1978/work_areas/?page=2",
        json={"results": [{"id": 2, "ward": "Unguwar Gabas"}], "next": None},
    )

    records = fetch_connect_export_as_visit_dicts(
        request=None,
        data_source=_data_source(),
        access_token="tok",
        opportunity_id=1978,
    )

    assert [r["entity_id"] for r in records] == ["1", "2"]
