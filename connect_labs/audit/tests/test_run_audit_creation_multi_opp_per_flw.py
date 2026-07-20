"""Regression test: a program-owned per_flw batch spanning multiple opportunities
must scope each FLW's images/session to ITS OWN opportunity, not to
opportunities[0]. Before this fix, every session in a multi-opp batch was
created via a single opp_id-scoped AuditDataAccess pinned to the first
selected opportunity -- FLWs belonging to any other opportunity got sessions
with no images (extract_images_for_visits' CommCare fetch only covers one
opportunity) and were physically stored under the wrong opportunity's scope."""
from unittest.mock import MagicMock

from connect_labs.audit import tasks
from connect_labs.audit.models import AuditSessionRecord


def _fake_data_access_factory(opp_visit_images, created_sessions):
    """Returns one MagicMock AuditDataAccess per distinct opportunity_id it's
    constructed with, so the test can assert each FLW's session/images came
    from ITS OWN opportunity-scoped instance rather than always the first."""
    instances = {}

    def _make(opportunity_id=None, **_kwargs):
        if opportunity_id not in instances:
            da = MagicMock()
            da.extract_images_for_visits.return_value = opp_visit_images.get(opportunity_id, {})
            da.get_flw_names.return_value = {}

            def fake_create_audit_session(_opp=opportunity_id, **kw):
                kw["_constructed_for_opp"] = _opp
                created_sessions.append(kw)
                return AuditSessionRecord(
                    {
                        "id": len(created_sessions),
                        "experiment": "audit",
                        "type": "AuditSession",
                        "data": {"title": kw["title"], "tag": kw["tag"]},
                        "opportunity_id": kw.get("opportunity_id"),
                    }
                )

            da.create_audit_session.side_effect = fake_create_audit_session
            instances[opportunity_id] = da
        return instances[opportunity_id]

    return _make


def test_multi_opp_per_flw_scopes_each_session_to_its_own_opportunity(monkeypatch):
    created_sessions = []
    factory = _fake_data_access_factory(
        opp_visit_images={
            1973: {"101": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flwA"}]},
            1976: {"201": [{"blob_id": "b", "name": "b.jpg", "question_id": "form/muac", "username": "flwB"}]},
        },
        created_sessions=created_sessions,
    )
    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(side_effect=factory))

    result = tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "EHA"}, {"id": 1976, "name": "JHF"}],
            "criteria": {
                "audit_type": "date_range",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "selected_flw_user_ids": ["flwA", "flwB"],
            },
            "visit_ids": [101, 201],
            "flw_visit_ids": {"flwA": [101], "flwB": [201]},
            "flw_opportunity_ids": {"flwA": 1973, "flwB": 1976},
        }
    ).result

    assert result["success"] is True
    assert len(created_sessions) == 2

    by_opp = {s["opportunity_id"]: s for s in created_sessions}
    assert set(by_opp) == {1973, 1976}

    # Each session was created via the AuditDataAccess instance scoped to ITS
    # real opportunity, not always opportunities[0] (1973).
    assert by_opp[1973]["_constructed_for_opp"] == 1973
    assert by_opp[1976]["_constructed_for_opp"] == 1976

    # Each FLW's images came from its OWN opportunity's extraction call. Under
    # the bug, opp 1976's images would be silently empty (fetched via a single
    # call scoped to opp 1973 alone).
    assert by_opp[1973]["visit_images"] == {
        "101": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flwA"}]
    }
    assert by_opp[1976]["visit_images"] == {
        "201": [{"blob_id": "b", "name": "b.jpg", "question_id": "form/muac", "username": "flwB"}]
    }

    # Opportunity name resolved per-session from the real opportunity, not opportunities[0].
    assert by_opp[1973]["opportunity_name"] == "EHA"
    assert by_opp[1976]["opportunity_name"] == "JHF"


def test_single_opp_per_flw_unaffected_by_multi_opp_path(monkeypatch):
    """Legacy/single-opportunity callers (no flw_opportunity_ids) must keep
    using the single combined data_access exactly as before."""
    created_sessions = []
    factory = _fake_data_access_factory(
        opp_visit_images={
            1973: {"101": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flwA"}]}
        },
        created_sessions=created_sessions,
    )
    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(side_effect=factory))

    result = tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                "start_date": "2026-06-22",
                "end_date": "2026-06-28",
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "selected_flw_user_ids": ["flwA"],
            },
            "visit_ids": [101],
            "flw_visit_ids": {"flwA": [101]},
        }
    ).result

    assert result["success"] is True
    assert len(created_sessions) == 1
    assert created_sessions[0]["opportunity_id"] == 1973
    assert created_sessions[0]["_constructed_for_opp"] == 1973
