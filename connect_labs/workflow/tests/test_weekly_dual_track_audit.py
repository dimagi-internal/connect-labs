from unittest import mock

import pytest

from connect_labs.workflow.templates.weekly_dual_track_audit import build_track_audit_calls

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
            "reviewers": [
                {"agent_id": "muac_overzoom", "auto_apply_actions": ["fail_overzoomed"]},
                {
                    "agent_id": "muac_match",
                    "config": {
                        "comparison_field": "muac_group/muac_display_group_2/muac_colour_display/soliciter_muac_cm",
                        "label": "MUAC Reading",
                    },
                    "auto_apply_actions": ["fail_unmatched"],
                },
            ],
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


def test_omits_pr884_filters_from_criteria_when_not_provided():
    """Default behavior (no filters passed) must be unchanged — no new keys leak in."""
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"]}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert len(calls) == 1
    for key in ("pass_threshold", "deliver_unit_types", "visit_statuses"):
        assert key not in calls[0]["criteria"]


def test_applies_pr884_filters_identically_to_every_track():
    """pass_threshold / deliver_unit_types / visit_statuses (PR #884) land on every
    track's criteria unchanged, so AuditCriteria.from_dict parses them downstream."""
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={
            "101": {
                "muac_image_paths": ["form.muac"],
                "rest_image_paths": ["form.house"],
            }
        },
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
        pass_threshold=85,
        deliver_unit_types=["CHW Home Visit"],
        visit_statuses=["approved", "pending"],
    )
    assert len(calls) == 2
    for call in calls:
        assert call["criteria"]["pass_threshold"] == 85
        assert call["criteria"]["deliver_unit_types"] == ["CHW Home Visit"]
        assert call["criteria"]["visit_statuses"] == ["approved", "pending"]


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
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1, 2, 3]}  # 3 FLWs

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
    assert written["window_start"] == "2026-06-22"  # window persisted onto the run for the PAR + reload
    assert written["last_batch"]["window_start"] == "2026-06-22"
    assert written["last_batch"]["calls"] == 4


def test_handler_stops_between_calls_when_cancelled():
    """Cancelling (e.g. the user stops a review after selecting too large a
    sample) is keyed by run_workflow_job's own task id (job_config["_task_id"],
    injected by run_workflow_job) -- the same fresh id the browser already has
    and the one run_audit_creation's own .apply()-generated task_id can never
    be, since that's a throwaway id nothing outside this process learns. Once
    flagged, the handler must not start any further opp/track call, leaving
    whatever sessions earlier calls already created untouched."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1, 2, 3]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
        mock.patch.object(h, "is_audit_creation_cancelled") as cancelled,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager
        # Not cancelled before call #1; cancelled by the time call #2 is checked.
        cancelled.side_effect = [False, True, True, True]

        result = h.weekly_dual_track_audit_create(
            {"run_id": 555, "opportunity_id": 101, "_task_id": "outer-task-abc"}, access_token="tok"
        )

    assert rac.apply.call_count == 1
    assert result["successful"] == 1
    assert result["sessions_created"] == 3
    cancelled.assert_called_with("outer-task-abc")
    rac.apply.assert_called_once()
    assert rac.apply.call_args.kwargs["kwargs"]["cancel_key"] == "outer-task-abc"


def test_handler_scopes_data_access_by_program_id_for_program_owned_runs():
    """A program-owned run has no owning opportunity_id — job_config carries
    program_id instead (injected by run_workflow_job). The handler must thread
    it into WorkflowDataAccess, or get_run()/get_definition() 404 against the
    Labs Record API and the batch dies with "run {run_id} not found"."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1, 2, 3]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        result = h.weekly_dual_track_audit_create(
            {"run_id": 555, "opportunity_id": None, "program_id": 176}, access_token="tok"
        )

    WDA.assert_called_once_with(access_token="tok", opportunity_id=None, program_id=176)
    assert result["successful"] == 4


