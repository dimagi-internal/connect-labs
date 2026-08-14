"""A workflow run may create MORE THAN ONE audit, and each must actually be created.

``run_audit_creation``'s resume check used to ask only "does ANY session exist
for this workflow_run_id?" — so every audit after the first on a given run was
silently swallowed: nothing created, ``success: True``, and the FIRST audit's
sessions returned as if they belonged to this call. That hit

- the dual-track template, whose Track A and Track B are two invocations
  sharing one run_id (so Track B produced nothing), and
- any long-lived creator run (Muac Picture Audit), where a second
  "Create Audit" click reuses the same run.

Resume itself must still work: re-entering the SAME call must not create a
second copy of its sessions.
"""

from unittest.mock import MagicMock

from connect_labs.audit import tasks
from connect_labs.audit.models import AuditSessionRecord

RUN_ID = 777
OPP = 1973
WINDOW = {"start_date": "2026-06-22", "end_date": "2026-06-28"}


def _existing(session_id, tag, *, start=WINDOW["start_date"], end=WINDOW["end_date"]):
    return AuditSessionRecord(
        {
            "id": session_id,
            "experiment": "audit",
            "type": "AuditSession",
            "labs_record_id": RUN_ID,
            "opportunity_id": OPP,
            "data": {
                "title": f"flwA - {tag}",
                "tag": tag,
                "opportunity_id": OPP,
                "visit_ids": [101],
                "criteria": {"start_date": start, "end_date": end},
            },
        }
    )


def _patch_audit_stack(monkeypatch, created, existing_sessions):
    """One MagicMock AuditDataAccess per opportunity, returning ``existing_sessions``
    from the resume lookup and recording every session creation."""

    def _make(opportunity_id=None, **_kwargs):
        da = MagicMock()
        da.extract_images_for_visits.return_value = {
            "101": [{"blob_id": "a", "name": "a.jpg", "question_id": "form/muac", "username": "flwA"}]
        }
        da.get_flw_names.return_value = {}
        da.get_sessions_by_workflow_run.return_value = existing_sessions

        def _create(**kw):
            created.append(kw)
            return AuditSessionRecord(
                {
                    "id": 900 + len(created),
                    "experiment": "audit",
                    "type": "AuditSession",
                    "opportunity_id": kw.get("opportunity_id"),
                    "data": {"title": kw["title"], "tag": kw["tag"]},
                }
            )

        da.create_audit_session.side_effect = _create
        return da

    monkeypatch.setattr(tasks, "AuditDataAccess", MagicMock(side_effect=_make))
    # Celery result-backend writes need redis; irrelevant to what's under test.
    monkeypatch.setattr(tasks, "set_task_progress", lambda *a, **k: None)
    monkeypatch.setattr(tasks, "_update_job_progress", lambda *a, **k: None)


def _run(tag, **criteria_overrides):
    criteria = {
        "audit_type": "date_range",
        "start_date": WINDOW["start_date"],
        "end_date": WINDOW["end_date"],
        "sample_percentage": 100,
        "granularity": "per_flw",
        "tag": tag,
        **criteria_overrides,
    }
    return tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": OPP, "name": "EHA"}],
            "criteria": criteria,
            "visit_ids": [101],
            "flw_visit_ids": {"flwA": [101]},
            "workflow_run_id": RUN_ID,
        }
    ).result


def test_second_track_on_the_same_run_is_created(monkeypatch):
    """Track B must be created even though Track A already has sessions on this run."""
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing(5, "muac")])

    result = _run("rest")

    assert [c["tag"] for c in created] == ["rest"]
    assert result["success"] is True
    # ...and it reports ITS OWN session, not track A's.
    assert [s["id"] for s in result["sessions"]] == [901]


def test_same_call_re_entered_does_not_duplicate_its_sessions(monkeypatch):
    """Resume still works: re-running the same (opportunity, tag, window)
    picks the existing sessions back up instead of creating a second copy."""
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing(5, "muac")])

    result = _run("muac")

    assert created == []
    assert [s["id"] for s in result["sessions"]] == [5]


def test_same_tag_different_window_is_a_different_audit(monkeypatch):
    """A creator run reused for a later day must create that day's audit —
    tag alone doesn't identify a call."""
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing(5, "muac", start="2026-06-15", end="2026-06-21")])

    _run("muac")

    assert [c["tag"] for c in created] == ["muac"]


