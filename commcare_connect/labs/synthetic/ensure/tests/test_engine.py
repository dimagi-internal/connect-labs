"""Tests for the ensure engine skeleton: ordered dispatch, context threading,
and ``realized.json`` output. The real per-kind ensurers come in later tasks;
here we monkeypatch the dispatch dict so no backend is needed."""

import json

import pytest

from commcare_connect.labs.synthetic.ensure import engine


def test_walks_resources_in_order_threading_context(tmp_path, monkeypatch):
    calls = []

    def fake(resource, ctx):
        calls.append(resource.kind)
        ctx.ids[resource.kind] = len(calls)
        return {resource.kind: len(calls)}

    monkeypatch.setattr(engine, "ENSURERS", {"opp_data": fake, "rollup": fake})
    env = tmp_path / "e.yaml"
    env.write_text(
        "env: d\n"
        "timeline: {completed_weeks: 1}\n"
        "resources:\n"
        "  - {kind: opp_data, opportunity_id: 1, manifest: x}\n"
        "  - {kind: rollup, opportunity_ids: [1], template: t}\n"
    )
    realized = engine.ensure_synthetic_data(str(env), out=str(tmp_path / "realized.json"))
    assert calls == ["opp_data", "rollup"]
    assert realized["opp_data"] == 1 and realized["rollup"] == 2
    assert json.loads((tmp_path / "realized.json").read_text())["rollup"] == 2


def test_unknown_kind_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(engine, "ENSURERS", {})  # no ensurers registered
    env = tmp_path / "e.yaml"
    env.write_text(
        "env: d\ntimeline: {completed_weeks: 1}\nresources:\n  - {kind: rollup, opportunity_ids: [1], template: t}\n"
    )
    with pytest.raises(KeyError):
        engine.ensure_synthetic_data(str(env))


@pytest.mark.django_db
def test_fresh_wipes_regenerable_records_for_env_opps(tmp_path, monkeypatch):
    """fresh=True deletes runs/flags/audits/tasks for the env's opps, keeps
    scaffolding (definitions/pipelines) and other opps' records untouched."""
    from commcare_connect.labs.synthetic.models import LabsLocalRecord

    # Env opps 10000 + 10001: regenerable records (deleted) + scaffolding (kept).
    for opp in (10000, 10001):
        for t in ("workflow_run", "Flag", "AuditSession", "Task"):
            LabsLocalRecord.objects.create(opportunity_id=opp, experiment="x", type=t, data={})
        LabsLocalRecord.objects.create(opportunity_id=opp, experiment="x", type="workflow_definition", data={})
        LabsLocalRecord.objects.create(opportunity_id=opp, experiment="x", type="pipeline_definition", data={})
    # An unrelated opp must be left completely alone.
    LabsLocalRecord.objects.create(opportunity_id=99999, experiment="x", type="workflow_run", data={})

    monkeypatch.setattr(engine, "ENSURERS", {"weekly_runs": lambda r, c: None, "rollup": lambda r, c: None})
    env = tmp_path / "e.yaml"
    env.write_text(
        "env: d\ntimeline: {completed_weeks: 1}\nresources:\n"
        "  - {kind: weekly_runs, opportunity_ids: [10000, 10001], template: t}\n"
        "  - {kind: rollup, opportunity_ids: [10000], template: t}\n"
    )

    engine.ensure_synthetic_data(str(env), fresh=True)

    # Regenerable types gone for env opps; scaffolding + the unrelated opp survive.
    assert not LabsLocalRecord.objects.filter(
        opportunity_id__in=[10000, 10001], type__in=["workflow_run", "Flag", "AuditSession", "Task"]
    ).exists()
    assert (
        LabsLocalRecord.objects.filter(
            opportunity_id__in=[10000, 10001], type__in=["workflow_definition", "pipeline_definition"]
        ).count()
        == 4
    )
    assert LabsLocalRecord.objects.filter(opportunity_id=99999).count() == 1
