"""A combined audit session holds images from many FLWs but was labeled with
just the first FLW's name on the run screen. Expose a distinct-FLW count so the
UI can show "All FLWs (N)" for combined sessions instead of one misleading name.
"""
from connect_labs.audit.models import AuditSessionRecord


def _session(visit_images):
    return AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 1,
            "data": {"visit_images": visit_images},
        }
    )


def test_counts_distinct_usernames_across_visits():
    s = _session(
        {
            "v1": [{"blob_id": "a", "username": "u1"}, {"blob_id": "b", "username": "u1"}],
            "v2": [{"blob_id": "c", "username": "u2"}],
        }
    )
    assert s.get_flw_count() == 2


def test_single_flw():
    assert _session({"v1": [{"blob_id": "a", "username": "u1"}]}).get_flw_count() == 1


def test_empty():
    assert _session({}).get_flw_count() == 0


def test_to_summary_dict_includes_flw_count():
    s = _session({"v1": [{"blob_id": "a", "username": "u1"}], "v2": [{"blob_id": "b", "username": "u2"}]})
    assert s.to_summary_dict()["flw_count"] == 2
