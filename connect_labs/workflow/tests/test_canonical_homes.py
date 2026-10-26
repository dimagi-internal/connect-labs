"""Architectural regression tests for the MBW re-homing refactor.

Two pieces of generic infrastructure used to live inside the MBW v1 template
package (since deleted) and were imported by core, non-MBW code. They live in
canonical homes:

- ``fetch_opportunity_metadata`` -> ``labs.analysis.data_access``
- ``OpportunityFLWListAPIView`` -> ``workflow.views`` (at
  ``/labs/workflow/api/opportunity-flws/``)

These tests lock that in so the coupling can't silently regress.
"""

from unittest import mock

from django.core.cache import cache
from django.urls import reverse


def test_fetch_opportunity_metadata_canonical_home():
    """The cc_domain resolver is importable from labs.analysis.data_access."""
    from connect_labs.labs.analysis.data_access import fetch_opportunity_metadata

    assert callable(fetch_opportunity_metadata)


def test_opportunity_flws_endpoint_in_workflow_app():
    """The FLW-list endpoint is owned by the workflow app, not a template."""
    from connect_labs.workflow.views import OpportunityFLWListAPIView  # noqa: F401

    assert reverse("labs:workflow:api_opportunity_flws") == "/labs/workflow/api/opportunity-flws/"


def test_opportunity_detail_is_single_owner_of_the_get():
    """fetch_opportunity_metadata is built on the shared fetch_opportunity_detail primitive."""
    from connect_labs.labs.analysis import data_access

    payload = {"name": "Opp", "deliver_app": {"cc_domain": "ccc-x", "cc_app_id": "app1"}}
    resp = mock.Mock()
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None

    cache.delete("opp_metadata:42")
    with mock.patch.object(data_access.httpx, "get", return_value=resp) as mocked_get:
        assert data_access.fetch_opportunity_detail("tok", 42) == payload
        meta = data_access.fetch_opportunity_metadata("tok", 42)

    assert mocked_get.call_count == 2  # one per call; metadata reuses the same primitive
    assert meta["cc_domain"] == "ccc-x"
    assert meta["raw"] == payload


def test_admin_get_opportunity_details_delegates_to_canonical():
    """The labs admin app-downloader no longer re-implements the opportunity GET; it delegates."""
    from connect_labs.labs.admin.app_data_access import AppDownloaderDataAccess
    from connect_labs.labs.analysis import data_access

    da = AppDownloaderDataAccess(access_token="tok")
    payload = {"name": "Opp", "deliver_app": {}}
    with mock.patch.object(data_access, "fetch_opportunity_detail", return_value=payload) as m:
        assert da.get_opportunity_details(99) == payload
    m.assert_called_once_with("tok", 99)
