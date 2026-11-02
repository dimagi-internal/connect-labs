"""The ``labs_audits`` pipeline source's row builder: one row per audited visit.

Every fixture is invented: usernames, ids and blob ids are made up.
"""

from connect_labs.audit.audited_visit_rows import ROW_FIELDS, build_audited_visit_rows
from connect_labs.audit.models import HUMAN_VERDICTS, AuditSessionRecord, human_verdict


def _session(id, visit_results, *, status="completed", completed_at=None, visit_images=None, auditor="auditor_a"):
    data = {"status": status, "visit_results": visit_results, "opportunity_id": 10901}
    if completed_at:
        data["completed_at"] = completed_at
    if visit_images is not None:
        data["visit_images"] = visit_images
    return AuditSessionRecord(
        {
            "id": id,
            "experiment": "audit",
            "type": "AuditSession",
            "opportunity_id": 10901,
            "data": data,
            "username": auditor,
        }
    )


def _a(result=None, question="form/photo_a", ai_result=None):
    out = {"question_id": question, "result": result, "notes": ""}
    if ai_result is not None:
        out["ai_result"] = ai_result
    return out


def _vr(**assessments):
    return {"assessments": assessments}


def _by_visit(rows):
    return {r["visit_id"]: r for r in rows}


def test_counts_human_results_per_visit():
    s = _session(
        1,
        {
            "101": _vr(b1=_a("pass"), b2=_a("fail"), b3=_a("duplicate_fake"), b4=_a(None)),
            "102": _vr(b5=_a("pass")),
        },
        completed_at="2026-09-01T10:00:00Z",
    )
    rows = _by_visit(build_audited_visit_rows([s]))
    r = rows["101"]
    assert (r["n_pass"], r["n_fail"], r["n_duplicate_fake"], r["n_pending"]) == (1, 1, 1, 1)
    assert r["n_decided"] == 3 and r["n_images"] == 4
    assert rows["102"]["n_pass"] == 1 and rows["102"]["n_decided"] == 1
    assert set(r) == set(ROW_FIELDS)


def test_ai_result_is_never_a_decision():
    """An AI match with no human result is pending; an AI no_match never fails a human pass."""
    s = _session(1, {"101": _vr(b1=_a(None, ai_result="match"), b2=_a("pass", ai_result="no_match"))})
    r = build_audited_visit_rows([s])[0]
    assert (r["n_pass"], r["n_fail"], r["n_pending"], r["n_decided"]) == (1, 0, 1, 1)


def test_legacy_duplicate_and_fake_fold_into_duplicate_fake():
    s = _session(1, {"101": _vr(b1=_a("duplicate"), b2=_a("fake"), b3=_a("something_else"))})
    r = build_audited_visit_rows([s])[0]
    assert r["n_duplicate_fake"] == 2 and r["n_pending"] == 1


def test_the_most_recently_completed_session_wins_per_image():
    older = _session(1, {"101": _vr(b1=_a("fail"), b2=_a("pass"))}, completed_at="2026-09-01T10:00:00Z")
    newer = _session(2, {"101": _vr(b1=_a("pass"))}, completed_at="2026-09-05T10:00:00Z")
    for order in ([older, newer], [newer, older]):
        r = build_audited_visit_rows(order)[0]
        # b1 is counted once, as the newer session's pass; b2 keeps the older verdict.
        assert (r["n_images"], r["n_pass"], r["n_fail"]) == (2, 2, 0), order


def test_a_later_completed_at_beats_a_higher_session_id():
    later = _session(1, {"101": _vr(b1=_a("fail"))}, completed_at="2026-09-09T10:00:00Z")
    earlier = _session(2, {"101": _vr(b1=_a("pass"))}, completed_at="2026-09-01T10:00:00Z")
    r = build_audited_visit_rows([earlier, later])[0]
    assert (r["n_pass"], r["n_fail"]) == (0, 1)


