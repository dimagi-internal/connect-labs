"""``?include=images`` on the sessions-summary endpoints: every image, in one request.

A report that compares the AI's verdict with the reviewer's on every image of a
programme (labs workflow 26488) used to call the audit page's bulk-data endpoint
once per session. Each of those calls re-reads the session and also fetches
visits, worker names and audit history the report never shows, so hundreds of
sessions took minutes. The summary endpoints already hold every session in full;
``?include=images`` returns each one's image rows from that record alone.
"""

import json
from unittest import mock

from django.test import RequestFactory

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.views import (
    SUMMARY_IMAGE_FIELDS,
    ExperimentBulkAssessmentDataView,
    OpportunityAuditSessionsSummaryAPIView,
    ProgramAuditSessionsSummaryAPIView,
    _session_image_rows,
)

OPP = 1996


def _image(blob_id, question_id="form/muac_photo", username="flw1", visit_date="2026-10-12T13:35:00Z", **extra):
    return {
        "blob_id": blob_id,
        "question_id": question_id,
        "name": f"{blob_id}.jpg",
        "username": username,
        "visit_date": visit_date,
        "child_name": "should-not-appear",
        **extra,
    }


def _session(session_id=10, *, visit_images, visit_results, related_fields=None, storage_opp=OPP):
    data = {
        "opportunity_id": OPP,
        "visit_ids": [int(v) for v in visit_images],
        "visit_images": visit_images,
        "visit_results": visit_results,
    }
    if related_fields is not None:
        data["related_fields"] = related_fields
    return AuditSessionRecord(
        {
            "id": session_id,
            "experiment": "audit",
            "type": "AuditSession",
            "data": data,
            "opportunity_id": storage_opp,
            "username": "auditor",
        }
    )


def _two_visit_session(session_id=10):
    return _session(
        session_id,
        visit_images={
            "101": [_image("b1"), _image("b2", question_id="form/other_photo")],
            "102": [_image("b3", question_id="form/other_photo", username="flw2")],
        },
        visit_results={
            "101": {
                "assessments": {
                    "b1": {
                        "result": "fail",
                        "notes": "zoomed in",
                        "ai_result": "no_match",
                        "ai_notes": "Hyperzoomed",
                        "ai_confidence": 0.91,
                        "question_id": "form/muac_photo",
                    },
                    # An assessment with no stored image metadata still gets a row.
                    "b9": {"result": "pass", "question_id": "form/late_photo", "ai_result": "match"},
                }
            },
            "102": {"assessments": {"b3": {"result": "duplicate_fake", "duplicate_group": 0}}},
        },
    )


class TestSessionImageRows:
    def test_one_row_per_image_with_only_the_lean_fields(self):
        rows = _session_image_rows(_two_visit_session())
        assert [(r["visit_id"], r["blob_id"]) for r in rows] == [(101, "b1"), (101, "b2"), (101, "b9"), (102, "b3")]
        for r in rows:
            assert set(r) == set(SUMMARY_IMAGE_FIELDS)

    def test_carries_verdicts_ai_review_and_where_the_photo_is(self):
        b1, b2, b9, b3 = _session_image_rows(_two_visit_session())
        assert b1["result"] == "fail" and b1["notes"] == "zoomed in"
        assert (b1["ai_result"], b1["ai_notes"], b1["ai_confidence"]) == ("no_match", "Hyperzoomed", 0.91)
        assert b1["username"] == "flw1" and b1["visit_date_sort"].startswith("2026-10-12")
        assert "b1" in b1["image_url"]
        # Not yet reviewed: empty verdict, not missing.
        assert b2["result"] == "" and b2["question_id"] == "form/other_photo"
        assert b9["question_id"] == "form/late_photo" and b9["result"] == "pass"
        # duplicate_group 0 is a real group, kept as 0.
        assert b3["duplicate_group"] == 0 and b3["username"] == "flw2"

    def test_leaves_case_details_out(self):
        for r in _session_image_rows(_two_visit_session()):
            assert "should-not-appear" not in json.dumps(r)

    def test_applies_the_sessions_own_image_filter(self):
        session = _two_visit_session()
        session.data["related_fields"] = [{"image_path": "form/muac_photo", "filter_by_image": True}]
        rows = _session_image_rows(session)
        # b2 is filtered out; visit 102's only image is filtered out, so the visit is skipped entirely
        # (as the audit page does). b9 has no stored metadata and survives on visit 101.
        assert [r["blob_id"] for r in rows] == ["b1", "b9"]


