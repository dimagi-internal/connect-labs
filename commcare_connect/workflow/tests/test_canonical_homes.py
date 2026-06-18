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

from django.urls import reverse


def test_fetch_opportunity_metadata_canonical_home():
    """The cc_domain resolver is importable from labs.analysis.data_access."""
    from commcare_connect.labs.analysis.data_access import fetch_opportunity_metadata

    assert callable(fetch_opportunity_metadata)


def test_sql_backend_does_not_import_mbw_template_package():
    """The core SQL CCHQ fetcher must not import from a template package."""
    from commcare_connect.labs.analysis.backends.sql import cchq_fetcher

    source = Path(inspect.getfile(cchq_fetcher)).read_text(encoding="utf-8")
    assert "templates.mbw_monitoring" not in source, (
        "cchq_fetcher imports from the mbw_monitoring template package; "
        "it should import generic helpers from labs.analysis.data_access instead."
    )


def test_opportunity_flws_endpoint_in_workflow_app():
    """The FLW-list endpoint is owned by the workflow app, not a template."""
    from commcare_connect.workflow.views import OpportunityFLWListAPIView  # noqa: F401

    assert reverse("labs:workflow:api_opportunity_flws") == "/labs/workflow/api/opportunity-flws/"


def test_v5_render_does_not_call_mbw_monitoring_namespace():
    """The v5 auditing template must not phone home to the mbw_monitoring URLs."""
    render = (Path(__file__).resolve().parents[1] / "templates" / "mbw_auditing_v5_render.js").read_text(
        encoding="utf-8"
    )
    assert "/custom_analysis/mbw_monitoring/api/opportunity-flws/" not in render
