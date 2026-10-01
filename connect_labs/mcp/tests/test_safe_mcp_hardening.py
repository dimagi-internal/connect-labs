"""Gaps found auditing the /mcp/no_user_visit/ endpoint (Hal, 2026-10-01).

Each test pins one way real data could still reach a restricted caller, or one way a
single person could exhaust connect-labs by profiling.
"""

from unittest.mock import MagicMock

import pytest

from connect_labs.labs.analysis.config import DataSourceConfig
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.provenance import is_generated, mark_generated, replays_real_cases
from connect_labs.mcp import profile_limits, visit_access
from connect_labs.mcp.tests.test_no_user_visit import SAFE_URL, _call
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.users.models import User


def _user(username):
    return User.objects.create(username=username, email=f"{username}@dimagi.com", view_synthetic_opps=True)


@pytest.fixture
def restricted(monkeypatch):
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: True)


@pytest.fixture
def unrestricted(monkeypatch):
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: False)


# ---------------------------------------------------------------------------
# Stored run data is never served to a restricted caller
# ---------------------------------------------------------------------------


def _completed_run(snapshot):
    run = MagicMock()
    run.id = 7
    run.is_completed = True
    run.snapshot = snapshot
    run.data = {"definition_id": 3, "snapshot": snapshot}
    return run


def _snapshot_tool(monkeypatch, run):
    from connect_labs.mcp.tools import workflow_snapshots as ws
    from connect_labs.workflow import snapshot_runtime

    wda = MagicMock()
    wda.get_run.return_value = run
    monkeypatch.setattr(ws, "_wda_for_user", lambda *a, **k: wda)
    monkeypatch.setattr(
        snapshot_runtime,
        "build_snapshot_for_run",
        lambda *a, **k: {"opportunity_id": 10900, "opportunity_ids": [10900], "payload": {"live": True}},
    )
    monkeypatch.setattr(snapshot_runtime, "cache_state", lambda ids: {"cold": False})
    return get_tool("workflow_preview_snapshot")


@pytest.mark.django_db
def test_a_restricted_caller_gets_a_live_build_never_the_stored_snapshot(monkeypatch, restricted):
    tool = _snapshot_tool(monkeypatch, _completed_run({"cases": ["REAL-STORED-CASE"]}))

    out = tool.handler(user=_user("snap-r"), run_id=7, opportunity_id=10900)

    assert out["source"] == "preview"
    assert "REAL-STORED-CASE" not in str(out)


@pytest.mark.django_db
def test_a_full_caller_still_gets_the_stored_snapshot(monkeypatch, unrestricted):
    tool = _snapshot_tool(monkeypatch, _completed_run({"cases": ["STORED"]}))

    out = tool.handler(user=_user("snap-f"), run_id=7, opportunity_id=10900)

    assert out["source"] == "stored"


@pytest.mark.django_db
def test_history_runs_refuses_stored_run_data_to_a_restricted_caller(restricted):
    tool = get_tool("workflow_history_runs")

    with pytest.raises(MCPToolError) as e:
        tool.handler(user=_user("hist-r"), definition_id=3, opportunity_id=10900, include_snapshot=True)

    assert e.value.code == "PERMISSION_DENIED"


@pytest.mark.django_db
def test_live_grading_is_used_for_a_restricted_caller_and_cached_by_scope(monkeypatch, restricted):
    from django.core.cache import cache

    from connect_labs.mcp.tools import workflow_run
    from connect_labs.workflow import agent_sharing, snapshot_runtime

    run = _completed_run({"state": {"graded": "REAL-STORED"}})
    r = workflow_run._Run.__new__(workflow_run._Run)
    r.user, r.run, r.wda, r.opportunity_id, r.program_id = _user("grade-r"), run, MagicMock(), 10900, None
    monkeypatch.setattr(
        snapshot_runtime,
        "build_snapshot_for_run",
        lambda *a, **k: {"payload": {"live": 1}, "opportunity_ids": [10900]},
    )
    monkeypatch.setattr(snapshot_runtime, "cache_state", lambda ids: {})
    monkeypatch.setattr(agent_sharing, "graded_payload", lambda p: p)
    cache.clear()

    out = r.graded()

    assert out["source"] == "live"
    assert cache.get(f"wf-agent-graded:v2:{r.user.pk}:o10900:7:r") is not None


# ---------------------------------------------------------------------------
# pipeline_preview reads only the opportunity's own visits
# ---------------------------------------------------------------------------


def test_an_export_endpoint_cannot_carry_a_path():
    with pytest.raises(ValueError):
        DataSourceConfig(type="connect_export", endpoint="../../opportunity/999/user_visits")
    assert DataSourceConfig(type="connect_export", endpoint="audit_reports").endpoint == "audit_reports"


