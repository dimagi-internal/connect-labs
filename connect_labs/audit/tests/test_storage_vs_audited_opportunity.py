"""An audit session's two opportunities must stay distinguishable.

``opportunity_id`` is what the audit is ABOUT; ``storage_opportunity_id`` is
where the record is FILED. They are equal for a session created under a single
selected opportunity — which is why the collision went eight months unnoticed
— and diverge under program scope.

Every incident in that series came from code reading one and meaning the other:
#933 (workflow scope), #1012 ("Complete Review" failed with a generic error for
real reviewers), #1037 (54 minutes at 100% CPU, ~700 req/min at production
Connect), #1060/#1074 (23,445 scoped probes in a day). These tests pin the
distinction itself rather than any one of those call sites.
"""

from connect_labs.audit.data_access import _storage_record
from connect_labs.audit.models import AuditSessionRecord


def _session(storage_opp, audited_opp):
    """A record as the API hands it over: storage scope outside, subject inside."""
    return AuditSessionRecord(
        {
            "id": 500,
            "experiment": "audit",
            "type": "AuditSession",
            "data": {"opportunity_id": audited_opp, "title": "t"},
            "username": "auditor",
            "opportunity_id": storage_opp,
            "organization_id": "org",
            "program_id": 9,
            "labs_record_id": None,
        }
    )


def test_the_two_opportunities_do_not_collide():
    s = _session(storage_opp=7200, audited_opp=7199)
    assert s.storage_opportunity_id == 7200, "storage scope must survive construction"
    assert s.opportunity_id == 7199, "the audit subject must remain what the UI reads"


def test_storage_scope_survives_the_base_constructor():
    """LocalLabsRecord.__init__ assigns opportunity_id; that write is the storage
    scope and must not land on the subject."""
    s = _session(storage_opp=7200, audited_opp=7199)
    assert s.data["opportunity_id"] == 7199, "the payload's subject was overwritten"


def test_storage_falls_back_to_subject_when_no_api_envelope():
    """Locally-built records carry only the payload; there the two are the same."""
    s = AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "data": {"opportunity_id": 4242},
            "opportunity_id": None,
        }
    )
    assert s.storage_opportunity_id == 4242


def test_to_api_dict_files_under_storage_not_subject():
    """#1012's failure mode, closed at the serializer: writing back must not move
    the record to the opportunity it merely audits."""
    s = _session(storage_opp=7200, audited_opp=7199)
    payload = s.to_api_dict()
    assert payload["opportunity_id"] == 7200, "write payload would relocate the record"
    assert payload["data"]["opportunity_id"] == 7199, "the subject must ride along in data"


def test_storage_record_view_uses_the_storage_scope():
    """_storage_record is what save_audit_session hands to update_record."""
    s = _session(storage_opp=7200, audited_opp=7199)
    assert _storage_record(s).opportunity_id == 7200
    assert _storage_record(s).data["opportunity_id"] == 7199


def test_equal_opportunities_are_unaffected():
    """The common case — one selected opportunity — must be untouched."""
    s = _session(storage_opp=7200, audited_opp=7200)
    assert s.storage_opportunity_id == s.opportunity_id == 7200
    assert s.to_api_dict()["opportunity_id"] == 7200
