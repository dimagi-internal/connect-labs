"""Per-photo-type counters must key on the PHOTO, not on whatever field it was checked against.

An audit session can hold several kinds of photo at once. KMC's does: a weight photo the
scale classifier reads, plus equipment and KMC-wrap photos that no classifier exists for.
Reporting those together as one blended pass rate reads as a drop in quality when it is
only a change in what was photographed, so the per-type split has to be trustworthy.

The obvious source for it -- ``get_assessment_stats_by_question`` -- is NOT trustworthy for
this. ``_persist_outcome`` (connect_labs/audit/tasks.py) files an assessment under the first
related field that carried a VALUE, so a photo checked against a typed reading is stored
under that FIELD's path: a KMC weight photo lands under "child_weight_visit", not under
"anthropometric/upload_weight_image". Every other surface -- the image rows the review UI
renders, the opportunity image-questions endpoint, an audit's related_fields rules -- speaks
image paths. Keying a per-type breakdown off the assessment therefore produces a dict that
looks right and joins to nothing.

``get_stats_by_image_type`` walks the stored IMAGES instead and joins each to its assessment
by blob_id, which is the only direction that keeps one vocabulary end to end.
"""

from connect_labs.audit.models import AuditSessionRecord

WEIGHT_IMAGE = "anthropometric/upload_weight_image"
WEIGHT_FIELD = "child_weight_visit"
EQUIPMENT = "danger_signs_checklist/equipment_image_capture_checklist/equipments_image_capture"
WRAP = "commodities_delivered/kmc_wrap_provided_image"


def _image(blob_id, question_id, visit_date="2026-08-20T09:00:00"):
    return {
        "blob_id": blob_id,
        "question_id": question_id,
        "username": "flw1",
        "visit_date": visit_date,
        "entity_name": "Child A",
    }


def _session(visit_images, visit_results=None):
    return AuditSessionRecord(
        {
            "id": 1,
            "experiment": "audit",
            "type": "AuditSession",
            "data": {"visit_images": visit_images, "visit_results": visit_results or {}},
            "opportunity_id": 1487,
        }
    )


def _kmc_session():
    """The real shape a mixed KMC run produces.

    Two weight photos scored by the scale agent -- and so filed by the review task under
    the READING's field path -- alongside one equipment photo and one wrap photo that no
    classifier can score and that therefore have no assessment at all.
    """
    return _session(
        visit_images={
            "101": [_image("b1", WEIGHT_IMAGE), _image("b2", EQUIPMENT)],
            "102": [_image("b3", WEIGHT_IMAGE), _image("b4", WRAP)],
        },
        visit_results={
            "101": {
                "assessments": {
                    "b1": {"question_id": WEIGHT_FIELD, "result": None, "ai_result": "match"},
                }
            },
            "102": {
                "assessments": {
                    "b3": {
                        "question_id": WEIGHT_FIELD,
                        "result": "fail",
                        "ai_result": "no_match",
                        "ai_notes": "Scale Mismatch",
                    },
                }
            },
        },
    )


class TestKeyedByImagePathNotFieldPath:
    def test_buckets_are_keyed_by_the_photo_path(self):
        by_type = _kmc_session().get_stats_by_image_type()

        assert set(by_type) == {WEIGHT_IMAGE, EQUIPMENT, WRAP}
        assert WEIGHT_FIELD not in by_type, "the comparison field is not a photo type"

    def test_the_older_method_really_does_key_on_the_field_and_so_cannot_be_used(self):
        """Pins the divergence rather than assuming it.

        If assessment storage is ever changed so question_id holds the image path, this
        fails and whoever made that change can collapse the two methods deliberately --
        instead of one of them silently starting to disagree with the UI.
        """
        by_question = _kmc_session().get_assessment_stats_by_question()

        assert WEIGHT_FIELD in by_question
        assert WEIGHT_IMAGE not in by_question
        # And it cannot see the photos nothing reviewed, which is the other half of why
        # it is the wrong source for a per-type denominator.
        assert EQUIPMENT not in by_question
        assert WRAP not in by_question


class TestUnreviewedPhotosAreCountedNotDropped:
    def test_a_photo_with_no_assessment_counts_as_pending(self):
        by_type = _kmc_session().get_stats_by_image_type()

        assert by_type[EQUIPMENT]["total"] == 1
        assert by_type[EQUIPMENT]["pending"] == 1
        assert by_type[EQUIPMENT]["ai_pending"] == 1
        assert by_type[EQUIPMENT]["ai_match"] == 0
        assert by_type[EQUIPMENT]["ai_no_match"] == 0

    def test_total_is_the_image_count_so_it_can_be_a_pass_rate_denominator(self):
        by_type = _kmc_session().get_stats_by_image_type()

        assert by_type[WEIGHT_IMAGE]["total"] == 2
        assert by_type[WRAP]["total"] == 1
        assert sum(b["total"] for b in by_type.values()) == 4  # every stored image, once

    def test_the_weight_pass_rate_is_unaffected_by_adding_unscoreable_types(self):
        """The whole point of the split: 1 of 2 weight photos matched, and that stays
        1 of 2 no matter how many equipment or wrap photos ride along in the session."""
        by_type = _kmc_session().get_stats_by_image_type()
        weight = by_type[WEIGHT_IMAGE]

        assert weight["ai_match"] == 1
        assert weight["ai_no_match"] == 1
        assert weight["total"] == 2


