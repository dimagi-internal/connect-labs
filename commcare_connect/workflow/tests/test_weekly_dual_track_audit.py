from unittest import mock

from commcare_connect.workflow.templates.weekly_dual_track_audit import build_track_audit_calls

TRACK_A = {
    "tag": "muac",
    "sample_percentage": 100,
    "reviewer": {
        "agent_id": "muac_overzoom",
        "auto_apply_actions": ["fail_overzoomed"],
    },
}
TRACK_B = {"tag": "rest", "sample_percentage": 10, "reviewer": None}


def test_builds_two_calls_per_opp_with_tags_and_image_audits():
    calls = build_track_audit_calls(
        opportunity_ids=[101, 102],
        opp_names={"101": "Opp A", "102": "Opp B"},
        per_opp={
            "101": {
                "muac_image_paths": ["form.muac"],
                "rest_image_paths": ["form.house", "form.id"],
            },
            "102": {
                "muac_image_paths": ["form.muac"],
                "rest_image_paths": ["form.house"],
            },
        },
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert len(calls) == 4  # 2 opps x 2 tracks

    a = next(c for c in calls if c["opportunities"][0]["id"] == 101 and c["criteria"]["tag"] == "muac")
    assert a["criteria"]["granularity"] == "per_flw"
    assert a["criteria"]["sample_percentage"] == 100
    assert a["criteria"]["audit_type"] == "date_range"
    assert a["criteria"]["start_date"] == "2026-06-22"
    assert a["criteria"]["end_date"] == "2026-06-28"
    # PR #771 model: reviewer rides inside image_audits; no related_fields / ai_agent_id emitted.
    assert "related_fields" not in a["criteria"]
    assert "ai_agent_id" not in a
    assert a["image_audits"] == [
        {
            "image_path": "form.muac",
            "reviewers": [{"agent_id": "muac_overzoom", "auto_apply_actions": ["fail_overzoomed"]}],
        }
    ]
    assert a["context_fields"] is None
    assert a["workflow_run_id"] == 555
    assert a["opportunities"][0]["name"] == "Opp A"

    b = next(c for c in calls if c["opportunities"][0]["id"] == 101 and c["criteria"]["tag"] == "rest")
    assert b["criteria"]["sample_percentage"] == 10
    # Track B has no reviewer: every image_audits entry carries an empty reviewers list.
    assert all(e["reviewers"] == [] for e in b["image_audits"])
    assert {e["image_path"] for e in b["image_audits"]} == {"form.house", "form.id"}


def test_skips_track_with_no_image_paths():
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"], "rest_image_paths": []}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert len(calls) == 1
    assert calls[0]["criteria"]["tag"] == "muac"


def _fake_run(state, definition_id=42):
    run = mock.Mock()
    run.is_completed = False
    run.definition_id = definition_id
    run.data = {"state": state}
    return run


def _fake_definition():
    d = mock.Mock()
    d.data = {
        "opportunity_ids": [101, 102],
        "config": {
            "audit_batch": {
                "track_a": TRACK_A,
                "track_b": TRACK_B,
                "per_opp": {
                    "101": {
                        "muac_image_paths": ["form.muac"],
                        "rest_image_paths": ["form.house"],
                    },
                    "102": {
                        "muac_image_paths": ["form.muac"],
                        "rest_image_paths": ["form.house"],
                    },
                },
                "opp_names": {"101": "Opp A", "102": "Opp B"},
            }
        },
    }
    return d


def test_handler_invokes_run_audit_creation_per_call_and_writes_summary():
    from commcare_connect.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"session_ids": [1, 2, 3]}  # 3 FLWs

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        result = h.weekly_dual_track_audit_create({"run_id": 555, "opportunity_id": 101}, access_token="tok")

    assert rac.apply.call_count == 4  # 2 opps x 2 tracks
    assert result["successful"] == 4
    assert result["sessions_created"] == 12  # 4 calls x 3 sessions
    wda.update_run_state.assert_called_once()
    written = wda.update_run_state.call_args[0][1]
    assert written["last_batch"]["window_start"] == "2026-06-22"
    assert written["last_batch"]["calls"] == 4


def test_template_registered_and_multi_opp():
    from commcare_connect.workflow.templates import get_template

    tpl = get_template("weekly_dual_track_audit")
    assert tpl is not None
    assert tpl["multi_opp"] is True
    assert tpl["definition"]["templateType"] == "weekly_dual_track_audit"
    assert isinstance(tpl["render_code"], str) and "startJob" in tpl["render_code"]


def test_run_audit_creation_accepts_image_audits_contract():
    """Guard the cross-PR boundary: build_track_audit_calls emits image_audits /
    context_fields and the handler forwards them to run_audit_creation. The
    handler test mocks run_audit_creation, so without this non-mocked check a
    signature drift in the audit task (PR #771's per-image-type model) would go
    undetected. See plan Global Constraints + final review."""
    import inspect

    from commcare_connect.audit.tasks import run_audit_creation

    params = inspect.signature(run_audit_creation).parameters
    assert "image_audits" in params
    assert "context_fields" in params
