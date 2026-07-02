"""Tests for the `program_audit_creator` template.

The program-level, trackable, saved-runs creator that GENERATES a program's
weekly audits by fanning out to the per-opp `weekly_dual_track_audit` creator
instances, and gates program-week completion on every per-opp audit finishing.

Covers:
- `fan_out_generate` / `run_default` fans out — one `run_default_for_definition`
  per configured per-opp instance, records each result into the PROGRAM run's
  `generation` state, returns `{"per_opp": {...}}`.
- idempotent per window (reuses the program run for the same window).
- `build_snapshot` program-level gate: raises until all per-opp audits complete;
  returns a rollup when all complete. `resolve_snapshot_contract` → template_hook.
- template registered with supports_default_run + supports_saved_runs.
- `audit_par` no longer supports default-run.
"""

from unittest import mock

import pytest


def _program_def(opp_id=9000, def_id=77, instances=None, program_id=None):
    d = mock.Mock()
    d.template_type = "program_audit_creator"
    d.id = def_id
    d.opportunity_id = opp_id
    d.opportunity_ids = [opp_id]
    # Ownership marker: None => legacy opp-owned creator; an int => program-owned
    # (its LabsRecord carries a program FK). mock.Mock would otherwise auto-create
    # a truthy program_id, so set it explicitly.
    d.program_id = program_id
    d.data = {
        "config": {
            "templateType": "program_audit_creator",
            "per_opp_instances": instances
            if instances is not None
            else [
                {"opportunity_id": 1973, "workflow_definition_id": 42},
                {"opportunity_id": 1976, "workflow_definition_id": 43},
            ],
        }
    }
    return d


def _run(run_id, window_start=None):
    r = mock.Mock()
    r.id = run_id
    r.data = {"state": {"window_start": window_start}} if window_start else {"state": {}}
    return r


# ── fan_out_generate / run_default: per-opp fan-out ──────────────────────────


def test_fan_out_generates_per_source_and_records_state(monkeypatch):
    from connect_labs.workflow import templates as templates_pkg
    from connect_labs.workflow.templates import program_audit_creator as m

    state_writes = []

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        creator = mock.Mock()
        creator.id = 40 + opportunity_id
        wda.get_definition.return_value = creator
        wda.update_run_state.side_effect = lambda rid, s: state_writes.append(s)
        return wda

    monkeypatch.setattr(m, "WorkflowDataAccess", make_wda)

    calls = []

    def fake_dispatch(defn, *, access_token, request=None, window=None, **kw):
        calls.append((defn, window))
        return {"run_id": defn.id, "created": True, "sessions_created": 3}

    monkeypatch.setattr(templates_pkg, "run_default_for_definition", fake_dispatch)

    result = m.fan_out_generate(
        definition=_program_def(),
        run_id=700,
        access_token="t",
        window=("2026-06-21", "2026-06-27"),
    )

    assert set(result["per_opp"].keys()) == {1973, 1976}
    assert len(calls) == 2
    assert result["window_start"] == "2026-06-21"
    # per-opp creator got the window forwarded
    assert all(w == ("2026-06-21", "2026-06-27") for _, w in calls)
    # accumulating generation record was written into the PROGRAM run state
    assert state_writes  # at least one write
    last = state_writes[-1]
    assert set(last["generation"].keys()) == {"1973", "1976"}
    assert last["generation"]["1973"]["run_id"] == 40 + 1973
    assert last["generation"]["1976"]["sessions_created"] == 3


