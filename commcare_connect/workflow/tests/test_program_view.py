"""Unit tests for the pure program-view helpers.

No DB, no LabsRecord API — the @django_db workflow view tests error on main due
to a test-DB migration collision (``hq_case_id … already exists``), so all
program-view logic lives in pure functions tested here with lightweight fakes.
"""

from commcare_connect.workflow.program_view import (
    collect_program_workflows,
    is_program_spanning,
    partition_by_span,
    program_opportunity_ids,
)


class FakeDefinition:
    """Stand-in for WorkflowDefinitionRecord exposing .id and .opportunity_ids."""

    def __init__(self, id, opportunity_ids):
        self.id = id
        self.opportunity_ids = opportunity_ids


class FakeDao:
    """Stand-in for a per-opp WorkflowDataAccess with list_definitions/close."""

    def __init__(self, definitions):
        self._definitions = definitions
        self.closed = False

    def list_definitions(self):
        return self._definitions

    def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# is_program_spanning
# ---------------------------------------------------------------------------


def test_is_program_spanning_true_for_multiple_opps():
    assert is_program_spanning(FakeDefinition(1, [1, 2])) is True


def test_is_program_spanning_false_for_single_opp():
    assert is_program_spanning(FakeDefinition(1, [1])) is False


def test_is_program_spanning_false_for_empty():
    assert is_program_spanning(FakeDefinition(1, [])) is False


def test_is_program_spanning_handles_missing_attribute():
    class NoAttr:
        pass

    assert is_program_spanning(NoAttr()) is False


# ---------------------------------------------------------------------------
# partition_by_span
# ---------------------------------------------------------------------------


def test_partition_by_span_splits_mixed_list():
    single_a = FakeDefinition(1, [1973])
    single_b = FakeDefinition(2, [])
    spanning = FakeDefinition(3, [1973, 1976, 1978, 1982])

    singles, spanners = partition_by_span([single_a, single_b, spanning])

    assert singles == [single_a, single_b]
    assert spanners == [spanning]


def test_partition_by_span_empty():
    assert partition_by_span([]) == ([], [])


# ---------------------------------------------------------------------------
# program_opportunity_ids
# ---------------------------------------------------------------------------


def test_program_opportunity_ids_picks_matching_program():
    org_data = {
        "opportunities": [
            {"id": 1973, "program": 176},
            {"id": 1976, "program": 176},
            {"id": 99, "program": 200},  # different program
            {"id": None, "program": 176},  # missing id, skip
            {"program": 176},  # no id key, skip
        ]
    }
    assert program_opportunity_ids(org_data, 176) == [1973, 1976]


def test_program_opportunity_ids_coerces_to_int():
    org_data = {"opportunities": [{"id": "1973", "program": 176}]}
    assert program_opportunity_ids(org_data, 176) == [1973]


def test_program_opportunity_ids_empty_when_no_org_data():
    assert program_opportunity_ids(None, 176) == []
    assert program_opportunity_ids({}, 176) == []
    assert program_opportunity_ids({"opportunities": None}, 176) == []


# ---------------------------------------------------------------------------
# collect_program_workflows
# ---------------------------------------------------------------------------


def test_collect_program_workflows_keeps_only_spanning_dedupes_and_closes():
    # The spanning def (owned by opp 1973) also surfaces when we list opp 1976,
    # so it must be deduped by id. Single-opp defs are excluded.
    spanning = FakeDefinition(10, [1973, 1976])
    single = FakeDefinition(20, [1973])

    daos = {
        1973: FakeDao([spanning, single]),
        1976: FakeDao([spanning]),  # duplicate spanning appearance
    }

    def factory(opp_id):
        return daos[opp_id]

    result = collect_program_workflows([1973, 1976], dao_factory=factory)

    assert [d.id for d in result] == [10]  # only spanning, once
    assert all(dao.closed for dao in daos.values())  # every dao closed


def test_collect_program_workflows_closes_dao_even_on_error():
    class BoomDao:
        def __init__(self):
            self.closed = False

        def list_definitions(self):
            raise RuntimeError("boom")

        def close(self):
            self.closed = True

    dao = BoomDao()

    try:
        collect_program_workflows([1], dao_factory=lambda oid: dao)
    except RuntimeError:
        pass

    assert dao.closed is True


def test_collect_program_workflows_empty_opp_list():
    calls = []

    def factory(opp_id):  # pragma: no cover - should never be called
        calls.append(opp_id)
        return FakeDao([])

    assert collect_program_workflows([], dao_factory=factory) == []
    assert calls == []
