# connect_labs/solicitations/models.py
"""
Proxy models for solicitations.

These proxy models extend LocalLabsRecord with typed @property access
to JSON data stored via the LabsRecord API. They cannot be .save()d locally.
"""
from datetime import datetime

from connect_labs.labs.models import LocalLabsRecord


class SolicitationRecord(LocalLabsRecord):
    """Proxy model for solicitation records. Scoped by program_id."""

    @property
    def title(self):
        return self.data.get("title", "")

    @property
    def description(self):
        return self.data.get("description", "")

    @property
    def scope_of_work(self):
        return self.data.get("scope_of_work", "")

    @property
    def solicitation_type(self):
        return self.data.get("solicitation_type", "")

    @property
    def type_label(self):
        """Human-readable solicitation type for UI badges — never a raw code.

        An outside firm shouldn't have to decode "EOI"; spell it out.
        """
        return {
            "eoi": "Expression of Interest",
            "rfp": "Request for Proposal",
        }.get(self.solicitation_type, (self.solicitation_type or "").upper())

    @property
    def status(self):
        return self.data.get("status", "draft")

    @property
    def is_public(self):
        # Single source of truth is the server-side LabsRecord.public column —
        # the marketplace listing query filters on it, and there's no scenario
        # where a record is conceptually "public-listed" but record.public=False.
        return bool(self.public)

    @property
    def questions(self):
        return self.data.get("questions", [])

    @property
    def evaluation_criteria(self):
        return self.data.get("evaluation_criteria", [])

    @property
    def application_deadline(self):
        date_str = self.data.get("application_deadline")
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return None
        return None

    @property
    def expected_start_date(self):
        date_str = self.data.get("expected_start_date")
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return None
        return None

    @property
    def expected_end_date(self):
        date_str = self.data.get("expected_end_date")
        if date_str:
            try:
                return datetime.strptime(date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                return None
        return None

    @property
    def estimated_scale(self):
        return self.data.get("estimated_scale", "")

    @property
    def contact_email(self):
        return self.data.get("contact_email", "")

    @property
    def created_by(self):
        return self.data.get("created_by", "")

    @property
    def program_name(self):
        return self.data.get("program_name", "")

    @property
    def fund_id(self):
        return self.data.get("fund_id")

    @property
    def connect_opportunity_id(self):
        return self.data.get("connect_opportunity_id")

    @property
    def plans(self):
        return self.data.get("plans", [])

    @property
    def source_program_id(self):
        return self.data.get("source_program_id")

    @property
    def source_group_id(self):
        return self.data.get("source_group_id")

    @property
    def source_plan_ids(self):
        return self.data.get("source_plan_ids", [])

    def can_accept_responses(self):
        return self.status == "active"


class ResponseRecord(LocalLabsRecord):
    """Proxy model for response records. Scoped by llo_entity_id."""

    @property
    def solicitation_id(self):
        return self.data.get("solicitation_id")

    @property
    def llo_entity_id(self):
        return self.data.get("llo_entity_id", "")

    @property
    def llo_entity_name(self):
        return self.data.get("llo_entity_name", "")

    @property
    def responses(self):
        return self.data.get("responses", {})

    @property
    def status(self):
        return self.data.get("status", "draft")

    @property
    def submitted_by_name(self):
        return self.data.get("submitted_by_name", "")

    @property
    def submitted_by_email(self):
        return self.data.get("submitted_by_email", "")

    @property
    def org_id(self):
        return self.data.get("org_id", "")

    @property
    def org_name(self):
        return self.data.get("org_name", "")

    @property
    def submission_date(self):
        date_str = self.data.get("submission_date")
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None

    @property
    def selected_plan_ids(self):
        return self.data.get("selected_plan_ids", [])

    @property
    def selected_plan_names(self):
        return self.data.get("selected_plan_names", [])

    @property
    def reward_budget(self):
        return self.data.get("reward_budget")

    @property
    def awarded_at(self):
        date_str = self.data.get("awarded_at")
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None

    @property
    def awardee_notified(self):
        return bool(self.data.get("awardee_notified", False))

    @property
    def is_awarded(self):
        return self.status == "awarded"


class ReviewRecord(LocalLabsRecord):
    """Proxy model for review records."""

    @property
    def response_id(self):
        return self.data.get("response_id")

    @property
    def score(self):
        return self.data.get("score")

    @property
    def recommendation(self):
        return self.data.get("recommendation", "")

    @property
    def notes(self):
        return self.data.get("notes", "")

    @property
    def tags(self):
        return self.data.get("tags", "")

    @property
    def reviewer_username(self):
        return self.data.get("reviewer_username", "")

    @property
    def criteria_scores(self):
        return self.data.get("criteria_scores", {})

    @property
    def reward_budget(self):
        return self.data.get("reward_budget")

    @property
    def review_date(self):
        date_str = self.data.get("review_date")
        if date_str:
            try:
                return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                return None
        return None
