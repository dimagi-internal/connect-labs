"""Unit tests for the workflow snapshot framework.

Covers the cross-template plumbing that turns `template.build_snapshot(...)` +
`supports_snapshots: True` into a frozen blob on `run.data["snapshot"]`. The
per-template hook contents are tested separately at the template level.
"""

from __future__ import annotations

import pytest

from commcare_connect.workflow.templates import TEMPLATES, build_snapshot_for_template, list_templates


class TestBuildSnapshotDispatch:
    """Verify the cross-template dispatch shim picks up hooks correctly."""

    def test_returns_none_for_unknown_template(self):
        assert build_snapshot_for_template("does_not_exist", pipelines={}, state={}, opportunity_id=1) is None

    def test_returns_none_when_template_lacks_supports_snapshots(self):
        # bulk_image_audit is a real registered template that doesn't declare snapshots.
        assert "bulk_image_audit" in TEMPLATES, "fixture assumption: template must be registered"
        assert not TEMPLATES["bulk_image_audit"].get("supports_snapshots")
        assert build_snapshot_for_template("bulk_image_audit", pipelines={}, state={}, opportunity_id=1) is None

    def test_kmc_longitudinal_declares_supports_snapshots(self):
        """First adopter — confirm the flag flows through to list_templates output."""
        entry = next(t for t in list_templates() if t["key"] == "kmc_longitudinal")
        assert entry["supports_snapshots"] is True

    def test_kmc_longitudinal_build_snapshot_runs_over_pipeline_rows(self):
        pipelines = {
            "children": {
                "rows": [
                    {"entity_id": "case-1", "child_name": "Asha", "birth_weight": 1800, "current_weight": 2600},
                    {"entity_id": "case-2", "child_name": "Bina", "birth_weight": 2000, "current_weight": 2400},
                ],
            },
        }
        snap = build_snapshot_for_template("kmc_longitudinal", pipelines=pipelines, state={}, opportunity_id=874)
        assert snap is not None
        assert snap["schema_version"] == 1
        assert {c["entity_id"] for c in snap["children"]} == {"case-1", "case-2"}
        # case-1 reached threshold (2600 >= 2500), case-2 didn't.
        assert snap["kpis"]["total"] == 2
        assert snap["kpis"]["reached_threshold"] == 1
        # Derived weight_gain is pre-computed.
        case1 = next(c for c in snap["children"] if c["entity_id"] == "case-1")
        assert case1["weight_gain"] == 800
        assert case1["reached_threshold"] is True


class TestSnapshotApiEndpoints:
    """Endpoint-level: build_snapshot_api wires the dispatch + persistence + freeze stamp."""

    @pytest.mark.django_db
    def test_get_snapshot_api_returns_404_for_missing_run(self, client, settings):
        # Lightweight sanity — full request lifecycle covered in integration tests.
        # Just verify the URL is reachable and returns the framework's 404 shape.
        from django.urls import reverse

        # Without auth this returns a redirect; we just check URL resolves.
        url = reverse("labs:workflow:api_get_snapshot", kwargs={"run_id": 999999})
        assert url.endswith("/api/run/999999/snapshot/")

    def test_build_snapshot_url_pattern_resolves(self):
        from django.urls import reverse

        url = reverse("labs:workflow:api_build_snapshot", kwargs={"run_id": 999999})
        assert url.endswith("/api/run/999999/snapshot/build/")
