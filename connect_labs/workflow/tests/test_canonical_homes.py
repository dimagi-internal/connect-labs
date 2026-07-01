"""Architectural regression tests for the MBW re-homing refactor.

Two pieces of generic infrastructure used to live inside the
``workflow.templates.mbw_monitoring`` template package and were imported by
core, non-MBW code (the SQL backend, the workflow views, and the v5 auditing
template's render JS). They now live in canonical homes:

- ``fetch_opportunity_metadata`` -> ``labs.analysis.data_access``
- ``OpportunityFLWListAPIView`` -> ``workflow.views`` (at
  ``/labs/workflow/api/opportunity-flws/``)

These tests lock that in so the coupling can't silently regress.
"""

import inspect
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.urls import reverse


def test_fetch_opportunity_metadata_canonical_home():
    """The cc_domain resolver is importable from labs.analysis.data_access."""
    from connect_labs.labs.analysis.data_access import fetch_opportunity_metadata

    assert callable(fetch_opportunity_metadata)


def test_sql_backend_does_not_import_mbw_template_package():
    """The core SQL CCHQ fetcher must not import from a template package."""
    from connect_labs.labs.analysis.backends.sql import cchq_fetcher

    source = Path(inspect.getfile(cchq_fetcher)).read_text(encoding="utf-8")
    assert "templates.mbw_monitoring" not in source, (
        "cchq_fetcher imports from the mbw_monitoring template package; "
        "it should import generic helpers from labs.analysis.data_access instead."
    )


def test_opportunity_flws_endpoint_in_workflow_app():
    """The FLW-list endpoint is owned by the workflow app, not a template."""
    from connect_labs.workflow.views import OpportunityFLWListAPIView  # noqa: F401

    assert reverse("labs:workflow:api_opportunity_flws") == "/labs/workflow/api/opportunity-flws/"


def test_v5_render_does_not_call_mbw_monitoring_namespace():
    """The v5 auditing template must not phone home to the mbw_monitoring URLs."""
    render = (Path(__file__).resolve().parents[1] / "templates" / "mbw_auditing_v5_render.js").read_text(
        encoding="utf-8"
    )
    assert "/custom_analysis/mbw_monitoring/api/opportunity-flws/" not in render


def test_mbw_monitoring_v1_is_deprecated_and_delisted():
    """v1 mbw_monitoring stays in the registry (live instances) but is hidden
    from the creatable list and cannot be instantiated anew."""
    import pytest

    from connect_labs.workflow.templates import TEMPLATES, create_workflow_from_template, get_template, list_templates

    # Still registered so existing instances resolve.
    assert "mbw_monitoring" in TEMPLATES
    assert get_template("mbw_monitoring") is not None
    assert get_template("mbw_monitoring").get("deprecated") is True

    # Hidden from the creatable/listing surface (UI menu + MCP list_templates).
    listed = {t["key"] for t in list_templates()}
    assert "mbw_monitoring" not in listed
    assert "mbw_auditing_v5" in listed  # the current pattern is still listed

    # Cannot be created anew.
    with pytest.raises(ValueError, match="deprecated"):
        create_workflow_from_template(data_access=None, template_key="mbw_monitoring")


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


def test_explorer_get_opportunity_details_delegates_to_canonical():
    """The explorer no longer re-implements the opportunity GET; it delegates."""
    from connect_labs.labs.analysis import data_access
    from connect_labs.labs.explorer.app_data_access import AppDownloaderDataAccess

    da = AppDownloaderDataAccess(access_token="tok")
    payload = {"name": "Opp", "deliver_app": {}}
    with mock.patch.object(data_access, "fetch_opportunity_detail", return_value=payload) as m:
        assert da.get_opportunity_details(99) == payload
    m.assert_called_once_with("tok", 99)