def test_direct_runs_without_a_workflow_run_never_resume(monkeypatch):
    """No workflow_run_id means no stable run identity, so the resume check
    must not fire at all (unchanged behaviour)."""
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing(5, "muac")])

    tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": OPP, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                **WINDOW,
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
            },
            "visit_ids": [101],
            "flw_visit_ids": {"flwA": [101]},
        }
    )

    assert [c["tag"] for c in created] == ["muac"]


def test_a_multi_opp_call_recognises_sessions_filed_under_any_of_its_opportunities(monkeypatch):
    """A per_flw call spanning several opportunities files each FLW's session
    under that FLW's OWN opportunity. Re-entering it must recognise all of
    them — keying only off opportunities[0] would duplicate every session
    belonging to the others whenever the first contributed no FLWs."""
    created = []
    other_opp = _existing(7, "muac")
    other_opp.data["opportunity_id"] = 1976  # not opportunities[0]
    _patch_audit_stack(monkeypatch, created, [other_opp])

    result = tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": OPP, "name": "EHA"}, {"id": 1976, "name": "JHF"}],
            "criteria": {
                "audit_type": "date_range",
                **WINDOW,
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
            },
            "visit_ids": [101],
            "flw_visit_ids": {"flwA": [101]},
            "flw_opportunity_ids": {"flwA": 1976},
            "workflow_run_id": RUN_ID,
        }
    ).result

    assert created == []
    assert [s["id"] for s in result["sessions"]] == [7]


def _existing_per_flw(session_id, flw_username, tag="muac"):
    """A per-FLW session, identified by the username on its images (which is
    what AuditSessionRecord.flw_username reads)."""
    session = _existing(session_id, tag)
    session.data["visit_images"] = {"101": [{"blob_id": "a", "username": flw_username}]}
    return session


def test_a_call_killed_part_way_through_creation_creates_only_the_missing_flws(monkeypatch):
    """Per-FLW granularity checkpoints per FLW: a process killed between two of
    a call's FLWs left the rest with no session at all, and "some sessions
    exist" would strand them permanently."""
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing_per_flw(5, "flwA")])
    # Both FLWs are in scope; only flwA has a session.
    monkeypatch.setattr(
        tasks,
        "AuditDataAccess",
        tasks.AuditDataAccess,  # keep the patched factory installed above
    )

    result = tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": OPP, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                **WINDOW,
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "selected_flw_user_ids": ["flwA", "flwB"],
            },
            "visit_ids": [101, 102],
            "flw_visit_ids": {"flwA": [101], "flwB": [102]},
            "workflow_run_id": RUN_ID,
        }
    ).result

    # flwB created, flwA reused rather than duplicated.
    assert [c["title"] for c in created] == ["flwB"]
    assert sorted(s["id"] for s in result["sessions"]) == [5, 901]


def test_a_fully_covered_call_creates_nothing(monkeypatch):
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing_per_flw(5, "flwA"), _existing_per_flw(6, "flwB")])

    result = tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": OPP, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                **WINDOW,
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "selected_flw_user_ids": ["flwA", "flwB"],
            },
            "visit_ids": [101, 102],
            "flw_visit_ids": {"flwA": [101], "flwB": [102]},
            "workflow_run_id": RUN_ID,
        }
    ).result

    assert created == []
    assert sorted(s["id"] for s in result["sessions"]) == [5, 6]


def test_unidentifiable_existing_sessions_fall_back_to_creating_nothing(monkeypatch):
    """flw_username reads the username off a session's first image. When a
    session has none, "which FLWs are still missing?" is a guess — and guessing
    wrong duplicates a real FLW's audit. Create nothing instead."""
    created = []
    _patch_audit_stack(monkeypatch, created, [_existing(5, "muac")])  # no visit_images

    tasks.run_audit_creation.apply(
        kwargs={
            "access_token": "tok",
            "username": "nm1",
            "opportunities": [{"id": OPP, "name": "EHA"}],
            "criteria": {
                "audit_type": "date_range",
                **WINDOW,
                "sample_percentage": 100,
                "granularity": "per_flw",
                "tag": "muac",
                "selected_flw_user_ids": ["flwA", "flwB"],
            },
            "visit_ids": [101, 102],
            "flw_visit_ids": {"flwA": [101], "flwB": [102]},
            "workflow_run_id": RUN_ID,
        }
    )

    assert created == []