def test_run_default_creates_program_run_then_fans_out(monkeypatch):
    from connect_labs.workflow.templates import program_audit_creator as m
    from connect_labs.workflow.templates import run_default_for_definition

    created = {}

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []  # no existing program run

        def _create(def_id, *, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None):
            created["window"] = (period_start, period_end)
            return _run(555)

        wda.create_run.side_effect = _create
        creator = mock.Mock()
        creator.id = 40 + (opportunity_id or 0)
        wda.get_definition.return_value = creator
        return wda

    monkeypatch.setattr(m, "WorkflowDataAccess", make_wda)

    fan = mock.Mock(return_value={"per_opp": {1973: {"run_id": 1}}, "window_start": "x", "window_end": "y"})
    monkeypatch.setattr(m, "fan_out_generate", fan)

    result = run_default_for_definition(_program_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    assert result["per_opp"] == {1973: {"run_id": 1}}
    # created a program run for the window, then fanned out against its id
    assert created["window"] == ("2026-06-21", "2026-06-27")
    assert fan.call_args.kwargs["run_id"] == 555


def test_run_default_is_idempotent_per_window(monkeypatch):
    """A program run already exists for the window → reuse it, don't create a new one."""
    from connect_labs.workflow.templates import program_audit_creator as m
    from connect_labs.workflow.templates import run_default_for_definition

    wda = mock.Mock()
    wda.list_runs.return_value = [_run(500, "2026-06-21")]
    monkeypatch.setattr(m, "WorkflowDataAccess", mock.Mock(return_value=wda))

    fan = mock.Mock(return_value={"per_opp": {}, "window_start": "2026-06-21", "window_end": "2026-06-27"})
    monkeypatch.setattr(m, "fan_out_generate", fan)

    run_default_for_definition(_program_def(), access_token="t", window=("2026-06-21", "2026-06-27"))

    wda.create_run.assert_not_called()  # reused the existing program run
    assert fan.call_args.kwargs["run_id"] == 500


def test_run_default_defaults_window_to_last_week(monkeypatch):
    from connect_labs.workflow.templates import program_audit_creator as m
    from connect_labs.workflow.templates import run_default_for_definition

    captured = {}

    def make_wda(access_token=None, opportunity_id=None, **_):
        wda = mock.Mock()
        wda.list_runs.return_value = []

        def _create(def_id, *, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None):
            captured["window"] = (period_start, period_end)
            return _run(9)

        wda.create_run.side_effect = _create
        return wda

    monkeypatch.setattr(m, "WorkflowDataAccess", make_wda)
    monkeypatch.setattr(m, "fan_out_generate", mock.Mock(return_value={"per_opp": {}}))

    run_default_for_definition(_program_def(), access_token="t")

    ws, we = captured["window"]
    assert ws < we  # a concrete resolved window


# ── program-owned vs opp-owned run scope ─────────────────────────────────────


def test_program_owner_prefers_program_fk_when_program_owned():
    from connect_labs.workflow.templates import program_audit_creator as m

    assert m._program_owner(_program_def(program_id=176)) == ("program", 176)


def test_program_owner_falls_back_to_opp_when_not_program_owned():
    from connect_labs.workflow.templates import program_audit_creator as m

    assert m._program_owner(_program_def(opp_id=9000, program_id=None)) == ("opportunity", 9000)


def test_program_run_owner_kwargs_program_owned():
    from connect_labs.workflow.templates import program_audit_creator as m

    assert m._program_run_owner_kwargs(_program_def(program_id=176)) == {"program_id": 176}


def test_program_run_owner_kwargs_opp_owned():
    from connect_labs.workflow.templates import program_audit_creator as m

    assert m._program_run_owner_kwargs(_program_def(opp_id=9000, program_id=None)) == {"opportunity_id": 9000}


def test_run_default_program_owned_creates_program_scoped_run(monkeypatch):
    """A program-owned creator builds a PROGRAM-scoped DAO and a program run
    (create_run(program_id=...), no owning opportunity)."""
    from connect_labs.workflow.templates import program_audit_creator as m
    from connect_labs.workflow.templates import run_default_for_definition

    dao_kwargs = {}
    create_kwargs = {}

    def make_wda(access_token=None, opportunity_id=None, program_id=None, **_):
        dao_kwargs["opportunity_id"] = opportunity_id
        dao_kwargs["program_id"] = program_id
        wda = mock.Mock()
        wda.list_runs.return_value = []

        def _create(def_id, *, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None):
            create_kwargs["opportunity_id"] = opportunity_id
            create_kwargs["program_id"] = program_id
            return _run(600)

        wda.create_run.side_effect = _create
        return wda

    monkeypatch.setattr(m, "WorkflowDataAccess", make_wda)
    fan = mock.Mock(return_value={"per_opp": {}, "window_start": "x", "window_end": "y"})
    monkeypatch.setattr(m, "fan_out_generate", fan)

    run_default_for_definition(_program_def(program_id=176), access_token="t", window=("2026-06-21", "2026-06-27"))

    # PROGRAM run DAO + create_run are program-scoped, no owning opp.
    assert dao_kwargs == {"opportunity_id": None, "program_id": 176}
    assert create_kwargs == {"opportunity_id": None, "program_id": 176}
    assert fan.call_args.kwargs["run_id"] == 600


def test_run_default_opp_owned_still_creates_opp_scoped_run(monkeypatch):
    """A still-opp-owned creator keeps the legacy opp path."""
    from connect_labs.workflow.templates import program_audit_creator as m
    from connect_labs.workflow.templates import run_default_for_definition

    dao_kwargs = {}
    create_kwargs = {}

    def make_wda(access_token=None, opportunity_id=None, program_id=None, **_):
        dao_kwargs["opportunity_id"] = opportunity_id
        dao_kwargs["program_id"] = program_id
        wda = mock.Mock()
        wda.list_runs.return_value = []

        def _create(def_id, *, opportunity_id=None, program_id=None, period_start, period_end, initial_state=None):
            create_kwargs["opportunity_id"] = opportunity_id
            create_kwargs["program_id"] = program_id
            return _run(601)

        wda.create_run.side_effect = _create
        return wda

    monkeypatch.setattr(m, "WorkflowDataAccess", make_wda)
    monkeypatch.setattr(m, "fan_out_generate", mock.Mock(return_value={"per_opp": {}}))

    run_default_for_definition(
        _program_def(opp_id=9000, program_id=None), access_token="t", window=("2026-06-21", "2026-06-27")
    )

    assert dao_kwargs == {"opportunity_id": 9000, "program_id": None}
    assert create_kwargs == {"opportunity_id": 9000, "program_id": None}


def test_fan_out_writes_program_run_state_program_scoped(monkeypatch):
    """The PROGRAM-run state write is program-scoped for a program-owned creator."""
    from connect_labs.workflow import templates as templates_pkg
    from connect_labs.workflow.templates import program_audit_creator as m

    seen_scopes = []

    def make_wda(access_token=None, opportunity_id=None, program_id=None, **_):
        seen_scopes.append({"opportunity_id": opportunity_id, "program_id": program_id})
        wda = mock.Mock()
        creator = mock.Mock()
        creator.id = 40 + (opportunity_id or 0)
        wda.get_definition.return_value = creator
        wda.update_run_state.side_effect = lambda rid, s: None
        return wda

    monkeypatch.setattr(m, "WorkflowDataAccess", make_wda)
    monkeypatch.setattr(
        templates_pkg,
        "run_default_for_definition",
        lambda defn, *, access_token, request=None, window=None, **kw: {"run_id": 1, "created": True},
    )

    m.fan_out_generate(
        definition=_program_def(program_id=176),
        run_id=700,
        access_token="t",
        window=("2026-06-21", "2026-06-27"),
    )

    # The per-opp creator reads stay opp-scoped; the PROGRAM-run state write is
    # program-scoped (program_id=176, no owning opp).
    assert {"opportunity_id": None, "program_id": 176} in seen_scopes


# ── build_snapshot: program-level completion gate ────────────────────────────


def _sess(status, opp=1973):
    s = mock.Mock()
    s.status = status
    s.opportunity_id = opp
    return s


_GEN_STATE = {
    "generation": {
        "1973": {"opportunity_id": 1973, "workflow_definition_id": 42, "run_id": 501},
        "1976": {"opportunity_id": 1976, "workflow_definition_id": 43, "run_id": 502},
    },
    "window_start": "2026-06-21",
    "window_end": "2026-06-27",
}


def test_build_snapshot_gate_raises_until_all_complete(monkeypatch):
    from connect_labs.workflow.templates import program_audit_creator as m

    sessions_by_run = {
        501: [_sess("completed", 1973), _sess("completed", 1973)],
        502: [_sess("completed", 1976), _sess("in_progress", 1976)],  # 1 open
    }

    def make_ada(request=None, access_token=None, opportunity_id=None, **_):
        ada = mock.Mock()
        opp_run = {1973: 501, 1976: 502}[opportunity_id]
        ada.get_sessions_by_workflow_run.return_value = sessions_by_run[opp_run]
        return ada

    monkeypatch.setattr(m, "AuditDataAccess", make_ada)

    with pytest.raises(ValueError, match="1 of 4 audits still open across the program"):
        m.build_snapshot(pipelines={}, state=_GEN_STATE, opportunity_id=9000, run_id=700, access_token="t")


def test_build_snapshot_returns_rollup_when_all_complete(monkeypatch):
    from connect_labs.workflow.templates import program_audit_creator as m

    def make_ada(request=None, access_token=None, opportunity_id=None, **_):
        ada = mock.Mock()
        ada.get_sessions_by_workflow_run.return_value = [_sess("completed", opportunity_id)]
        return ada

    monkeypatch.setattr(m, "AuditDataAccess", make_ada)

    snap = m.build_snapshot(pipelines={}, state=_GEN_STATE, opportunity_id=9000, run_id=700, access_token="t")

    assert snap["completed_counts"]["open"] == 0
    assert snap["completed_counts"]["total"] == 2
    assert snap["window_start"] == "2026-06-21"
    assert snap["per_opp_completion"]["1973"]["status"] == "completed"
    assert snap["per_opp_completion"]["1976"]["open_audits"] == 0


def test_snapshot_contract_resolves_to_template_hook():
    from connect_labs.workflow.templates import resolve_snapshot_contract

    instance = mock.Mock()
    instance.template_type = "program_audit_creator"
    instance.data = {"name": "Program Audit Creator"}  # no instance snapshot_inputs

    contract = resolve_snapshot_contract(instance)
    assert contract["ok"] is True
    assert contract["source"] == "template_hook"
    assert contract["template_key"] == "program_audit_creator"


# ── registration + audit_par no longer default-runs ──────────────────────────


def test_template_registered_with_flags():
    from connect_labs.workflow.templates import get_template

    tpl = get_template("program_audit_creator")
    assert tpl is not None
    assert tpl["multi_opp"] is True
    assert tpl["supports_saved_runs"] is True
    assert tpl["supports_default_run"] is True
    assert callable(tpl.get("build_snapshot"))
    assert callable(tpl.get("run_default"))
    # gate governs via hook: template must NOT declare snapshot_inputs
    assert "snapshot_inputs" not in tpl
    assert "program_audit_generate" in tpl["render_code"]


def test_audit_par_no_longer_supports_default_run():
    from connect_labs.workflow.templates import get_template, run_default_for_definition

    tpl = get_template("audit_par")
    assert not tpl.get("supports_default_run")
    assert not callable(tpl.get("run_default"))

    d = mock.Mock()
    d.template_type = "audit_par"
    d.id = 88
    d.data = {"config": {}}
    with pytest.raises(ValueError):
        run_default_for_definition(d, access_token="t")
