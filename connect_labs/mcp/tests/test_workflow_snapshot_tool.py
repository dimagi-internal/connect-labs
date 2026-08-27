"""Tests for workflow_save_snapshot MCP tool."""

from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

# Trigger @register
import connect_labs.mcp.tools.workflow_snapshots  # noqa: F401
from connect_labs.mcp.tool_registry import MCPToolError, get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_workflow_save_snapshot_completes_run(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_snapshots as ws

    fake_run = MagicMock()
    fake_run.id = 100
    fake_run.opportunity_id = 4242
    fake_run.is_completed = False
    fake_run.data = {
        "definition_id": 999,
        "state": {"worker_states": {"asha": "ok"}, "spawned_tasks": {}},
    }

    fake_definition = MagicMock()
    fake_definition.template_type = "performance_review"
    fake_definition.opportunity_id = 4242
    fake_definition.opportunity_ids = []  # falls back to [opportunity_id]
    # Real dict so resolve_snapshot_contract sees no instance manifest and
    # falls back to the (patched) template registry.
    fake_definition.data = {"name": "Performance Review", "config": {"templateType": "performance_review"}}

    fake_completed = MagicMock()

    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run
    fake_wda.get_definition.return_value = fake_definition
    fake_wda.get_cached_pipeline_data.return_value = {"flw_kpis": {"rows": []}}
    fake_wda.get_workers.return_value = [{"username": "asha"}]
    fake_wda.complete_run.return_value = fake_completed

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    # Replace TEMPLATES at the call site so the template lookup succeeds.
    import connect_labs.workflow.templates as templates_mod

    monkeypatch.setitem(
        templates_mod.TEMPLATES,
        "performance_review",
        {"supports_saved_runs": True},
    )
    monkeypatch.setattr(
        templates_mod,
        "build_snapshot_for_contract",
        lambda contract, **kwargs: {
            "metrics": {"workers_reviewed": 1},
            "state": kwargs["state"],
        },
    )

    tool = get_tool("workflow_save_snapshot")
    result = tool.handler(
        user=user,
        run_id=100,
        opportunity_id=4242,
        snapshot_name="Week 1",
        captured_at="2026-02-07T12:00:00Z",
    )

    assert result["run_id"] == 100
    assert result["snapshot_name"] == "Week 1"
    fake_wda.complete_run.assert_called_once()
    call_args = fake_wda.complete_run.call_args
    # First positional should be run_id (100)
    assert call_args.args[0] == 100
    # Second positional should be the snapshot payload
    snapshot = call_args.args[1]
    assert snapshot["name"] == "Week 1"
    assert snapshot["captured_at"] == "2026-02-07T12:00:00Z"
    assert snapshot["metrics"]["workers_reviewed"] == 1
    fake_wda.close.assert_called_once()


@pytest.mark.django_db
def test_workflow_save_snapshot_passes_opportunity_id_to_wda(user, monkeypatch):
    """Confirms the WDA is constructed with opportunity_id — without this scope
    the upstream GET would only return public records and the run would 404."""
    from connect_labs.mcp.tools import workflow_snapshots as ws

    captured_kwargs = {}

    def _fake_wda_factory(u, opportunity_id=None, program_id=None):
        captured_kwargs["opportunity_id"] = opportunity_id
        captured_kwargs["program_id"] = program_id
        fake_wda = MagicMock()
        fake_wda.get_run.return_value = None  # bail out early; we only care about scope
        return fake_wda

    monkeypatch.setattr(ws, "_wda_for_user", _fake_wda_factory)

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError):
        tool.handler(
            user=user,
            run_id=1,
            opportunity_id=7777,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert captured_kwargs["opportunity_id"] == 7777


@pytest.mark.django_db
def test_workflow_save_snapshot_404s_on_missing_run(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_snapshots as ws

    fake_wda = MagicMock()
    fake_wda.get_run.return_value = None

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            run_id=12345,
            opportunity_id=4242,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "NOT_FOUND"


@pytest.mark.django_db
def test_workflow_save_snapshot_409s_on_completed_run(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_snapshots as ws

    fake_run = MagicMock()
    fake_run.is_completed = True
    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            run_id=999,
            opportunity_id=4242,
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "VERSION_CONFLICT"


@pytest.mark.django_db
def test_workflow_save_snapshot_rejects_mismatched_opp(user, monkeypatch):
    """If the run's opportunity_id doesn't match the param, fail loud rather than
    silently snapshotting under the wrong scope."""
    from connect_labs.mcp.tools import workflow_snapshots as ws

    fake_run = MagicMock()
    fake_run.id = 100
    fake_run.opportunity_id = 4242  # the run's actual opp
    fake_run.is_completed = False
    fake_run.data = {"definition_id": 999, "state": {}}

    fake_definition = MagicMock()
    fake_definition.template_type = "performance_review"
    fake_definition.opportunity_id = 4242
    fake_definition.opportunity_ids = []

    fake_wda = MagicMock()
    fake_wda.get_run.return_value = fake_run
    fake_wda.get_definition.return_value = fake_definition

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: fake_wda)
    import connect_labs.workflow.templates as templates_mod

    monkeypatch.setitem(
        templates_mod.TEMPLATES,
        "performance_review",
        {"supports_saved_runs": True},
    )

    tool = get_tool("workflow_save_snapshot")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(
            user=user,
            run_id=100,
            opportunity_id=9999,  # mismatch
            snapshot_name="x",
            captured_at="2026-02-07T12:00:00Z",
        )
    assert exc.value.code == "INVALID_SCHEMA"
    assert "9999" in str(exc.value.message)
    fake_wda.complete_run.assert_not_called()


class _Def:
    def __init__(self, opportunity_id=None, opportunity_ids=None):
        self.opportunity_id = opportunity_id
        self.opportunity_ids = opportunity_ids or []


class _Run:
    def __init__(self, opportunity_id=None):
        self.opportunity_id = opportunity_id


class TestSnapshotOppScopeResolution:
    """#1182: a program-owned multi-opp definition has no singular
    opportunity_id anywhere — its opps live in `opportunity_ids`. Both the MCP
    tool and the runner page resolved only the singular fields and gave up, so
    such a run could be created and never concluded."""

    def _resolve(self, *args, **kwargs):
        from connect_labs.workflow.templates import resolve_snapshot_opp_scope

        return resolve_snapshot_opp_scope(*args, **kwargs)

    def test_a_program_owned_multi_opp_definition_resolves_from_the_list(self):
        primary, effective = self._resolve(_Run(), _Def(opportunity_ids=[1978, 1979]))
        assert primary == 1978, "first entry is the primary-opp convention (WORKFLOW_REFERENCE §8)"
        assert effective == [1978, 1979]

    def test_an_explicit_request_wins(self):
        primary, effective = self._resolve(_Run(1978), _Def(opportunity_ids=[1978, 1979]), 1979)
        assert primary == 1979

    def test_a_plain_opp_scoped_run_is_unchanged(self):
        primary, effective = self._resolve(_Run(1978), _Def(opportunity_id=1978))
        assert (primary, effective) == (1978, [1978])

    def test_the_definition_is_the_fallback_when_the_run_has_no_opp(self):
        primary, effective = self._resolve(_Run(), _Def(opportunity_id=1978))
        assert (primary, effective) == (1978, [1978])

    def test_nothing_anywhere_resolves_to_nothing(self):
        primary, effective = self._resolve(_Run(), _Def())
        assert primary is None and effective == []

    def test_null_entries_in_the_list_are_ignored(self):
        primary, effective = self._resolve(_Run(), _Def(opportunity_ids=[None, 1979]))
        assert (primary, effective) == (1979, [1979])


@pytest.mark.django_db
def test_workflow_save_snapshot_requires_exactly_one_scope(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_snapshots as ws

    """Same contract as workflow_create_run, so a run created program-scoped can
    be concluded the same way rather than through a different door."""
    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: MagicMock())
    tool = get_tool("workflow_save_snapshot")

    for kwargs in ({}, {"opportunity_id": 1, "program_id": 2}):
        with pytest.raises(MCPToolError) as exc:
            tool.handler(user=user, run_id=1, snapshot_name="x", captured_at="2026-02-07T12:00:00Z", **kwargs)
        assert "exactly one" in str(exc.value).lower()


@pytest.mark.django_db
def test_workflow_save_snapshot_accepts_program_scope(user, monkeypatch):
    from connect_labs.mcp.tools import workflow_snapshots as ws

    captured = {}

    def _factory(u, opportunity_id=None, program_id=None):
        captured["opportunity_id"] = opportunity_id
        captured["program_id"] = program_id
        wda = MagicMock()
        wda.get_run.return_value = None  # bail early; we only care about the scope
        return wda

    monkeypatch.setattr(ws, "_wda_for_user", _factory)
    with pytest.raises(MCPToolError):
        get_tool("workflow_save_snapshot").handler(
            user=user, run_id=5048, program_id=176, snapshot_name="x", captured_at="2026-02-07T12:00:00Z"
        )

    assert captured == {"opportunity_id": None, "program_id": 176}


@pytest.mark.django_db
def test_a_program_scoped_run_skips_the_opp_cross_check(user, monkeypatch):
    """The cross-check compares against a singular opportunity_id the caller
    supplied. A program-scoped call has none, and the GET's program filter is
    what authorized the read — so applying it anyway would reject every
    program-owned run."""
    import connect_labs.workflow.templates as templates_mod
    from connect_labs.mcp.tools import workflow_snapshots as ws

    run = MagicMock()
    run.is_completed = False
    run.opportunity_id = None
    run.period_start = None
    run.period_end = None
    run.data = {"definition_id": 99, "state": {}}

    definition = MagicMock()
    definition.opportunity_id = None
    definition.opportunity_ids = [1978, 1979]
    definition.data = {}

    wda = MagicMock()
    wda.get_run.return_value = run
    wda.get_definition.return_value = definition
    wda.get_cached_pipeline_data.return_value = {}
    wda.get_workers.return_value = [{"username": "asha"}]
    wda.complete_run.return_value = MagicMock()

    monkeypatch.setattr(ws, "_wda_for_user", lambda u, opportunity_id=None, program_id=None: wda)
    monkeypatch.setitem(templates_mod.TEMPLATES, "performance_review", {"supports_saved_runs": True})
    monkeypatch.setattr(
        templates_mod,
        "resolve_snapshot_contract",
        lambda d: {"ok": True, "source": "x", "snapshot_inputs": {"pipelines": []}},
    )
    monkeypatch.setattr(
        templates_mod,
        "build_snapshot_for_contract",
        lambda contract, **kw: {"captured": kw["opportunity_id"], "ids": kw["opportunity_ids"]},
    )

    out = get_tool("workflow_save_snapshot").handler(
        user=user, run_id=5048, program_id=176, snapshot_name="wk-1", captured_at="2026-02-07T12:00:00Z"
    )

    assert out["opportunity_id"] == 1978
    assert out["opportunity_ids"] == [1978, 1979]
    # workers fanned out over every member opp, not the (absent) singular one
    assert [c.args[0] for c in wda.get_workers.call_args_list] == [1978, 1979]
