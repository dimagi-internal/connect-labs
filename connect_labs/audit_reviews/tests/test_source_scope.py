"""A followed report that lives in another scope (KC Pulse moved to program 46) is read there."""

from types import SimpleNamespace

import pytest

from connect_labs.audit_reviews import service
from connect_labs.audit_reviews.tests.test_service import CASES, FakeDao, kc_state, source_for

CASE = CASES["go_live_first_run"]


def _dao(**scope):
    return SimpleNamespace(opportunity_id=scope.get("opportunity_id"), program_id=scope.get("program_id"))


def test_no_source_scope_reads_with_the_audit_workflows_own_access():
    assert service.source_scope({}, _dao(opportunity_id=1487)) is None
    # Naming its own opportunity (as 30017 always has) changes nothing.
    assert service.source_scope({"audit_source_opportunity_id": 1487}, _dao(opportunity_id=1487)) is None


def test_a_program_owned_report_is_read_in_its_program():
    cfg = {"audit_source_program_id": 46, "audit_source_opportunity_id": 1487}
    assert service.source_scope(cfg, _dao(opportunity_id=1487)) == {"program_id": 46}
    assert service.source_scope(cfg, _dao(program_id=46)) is None


def test_a_report_in_another_opportunity_is_read_there():
    assert service.source_scope({"audit_source_opportunity_id": 7}, _dao(opportunity_id=1487)) == {"opportunity_id": 7}


@pytest.mark.django_db
def test_sync_lists_the_report_runs_in_its_own_scope_as_the_same_person(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))
    made = []

    class ProgramDao(FakeDao):
        def __init__(self, **kwargs):
            super().__init__(CASE)
            made.append(kwargs)
            self.closed = False

        def close(self):
            self.closed = True
            made.append("closed")

    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", ProgramDao)
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}}, audit_source_program_id=46)

    class PortalDao(FakeDao):
        access_token = "token-of-the-viewer"
        opportunity_id = 10301
        program_id = None

        def list_runs(self, definition_id):
            raise AssertionError("the report's runs are not in the audit workflow's opportunity")

    assert service.sync(source, PortalDao(CASE), d) is True
    assert made == [{"access_token": "token-of-the-viewer", "program_id": 46}, "closed"]
    assert source.source_runs.count() == len(CASE["D"]["runs"])


def test_without_a_sign_in_the_report_cannot_be_read_elsewhere():
    with pytest.raises(service.Refused, match="no sign-in"):
        service._source_dao(SimpleNamespace(), {"program_id": 46})
