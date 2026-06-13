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