def test_handler_reads_window_from_job_payload_when_state_lacks_it():
    """The render passes the window in the job payload, so audit creation works
    even when the best-effort run-state write flaked (state has no window)."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({})  # no window in run state
    eager = mock.Mock()
    eager.result = {"sessions": [1, 2, 3]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        result = h.weekly_dual_track_audit_create(
            {"run_id": 555, "opportunity_id": 101, "window_start": "2026-06-22", "window_end": "2026-06-28"},
            access_token="tok",
        )

    assert rac.apply.call_count == 4  # window came from the payload; batch ran
    first_criteria = rac.apply.call_args_list[0].kwargs["kwargs"]["criteria"]
    assert first_criteria["start_date"] == "2026-06-22"
    assert first_criteria["end_date"] == "2026-06-28"
    assert result["successful"] == 4


def test_handler_relays_per_flw_progress_for_gliding_bar():
    """The handler passes a progress_callback into run_audit_creation so its
    fine-grained per-FLW progress reaches the caller (tag-prefixed) — that's what
    lets the program-creator row glide (e.g. 'muac · 3/3 field workers') instead of
    stepping once per track."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})

    seen = []

    def cb(msg, processed=0, total=0):
        seen.append((msg, processed, total))

    from connect_labs.audit.tasks import AUDIT_PROGRESS_RELAYS

    def fake_apply(kwargs=None):
        # Simulate run_audit_creation: look up the in-process relay by
        # workflow_run_id (NOT a serialized closure in kwargs) and fire per-FLW ticks.
        assert "progress_callback" not in (kwargs or {})  # never through .apply()
        relay = AUDIT_PROGRESS_RELAYS.get(555)
        if relay:
            relay("Creating audits · 1/3 field workers", processed=1, total=3)
            relay("Creating audits · 3/3 field workers", processed=3, total=3)
        eager = mock.Mock()
        eager.result = {"sessions": [1]}
        return eager

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.side_effect = fake_apply

        h.weekly_dual_track_audit_create(
            {"run_id": 555, "opportunity_id": 101}, access_token="tok", progress_callback=cb
        )

    # Per-FLW ticks reached the caller with processed/total for a gliding bar,
    # tag-prefixed so the row shows which track is generating.
    assert any(p == 1 and t == 3 for _, p, t in seen)
    assert any(p == 3 and t == 3 for _, p, t in seen)
    assert all(" · " in msg and "field workers" in msg for msg, _, _ in seen)
    # The registry is cleaned up after the run (no leaked relays).
    assert AUDIT_PROGRESS_RELAYS.get(555) is None


def test_template_registered_and_multi_opp():
    from connect_labs.workflow.templates import get_template

    tpl = get_template("weekly_dual_track_audit")
    assert tpl is not None
    assert tpl["multi_opp"] is True
    assert tpl["definition"]["templateType"] == "weekly_dual_track_audit"
    assert isinstance(tpl["render_code"], str) and "startJob" in tpl["render_code"]


def test_uses_shared_flw_breakdown_primitive():
    """The FLW breakdown must render via the shared window.LabsAudit primitive,
    not a re-inlined copy — that's what keeps this run, the program creator's
    inline expand, and the pages card identical. See labs_audit_breakdown.js."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "LabsAudit.renderFlwBreakdown" in rc
    # And it should NOT have re-grown its own copy of the grouping helper.
    assert "groupByOppFlw" not in rc


def test_run_audit_creation_accepts_image_audits_contract():
    """Guard the cross-PR boundary: build_track_audit_calls emits image_audits /
    context_fields and the handler forwards them to run_audit_creation. The
    handler test mocks run_audit_creation, so without this non-mocked check a
    signature drift in the audit task (PR #771's per-image-type model) would go
    undetected. See plan Global Constraints + final review."""
    import inspect

    from connect_labs.audit.tasks import run_audit_creation

    params = inspect.signature(run_audit_creation).parameters
    assert "image_audits" in params
    assert "context_fields" in params


def test_handler_applies_per_run_sampling_override():
    """The render can pass MUAC/Other sampling % for a run; the handler overrides
    the pinned config defaults with them before building the audit calls."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create(
            {
                "run_id": 555,
                "opportunity_id": 101,
                "window_start": "2026-06-22",
                "window_end": "2026-06-28",
                "muac_sample_percentage": 50,  # config default is 100
                "other_sample_percentage": 25,  # config default is 10
            },
            access_token="tok",
        )

    by_tag = {}
    for c in rac.apply.call_args_list:
        cr = c.kwargs["kwargs"]["criteria"]
        by_tag[cr["tag"]] = cr["sample_percentage"]
    assert by_tag["muac"] == 50
    assert by_tag["rest"] == 25


