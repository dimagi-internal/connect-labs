"""`PERIODIC_BUILDERS` is a claim about a builder. This is its proof.

Declaring a builder periodic is what lets `history_rebuild` write a series of
runs against it. The claim is that the builder's payload is a function of
`period_end` -- so a run dated for a past week reports THAT week.

If it ever stops being true, nothing else notices. The rebuild still succeeds,
every period still gets a run, and each one carries today's figures under a
past date. The trend then draws a flat line across real dates, which is
indistinguishable from a programme that genuinely did not move. There is no
error to read and no number that looks wrong.

So the declaration is pinned here rather than trusted: every builder named in
`PERIODIC_BUILDERS` must demonstrably carry its run's period end into the
evaluation, and must refuse to invent one when there is none.
"""

from __future__ import annotations

import pytest

from connect_labs.workflow.snapshot_builders import PERIODIC_BUILDERS, as_of_iso, cut_as_of, semantic_snapshot


class TestTheDeclarationIsHonest:
    def test_every_declared_builder_is_registered(self):
        from connect_labs.workflow.snapshot_builders import BUILDERS

        assert PERIODIC_BUILDERS <= set(
            BUILDERS
        ), "a periodic builder that is not registered can never run; the two lists have drifted"

    def test_semantic_snapshot_is_the_declared_periodic_builder(self):
        # Guards the pairing this whole test module is about: if a second
        # builder is declared periodic, it needs its own pass-through proof
        # below, and this assertion is what makes that impossible to forget.
        assert PERIODIC_BUILDERS == {"semantic_snapshot"}


class TestAsOfIso:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("2026-09-06", "2026-09-06"),
            ("2026-09-06T23:59:59Z", "2026-09-06"),
            (None, None),
            ("", None),
            # Not a date: refused rather than passed through, because the value
            # is spliced into SQL as a DATE literal.
            ("last Tuesday", None),
            ("2026/09/06", None),
        ],
    )
    def test_only_a_real_iso_date_survives(self, value, expected):
        assert as_of_iso(value) == expected


class TestCutAsOf:
    def test_rows_after_the_as_of_are_dropped(self):
        rows = [{"reg_date": "2026-09-01"}, {"reg_date": "2026-09-30"}]
        assert cut_as_of(rows, ("reg_date",), "2026-09-06") == [{"reg_date": "2026-09-01"}]

    def test_a_row_on_the_boundary_is_kept(self):
        rows = [{"reg_date": "2026-09-06"}]
        assert cut_as_of(rows, ("reg_date",), "2026-09-06") == rows

    def test_an_undated_row_is_kept_because_it_is_a_fact_to_show(self):
        rows = [{"reg_date": None}]
        assert cut_as_of(rows, ("reg_date",), "2026-09-06") == rows

    def test_no_as_of_means_no_cut(self):
        rows = [{"reg_date": "2099-01-01"}]
        assert cut_as_of(rows, ("reg_date",), None) == rows


# ---------------------------------------------------------------------------
# The pass-through itself: run.period_end -> the evaluation's as_of.
# ---------------------------------------------------------------------------


class _Definition:
    id = 1
    data: dict = {}
    template_type = None
    opportunity_id = 10
    opportunity_ids = [10]
    name = "t"
    registry_source = None
    pipeline_sources = []


class _DAO:
    def __init__(self, *a, **kw):
        pass

    def get_definition(self, definition_id):
        return _Definition()

    def close(self):
        pass


@pytest.fixture
def captured(monkeypatch):
    """Wire semantic_snapshot to stubs, capturing what evaluate() is asked for."""
    from connect_labs.semantic import runtime
    from connect_labs.semantic import snapshot as snap
    from connect_labs.semantic import workflow_binding
    from connect_labs.workflow import data_access as wf_data_access

    seen: dict = {}

    monkeypatch.setattr(wf_data_access, "WorkflowDataAccess", _DAO)
    monkeypatch.setattr(wf_data_access, "PipelineDataAccess", _DAO)
    monkeypatch.setattr(wf_data_access, "SemanticRegistryDataAccess", _DAO)
    monkeypatch.setattr(workflow_binding, "build_evaluate_inputs", lambda d, f: ({}, {}))
    monkeypatch.setattr(
        workflow_binding, "resolve_registry_for", lambda d, registry_access_factory=None: ({}, {}, {}, {}, {}, "x")
    )

    def fake_evaluate(*args, **kw):
        seen["as_of"] = kw.get("as_of")
        return []

    monkeypatch.setattr(runtime, "evaluate", fake_evaluate)
    monkeypatch.setattr(runtime, "filter_to_series", lambda reg, s: reg)
    monkeypatch.setattr(runtime, "measure_catalog", lambda reg: {})
    monkeypatch.setattr(snap, "case_rows", lambda pipelines, spec, llo_map: [])

    def fake_build(*, as_of=None, meta=None, **kw):
        seen["payload_as_of"] = as_of
        seen["meta_as_of"] = (meta or {}).get("as_of")
        seen["meta_registry"] = (meta or {}).get("registry")
        return {"ok": True}

    monkeypatch.setattr(snap, "build", fake_build)
    return seen


def _run_builder(period_end):
    return semantic_snapshot(
        spec={"series": "C", "scopes": ["programme"], "state_key": "snapshot"},
        pipelines={},
        opportunity_id=10,
        context={"definition_id": 1, "opportunity_ids": [10], "period_end": period_end},
    )


@pytest.mark.django_db
class TestSemanticSnapshotIsAFunctionOfPeriodEnd:
    def test_the_runs_period_end_becomes_the_evaluations_as_of(self, captured):
        _run_builder("2026-09-06")
        assert captured["as_of"] == "DATE '2026-09-06'"

    def test_a_different_period_end_evaluates_at_a_different_date(self, captured):
        # The claim, stated as the difference it must make. Two periods, two
        # as-ofs -- this is the single assertion that separates a real trend
        # from a flat line drawn over real dates.
        _run_builder("2026-08-30")
        first = captured["as_of"]
        _run_builder("2026-09-06")
        assert first == "DATE '2026-08-30'"
        assert captured["as_of"] == "DATE '2026-09-06'"
        assert first != captured["as_of"]

    def test_a_datetime_period_end_is_reduced_to_its_date(self, captured):
        _run_builder("2026-09-06T18:30:00Z")
        assert captured["as_of"] == "DATE '2026-09-06'"

    def test_no_period_end_evaluates_as_of_today_rather_than_inventing_a_date(self, captured):
        _run_builder(None)
        assert captured["as_of"] == "CURRENT_DATE"

    def test_the_as_of_is_published_on_the_payload_and_its_meta(self, captured):
        # The render reads `meta.as_of` to date each trend point. A payload
        # whose figures were cut at one date but labelled with another would
        # plot correct numbers in the wrong place.
        _run_builder("2026-09-06")
        assert captured["payload_as_of"] == "2026-09-06"
        assert captured["meta_as_of"] == "2026-09-06"

    def test_an_unparseable_period_end_falls_back_rather_than_reaching_sql(self, captured):
        # as_of is spliced into SQL as a literal, so anything that is not a
        # date must not get that far.
        _run_builder("not a date")
        assert captured["as_of"] == "CURRENT_DATE"


@pytest.mark.django_db
def test_every_snapshot_says_which_registry_graded_it(captured):
    # A rebuilt history restates each point under the definitions in force when it
    # ran; unbound means the on-disk registry. The point has to carry which.
    _run_builder("2026-09-06")
    assert captured["meta_registry"] == {"source": "disk", "name": "kmc"}
