"""Unit tests for the workflow snapshot framework.

Covers the cross-template plumbing that turns `template.build_snapshot(...)` +
`supports_saved_runs: True` (or `snapshot_inputs`) into a snapshot blob on
`run.data["snapshot"]` at completion time. Per-template hook contents are
tested separately at the template level.
"""

from __future__ import annotations

from commcare_connect.workflow.templates import TEMPLATES, build_snapshot_for_template, list_templates


class TestBuildSnapshotDispatch:
    """Verify the cross-template dispatch shim picks up hooks correctly."""

    def test_returns_none_for_unknown_template(self):
        assert build_snapshot_for_template("does_not_exist", pipelines={}, state={}, opportunity_id=1) is None

    def test_returns_none_when_template_lacks_supports_saved_runs(self):
        # bulk_image_audit is action-shaped — no saved runs, so completion is
        # not a thing for it and build_snapshot_for_template returns None.
        assert "bulk_image_audit" in TEMPLATES, "fixture assumption: template must be registered"
        assert not TEMPLATES["bulk_image_audit"].get("supports_saved_runs")
        assert build_snapshot_for_template("bulk_image_audit", pipelines={}, state={}, opportunity_id=1) is None

    def test_performance_review_declares_supports_saved_runs(self):
        """Reference adopter — confirm the flag flows through to list_templates output."""
        entry = next(t for t in list_templates() if t["key"] == "performance_review")
        assert entry["supports_saved_runs"] is True
        # kmc_longitudinal opts out: continuous tracking has no "moment of completion."
        kmc_entry = next(t for t in list_templates() if t["key"] == "kmc_longitudinal")
        assert kmc_entry["supports_saved_runs"] is False

    def test_performance_review_snapshot_captures_workers_and_decisions(self):
        """performance_review uses the declarative path (snapshot_inputs) — the
        framework's default hook captures workers + state.worker_states verbatim;
        render code recomputes summary cards from that data on load. No Python
        precomputation needed.
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
            },
            # ui_scratch is NOT in snapshot_inputs.state_keys — should be dropped.
            "ui_scratch": {"sort": "name"},
        }
        snap = build_snapshot_for_template(
            "performance_review",
            pipelines={"performance_data": {"rows": []}},
            state=state,
            opportunity_id=1,
            workers=workers,
            opportunity_ids=[1, 2],
        )
        assert snap is not None
        assert snap["schema_version"] == 1
        # Workers frozen as-is, including opportunity_id tag.
        assert len(snap["workers"]) == 3
        # Per-worker decisions live under snapshot.state.worker_states so the
        # view helper exposes them as view.state.worker_states.
        assert snap["state"]["worker_states"]["alice"]["status"] == "confirmed"
        # Only declared state_keys flow through — ui_scratch is filtered out.
        assert "ui_scratch" not in snap["state"]
        # Pipelines: [] in the manifest means capture none — keeps the snapshot tight.
        assert snap["pipelines"] == {}
        # Multi-opp tracking baked in.
        assert snap["opportunity_ids"] == [1, 2]
        # No precomputed summary — render code does that at render time.
        assert "summary" not in snap

    def test_performance_review_snapshot_handles_empty_state(self):
        """A run with no decisions yet still produces a valid snapshot —
        the worker_states key is just absent (or empty) under state."""
        workers = [{"username": "u1", "opportunity_id": 1}]
        snap = build_snapshot_for_template(
            "performance_review",
            pipelines={},
            state={},
            opportunity_id=1,
            workers=workers,
            opportunity_ids=[1],
        )
        assert snap is not None
        assert snap["workers"] == workers
        assert snap["state"] == {}  # worker_states absent → snapshot.state is empty dict
        assert snap["opportunity_ids"] == [1]

    def test_default_snapshot_when_template_has_no_hook(self, monkeypatch):
        """Templates that opt in via `supports_saved_runs: True` but do NOT
        define a `build_snapshot` callable get a default snapshot of the
        inputs (pipelines + workers + state). Lets a template adopt
        snapshots with a single line and let render JS reconstruct the
        dashboard from frozen pipelines on load.
        """
        fake_template = {
            "key": "fake_minimal_adopter",
            "name": "Fake Minimal Adopter",
            "description": "Test fixture — opts in without a hook",
            "supports_saved_runs": True,
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
            {"key": "fake_single_opp", "supports_saved_runs": True},
        )
        snap = build_snapshot_for_template(
            "fake_single_opp",
            pipelines={},
            state={},
            opportunity_id=42,
        )
        assert snap["opportunity_ids"] == [42]
        assert snap["workers"] == []


class TestDefaultHookSnapshotInputs:
    """Templates that opt in without a `build_snapshot` hook get the framework
    default, which honors a declarative `snapshot_inputs` manifest."""

    def _stub_template(self, monkeypatch, **template_overrides):
        """Register a temporary stub template for default-hook testing."""
        key = "_test_default_hook"
        template = {
            "key": key,
            "name": "Test default hook",
            "description": "stub",
            "supports_saved_runs": True,
            **template_overrides,
        }
        monkeypatch.setitem(TEMPLATES, key, template)
        return key

    def test_default_hook_with_full_pipelines_workers_state(self, monkeypatch):
        """No snapshot_inputs declared — falls back to dump-everything (with
        a logged warning)."""
        key = self._stub_template(monkeypatch)
        snap = build_snapshot_for_template(
            key,
            pipelines={"visits": {"rows": [{"id": 1}]}},
            state={"foo": "bar"},
            opportunity_id=42,
            workers=[{"username": "u"}],
            opportunity_ids=[42],
        )
        assert snap is not None
        assert snap["pipelines"] == {"visits": {"rows": [{"id": 1}]}}
        assert snap["workers"] == [{"username": "u"}]
        assert snap["state"] == {"foo": "bar"}
        assert snap["opportunity_ids"] == [42]

    def test_default_hook_filters_to_declared_pipeline_aliases(self, monkeypatch):
        key = self._stub_template(
            monkeypatch,
            snapshot_inputs={"pipelines": ["visits"]},
        )
        snap = build_snapshot_for_template(
            key,
            pipelines={
                "visits": {"rows": [{"id": 1}]},
                "registrations": {"rows": [{"id": 2}]},  # not declared — should drop
            },
            state={},
            opportunity_id=1,
            workers=[],
            opportunity_ids=[1],
        )
        assert "visits" in snap["pipelines"]
        assert "registrations" not in snap["pipelines"]

    def test_default_hook_filters_state_keys(self, monkeypatch):
        key = self._stub_template(
            monkeypatch,
            snapshot_inputs={"state_keys": ["worker_states"]},
        )
        snap = build_snapshot_for_template(
            key,
            pipelines={},
            state={"worker_states": {"a": {}}, "ui_scratch": {"sort": "name"}},
            opportunity_id=1,
            workers=[],
        )
        assert snap["state"] == {"worker_states": {"a": {}}}

    def test_default_hook_can_omit_workers(self, monkeypatch):
        key = self._stub_template(
            monkeypatch,
            snapshot_inputs={"workers": False},
        )
        snap = build_snapshot_for_template(
            key,
            pipelines={},
            state={},
            opportunity_id=1,
            workers=[{"username": "should_not_be_captured"}],
        )
        assert "workers" not in snap

    def test_default_hook_warns_on_missing_pipeline_alias(self, monkeypatch, caplog):
        """If a declared alias isn't in the live pipelines dict, we log and skip it
        — this is contract drift between the template and its workflow definition."""
        key = self._stub_template(
            monkeypatch,
            snapshot_inputs={"pipelines": ["visits", "registrations"]},
        )
        with caplog.at_level("WARNING"):
            snap = build_snapshot_for_template(
                key,
                pipelines={"visits": {"rows": []}},  # registrations missing
                state={},
                opportunity_id=1,
                workers=[],
            )
        assert "registrations" in caplog.text
        assert "registrations" not in snap["pipelines"]