def test_handler_applies_pr884_filters_from_job_payload():
    """PR #884 filters chosen on a program/creator Generate screen ride through
    job_config onto every track's run_audit_creation criteria."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create(
            {
                "run_id": 555,
                "opportunity_id": 101,
                "window_start": "2026-06-22",
                "window_end": "2026-06-28",
                "pass_threshold": 85,
                "deliver_unit_types": ["CHW Home Visit"],
                "visit_statuses": ["approved", "pending"],
            },
            access_token="tok",
        )

    for c in rac.apply.call_args_list:
        cr = c.kwargs["kwargs"]["criteria"]
        assert cr["pass_threshold"] == 85
        assert cr["deliver_unit_types"] == ["CHW Home Visit"]
        assert cr["visit_statuses"] == ["approved", "pending"]


def test_handler_falls_back_to_persisted_pr884_filters_when_payload_lacks_them():
    """A re-run without a fresh job payload (e.g. cron) reuses whatever filters
    were last persisted onto run state, mirroring the window fallback."""
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run(
        {
            "window_start": "2026-06-22",
            "window_end": "2026-06-28",
            "pass_threshold": 90,
            "deliver_unit_types": ["Malnutrition Screening"],
            "visit_statuses": ["rejected"],
        }
    )
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create({"run_id": 555, "opportunity_id": 101}, access_token="tok")

    for c in rac.apply.call_args_list:
        cr = c.kwargs["kwargs"]["criteria"]
        assert cr["pass_threshold"] == 90
        assert cr["deliver_unit_types"] == ["Malnutrition Screening"]
        assert cr["visit_statuses"] == ["rejected"]


# ── Task 2: saved-runs completion-gate snapshot hook ─────────────────────────


def _sess(status, tag="muac", stats=None, img=10, fid="flw1"):
    s = mock.Mock()
    s.status = status
    s.tag = tag
    s.image_count = img
    s.id = 1
    s.opportunity_id = 1973
    s.flw_username = fid
    s.flw_display_name = fid
    s.get_assessment_stats.return_value = stats or {
        "pass": 0,
        "fail": 0,
        "pending": 0,
        "ai_no_match": 0,
    }
    return s


def test_build_snapshot_raises_until_all_audits_complete():
    from connect_labs.workflow.templates import weekly_dual_track_audit as m

    ada = mock.Mock()
    ada.get_sessions_by_workflow_run.return_value = [_sess("completed"), _sess("in_progress")]
    with mock.patch.object(m, "AuditDataAccess", return_value=ada):
        with pytest.raises(ValueError, match="1 of 2 audits still open"):
            m.build_snapshot(pipelines={}, state={}, opportunity_id=1973, run_id=55, access_token="t")


def test_build_snapshot_returns_rollup_when_all_complete():
    from connect_labs.workflow.templates import weekly_dual_track_audit as m

    ada = mock.Mock()
    ada.get_sessions_by_workflow_run.return_value = [
        _sess("completed", "muac", {"pass": 8, "fail": 2, "pending": 0, "ai_no_match": 2}, img=10),
        _sess("completed", "rest", {"pass": 5, "fail": 0, "pending": 0, "ai_no_match": 0}, img=5, fid="flw1"),
    ]
    with mock.patch.object(m, "AuditDataAccess", return_value=ada):
        snap = m.build_snapshot(
            pipelines={},
            state={"window_start": "2026-06-21"},
            opportunity_id=1973,
            run_id=55,
            access_token="t",
        )
    assert snap["completed_counts"]["total"] == 2
    assert snap["completed_counts"]["incomplete"] == 0
    assert snap["window_start"] == "2026-06-21"
    assert "flw1" in {r["flw_id"] for r in snap["audit_summary"]["flw_rows"]}


def test_applies_visit_clustering_filters_identically_to_every_track():
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"], "rest_image_paths": ["form.house"]}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
        enable_time_gap=True,
        time_gap_minutes=15,
        enable_distance=True,
        distance_meters=20,
    )
    assert len(calls) == 2
    for call in calls:
        assert call["criteria"]["enable_time_gap"] is True
        assert call["criteria"]["time_gap_minutes"] == 15
        assert call["criteria"]["enable_distance"] is True
        assert call["criteria"]["distance_meters"] == 20


def test_omits_visit_clustering_filters_from_criteria_when_not_provided():
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"]}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    for key in ("enable_time_gap", "time_gap_minutes", "enable_distance", "distance_meters"):
        assert key not in calls[0]["criteria"]


def test_handler_applies_visit_clustering_filters_from_job_payload():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create(
            {
                "run_id": 555,
                "opportunity_id": 101,
                "window_start": "2026-06-22",
                "window_end": "2026-06-28",
                "enable_time_gap": True,
                "time_gap_minutes": 15,
                "enable_distance": False,
                "distance_meters": 20,
            },
            access_token="tok",
        )

    for c in rac.apply.call_args_list:
        cr = c.kwargs["kwargs"]["criteria"]
        assert cr["enable_time_gap"] is True
        assert cr["time_gap_minutes"] == 15
        assert cr["enable_distance"] is False
        assert cr["distance_meters"] == 20


def test_handler_falls_back_to_persisted_visit_clustering_filters_when_payload_lacks_them():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run(
        {
            "window_start": "2026-06-22",
            "window_end": "2026-06-28",
            "enable_time_gap": True,
            "time_gap_minutes": 12,
            "enable_distance": True,
            "distance_meters": 8,
        }
    )
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create({"run_id": 555, "opportunity_id": 101}, access_token="tok")

    for c in rac.apply.call_args_list:
        cr = c.kwargs["kwargs"]["criteria"]
        assert cr["enable_time_gap"] is True
        assert cr["time_gap_minutes"] == 12
        assert cr["enable_distance"] is True
        assert cr["distance_meters"] == 8


def test_definition_pins_visit_clustering_defaults():
    from connect_labs.workflow.templates.weekly_dual_track_audit import DEFINITION

    vc = DEFINITION["config"]["audit_batch"]["visit_clustering"]
    assert vc == {
        "enable_time_gap": False,
        "time_gap_minutes": 10,
        "enable_distance": False,
        "distance_meters": 10,
        "enable_duplicate_detection": False,
    }


def test_render_code_includes_visit_clustering_card():
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "Visit Clustering" in rc
    assert "enable_time_gap" in rc
    assert "enable_distance" in rc


def test_render_code_includes_cancel_button():
    """A large sample's AI review can take a while -- the run needs a way to
    stop it via the generic job-cancel action (cancelJob), not just createAudit's
    audit-specific cancelAudit (which this template never calls)."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "actions.cancelJob(taskId, instance.id)" in rc
    assert "handleCancel" in rc