class TestVerdictsAreReadThroughTheSharedTally:
    def test_ai_verdicts_land_on_the_right_photo_type(self):
        by_type = _kmc_session().get_stats_by_image_type()

        assert by_type[WEIGHT_IMAGE]["ai_flags_by_label"] == {"Scale Mismatch": 1}
        assert by_type[WEIGHT_IMAGE]["fail"] == 1
        # An unscoreable type must never pick up another type's flags.
        assert by_type[EQUIPMENT]["ai_flags_by_label"] == {}

    def test_the_by_question_buckets_now_carry_ai_counters_too(self):
        """Both breakdowns are filled by ``tally_assessment``, so neither can classify a
        result differently from the session total -- the reason they were unified."""
        by_question = _kmc_session().get_assessment_stats_by_question()

        assert by_question[WEIGHT_FIELD]["ai_match"] == 1
        assert by_question[WEIGHT_FIELD]["ai_no_match"] == 1
        assert by_question[WEIGHT_FIELD]["ai_flags_by_label"] == {"Scale Mismatch": 1}

    def test_by_question_reconciles_with_the_session_total(self):
        session = _kmc_session()
        stats = session.get_assessment_stats()
        buckets = session.get_assessment_stats_by_question().values()

        for key in ("total", "pass", "fail", "duplicate_fake", "pending", "ai_match", "ai_no_match", "ai_error"):
            assert stats[key] == sum(b[key] for b in buckets), key

    def test_by_image_type_exceeds_the_assessment_total_by_exactly_the_unreviewed(self):
        """Documented difference, asserted rather than left to be rediscovered: the
        image-keyed breakdown counts photos, the session total counts assessments."""
        session = _kmc_session()
        images = sum(b["total"] for b in session.get_stats_by_image_type().values())
        assessments = session.get_assessment_stats()["total"]

        assert images == 4
        assert assessments == 2
        assert images - assessments == 2  # the equipment and wrap photos


class TestJoinIsScopedToItsOwnVisit:
    def test_an_assessment_does_not_leak_across_visits_with_reused_blob_ids(self):
        session = _session(
            visit_images={"101": [_image("dup", WEIGHT_IMAGE)], "102": [_image("dup", EQUIPMENT)]},
            visit_results={"101": {"assessments": {"dup": {"question_id": WEIGHT_FIELD, "ai_result": "match"}}}},
        )

        by_type = session.get_stats_by_image_type()

        assert by_type[WEIGHT_IMAGE]["ai_match"] == 1
        assert by_type[EQUIPMENT]["ai_match"] == 0, "the second visit's photo has no assessment of its own"
        assert by_type[EQUIPMENT]["ai_pending"] == 1


class TestSummaryDictCarriesIt:
    def test_by_image_type_is_on_the_summary_the_workflow_endpoint_returns(self):
        summary = _kmc_session().to_summary_dict()

        assert summary["by_image_type"][WEIGHT_IMAGE]["total"] == 2
        assert summary["by_image_type"][EQUIPMENT]["ai_pending"] == 1

    def test_the_label_matches_what_the_image_questions_endpoint_would_show(self):
        """OpportunityImageTypesAPIView labels a type with the last path segment; a
        different rule here would show a reviewer two names for one photo type."""
        by_type = _kmc_session().get_stats_by_image_type()

        assert by_type[WEIGHT_IMAGE]["label"] == "upload_weight_image"
        assert by_type[EQUIPMENT]["label"] == "equipments_image_capture"
        assert by_type[WRAP]["label"] == "kmc_wrap_provided_image"


class TestDegenerateData:
    def test_missing_or_malformed_images_do_not_raise(self):
        session = _session(
            visit_images={"101": None, "102": ["not-a-dict", _image("b1", "")], "103": []},
            visit_results={},
        )

        by_type = session.get_stats_by_image_type()

        assert by_type["unknown"]["total"] == 1  # the image with no question_id
        assert by_type["unknown"]["label"] == "unknown"

    def test_a_session_with_no_images_at_all_is_empty_not_an_error(self):
        assert _session(visit_images={}).get_stats_by_image_type() == {}
