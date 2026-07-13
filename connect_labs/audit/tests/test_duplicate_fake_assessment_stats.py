"""Regression coverage for the "duplicate_fake" assessment result bucketing.

A colleague's frontend change (PR #901) introduced "Duplicate/Fake" as a
per-image assessment result, replacing "Incomplete". The stored result
value became "duplicate_fake" — but no backend Python code was updated to
recognize it, per that PR's own commit message. get_assessment_stats() and
get_assessment_stats_by_question() therefore silently bucketed
duplicate_fake images as "pending" (not yet assessed) instead of a distinct,
completed-but-flagged bucket — indistinguishable from images nobody has
looked at yet.
"""

from connect_labs.audit.models import AuditSessionRecord


def _make_session(visit_results, pass_threshold=None):
    data = {"visit_results": visit_results}
    if pass_threshold is not None:
        data["pass_threshold"] = pass_threshold
    return AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "data": data,
            "opportunity_id": 1973,
        }
    )


def _assessment(result, question_id="group/muac_photo"):
    return {"result": result, "question_id": question_id}


class TestGetAssessmentStats:
    def test_duplicate_fake_is_its_own_bucket_not_pending(self):
        session = _make_session(
            {
                "v1": {"assessments": {"a": _assessment("pass"), "b": _assessment("duplicate_fake")}},
                "v2": {"assessments": {"c": _assessment("fail"), "d": _assessment(None)}},  # d = genuinely pending
            }
        )
        stats = session.get_assessment_stats()
        assert stats["total"] == 4
        assert stats["pass"] == 1
        assert stats["fail"] == 1
        assert stats["duplicate_fake"] == 1
        assert stats["pending"] == 1  # only the never-assessed one


class TestGetAssessmentStatsByQuestion:
    def test_duplicate_fake_bucketed_separately_per_question(self):
        session = _make_session(
            {
                "v1": {
                    "assessments": {
                        "a": _assessment("pass", "group/muac_photo"),
                        "b": _assessment("duplicate_fake", "group/muac_photo"),
                        "c": _assessment("duplicate_fake", "group/muac_photo"),
                        "d": _assessment("pass", "group/vaccine_card"),
                    }
                },
            }
        )
        by_q = session.get_assessment_stats_by_question()
        assert by_q["group/muac_photo"]["pass"] == 1
        assert by_q["group/muac_photo"]["duplicate_fake"] == 2
        assert by_q["group/muac_photo"]["pending"] == 0
        assert by_q["group/muac_photo"]["total"] == 3
        assert by_q["group/vaccine_card"]["pass"] == 1

    def test_pass_rate_already_treats_duplicate_fake_as_not_passing(self):
        """pass/total is the ratio callers use for threshold checks — verify
        it already excludes duplicate_fake from the numerator without any
        extra bucket math, since pass only increments on an exact "pass"."""
        session = _make_session(
            {
                "v1": {
                    "assessments": {
                        "a": _assessment("pass"),
                        "b": _assessment("duplicate_fake"),
                        "c": _assessment("duplicate_fake"),
                    }
                },
            }
        )
        by_q = session.get_assessment_stats_by_question()
        bucket = by_q["group/muac_photo"]
        pass_rate = bucket["pass"] / bucket["total"]
        assert pass_rate == 1 / 3


class TestToQuestionSummaryDict:
    def test_includes_pass_threshold(self):
        session = _make_session({}, pass_threshold=85)
        summary = session.to_question_summary_dict()
        assert summary["pass_threshold"] == 85

    def test_pass_threshold_defaults_to_100_when_unset(self):
        session = _make_session({})
        summary = session.to_question_summary_dict()
        assert summary["pass_threshold"] == 100
