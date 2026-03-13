"""Tests for solicitations_new forms.

These tests are pure Python — no database required.
"""
import json

import pytest

from commcare_connect.solicitations_new.forms import ReviewForm, SolicitationForm, SolicitationResponseForm


class TestSolicitationForm:
    def test_valid_minimal(self):
        form = SolicitationForm(
            data={
                "title": "Test RFP",
                "description": "A description",
                "solicitation_type": "rfp",
                "status": "draft",
                "is_public": True,
                "contact_email": "test@example.com",
            }
        )
        assert form.is_valid(), form.errors

    def test_missing_title(self):
        form = SolicitationForm(
            data={
                "description": "A description",
                "solicitation_type": "rfp",
                "status": "draft",
            }
        )
        assert not form.is_valid()
        assert "title" in form.errors

    def test_missing_description(self):
        form = SolicitationForm(
            data={
                "title": "Test RFP",
                "solicitation_type": "rfp",
                "status": "draft",
            }
        )
        assert not form.is_valid()
        assert "description" in form.errors

    def test_invalid_solicitation_type(self):
        form = SolicitationForm(
            data={
                "title": "Test RFP",
                "description": "A description",
                "solicitation_type": "invalid_type",
                "status": "draft",
            }
        )
        assert not form.is_valid()
        assert "solicitation_type" in form.errors

    def test_invalid_status(self):
        form = SolicitationForm(
            data={
                "title": "Test RFP",
                "description": "A description",
                "solicitation_type": "rfp",
                "status": "unknown_status",
            }
        )
        assert not form.is_valid()
        assert "status" in form.errors

    def test_invalid_email(self):
        form = SolicitationForm(
            data={
                "title": "Test RFP",
                "description": "A description",
                "solicitation_type": "rfp",
                "status": "draft",
                "contact_email": "not-an-email",
            }
        )
        assert not form.is_valid()
        assert "contact_email" in form.errors

    def test_is_public_defaults_true(self):
        """is_public should default to True when not explicitly provided."""
        form = SolicitationForm()
        assert form.fields["is_public"].initial is True

    def test_optional_date_fields(self):
        form = SolicitationForm(
            data={
                "title": "Test",
                "description": "Desc",
                "solicitation_type": "eoi",
                "status": "active",
                "application_deadline": "2026-06-01",
                "expected_start_date": "2026-07-01",
                "expected_end_date": "2026-12-31",
            }
        )
        assert form.is_valid(), form.errors

    def test_to_data_dict_basic(self):
        form = SolicitationForm(
            data={
                "title": "Test RFP",
                "description": "A description",
                "solicitation_type": "rfp",
                "status": "draft",
                "is_public": True,
                "contact_email": "test@example.com",
            }
        )
        assert form.is_valid(), form.errors
        data = form.to_data_dict()
        assert data["title"] == "Test RFP"
        assert data["description"] == "A description"
        assert data["solicitation_type"] == "rfp"
        assert data["status"] == "draft"
        assert data["is_public"] is True
        assert data["contact_email"] == "test@example.com"

    def test_to_data_dict_serializes_dates(self):
        form = SolicitationForm(
            data={
                "title": "Test",
                "description": "Desc",
                "solicitation_type": "eoi",
                "status": "active",
                "application_deadline": "2026-06-01",
                "expected_start_date": "2026-07-01",
                "expected_end_date": "2026-12-31",
            }
        )
        assert form.is_valid(), form.errors
        data = form.to_data_dict()
        assert data["application_deadline"] == "2026-06-01"
        assert data["expected_start_date"] == "2026-07-01"
        assert data["expected_end_date"] == "2026-12-31"

    def test_to_data_dict_parses_questions_json(self):
        questions = [{"id": "q1", "text": "Why?", "type": "textarea", "required": True}]
        form = SolicitationForm(
            data={
                "title": "Test",
                "description": "Desc",
                "solicitation_type": "rfp",
                "status": "draft",
                "questions_json": json.dumps(questions),
            }
        )
        assert form.is_valid(), form.errors
        data = form.to_data_dict()
        assert data["questions"] == questions

    def test_to_data_dict_empty_questions_json(self):
        form = SolicitationForm(
            data={
                "title": "Test",
                "description": "Desc",
                "solicitation_type": "rfp",
                "status": "draft",
                "questions_json": "",
            }
        )
        assert form.is_valid(), form.errors
        data = form.to_data_dict()
        assert data["questions"] == []

    def test_to_data_dict_omits_none_dates(self):
        form = SolicitationForm(
            data={
                "title": "Test",
                "description": "Desc",
                "solicitation_type": "rfp",
                "status": "draft",
            }
        )
        assert form.is_valid(), form.errors
        data = form.to_data_dict()
        assert data.get("application_deadline") is None
        assert data.get("expected_start_date") is None
        assert data.get("expected_end_date") is None