def test_pending_in_a_newer_session_never_erases_a_decision():
    decided = _session(1, {"101": _vr(b1=_a("fail"))}, completed_at="2026-09-01T10:00:00Z")
    reopened = _session(2, {"101": _vr(b1=_a(None))}, completed_at="2026-09-05T10:00:00Z")
    r = build_audited_visit_rows([decided, reopened])[0]
    assert (r["n_fail"], r["n_pending"], r["n_images"]) == (1, 0, 1)


def test_in_progress_sessions_count_and_lose_to_completed_ones():
    in_progress = _session(5, {"101": _vr(b1=_a("pass"), b2=_a("fail"))}, status="in_progress")
    completed = _session(1, {"101": _vr(b1=_a("fail"))}, completed_at="2026-09-01T10:00:00Z")
    r = build_audited_visit_rows([in_progress, completed])[0]
    # b1: the completed session's fail outranks the in-progress pass. b2: only the
    # in-progress session decided it, and that decision counts.
    assert (r["n_pass"], r["n_fail"], r["n_images"]) == (0, 2, 2)
    assert r["session_ids"] == "1,5"


def test_a_visit_with_no_assessment_gets_no_row():
    s = _session(
        1,
        {"101": _vr(), "102": {"result": "pass", "notes": ""}},
        visit_images={"103": [{"blob_id": "b9", "username": "flw_x"}]},
    )
    assert build_audited_visit_rows([s]) == []
    assert build_audited_visit_rows([]) == []


def test_username_is_the_visit_maker_not_the_auditor_or_first_worker():
    """A combined session: two workers' visits. Each row names its own visit's worker."""
    images = {
        "101": [{"blob_id": "b1", "username": "flw_one", "visit_date": "2026-08-30T09:15:00", "question_id": "q/a"}],
        "102": [{"blob_id": "b2", "username": "flw_two", "visit_date": "2026-08-31T09:15:00", "question_id": "q/b"}],
    }
    s = _session(
        1,
        {"101": _vr(b1=_a("pass", question=None)), "102": _vr(b2=_a("fail", question=None))},
        visit_images=images,
        auditor="auditor_a",
    )
    assert s.flw_username == "flw_one"  # the session-level field would credit flw_one for both
    rows = _by_visit(build_audited_visit_rows([s]))
    assert rows["101"]["username"] == "flw_one" and rows["102"]["username"] == "flw_two"
    assert rows["101"]["visit_date"] == "2026-08-30" and rows["102"]["visit_date"] == "2026-08-31"
    # question_id falls back to visit_images when the assessment lacks it
    assert rows["101"]["question_ids"] == "q/a"


def test_question_ids_are_distinct_and_sorted():
    s = _session(1, {"101": _vr(b1=_a("pass", "q/z"), b2=_a("pass", "q/a"), b3=_a("fail", "q/z"))})
    assert build_audited_visit_rows([s])[0]["question_ids"] == "q/a,q/z"


def test_rows_are_ordered_by_numeric_visit_id():
    s = _session(1, {"20": _vr(b1=_a("pass")), "3": _vr(b2=_a("pass")), "100": _vr(b3=_a("pass"))})
    assert [r["visit_id"] for r in build_audited_visit_rows([s])] == ["3", "20", "100"]


def test_human_verdict_agrees_with_the_session_stats():
    """tally_assessment now classifies through human_verdict: the per-visit rows and the
    session's own get_assessment_stats must reconcile for a single session."""
    s = _session(
        1,
        {
            "101": _vr(b1=_a("pass"), b2=_a("fail"), b3=_a("duplicate")),
            "102": _vr(b4=_a(None), b5=_a("fake"), b6=_a("pass", ai_result="no_match")),
        },
    )
    stats = s.get_assessment_stats()
    rows = build_audited_visit_rows([s])
    for verdict in HUMAN_VERDICTS:
        assert stats[verdict] == sum(r[f"n_{verdict}"] for r in rows), verdict
    assert human_verdict({}) == "pending"
