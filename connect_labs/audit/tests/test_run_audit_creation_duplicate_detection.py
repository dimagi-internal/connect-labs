# connect_labs/audit/tests/test_run_audit_creation_duplicate_detection.py
"""Wiring test: run_audit_creation invokes run_duplicate_detection with the
right per-session targets when enable_duplicate_detection is set and visit
clusters exist. Mirrors the mocking style of
test_run_audit_creation_visit_clustering.py."""

from unittest.mock import MagicMock

from connect_labs.audit import tasks
from connect_labs.audit.models import AuditSessionRecord


def _fake_data_access(visit_ids, all_visit_images, meta_visits, created_sessions):
    da = MagicMock()
    da.get_visit_ids_for_audit.return_value = visit_ids
    da.extract_images_for_visits.return_value = all_visit_images
    da.get_flw_names.return_value = {}
    da.pipeline.fetch_raw_visits.return_value = meta_visits
    da.get_attachment_signed_url.side_effect = lambda blob_id, opp_id: f"https://signed.example/{blob_id}"

    def fake_create_audit_session(**kwargs):
        created_sessions.append(kwargs)
        return AuditSessionRecord(
            {
                "id": len(created_sessions),
                "experiment": "audit",
                "type": "AuditSession",
                "data": {"title": kwargs["title"], "tag": kwargs["tag"]},
                "opportunity_id": kwargs.get("opportunity_id"),
            }
        )

    da.create_audit_session.side_effect = fake_create_audit_session
    return da


def _criteria(**overrides):
    base = {
        "audit_type": "date_range",
        "start_date": "2026-06-22",
        "end_date": "2026-06-28",
        "sample_percentage": 100,
        "granularity": "per_flw",
        "tag": "muac",
        "enable_time_gap": True,
        "time_gap_minutes": 10,
    }
    base.update(overrides)
    return base


def _run(criteria, fake_da, monkeypatch):
    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(return_value=fake_da))
    return tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": 1973, "name": "ISODAF"}],
            "criteria": criteria,
        }
    ).result


def test_run_duplicate_detection_called_with_session_targets_when_enabled(monkeypatch):
    created_sessions = []
    fake_da = _fake_data_access(
        visit_ids=[111, 112],
        all_visit_images={
            "111": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flw1"}],
            "112": [{"blob_id": "b", "name": "b.jpg", "question_id": "form/muac", "username": "flw1"}],
        },
        meta_visits=[
            {"id": "111", "visit_date": "2026-06-22T10:00:00Z", "location": None},
            {"id": "112", "visit_date": "2026-06-22T10:05:00Z", "location": None},
        ],
        created_sessions=created_sessions,
    )

    fake_run_dd = MagicMock(
        return_value={
            "groupings_checked": 1,
            "groupings_skipped": 0,
            "images_flagged": 0,
            "errors": 0,
            "cancelled": False,
        }
    )
    monkeypatch.setattr(tasks, "run_grouping_duplicate_detection", fake_run_dd, raising=False)

    result = _run(_criteria(enable_duplicate_detection=True), fake_da, monkeypatch)

    assert result["success"] is True
    fake_run_dd.assert_called_once()
    targets = fake_run_dd.call_args.args[0]
    assert len(targets) == 1
    target = targets[0]
    assert target["opp_id"] == 1973
    assert target["clusters"] == [
        {"group_id": "g1", "visit_ids": [111, 112], "image_count": 2, "image_ids": ["a", "b"]}
    ]
    assert target["blob_meta_by_id"] == {
        "a": {"visit_id": 111, "question_id": "form/muac"},
        "b": {"visit_id": 112, "question_id": "form/muac"},
    }
    assert result["visit_cluster_duplicate_detection"]["groupings_checked"] == 1


def test_get_signed_url_passed_to_run_duplicate_detection_resolves_via_data_access(monkeypatch):
    created_sessions = []
    fake_da = _fake_data_access(
        visit_ids=[111, 112],
        all_visit_images={
            "111": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flw1"}],
            "112": [{"blob_id": "b", "name": "b.jpg", "question_id": "form/muac", "username": "flw1"}],
        },
        meta_visits=[
            {"id": "111", "visit_date": "2026-06-22T10:00:00Z", "location": None},
            {"id": "112", "visit_date": "2026-06-22T10:05:00Z", "location": None},
        ],
        created_sessions=created_sessions,
    )

    captured = {}

    def fake_run_dd(targets, *, get_signed_url, **kwargs):
        captured["url"] = get_signed_url("a", 1973)
        return {"groupings_checked": 0, "groupings_skipped": 0, "images_flagged": 0, "errors": 0, "cancelled": False}

    monkeypatch.setattr(tasks, "run_grouping_duplicate_detection", fake_run_dd, raising=False)

    _run(_criteria(enable_duplicate_detection=True), fake_da, monkeypatch)

    assert captured["url"] == "https://signed.example/a"


def test_run_duplicate_detection_not_called_when_flag_is_false(monkeypatch):
    created_sessions = []
    fake_da = _fake_data_access(
        visit_ids=[111],
        all_visit_images={"111": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flw1"}]},
        meta_visits=[],
        created_sessions=created_sessions,
    )
    fake_run_dd = MagicMock()
    monkeypatch.setattr(tasks, "run_grouping_duplicate_detection", fake_run_dd, raising=False)

    result = _run(_criteria(enable_duplicate_detection=False), fake_da, monkeypatch)

    fake_run_dd.assert_not_called()
    assert "visit_cluster_duplicate_detection" not in result


def test_run_duplicate_detection_not_called_when_no_clusters_computed(monkeypatch):
    """enable_duplicate_detection=True but clustering itself is off (no
    enable_time_gap/enable_distance) -- clusters are empty, so there's
    nothing to send and run_duplicate_detection should never be invoked."""
    created_sessions = []
    fake_da = _fake_data_access(
        visit_ids=[111],
        all_visit_images={"111": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flw1"}]},
        meta_visits=[],
        created_sessions=created_sessions,
    )
    fake_run_dd = MagicMock()
    monkeypatch.setattr(tasks, "run_grouping_duplicate_detection", fake_run_dd, raising=False)

    result = _run(
        {
            "audit_type": "date_range",
            "start_date": "2026-06-22",
            "end_date": "2026-06-28",
            "sample_percentage": 100,
            "granularity": "per_flw",
            "tag": "muac",
            "enable_duplicate_detection": True,
            # no enable_time_gap / enable_distance -- clustering_enabled is False
        },
        fake_da,
        monkeypatch,
    )

    fake_run_dd.assert_not_called()
    assert "visit_cluster_duplicate_detection" not in result
