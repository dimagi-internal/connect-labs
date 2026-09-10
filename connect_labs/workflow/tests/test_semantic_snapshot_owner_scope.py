"""The semantic snapshot builder reads the definition by its OWNER.

`opportunity_id` in the builder context is the data anchor (opportunity_ids[0]),
and a by-id record read filters on it whenever it is set. A program-owned report
has no opportunity FK, so reading it through the anchor found nothing: the first
program-owned KMC report (workflow 5626, run 5631) failed every preview and
save with "could not be read" while its opp-owned twin worked. These pin the
scope the builder opens: the program when the run is program-owned, the
opportunity otherwise — and never both.
"""

import pytest

from connect_labs.workflow import snapshot_builders


class _RecordingWDA:
    """Records how it was constructed; finds nothing, so the builder stops right
    after the read — the only line under test."""

    constructed: list[dict] = []

    def __init__(self, **kwargs):
        type(self).constructed.append(kwargs)

    def get_definition(self, definition_id):
        return None

    def close(self):
        pass


@pytest.fixture(autouse=True)
def _wda(monkeypatch):
    _RecordingWDA.constructed = []
    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", _RecordingWDA)
    yield


def _build(**context):
    with pytest.raises(snapshot_builders.SnapshotBuilderError, match="could not be read"):
        snapshot_builders.semantic_snapshot(
            spec={"series": ["C"]},
            pipelines={},
            opportunity_id=10013,
            context={"definition_id": 5626, "access_token": "tok", "opportunity_ids": [10013, 10014], **context},
        )
    assert len(_RecordingWDA.constructed) == 1
    return _RecordingWDA.constructed[0]


def test_a_program_owned_run_reads_the_definition_by_program_only():
    kwargs = _build(program_id=10011)
    assert kwargs.get("program_id") == 10011
    assert kwargs.get("opportunity_id") is None, "the anchor opportunity must not filter an owner read"


def test_an_opportunity_owned_run_reads_the_definition_by_its_opportunity():
    kwargs = _build(program_id=None)
    assert kwargs.get("opportunity_id") == 10013
    assert kwargs.get("program_id") is None
