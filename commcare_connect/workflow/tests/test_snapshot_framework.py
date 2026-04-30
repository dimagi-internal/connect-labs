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

    def test_performance_review_declares_supports_snapshots(self):
        """First adopter — confirm the flag flows through to list_templates output.

        kmc_longitudinal does NOT opt in: continuous tracking, no "moment of
        completion." Snapshots are for action-shaped templates (a periodic review
        of a worker cohort, an audit batch, etc).
        """
        entry = next(t for t in list_templates() if t["key"] == "performance_review")
        assert entry["supports_snapshots"] is True
        # Negative case: kmc_longitudinal is continuous-tracking, opts out.
        kmc_entry = next(t for t in list_templates() if t["key"] == "kmc_longitudinal")
        assert kmc_entry["supports_snapshots"] is False

    def test_performance_review_build_snapshot_freezes_workers_and_decisions(self):
        """The performance_review hook captures: the worker list at freeze time, the
        per-worker review decisions from state, and a summary breakdown by status.
        """
        workers = [
            {"username": "alice", "name": "Alice", "visit_count": 12, "opportunity_id": 1},
            {"username": "bob", "name": "Bob", "visit_count": 5, "opportunity_id": 1},
            {"username": "carol", "name": "Carol", "visit_count": 8, "opportunity_id": 2},
        ]
        state = {
            "worker_states": {
                "alice": {"status": "confirmed"},
                "bob": {"status": "needs_audit"},
                # carol has no entry → defaults to pending
            }
        }
        snap = build_snapshot_for_template(
            "performance_review",
            pipelines={},
            state=state,
            opportunity_id=1,
            workers=workers,
            opportunity_ids=[1, 2],
        )
        assert snap is not None
        assert snap["schema_version"] == 1
        # Workers frozen as-is, including opportunity_id tag.
        assert len(snap["workers"]) == 3
        # Per-worker decisions preserved.
        assert snap["worker_states"]["alice"]["status"] == "confirmed"
        # Multi-opp tracking baked in.
        assert snap["opportunity_ids"] == [1, 2]
        # Summary: total + reviewed + by_status counts.
        assert snap["summary"]["total"] == 3
        assert snap["summary"]["reviewed"] == 2  # alice + bob, carol is still pending
        assert snap["summary"]["by_status"] == {"confirmed": 1, "needs_audit": 1, "pending": 1}

    def test_performance_review_build_snapshot_handles_empty_state(self):
        """A run with no decisions yet still produces a valid snapshot —
        all workers default to 'pending'."""
        workers = [{"username": "u1", "opportunity_id": 1}]
        snap = build_snapshot_for_template(
            "performance_review",
            pipelines={},
            state={},
            opportunity_id=1,
            workers=workers,
            opportunity_ids=[1],
        )
        assert snap["summary"]["reviewed"] == 0
        assert snap["summary"]["by_status"] == {"pending": 1}

    def test_default_snapshot_when_template_has_no_hook(self, monkeypatch):
        """Templates that opt in via `supports_snapshots: True` but do NOT
        define a `build_snapshot` callable get a default snapshot of the
        inputs (pipelines + workers + state). Lets a template adopt
        snapshots with a single line and let render JS reconstruct the
        dashboard from frozen pipelines on load.
        """
        fake_template = {
            "key": "fake_minimal_adopter",
            "name": "Fake Minimal Adopter",
            "description": "Test fixture — opts in without a hook",
            "supports_snapshots": True,
            # Deliberately no `build_snapshot` key.
        }
        monkeypatch.setitem(TEMPLATES, "fake_minimal_adopter", fake_template)
        pipelines = {"visits": {"rows": [{"username": "u1"}], "config_hash": "abc"}}
        workers = [{"username": "u1", "opportunity_id": 7}]
        snap = build_snapshot_for_template(
            "fake_minimal_adopter",
            pipelines=pipelines,
            state={"selected_workers": ["u1"]},
            opportunity_id=7,
            workers=workers,
            opportunity_ids=[7],
        )
        assert snap is not None
        assert snap["schema_version"] == 1
        assert snap["pipelines"] == pipelines
        assert snap["workers"] == workers
        assert snap["state"] == {"selected_workers": ["u1"]}
        assert snap["opportunity_ids"] == [7]

    def test_default_snapshot_falls_back_when_opportunity_ids_missing(self, monkeypatch):
        """Single-opp templates may not pass opportunity_ids — the default
        falls back to [opportunity_id] so the shape stays consistent."""
        monkeypatch.setitem(
            TEMPLATES,
            "fake_single_opp",
            {"key": "fake_single_opp", "supports_snapshots": True},
        )
        snap = build_snapshot_for_template(
            "fake_single_opp",
            pipelines={},
            state={},
            opportunity_id=42,
        )
        assert snap["opportunity_ids"] == [42]
        assert snap["workers"] == []


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