class TestSolicitationResponseForm:
    def _questions(self):
        return [
            {"id": "q1", "text": "Why apply?", "type": "textarea", "required": True},
            {"id": "q2", "text": "Team size?", "type": "number", "required": False},
        ]

    def test_valid_with_required_question(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={"question_q1": "We are qualified"},
        )
        assert form.is_valid(), form.errors

    def test_missing_required_question(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={},
        )
        assert not form.is_valid()
        assert "question_q1" in form.errors

    def test_optional_field_not_required(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={"question_q1": "We are qualified"},
        )
        assert form.is_valid(), form.errors
        # question_q2 is optional and not provided — should still be valid

    def test_text_field_type(self):
        questions = [
            {"id": "q1", "text": "Name?", "type": "text", "required": True},
        ]
        form = SolicitationResponseForm(questions=questions, data={"question_q1": "Alice"})
        assert form.is_valid(), form.errors

    def test_multiple_choice_field(self):
        questions = [
            {
                "id": "q1",
                "text": "Preferred region?",
                "type": "multiple_choice",
                "required": True,
                "options": ["North", "South", "East", "West"],
            },
        ]
        form = SolicitationResponseForm(questions=questions, data={"question_q1": "North"})
        assert form.is_valid(), form.errors

    def test_multiple_choice_invalid_option(self):
        questions = [
            {
                "id": "q1",
                "text": "Preferred region?",
                "type": "multiple_choice",
                "required": True,
                "options": ["North", "South"],
            },
        ]
        form = SolicitationResponseForm(questions=questions, data={"question_q1": "Invalid"})
        assert not form.is_valid()
        assert "question_q1" in form.errors

    def test_get_responses_dict(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={"question_q1": "We are qualified", "question_q2": "5"},
        )
        assert form.is_valid(), form.errors
        responses = form.get_responses_dict()
        assert responses == {"q1": "We are qualified", "q2": 5}

    def test_get_responses_dict_skips_empty_optional(self):
        form = SolicitationResponseForm(
            questions=self._questions(),
            data={"question_q1": "We are qualified"},
        )
        assert form.is_valid(), form.errors
        responses = form.get_responses_dict()
        assert "q1" in responses
        # q2 was not provided, should not be in responses or should have None/empty value

    def test_no_questions(self):
        form = SolicitationResponseForm(questions=[], data={})
        assert form.is_valid(), form.errors

    def test_number_field_validates_numeric(self):
        questions = [
            {"id": "q1", "text": "Count?", "type": "number", "required": True},
        ]
        form = SolicitationResponseForm(questions=questions, data={"question_q1": "not_a_number"})
        assert not form.is_valid()
        assert "question_q1" in form.errors


class TestReviewForm:
    def test_valid(self):
        form = ReviewForm(
            data={
                "score": 85,
                "recommendation": "approved",
                "notes": "Good application",
            }
        )
        assert form.is_valid(), form.errors

    def test_score_out_of_range(self):
        form = ReviewForm(
            data={
                "score": 150,
                "recommendation": "approved",
            }
        )
        assert not form.is_valid()

    def test_score_below_minimum(self):
        form = ReviewForm(
            data={
                "score": 0,
                "recommendation": "approved",
            }
        )
        assert not form.is_valid()

    def test_valid_at_boundaries(self):
        form_low = ReviewForm(data={"score": 1, "recommendation": "approved"})
        assert form_low.is_valid(), form_low.errors

        form_high = ReviewForm(data={"score": 100, "recommendation": "approved"})
        assert form_high.is_valid(), form_high.errors

    def test_invalid_recommendation(self):
        form = ReviewForm(
            data={
                "score": 50,
                "recommendation": "invalid_choice",
            }
        )
        assert not form.is_valid()
        assert "recommendation" in form.errors

    def test_optional_fields(self):
        form = ReviewForm(
            data={
                "score": 50,
                "recommendation": "under_review",
            }
        )
        assert form.is_valid(), form.errors

    def test_all_recommendation_choices(self):
        for choice in ["under_review", "approved", "rejected", "needs_revision"]:
            form = ReviewForm(data={"score": 50, "recommendation": choice})
            assert form.is_valid(), f"Failed for recommendation: {choice}, errors: {form.errors}"
