"""Small builders for solicitation tests. Every name and address is invented."""

from __future__ import annotations

from django.utils import timezone

from connect_labs.labs.models import LabsOrg
from connect_labs.solicitations.local_models import (
    SOURCE_LABS,
    Solicitation,
    SolicitationResponse,
    SolicitationReview,
)
from connect_labs.users.models import User

QUESTIONS = [
    {"id": "q1", "text": "Describe your experience.", "type": "textarea", "required": True},
    {"id": "q2", "text": "How many staff?", "type": "number"},
]
CRITERIA = [
    {"id": "c1", "name": "Experience", "weight": 60, "linked_questions": ["q1"]},
    {"id": "c2", "name": "Capacity", "weight": 40, "linked_questions": ["q2"]},
]


def make_user(username, *, staff=False, email=None):
    return User.objects.create(
        username=username, email=email or f"{username}@example.org", is_staff=staff, name=username.title()
    )


def make_org(slug, name=None, **kw):
    return LabsOrg.objects.create(slug=slug, name=name or slug.replace("-", " ").title(), **kw)


def grant(user, org, role="admin"):
    from connect_labs.marketplace.models import OrgMembership

    return OrgMembership.objects.create(org=org, user=user, role=role)


def make_call(owner=None, **kw):
    defaults = {
        "title": "Household survey in two districts",
        "description": "We need a survey firm.",
        "solicitation_type": "rfp",
        "status": "active",
        "listed": True,
        "source": SOURCE_LABS,
        "questions": QUESTIONS,
        "evaluation_criteria": CRITERIA,
        "contact_email": "calls@owner.example",
    }
    defaults.update(kw)
    return Solicitation.objects.create(owner_org=owner, **defaults)


def make_response(call, user=None, *, status="submitted", **kw):
    defaults = {
        "responses": {"q1": "Ten years of surveys.", "q2": "12"},
        "submitted_by_name": "Applicant",
        "submitted_by_email": "applicant@firm.example",
        "org_name": "Firm Ltd",
        "submission_date": timezone.now(),
    }
    defaults.update(kw)
    return SolicitationResponse.objects.create(solicitation=call, submitted_by=user, status=status, **defaults)


def make_review(response, reviewer=None, **kw):
    return SolicitationReview.objects.create(response=response, reviewer=reviewer, **kw)
