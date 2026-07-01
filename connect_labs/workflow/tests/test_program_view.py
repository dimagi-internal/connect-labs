"""Unit tests for the pure program-view helpers.

No DB, no LabsRecord API — the @django_db workflow view tests error on main due
to a test-DB migration collision (``hq_case_id … already exists``), so all
program-view logic lives in pure functions tested here with lightweight fakes.

Program membership is by EXPLICIT OWNERSHIP: a definition is program-owned iff
``definition.data["config"]["program_id"]`` is set. A multi-opp workflow that is
merely opp-owned is NOT program-owned.
"""

from connect_labs.workflow.program_view import (
    collect_program_workflows,
    is_program_owned,
    opp_owned_definitions,
    owned_by_program,
    program_id_of,
    program_opportunity_ids,
)


class FakeDefinition:
    """Stand-in for WorkflowDefinitionRecord exposing .id, .data and .opportunity_ids.

    ``program_id`` (int/str/None) is placed at ``data.config.program_id``.
    """

    def __init__(self, id, program_id=None, opportunity_ids=None):
        self.id = id
        self.opportunity_ids = opportunity_ids or []
        config = {}
        if program_id is not None:
            config["program_id"] = program_id
        self.data = {"config": config}


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
# program_id_of / is_program_owned
# ---------------------------------------------------------------------------


def test_program_id_of_reads_config_program_id():
    assert program_id_of(FakeDefinition(1, program_id=176)) == 176


def test_program_id_of_none_when_absent():
    assert program_id_of(FakeDefinition(1)) is None


def test_program_id_of_coerces_to_int():
    assert program_id_of(FakeDefinition(1, program_id="176")) == 176


def test_program_id_of_handles_missing_data_attribute():
    class NoAttr:
        pass

    assert program_id_of(NoAttr()) is None


def test_program_id_of_handles_missing_config_key():
    class NoConfig:
        data = {}

    assert program_id_of(NoConfig()) is None


def test_is_program_owned_true_when_marked():
    assert is_program_owned(FakeDefinition(1, program_id=176)) is True


def test_is_program_owned_false_when_unmarked():
    assert is_program_owned(FakeDefinition(1)) is False


# ---------------------------------------------------------------------------
# owned_by_program
# ---------------------------------------------------------------------------


def test_owned_by_program_true_on_match():
    assert owned_by_program(FakeDefinition(1, program_id=176), 176) is True


def test_owned_by_program_true_on_match_int_coercion():
    assert owned_by_program(FakeDefinition(1, program_id="176"), 176) is True
    assert owned_by_program(FakeDefinition(1, program_id=176), "176") is True


def test_owned_by_program_false_on_mismatch():
    assert owned_by_program(FakeDefinition(1, program_id=200), 176) is False


def test_owned_by_program_false_when_unowned():
    assert owned_by_program(FakeDefinition(1), 176) is False


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
# opp_owned_definitions
# ---------------------------------------------------------------------------


def test_opp_owned_definitions_drops_program_owned():
    opp_single = FakeDefinition(1, opportunity_ids=[1973])
    opp_multi = FakeDefinition(2, opportunity_ids=[1973, 1976])  # opp-owned multi-opp
    program_owned = FakeDefinition(3, program_id=176, opportunity_ids=[1973, 1976])

    result = opp_owned_definitions([opp_single, opp_multi, program_owned])

    assert result == [opp_single, opp_multi]


def test_opp_owned_definitions_keeps_opp_owned_multi_opp():
    # An opp-owned multi-opp workflow (no config.program_id) stays in the opp view.
    opp_multi = FakeDefinition(2, opportunity_ids=[1973, 1976])
    assert opp_owned_definitions([opp_multi]) == [opp_multi]


def test_opp_owned_definitions_empty():
    assert opp_owned_definitions([]) == []


# ---------------------------------------------------------------------------
# collect_program_workflows
# ---------------------------------------------------------------------------


def test_collect_program_workflows_keeps_only_owned_dedupes_and_closes():
    # The program-owned def (surfacing via both opps) must be deduped by id.
    # An opp-owned multi-opp def and a def owned by a DIFFERENT program are excluded.
    owned = FakeDefinition(10, program_id=176, opportunity_ids=[1973, 1976])
    opp_owned = FakeDefinition(20, opportunity_ids=[1973])
    other_program = FakeDefinition(30, program_id=200, opportunity_ids=[1976])

    daos = {
        1973: FakeDao([owned, opp_owned]),
        1976: FakeDao([owned, other_program]),  # duplicate owned appearance
    }

    def factory(opp_id):
        return daos[opp_id]

    result = collect_program_workflows(176, [1973, 1976], dao_factory=factory)

    assert [d.id for d in result] == [10]  # only the program-owned def, once
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
        collect_program_workflows(176, [1], dao_factory=lambda oid: dao)
    except RuntimeError:
        pass

    assert dao.closed is True


def test_collect_program_workflows_empty_opp_list():
    calls = []

    def factory(opp_id):  # pragma: no cover - should never be called
        calls.append(opp_id)
        return FakeDao([])

    assert collect_program_workflows(176, [], dao_factory=factory) == []
    assert calls == []
