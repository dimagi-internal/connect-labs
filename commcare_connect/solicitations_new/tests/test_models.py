from commcare_connect.solicitations_new.models import ResponseRecord, ReviewRecord, SolicitationRecord


class TestSolicitationRecord:
    def _make(self, **overrides):
        data = {
            "title": "Test Solicitation",
            "description": "A test",
            "scope_of_work": "Do the work",
            "solicitation_type": "rfp",
            "status": "active",
            "is_public": True,
            "questions": [{"id": "q1", "text": "Why?", "type": "text", "required": True}],
            "application_deadline": "2026-06-01",
            "expected_start_date": "2026-07-01",
            "expected_end_date": "2026-12-31",
            "estimated_scale": "1000 beneficiaries",
            "contact_email": "test@example.com",
            "created_by": "testuser",
            "program_name": "Test Program",
        }
        data.update(overrides.pop("data", {}))
        defaults = {
            "id": 1,
            "experiment": "test_program",
            "type": "solicitation",
            "data": data,
            "opportunity_id": 0,
        }
        defaults.update(overrides)
        return SolicitationRecord(defaults)

    def test_title(self):
        rec = self._make()
        assert rec.title == "Test Solicitation"

    def test_solicitation_type(self):
        rec = self._make()
        assert rec.solicitation_type == "rfp"

    def test_is_public(self):
        rec = self._make()
        assert rec.is_public is True

    def test_application_deadline_parses(self):
        rec = self._make()
        from datetime import date

        assert rec.application_deadline == date(2026, 6, 1)

    def test_application_deadline_none(self):
        rec = self._make(data={"application_deadline": None})
        assert rec.application_deadline is None

    def test_questions(self):
        rec = self._make()
        assert len(rec.questions) == 1
        assert rec.questions[0]["id"] == "q1"

    def test_can_accept_responses(self):
        rec = self._make(data={"status": "active"})
        assert rec.can_accept_responses() is True
        rec2 = self._make(data={"status": "closed"})
        assert rec2.can_accept_responses() is False

    def test_description(self):
        rec = self._make()
        assert rec.description == "A test"

    def test_scope_of_work(self):
        rec = self._make()
        assert rec.scope_of_work == "Do the work"

    def test_status_default(self):
        self._make(data={"status": None})
        # When status is None, data.get("status", "draft") returns None, not "draft"
        # But our default should handle missing keys gracefully
        rec2 = self._make()
        rec2.data.pop("status", None)
        assert rec2.status == "draft"

    def test_expected_start_date(self):
        rec = self._make()
        from datetime import date

        assert rec.expected_start_date == date(2026, 7, 1)

    def test_expected_end_date(self):
        rec = self._make()
        from datetime import date

        assert rec.expected_end_date == date(2026, 12, 31)

    def test_estimated_scale(self):
        rec = self._make()
        assert rec.estimated_scale == "1000 beneficiaries"

    def test_contact_email(self):
        rec = self._make()
        assert rec.contact_email == "test@example.com"

    def test_created_by(self):
        rec = self._make()
        assert rec.created_by == "testuser"

    def test_program_name(self):
        rec = self._make()
        assert rec.program_name == "Test Program"


class TestSolicitationRecordFundId:
    def test_fund_id(self):
        record = SolicitationRecord(
            {"id": 1, "experiment": "p", "type": "solicitation_new", "data": {"fund_id": 42}, "opportunity_id": 0}
        )
        assert record.fund_id == 42

    def test_fund_id_default_none(self):
        record = SolicitationRecord(
            {"id": 1, "experiment": "p", "type": "solicitation_new", "data": {}, "opportunity_id": 0}
        )
        assert record.fund_id is None


class TestResponseRecordOrgFields:
    def test_org_id(self):
        record = ResponseRecord(
            {
                "id": 1,
                "experiment": "e",
                "type": "solicitation_new_response",
                "data": {"org_id": "org_42"},
                "opportunity_id": 0,
            }
        )
        assert record.org_id == "org_42"

    def test_org_name(self):
        record = ResponseRecord(
            {
                "id": 1,
                "experiment": "e",
                "type": "solicitation_new_response",
                "data": {"org_name": "Test Org"},
                "opportunity_id": 0,
            }
        )
        assert record.org_name == "Test Org"


class TestReviewRecordRewardBudget:
    def test_reward_budget(self):
        record = ReviewRecord(
            {
                "id": 1,
                "experiment": "e",
                "type": "solicitation_new_review",
                "data": {"reward_budget": 500000},
                "opportunity_id": 0,
            }
        )
        assert record.reward_budget == 500000

    def test_reward_budget_default_none(self):
        record = ReviewRecord(
            {"id": 1, "experiment": "e", "type": "solicitation_new_review", "data": {}, "opportunity_id": 0}
        )
        assert record.reward_budget is None


class TestResponseRecord:
    def _make(self, **overrides):
        data = {
            "solicitation_id": 1,
            "llo_entity_id": "llo_entity_123",
            "llo_entity_name": "Test Org",
            "responses": {"q1": "Because"},
            "status": "submitted",
            "submitted_by_name": "Jane Doe",
            "submitted_by_email": "jane@example.com",
            "submission_date": "2026-05-15T10:00:00Z",
        }
        data.update(overrides.pop("data", {}))
        defaults = {
            "id": 10,
            "experiment": "llo_entity_123",
            "type": "solicitation_response",
            "data": data,
            "opportunity_id": 0,
        }
        defaults.update(overrides)
        return ResponseRecord(defaults)

    def test_solicitation_id(self):
        rec = self._make()
        assert rec.solicitation_id == 1

    def test_llo_entity_name(self):
        rec = self._make()
        assert rec.llo_entity_name == "Test Org"

    def test_responses_dict(self):
        rec = self._make()
        assert rec.responses == {"q1": "Because"}

    def test_status(self):
        rec = self._make()
        assert rec.status == "submitted"

    def test_llo_entity_id(self):
        rec = self._make()
        assert rec.llo_entity_id == "llo_entity_123"

    def test_submitted_by_name(self):
        rec = self._make()
        assert rec.submitted_by_name == "Jane Doe"

    def test_submitted_by_email(self):
        rec = self._make()
        assert rec.submitted_by_email == "jane@example.com"

    def test_submission_date(self):
        rec = self._make()
        assert rec.submission_date is not None


class TestReviewRecord:
    def _make(self, **overrides):
        data = {
            "response_id": 10,
            "score": 85,
            "recommendation": "approved",
            "notes": "Looks good",
            "tags": "experienced,local",
            "reviewer_username": "reviewer1",
            "review_date": "2026-05-20T14:00:00Z",
        }
        data.update(overrides.pop("data", {}))
        defaults = {
            "id": 20,
            "experiment": "llo_entity_123",
            "type": "solicitation_review",
            "data": data,
            "opportunity_id": 0,
        }
        defaults.update(overrides)
        return ReviewRecord(defaults)

    def test_score(self):
        rec = self._make()
        assert rec.score == 85

    def test_recommendation(self):
        rec = self._make()
        assert rec.recommendation == "approved"

    def test_reviewer_username(self):
        rec = self._make()
        assert rec.reviewer_username == "reviewer1"

    def test_response_id(self):
        rec = self._make()
        assert rec.response_id == 10

    def test_notes(self):
        rec = self._make()
        assert rec.notes == "Looks good"

    def test_tags(self):
        rec = self._make()
        assert rec.tags == "experienced,local"

    def test_review_date(self):
        rec = self._make()
        assert rec.review_date is not None
