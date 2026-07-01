from connect_labs.workflow.templates import build_snapshot_for_contract


def test_build_snapshot_contract_relays_run_id(monkeypatch):
    seen = {}

    def fake_hook(*, pipelines, state, opportunity_id, **context):
        seen.update(context)
        return {"ok": True}

    contract = {"source": "template_hook", "template_key": "x"}
    monkeypatch.setattr(
        "connect_labs.workflow.templates.TEMPLATES",
        {"x": {"build_snapshot": fake_hook}},
    )
    build_snapshot_for_contract(
        contract,
        pipelines={},
        state={},
        opportunity_id=1,
        run_id=999,
        request=None,
    )
    assert seen["run_id"] == 999
