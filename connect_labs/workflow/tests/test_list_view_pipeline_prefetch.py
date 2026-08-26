"""The workflow list page must cost a CONSTANT number of Connect calls.

The LabsRecord API lives on production Connect, so every pipeline lookup the
list view makes is a sequential HTTPS round-trip. Resolving pipeline *names*
one id at a time made the page cost ``2 + N`` outbound calls, which is what
users actually felt: 6.6-10.1s for a 10-pipeline user and up to 68s for a
22-pipeline one (2026-08-26 telemetry; 263 loads over 5s across 12 users in a
week).

These tests pin the COST, not just the rendered names — the property that
regressed. A refactor that reintroduces a per-id fetch still renders the right
page and would pass a names-only assertion.
"""

from unittest import mock

from connect_labs.workflow.views import WorkflowListView


def _fake_def(def_id, pipeline_sources):
    return mock.Mock(
        id=def_id, template_type="performance_review", pipeline_sources=pipeline_sources, name=f"W{def_id}"
    )


def _pipeline(pid, name):
    # `name` is reserved by Mock's constructor (it labels the mock), so it has to
    # be assigned after construction to become a real attribute.
    p = mock.Mock(id=pid)
    p.name = name
    return p


def _access(pipelines):
    """A PipelineDataAccess whose per-id fetch is a hard failure if used."""
    access = mock.Mock()
    access.list_definitions.return_value = pipelines
    access.get_definition.side_effect = AssertionError("per-id pipeline fetch — the N+1 is back")
    return access


def test_prefetch_resolves_every_pipeline_in_one_call():
    """20 pipelines across 10 workflows => exactly ONE list call, zero per-id calls."""
    pipelines = [_pipeline(pid, f"Pipeline {pid} name") for pid in range(100, 120)]
    access = _access(pipelines)
    view = WorkflowListView()

    cache = view._prefetch_pipeline_cache(access)
    defs = [_fake_def(i, [{"pipeline_id": 100 + i, "alias": f"a{i}"}]) for i in range(10)]
    rows = [view._build_workflow_row(d, [], access, cache, {}) for d in defs]

    assert access.list_definitions.call_count == 1
    assert access.get_definition.call_count == 0
    # Names came from the prefetch, not the fallback.
    for i, row in enumerate(rows):
        assert row["pipelines"][0]["name"] == f"Pipeline {100 + i} name"


def test_out_of_scope_pipeline_still_falls_back_without_breaking_the_page():
    """A cross-opp/shared pipeline is absent from the in-scope list. It must still
    reach the per-id fetch and, on the 404 that scoping produces, degrade to the
    'Pipeline {id}' label rather than failing the whole list."""
    access = mock.Mock()
    access.list_definitions.return_value = [_pipeline(100, "In scope")]
    access.get_definition.side_effect = Exception("404 — cross-opp scoping")
    view = WorkflowListView()

    cache = view._prefetch_pipeline_cache(access)
    row = view._build_workflow_row(
        _fake_def(1, [{"pipeline_id": 100, "alias": "a"}, {"pipeline_id": 999, "alias": "b"}]),
        [],
        access,
        cache,
        {},
    )

    assert [p["name"] for p in row["pipelines"]] == ["In scope", "Pipeline 999"]
    assert access.get_definition.call_count == 1  # only the out-of-scope one


def test_prefetch_failure_degrades_to_the_old_per_id_behaviour():
    """If the batch call itself fails the page must still render, not 500."""
    access = mock.Mock()
    access.list_definitions.side_effect = Exception("Connect unavailable")
    access.get_definition.return_value = _pipeline(100, "Fetched by id")
    view = WorkflowListView()

    cache = view._prefetch_pipeline_cache(access)

    assert cache == {}
    row = view._build_workflow_row(_fake_def(1, [{"pipeline_id": 100, "alias": "a"}]), [], access, cache, {})
    assert row["pipelines"][0]["name"] == "Fetched by id"