def test_render_code_clustering_state_prefers_run_state_over_pinned_default():
    """A reopened run must show the clustering params it actually used, not the
    template's pinned default -- so the state init reads runState.enable_time_gap
    etc. before falling back to the DEFINITION's visit_clustering."""
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "runState.enable_time_gap" in rc
    assert "runState.time_gap_minutes" in rc
    assert "runState.enable_distance" in rc
    assert "runState.distance_meters" in rc


def test_render_code_view_only_card_shows_clustering_params_used():
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "Visit clustering:" in rc


def test_definition_pins_track_names_not_reviewers():
    """The reviewer used to be pinned per-track (track_a always got muac_overzoom);
    it's now decided per-path (see _reviewers_for_path), so the DEFINITION carries a
    cosmetic display "name" per track instead of a "reviewer" key."""
    from connect_labs.workflow.templates.weekly_dual_track_audit import DEFINITION

    batch = DEFINITION["config"]["audit_batch"]
    assert batch["track_a"] == {"tag": "muac", "sample_percentage": 100, "name": "MUAC"}
    assert batch["track_b"] == {"tag": "rest", "sample_percentage": 10, "name": "Other"}


def test_render_code_includes_editable_image_type_checkboxes():
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "Track A name" in rc
    assert "Track B name" in rc
    assert "Save configuration" in rc
    assert "audit-batch-config" in rc
    assert "image-questions" in rc


