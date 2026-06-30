from commcare_connect.workflow.templates.audit_par import summarize_run_sessions


class FakeSession:
    def __init__(self, opp, tag, flw, stats, name=None, status="completed", sid=None):
        self.opportunity_id = opp
        self.tag = tag
        self.flw_username = flw
        self.flw_display_name = name
        self.status = status
        self.id = sid
        self._stats = stats

    def get_assessment_stats(self):
        return self._stats


def test_groups_by_tag_and_builds_flw_rows():
    sessions = [
        FakeSession(101, "muac", "flw1", {"pass": 8, "fail": 2, "pending": 0, "ai_no_match": 2}, name="Ana", sid=11),
        FakeSession(101, "rest", "flw1", {"pass": 5, "fail": 0, "pending": 5, "ai_no_match": 0}, name="Ana", sid=12),
        FakeSession(101, "muac", "flw2", {"pass": 4, "fail": 0, "pending": 0, "ai_no_match": 0}, sid=13),
        FakeSession(999, "muac", "flwX", {"pass": 1, "fail": 0, "pending": 0, "ai_no_match": 0}, sid=99),  # other opp
    ]
    out = summarize_run_sessions(sessions, opportunity_id=101)

    assert out["by_tag"]["muac"]["sessions"] == 2
    assert out["by_tag"]["muac"]["pass"] == 12
    assert out["by_tag"]["muac"]["ai_flagged"] == 2
    assert out["by_tag"]["rest"]["pending"] == 5

    rows = {r["flw_id"]: r for r in out["flw_rows"]}
    assert rows["flw1"]["flw_name"] == "Ana"
    assert rows["flw1"]["muac"]["fail"] == 2
    assert rows["flw1"]["rest"]["pending"] == 5
    assert rows["flw2"]["rest"] is None
    assert "flwX" not in rows  # filtered to opp 101

    # session_id deviation: each cell carries the audit session id for deep-linking
    assert rows["flw1"]["muac"]["session_id"] == 11
    assert rows["flw1"]["rest"]["session_id"] == 12
    assert rows["flw2"]["muac"]["session_id"] == 13
