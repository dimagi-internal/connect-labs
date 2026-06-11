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


class TestResolveSnapshotContract:
    """The workflow definition is the source of truth for the completion
    contract: an instance-owned snapshot_inputs manifest wins over the
    template registry; the registry is a fallback for legacy instances and
    Python-hook templates."""

    KEY = "__tv_contract__"

    def _definition(self, data):
        from commcare_connect.workflow.data_access import WorkflowDefinitionRecord

        return WorkflowDefinitionRecord(
            {
                "id": 1,
                "experiment": "workflow",
                "type": "workflow_definition",
                "opportunity_id": 700,
                "data": data,
            }
        )

    def _register(self, **overrides):
        template = {
            "key": self.KEY,
            "name": "TV Contract",
            "description": "d",
            "definition": {"name": "TV Contract", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "supports_saved_runs": True,
            "snapshot_inputs": {"workers": True, "state_keys": ["template_key_only"]},
        }
        template.update(overrides)
        TEMPLATES[self.KEY] = template

    def teardown_method(self):
        TEMPLATES.pop(self.KEY, None)

    def test_instance_manifest_wins_over_template_manifest(self):
        from commcare_connect.workflow.templates import build_snapshot_for_contract, resolve_snapshot_contract

        self._register()
        definition = self._definition(
            {
                "name": "Anything",
                "config": {"templateType": self.KEY},
                "snapshot_inputs": {"workers": False, "state_keys": ["instance_key"], "pipelines": []},
            }
        )
        contract = resolve_snapshot_contract(definition)
        assert contract["ok"] is True
        assert contract["source"] == "definition"
        assert contract["snapshot_inputs"]["state_keys"] == ["instance_key"]

        snap = build_snapshot_for_contract(
            contract,
            pipelines={"data": {"rows": [1]}},
            state={"instance_key": "kept", "template_key_only": "dropped"},
            opportunity_id=700,
            workers=[{"username": "a"}],
        )
        assert snap["state"] == {"instance_key": "kept"}
        assert "workers" not in snap
        assert snap["pipelines"] == {}

    def test_bespoke_workflow_completes_via_instance_manifest_alone(self):
        from commcare_connect.workflow.templates import build_snapshot_for_contract, resolve_snapshot_contract

        definition = self._definition({"name": "Totally Bespoke", "config": {}, "snapshot_inputs": {}})
        contract = resolve_snapshot_contract(definition)
        assert contract["ok"] is True
        assert contract["source"] == "definition"
        assert contract["template_key"] is None

        snap = build_snapshot_for_contract(
            contract,
            pipelines={"data": {"rows": []}},
            state={"k": 1},
            opportunity_id=700,
            workers=[],
        )
        # Empty manifest = capture everything.
        assert snap["pipelines"] == {"data": {"rows": []}}
        assert snap["state"] == {"k": 1}
        assert snap["workers"] == []

    def test_instance_manifest_overrides_template_hook(self):
        from commcare_connect.workflow.templates import resolve_snapshot_contract

        self._register(build_snapshot=lambda **kwargs: {"hook": True})
        definition = self._definition(
            {"name": "X", "config": {"templateType": self.KEY}, "snapshot_inputs": {"workers": True}}
        )
        contract = resolve_snapshot_contract(definition)
        assert contract["source"] == "definition"

    def test_template_hook_fallback_when_no_instance_manifest(self):
        from commcare_connect.workflow.templates import build_snapshot_for_contract, resolve_snapshot_contract

        self._register(build_snapshot=lambda **kwargs: {"hook": True, "opp": kwargs["opportunity_id"]})
        definition = self._definition({"name": "X", "config": {"templateType": self.KEY}})
        contract = resolve_snapshot_contract(definition)
        assert contract["ok"] is True
        assert contract["source"] == "template_hook"

        snap = build_snapshot_for_contract(contract, pipelines={}, state={}, opportunity_id=42)
        assert snap == {"hook": True, "opp": 42}

    def test_template_inputs_fallback_when_no_instance_manifest(self):
        from commcare_connect.workflow.templates import resolve_snapshot_contract

        self._register()
        definition = self._definition({"name": "X", "config": {"templateType": self.KEY}})
        contract = resolve_snapshot_contract(definition)
        assert contract["ok"] is True
        assert contract["source"] == "template_inputs"
        assert contract["snapshot_inputs"]["state_keys"] == ["template_key_only"]

    def test_name_recovery_marks_recovered_flag(self):
        from commcare_connect.workflow.templates import resolve_snapshot_contract

        self._register()
        definition = self._definition({"name": "TV Contract", "config": {}})
        contract = resolve_snapshot_contract(definition)
        assert contract["ok"] is True
        assert contract["recovered_template_key"] is True
        assert contract["template_key"] == self.KEY

    def test_no_contract_when_nothing_resolves(self):
        from commcare_connect.workflow.templates import resolve_snapshot_contract

        definition = self._definition({"name": "No Match Here", "config": {}})
        contract = resolve_snapshot_contract(definition)
        assert contract == {"ok": False, "error": "no_contract", "template_key": None}

    def test_template_without_saved_runs_support_errors(self):
        from commcare_connect.workflow.templates import resolve_snapshot_contract

        self._register(supports_saved_runs=False)
        definition = self._definition({"name": "X", "config": {"templateType": self.KEY}})
        contract = resolve_snapshot_contract(definition)
        assert contract["ok"] is False
        assert contract["error"] == "template_not_saved_runs"
        assert contract["template_key"] == self.KEY


class TestCreateFromTemplateStampsManifest:
    """create_workflow_from_template stamps the template's snapshot manifest
    onto the new definition so the instance owns its completion contract."""

    KEY = "__tv_stamp__"

    def teardown_method(self):
        TEMPLATES.pop(self.KEY, None)

    def _create(self):
        from unittest.mock import MagicMock

        from commcare_connect.workflow.templates import create_workflow_from_template

        data_access = MagicMock()
        data_access.access_token = None
        create_workflow_from_template(data_access, self.KEY, request=None)
        return data_access.create_definition.call_args.kwargs

    def test_declarative_saved_runs_template_stamps_snapshot_inputs(self):
        TEMPLATES[self.KEY] = {
            "key": self.KEY,
            "name": "TV Stamp",
            "description": "d",
            "definition": {"name": "TV Stamp", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "supports_saved_runs": True,
            "snapshot_inputs": {"workers": True, "state_keys": ["worker_states"]},
        }
        kwargs = self._create()
        assert kwargs["snapshot_inputs"] == {"workers": True, "state_keys": ["worker_states"]}

    def test_hook_template_does_not_stamp(self):
        TEMPLATES[self.KEY] = {
            "key": self.KEY,
            "name": "TV Stamp",
            "description": "d",
            "definition": {"name": "TV Stamp", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
            "supports_saved_runs": True,
            "build_snapshot": lambda **kwargs: {"hook": True},
        }
        kwargs = self._create()
        assert "snapshot_inputs" not in kwargs

    def test_action_shaped_template_does_not_stamp(self):
        TEMPLATES[self.KEY] = {
            "key": self.KEY,
            "name": "TV Stamp",
            "description": "d",
            "definition": {"name": "TV Stamp", "description": "d", "statuses": [], "config": {}},
            "render_code": "function X(){return null}",
        }
        kwargs = self._create()
        assert "snapshot_inputs" not in kwargs