def _bulk_data_rows(session):
    """The audit page's own rows for ``session``, with every outside call stubbed."""
    stub = mock.MagicMock()
    stub.get_audit_session.return_value = session
    stub.get_opportunity_details.return_value = {"name": "Opp"}
    stub.get_flw_names.return_value = {}
    stub.get_prior_audited_images_for.return_value = {}
    stub.get_duplicate_history_for_flws.return_value = ({}, True)
    stub.pipeline.fetch_raw_visits.return_value = []
    request = RequestFactory().get(f"/audit/api/{session.id}/bulk-data/")
    request.user = mock.MagicMock()
    with mock.patch("connect_labs.audit.views.AuditDataAccess", return_value=stub):
        response = ExperimentBulkAssessmentDataView().get(request, session_id=session.id)
    return json.loads(response.content)["assessments"]


class TestMatchesTheAuditPage:
    def test_same_images_and_values_as_bulk_data(self):
        session = _two_visit_session()
        lean = {(r["visit_id"], r["blob_id"]): r for r in _session_image_rows(session)}
        page = {(a["visit_id"], a["blob_id"]): a for a in _bulk_data_rows(session)}
        assert set(lean) == set(page)
        for key, row in lean.items():
            for field in SUMMARY_IMAGE_FIELDS:
                assert row[field] == page[key][field], (key, field)


def _call(view_cls, url, kwargs, sessions):
    stub = mock.MagicMock()
    stub.get_audit_sessions.return_value = sessions
    stub.get_flw_names.return_value = {}
    with mock.patch("connect_labs.audit.views.AuditDataAccess", return_value=stub):
        response = view_cls().get(RequestFactory().get(url), **kwargs)
    return json.loads(response.content)


class TestSummaryEndpoints:
    def test_images_absent_unless_asked_for(self):
        body = _call(
            OpportunityAuditSessionsSummaryAPIView,
            f"/audit/api/opportunity/{OPP}/sessions-summary/",
            {"opp_id": OPP},
            [_two_visit_session()],
        )
        assert "images" not in body["sessions"][0]

    def test_opportunity_endpoint_includes_images(self):
        body = _call(
            OpportunityAuditSessionsSummaryAPIView,
            f"/audit/api/opportunity/{OPP}/sessions-summary/?include=images",
            {"opp_id": OPP},
            [_two_visit_session()],
        )
        s = body["sessions"][0]
        assert [r["blob_id"] for r in s["images"]] == ["b1", "b2", "b9", "b3"]
        assert s["storage_opportunity_id"] == OPP

    def test_program_endpoint_includes_images_and_where_each_session_is_stored(self):
        sessions = [
            _two_visit_session(10),
            _session(11, visit_images={"201": [_image("c1")]}, visit_results={}, storage_opp=1997),
        ]
        body = _call(
            ProgramAuditSessionsSummaryAPIView,
            "/audit/api/program/217/sessions-summary/?include=images",
            {"program_id": 217},
            sessions,
        )
        by_id = {s["id"]: s for s in body["sessions"]}
        assert len(by_id[10]["images"]) == 4
        assert [r["blob_id"] for r in by_id[11]["images"]] == ["c1"]
        assert by_id[11]["storage_opportunity_id"] == 1997
