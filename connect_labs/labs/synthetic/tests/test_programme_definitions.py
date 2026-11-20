"""A synthetic programme keeps its definition, and regenerating it is one action.

The definition lives on the programme (the `synthetic` settings namespace, labs DB);
a first build saves it, a regeneration reads it, and the page button and the MCP tool
run the same job as whoever asks. Templates in cohorts/ are starting points only.
"""

from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse

import connect_labs.mcp.tools.synthetic as synthetic_tools
from connect_labs.labs.synthetic import clone_from_prod, definitions, tasks
from connect_labs.labs.synthetic.clone_from_prod import CloneResult
from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig
from connect_labs.mcp import visit_access
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.scope_config.models import ScopeConfigChange

PROGRAM = 10121


def _result(source, opp_id):
    return CloneResult(
        source_opportunity_id=source,
        opportunity_id=opp_id,
        gdrive_folder_id=None,
        folder_url=None,
        record_counts={},
        app_structure_present=False,
        skipped=False,
    )


# --- the definition and templates -------------------------------------------------


def test_the_kmc_template_is_a_definition_without_a_programme_or_a_folder():
    definition = definitions.template_definition("kmc")
    assert "program_id" not in definition and "bundle_root" not in definition
    spec = definitions.spec_from_definition(definition, program_id=PROGRAM)
    assert spec.program_id == PROGRAM and spec.bundle_root == "gdrive:"
    assert spec.curate and spec.mirror and len(spec.opportunity_ids) == 12
    # Stored as JSON, the showcase answers' positions are strings; they still read as positions.
    config = ImageConfig(**spec.image_config)
    danger = next(c for c in config.showcase if c.name == "KMC Demo — Danger Sign Not Referred")
    assert -1 in danger.answers


def test_an_unknown_template_names_the_ones_there_are():
    with pytest.raises(ValueError, match="kmc"):
        definitions.template_definition("nope")


def test_a_definition_carrying_a_programme_id_is_refused():
    with pytest.raises(ValueError, match="program_id"):
        definitions.spec_from_definition({"opportunity_ids": [1], "program_id": 5})


def test_a_definition_with_a_bad_photo_case_is_refused():
    bad = {"opportunity_ids": [1], "image_config": {"showcase": [{"name": "x", "outcome": "nonsense"}]}}
    with pytest.raises(ValueError, match="image_config"):
        definitions.spec_from_definition(bad)


# --- the job ---------------------------------------------------------------------


@pytest.fixture
def job(monkeypatch, django_user_model):
    seen = {}

    def _profile(spec, *, failures, **kwargs):
        return spec

    def _generate(spec, *, failures, **kwargs):
        seen.update(kwargs)
        seen["program_id_in"] = spec.program_id
        spec.program_id = spec.program_id or 10600
        return spec, [_result(523, 10601)]

    monkeypatch.setattr(clone_from_prod, "profile_cohort", _profile)
    monkeypatch.setattr(clone_from_prod, "generate_cohort", _generate)
    monkeypatch.setattr("connect_labs.labs.synthetic.gdrive.DriveClient", lambda: object())
    monkeypatch.setattr(tasks, "_progress_reporter", lambda task, **k: (lambda *a, **kw: None))
    user = django_user_model.objects.create_user(username="builder", password="x")

    def run(definition, program_id=None):
        return tasks.run_synthetic_programme_generate.run(
            definition=definition, program_id=program_id, oauth_token="t", user_id=user.pk, restricted=False
        )

    return run, seen


@pytest.mark.django_db
def test_a_first_build_makes_new_clones_and_keeps_its_definition_on_the_new_programme(job):
    run, seen = job
    definition = {"opportunity_ids": [523], "program_name": "A build", "case_timelines": True}

    out = run(definition)

    assert seen["program_id_in"] is None and seen["new_opportunities"] is True and seen["fresh"] is False
    assert out["program_id"] == 10600 and out["regenerated"] is False
    layer = definitions.stored(10600)
    assert layer["definition"] == definition
    assert layer["last_generation"]["by"] == "builder"
    assert layer["last_generation"]["kind"] == "first build"
    assert layer["last_generation"]["opportunities"] == [{"source_opportunity_id": 523, "opportunity_id": 10601}]


@pytest.mark.django_db
def test_regenerating_rebuilds_the_programme_in_place_and_leaves_its_definition_alone(job):
    run, seen = job
    definition = {"opportunity_ids": [523], "program_name": "KMC"}
    definitions.record_generation(PROGRAM, {"at": "earlier"}, definition=definition)

    out = run(definitions.definition_for(PROGRAM), program_id=PROGRAM)

    # In place: this programme, each source's clone in it rewritten, none added.
    assert seen["program_id_in"] == PROGRAM and seen["fresh"] is True and seen["new_opportunities"] is False
    assert out["regenerated"] is True
    layer = definitions.stored(PROGRAM)
    assert layer["definition"] == definition
    assert layer["last_generation"]["kind"] == "regenerate"