@pytest.mark.django_db
@pytest.mark.parametrize("source", ["ocs_sessions", "connect_export", "cchq_forms", "cchq_cases"])
def test_a_restricted_preview_refuses_every_data_source_outside_the_opportunity(monkeypatch, restricted, source):
    from connect_labs.mcp.tools import pipelines

    pda = MagicMock()
    pda.get_definition.return_value = MagicMock(data={"schema": {}}, name="p")
    monkeypatch.setattr(pipelines, "PipelineDataAccess", lambda **k: pda)
    monkeypatch.setattr(pipelines, "require_connect_token", lambda user: "t")
    monkeypatch.setattr(pipelines, "_validate_pipeline_schema", lambda schema: None)

    with pytest.raises(MCPToolError) as e:
        get_tool("pipeline_preview").handler(
            user=_user(f"pp-{source}"),
            pipeline_id=1,
            opportunity_id=10900,
            schema_override={"data_source": {"type": source}, "fields": []},
        )

    assert e.value.code == "PERMISSION_DENIED"
    pda.execute_pipeline.assert_not_called()


@pytest.mark.django_db
def test_get_opportunity_apps_refuses_an_id_that_is_not_an_integer(monkeypatch):
    with pytest.raises(MCPToolError) as e:
        get_tool("get_opportunity_apps").handler(user=_user("apps"), opportunity_id="999/attachment_signed_url/?x=")
    assert e.value.code == "INVALID_SCHEMA"


@pytest.mark.django_db(transaction=True)
def test_the_safe_endpoint_holds_arguments_to_the_declared_schema():
    from connect_labs.mcp.models import MCPAccessToken

    _, raw = MCPAccessToken.create_token(_user("schema-r"), name="laptop")

    async def work(mcp_client):
        return await mcp_client.call_tool(
            "get_opportunity_apps", {"opportunity_id": "999/user_visits/?"}, raise_on_error=False
        )

    result = _call(SAFE_URL, raw, work)

    assert result.is_error
    assert "Invalid arguments" in str(result.content)


# ---------------------------------------------------------------------------
# Synthetic: no retired-mirror manifests, no server paths, no wiping shared demos,
# no real targets. Modelled case timelines ARE allowed: they ship sampled cases.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("synthetic_profile_opp", {"source_opportunity_id": 1, "out_dir": "/tmp/bundles"}),
        ("synthetic_generate_opp", {"bundle_dir": "/app/bundles/opp-1"}),
        ("synthetic_generate_opps_bulk", {"bundle_root": "../../etc"}),
        ("synthetic_clone_profile", {"spec_yaml": "opportunity_ids: [1]\nbundle_root: /tmp/x\n"}),
        ("synthetic_env_ensure", {"env": "kmc", "fresh": True}),
    ],
)
def test_restricted_synthetic_calls_that_are_refused(tool, arguments):
    assert visit_access.synthetic_denied_reason(tool, arguments)


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("synthetic_profile_opp", {"source_opportunity_id": 1, "out_dir": "gdrive:"}),
        ("synthetic_profile_opp", {"source_opportunity_id": 1, "out_dir": "gdrive:", "case_timelines": True}),
        ("synthetic_profile_opp", {"source_opportunity_id": 1, "out_dir": "gdrive:", "mirror": True}),
        ("synthetic_generate_opp", {"bundle_dir": "gdrive:abc123"}),
        ("synthetic_clone_profile", {"spec_yaml": "opportunity_ids: [1]\n"}),
        ("synthetic_clone_profile", {"spec_yaml": "opportunity_ids: [1]\ncase_timelines: true\n"}),
        ("synthetic_env_ensure", {"env": "kmc"}),
        ("workflow_get", {"mirror": True}),
    ],
)
def test_restricted_synthetic_calls_that_are_allowed(tool, arguments):
    assert visit_access.synthetic_denied_reason(tool, arguments) is None


def test_a_retired_mirror_manifest_is_recognised_and_a_modelled_one_is_not():
    assert replays_real_cases({"entities": [{"longitudinal": {"mode": "mirror", "transplant_pool": [{}]}}]})
    assert not replays_real_cases({"entities": [{"longitudinal": {"mode": "modelled", "transplant_pool": [{}]}}]})
    assert not replays_real_cases({"entities": [{"longitudinal": {"mode": "synthetic", "transplant_pool": []}}]})


@pytest.mark.django_db
def test_a_retired_mirror_manifest_is_refused_and_a_modelled_one_is_not():
    import yaml

    from connect_labs.labs.synthetic.generator.fixtures.profiler import profile
    from connect_labs.labs.synthetic.generator.fixtures.tests.test_case_timelines_privacy import _app, _source

    modelled = yaml.safe_load(
        profile(
            opportunity_id=10900,
            user_visits=_source(),
            user_data=[],
            opportunity_detail={"name": "KMC"},
            app_structure=_app(),
            case_timelines=True,
            noise_seed=3,
        )
    )
    retired = yaml.safe_load(yaml.safe_dump(modelled))
    retired["beneficiary_cohorts"][0]["longitudinal"]["mode"] = "mirror"

    def reason(manifest):
        return visit_access.synthetic_denied_reason(
            "synthetic_generate_from_manifest", {"opportunity_id": 10900, "manifest_yaml": yaml.safe_dump(manifest)}
        )

    assert reason(retired)
    assert reason(modelled) is None