class TestReviewersForPath:
    def test_attaches_both_muac_reviewers_to_any_path_containing_muac_case_insensitive(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import (
            MUAC_MATCH_REVIEWER,
            MUAC_OVERZOOM_REVIEWER,
            MUAC_READING_FIELD,
            _reviewers_for_path,
        )

        assert _reviewers_for_path("form.muac_photo") == [MUAC_OVERZOOM_REVIEWER, MUAC_MATCH_REVIEWER]
        assert MUAC_MATCH_REVIEWER["config"]["comparison_field"] == MUAC_READING_FIELD
        assert _reviewers_for_path("form.MUAC_photo") != []
        assert _reviewers_for_path("muac_group/muac_display_group_2/muac_photo") != []

    def test_no_reviewers_for_paths_without_muac(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import _reviewers_for_path

        assert _reviewers_for_path("form.house") == []
        assert _reviewers_for_path("") == []
        assert _reviewers_for_path(None) == []


def test_reviewer_assignment_is_per_path_not_per_track():
    """A muac-named path pinned to Track B still gets both AI reviewers; a
    non-muac path pinned to Track A gets none — the assignment is purely about
    the path's own name, independent of which track slot it lives in."""
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={
            "101": {
                "muac_image_paths": ["form.house"],
                "rest_image_paths": ["form.muac_photo"],
            }
        },
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )

    a = next(c for c in calls if c["criteria"]["tag"] == "muac")
    assert a["image_audits"] == [{"image_path": "form.house", "reviewers": []}]

    b = next(c for c in calls if c["criteria"]["tag"] == "rest")
    assert b["image_audits"] == [
        {
            "image_path": "form.muac_photo",
            "reviewers": [
                {"agent_id": "muac_overzoom", "auto_apply_actions": ["fail_overzoomed"]},
                {
                    "agent_id": "muac_match",
                    "config": {
                        "comparison_field": "muac_group/muac_display_group_2/muac_colour_display/soliciter_muac_cm",
                        "label": "MUAC Reading",
                    },
                    "auto_apply_actions": ["fail_unmatched"],
                },
            ],
        }
    ]


class TestClassifierGating:
    def test_hyperzoom_and_muac_mismatch_apply_only_to_muac_paths(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import _classifier_applies

        assert _classifier_applies("hyperzoom", "muac_group/muac_photo") is True
        assert _classifier_applies("muac_mismatch", "MUAC_Group/photo") is True
        assert _classifier_applies("hyperzoom", "form/house_photo") is False
        assert _classifier_applies("muac_mismatch", "") is False

    def test_kmc_scale_requires_exact_case_sensitive_path_match(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import _classifier_applies

        assert _classifier_applies("kmc_scale", "anthropometric/upload_weight_image") is True
        assert _classifier_applies("kmc_scale", "Anthropometric/Upload_Weight_Image") is False
        assert _classifier_applies("kmc_scale", "form/house_photo") is False

    def test_unknown_classifier_key_never_applies(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import _classifier_applies

        assert _classifier_applies("not_a_real_classifier", "muac_group/muac_photo") is False

    def test_default_classifiers_for_path_matches_legacy_muac_only_behavior(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import _default_classifiers_for_path

        assert _default_classifiers_for_path("muac_group/muac_photo") == ["hyperzoom", "muac_mismatch"]
        assert _default_classifiers_for_path("form/house_photo") == []
        assert _default_classifiers_for_path(None) == []

    def test_explicit_empty_classifier_list_means_no_reviewers_even_on_a_muac_path(self):
        """A user who explicitly unchecked both boxes on a muac path must get
        NO reviewers -- distinct from a path that was never touched at all
        (which falls back to the legacy default)."""
        from connect_labs.workflow.templates.weekly_dual_track_audit import _reviewers_for_path

        assert _reviewers_for_path("muac_group/muac_photo", {"muac_group/muac_photo": []}) == []

    def test_explicit_selection_of_a_subset_is_honored(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import MUAC_OVERZOOM_REVIEWER, _reviewers_for_path

        assert _reviewers_for_path("muac_group/muac_photo", {"muac_group/muac_photo": ["hyperzoom"]}) == [
            MUAC_OVERZOOM_REVIEWER
        ]

    def test_kmc_scale_selection_only_attaches_on_the_exact_weight_path(self):
        from connect_labs.workflow.templates.weekly_dual_track_audit import KMC_SCALE_REVIEWER, _reviewers_for_path

        assert _reviewers_for_path(
            "anthropometric/upload_weight_image", {"anthropometric/upload_weight_image": ["kmc_scale"]}
        ) == [KMC_SCALE_REVIEWER]
        # Selected but doesn't apply to this path -- server-side gating drops it.
        assert _reviewers_for_path("form/other_photo", {"form/other_photo": ["kmc_scale"]}) == []

    def test_path_absent_from_classifiers_dict_falls_back_to_default(self):
        """A dict that has entries for OTHER paths but not this one still
        falls back per-path -- distinct from a whole-dict None."""
        from connect_labs.workflow.templates.weekly_dual_track_audit import (
            MUAC_MATCH_REVIEWER,
            MUAC_OVERZOOM_REVIEWER,
            _reviewers_for_path,
        )

        assert _reviewers_for_path("muac_group/muac_photo", {"form/other_photo": ["kmc_scale"]}) == [
            MUAC_OVERZOOM_REVIEWER,
            MUAC_MATCH_REVIEWER,
        ]


def test_image_audits_threads_classifiers_per_path():
    from connect_labs.workflow.templates.weekly_dual_track_audit import KMC_SCALE_REVIEWER, _image_audits

    result = _image_audits(
        ["anthropometric/upload_weight_image", "form/house"],
        {"anthropometric/upload_weight_image": ["kmc_scale"]},
    )
    assert result == [
        {"image_path": "anthropometric/upload_weight_image", "reviewers": [KMC_SCALE_REVIEWER]},
        {"image_path": "form/house", "reviewers": []},
    ]


def test_build_track_audit_calls_reads_per_opp_classifiers():
    from connect_labs.workflow.templates.weekly_dual_track_audit import KMC_SCALE_REVIEWER

    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={
            "101": {
                "muac_image_paths": ["anthropometric/upload_weight_image"],
                "rest_image_paths": [],
                "classifiers": {"anthropometric/upload_weight_image": ["kmc_scale"]},
            }
        },
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert calls[0]["image_audits"] == [
        {"image_path": "anthropometric/upload_weight_image", "reviewers": [KMC_SCALE_REVIEWER]}
    ]


def test_build_track_audit_calls_threads_enable_duplicate_detection():
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"]}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
        enable_duplicate_detection=True,
    )
    assert calls[0]["criteria"]["enable_duplicate_detection"] is True


def test_build_track_audit_calls_omits_enable_duplicate_detection_when_not_provided():
    calls = build_track_audit_calls(
        opportunity_ids=[101],
        opp_names={"101": "Opp A"},
        per_opp={"101": {"muac_image_paths": ["form.muac"]}},
        track_a=TRACK_A,
        track_b=TRACK_B,
        window_start="2026-06-22",
        window_end="2026-06-28",
        username="nm1",
        workflow_run_id=555,
    )
    assert "enable_duplicate_detection" not in calls[0]["criteria"]


def test_definition_pins_duplicate_detection_default_off():
    from connect_labs.workflow.templates.weekly_dual_track_audit import DEFINITION

    assert DEFINITION["config"]["audit_batch"]["visit_clustering"]["enable_duplicate_detection"] is False


def test_handler_applies_duplicate_detection_flag_from_job_payload():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create(
            {
                "run_id": 555,
                "opportunity_id": 101,
                "window_start": "2026-06-22",
                "window_end": "2026-06-28",
                "enable_duplicate_detection": True,
            },
            access_token="tok",
        )

    for c in rac.apply.call_args_list:
        assert c.kwargs["kwargs"]["criteria"]["enable_duplicate_detection"] is True


def test_handler_falls_back_to_persisted_duplicate_detection_flag():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run(
        {"window_start": "2026-06-22", "window_end": "2026-06-28", "enable_duplicate_detection": True}
    )
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create({"run_id": 555, "opportunity_id": 101}, access_token="tok")

    for c in rac.apply.call_args_list:
        assert c.kwargs["kwargs"]["criteria"]["enable_duplicate_detection"] is True


def test_handler_persists_duplicate_detection_flag_onto_run_state():
    from connect_labs.workflow.job_handlers import weekly_dual_track_audit as h

    run = _fake_run({"window_start": "2026-06-22", "window_end": "2026-06-28"})
    eager = mock.Mock()
    eager.result = {"sessions": [1]}

    with (
        mock.patch.object(h, "WorkflowDataAccess") as WDA,
        mock.patch.object(h, "run_audit_creation") as rac,
    ):
        wda = WDA.return_value
        wda.get_run.return_value = run
        wda.get_definition.return_value = _fake_definition()
        rac.apply.return_value = eager

        h.weekly_dual_track_audit_create(
            {
                "run_id": 555,
                "opportunity_id": 101,
                "window_start": "2026-06-22",
                "window_end": "2026-06-28",
                "enable_duplicate_detection": True,
            },
            access_token="tok",
        )

    written = wda.update_run_state.call_args[0][1]
    assert written["enable_duplicate_detection"] is True


def test_render_code_includes_per_path_classifier_checkboxes():
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "'hyperzoom'" in rc
    assert "'muac_mismatch'" in rc
    assert "'kmc_scale'" in rc
    assert "AI Classifiers" in rc
    assert "classifiersByOpp" in rc


def test_render_code_includes_duplicate_detection_toggle():
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "enableDuplicateDetection" in rc
    assert "enable_duplicate_detection" in rc
    assert "Duplicate Detection API" in rc


def test_render_code_view_only_summary_mentions_duplicate_detection_when_enabled():
    from connect_labs.workflow.templates import get_template

    rc = get_template("weekly_dual_track_audit")["render_code"]
    assert "Duplicate Detection enabled" in rc