@pytest.mark.django_db
def test_the_old_clone_job_is_a_first_build_from_a_bare_definition(job):
    """Jobs queued before definitions still run, and their programme can be regenerated."""
    _, seen = job
    out = tasks.run_synthetic_clone_opp.run(
        source_opportunity_ids=[523],
        program_name="Old",
        case_timelines=False,
        oauth_token="t",
        user_id=get_user_model().objects.get(username="builder").pk,
        restricted=False,
    )
    assert seen["new_opportunities"] is True
    assert definitions.definition_for(out["program_id"])["opportunity_ids"] == [523]


# --- the MCP tools ---------------------------------------------------------------


@pytest.fixture
def staff(django_user_model):
    return django_user_model.objects.create_user(username="jo", password="x", email="jo@dimagi.com", is_staff=True)


@pytest.fixture
def queued(monkeypatch):
    calls = []
    cache.clear()
    monkeypatch.setattr(tasks.run_synthetic_programme_generate, "apply_async", lambda **kw: calls.append(kw))
    monkeypatch.setattr(synthetic_tools, "_require_opportunity_access", lambda u, opp: None)
    monkeypatch.setattr(synthetic_tools, "_require_labs_only_program", lambda u, pid: None)
    monkeypatch.setattr(synthetic_tools, "require_connect_token", lambda u: "tok")
    monkeypatch.setattr(visit_access, "caller_restricted", lambda: False)
    return calls


@pytest.mark.django_db
def test_regenerating_a_programme_with_no_definition_says_how_to_give_it_one(staff, queued):
    with pytest.raises(MCPToolError, match="template"):
        get_tool("synthetic_programme_regenerate").handler(user=staff, program_id=PROGRAM)
    assert queued == []


@pytest.mark.django_db
def test_regenerating_from_a_template_saves_it_as_the_person_then_queues_one_job(staff, queued):
    out = get_tool("synthetic_programme_regenerate").handler(user=staff, program_id=PROGRAM, template="kmc")

    (call,) = queued
    assert call["kwargs"]["program_id"] == PROGRAM
    assert call["kwargs"]["definition"] == definitions.template_definition("kmc")
    assert out["status_url"].endswith(f"/labs/synthetic/clone/{out['task_id']}/")
    change = ScopeConfigChange.objects.get(config__namespace="synthetic", config__scope_key=str(PROGRAM))
    assert change.changed_by == "jo" and change.via == "mcp:synthetic_programme_regenerate"


@pytest.mark.django_db
def test_regenerating_reads_the_stored_definition(staff, queued):
    definitions.record_generation(PROGRAM, {"at": "x"}, definition={"opportunity_ids": [523, 874]})
    get_tool("synthetic_programme_regenerate").handler(user=staff, program_id=PROGRAM)
    (call,) = queued
    assert call["kwargs"]["definition"]["opportunity_ids"] == [523, 874]


@pytest.mark.django_db
def test_a_first_build_from_a_template_takes_its_sources(staff, queued):
    get_tool("synthetic_clone_opp").handler(user=staff, template="kmc", program_name="KMC again")
    (call,) = queued
    assert call["kwargs"]["program_id"] is None
    assert len(call["kwargs"]["definition"]["opportunity_ids"]) == 12
    assert call["kwargs"]["definition"]["program_name"] == "KMC again"


@pytest.mark.django_db
def test_anyone_who_can_use_the_programme_can_follow_its_regeneration(staff, queued, django_user_model, monkeypatch):
    import celery.result

    from connect_labs.scope_config import service

    definitions.record_generation(PROGRAM, {"at": "x"}, definition={"opportunity_ids": [523]})
    out = get_tool("synthetic_programme_regenerate").handler(user=staff, program_id=PROGRAM)

    class _Running:
        state, info = "STARTED", None

    monkeypatch.setattr(celery.result, "AsyncResult", lambda task_id, app=None: _Running())
    member = django_user_model.objects.create_user(username="ace", password="x", email="ace@dimagi-ai.com")
    outsider = django_user_model.objects.create_user(username="out", password="x", email="out@example.org")
    monkeypatch.setattr(service, "refusal", lambda caller, scope: None if caller.user == member else "not a member")
    assert get_tool("synthetic_job_status").handler(user=member, task_id=out["task_id"])["state"] == "STARTED"
    with pytest.raises(MCPToolError):
        get_tool("synthetic_job_status").handler(user=outsider, task_id=out["task_id"])


# --- the page --------------------------------------------------------------------


@pytest.mark.django_db
def test_the_list_offers_regenerate_and_the_button_runs_the_tool_as_the_clicker(client, staff):
    definitions.record_generation(
        PROGRAM, {"at": "2026-11-20T05:00:00+00:00", "by": "jo"}, definition={"opportunity_ids": [523]}
    )
    client.force_login(staff)
    with mock.patch("connect_labs.labs.context.get_org_data", return_value={}):
        body = client.get(reverse("labs:synthetic:list")).content.decode()
    assert f'data-testid="regenerate-{PROGRAM}"' in body
    with mock.patch(
        "connect_labs.mcp.tools.synthetic.synthetic_programme_regenerate", return_value={"task_id": "t-9"}
    ) as regenerate:
        response = client.post(reverse("labs:synthetic:programme_regenerate", args=[PROGRAM]))
    assert response["Location"] == reverse("labs:synthetic:clone_status", args=["t-9"])
    (user,), kwargs = regenerate.call_args
    assert user.pk == staff.pk and kwargs == {"program_id": PROGRAM}