@pytest.mark.django_db
def test_generating_mirror_data_clears_the_generated_mark(monkeypatch):
    from connect_labs.labs.synthetic.generator.io import uploader

    user = _user("mirror-gen")
    SyntheticOpportunity.objects.create(
        opportunity_id=10950, gdrive_folder_id="old", labs_only=True, enabled=True, created_by=user
    )
    mark_generated(10950, "old")
    monkeypatch.setattr(
        uploader,
        "upload_fixtures",
        lambda **k: uploader.UploadResult(folder_id="new", folder_url="", record_counts={}),
    )
    monkeypatch.setattr(uploader, "invalidate_synthetic_caches", lambda opp: None)
    monkeypatch.setattr(uploader, "resync_visit_count", lambda row, previous_folder_id=None: None)

    uploader.upload_and_register(
        drive=None, opportunity_id=10950, opportunity_name="x", fixtures={}, replays_real_cases=True
    )

    assert not is_generated(SyntheticOpportunity.objects.get(opportunity_id=10950))


@pytest.mark.django_db
def test_a_restricted_caller_cannot_generate_onto_a_real_opportunity(restricted):
    with pytest.raises(MCPToolError) as e:
        get_tool("synthetic_generate_from_manifest").handler(
            user=_user("gen-real"), opportunity_id=874, manifest_yaml="opportunity_id: 874\n"
        )
    assert e.value.code == "PERMISSION_DENIED"


# ---------------------------------------------------------------------------
# Profiling limits
# ---------------------------------------------------------------------------


@pytest.fixture
def jobs_running(monkeypatch):
    from django.core.cache import cache

    cache.clear()
    monkeypatch.setattr(profile_limits, "_state", lambda task_id: "STARTED")


@pytest.mark.django_db
def test_an_identical_profiling_request_gets_the_running_job_back(jobs_running):
    user = _user("dedup")
    args = {"opportunity_id": 874, "out_dir": "gdrive:"}
    assert profile_limits.admit(user, kind="opp", opportunity_ids=[874], args=args, restricted=True) is None
    profile_limits.record(user, "task-1", kind="opp", opportunity_ids=[874], args=args)

    assert profile_limits.admit(user, kind="opp", opportunity_ids=[874], args=args, restricted=True) == "task-1"


@pytest.mark.django_db
def test_jobs_in_flight_are_capped_per_person_and_free_when_they_finish(monkeypatch, jobs_running):
    user = _user("inflight")
    for n in (1, 2):
        profile_limits.record(user, f"task-{n}", kind="opp", opportunity_ids=[n], args={"opportunity_id": n})

    with pytest.raises(MCPToolError) as e:
        profile_limits.admit(user, kind="opp", opportunity_ids=[3], args={"opportunity_id": 3}, restricted=False)
    assert e.value.code == "RATE_LIMITED"
    assert "task-1" in e.value.message

    monkeypatch.setattr(profile_limits, "_state", lambda task_id: "SUCCESS")
    assert (
        profile_limits.admit(user, kind="opp", opportunity_ids=[3], args={"opportunity_id": 3}, restricted=False)
        is None
    )


@pytest.mark.django_db
def test_the_daily_quota_counts_opportunities(settings, jobs_running):
    settings.SYNTHETIC_PROFILE_DAILY_OPPS_PER_USER = 5
    settings.SYNTHETIC_PROFILE_MAX_INFLIGHT_PER_USER = 99
    user = _user("daily")
    profile_limits.record(user, "task-1", kind="bulk", opportunity_ids=[1, 2, 3, 4], args={"ids": [1, 2, 3, 4]})

    with pytest.raises(MCPToolError) as e:
        profile_limits.admit(user, kind="bulk", opportunity_ids=[5, 6], args={"ids": [5, 6]}, restricted=False)
    assert "resets at 00:00 UTC" in e.value.message


@pytest.mark.django_db
def test_a_restricted_bulk_call_is_capped_and_a_full_one_is_not(jobs_running):
    user = _user("percall")
    ids = list(range(1, 12))

    with pytest.raises(MCPToolError):
        profile_limits.admit(user, kind="bulk", opportunity_ids=ids, args={"ids": ids}, restricted=True)
    assert profile_limits.admit(user, kind="bulk", opportunity_ids=ids, args={"ids": ids}, restricted=False) is None
