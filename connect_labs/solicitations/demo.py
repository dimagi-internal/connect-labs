"""A demo organisation with open calls and a few responses, for external demos.

Invented, all of it: the organisation, every firm, contact, answer and amount.
THIS REPOSITORY IS PUBLIC -- nothing here names a real applicant, and a demo
built on this never shows a real firm's answers. Deadlines are computed from
the day it runs, so the calls are always open.

Idempotent: everything is keyed by fixed slugs (calls) and invented addresses
(responses), and a re-run refreshes dates and text in place.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

ORG_KEY = "solicitations"
ORG_NAME = "Kestrel Community Health (demo)"
NOTE = "Invented for solicitation demos; nothing here is real."

QUESTIONS = [
    {
        "id": "q_experience",
        "text": "Describe comparable work you have delivered in the last three years.",
        "type": "textarea",
        "required": True,
    },
    {
        "id": "q_team",
        "text": "How many field staff would you deploy, and how are they supervised?",
        "type": "textarea",
        "required": True,
    },
    {"id": "q_budget", "text": "Total budget requested (in the call's currency).", "type": "number", "required": True},
]
CRITERIA = [
    {"id": "c_experience", "name": "Relevant experience", "weight": 40, "linked_questions": ["q_experience"]},
    {"id": "c_team", "name": "Field team and supervision", "weight": 35, "linked_questions": ["q_team"]},
    {"id": "c_value", "name": "Value for money", "weight": 25, "linked_questions": ["q_budget"]},
]

CALLS = [
    {
        "key": "household-survey",
        "title": "Household coverage survey in two districts (demo)",
        "solicitation_type": "rfp",
        "description": (
            "Kestrel Community Health is looking for a survey partner to measure household coverage of "
            "community health visits across two districts."
        ),
        "scope_of_work": "Sample households, train enumerators, collect and clean data, and report findings.",
        "estimated_scale": "About 3,000 households",
        "currency": "USD",
        "deadline_days": 21,
    },
    {
        "key": "chw-training",
        "title": "Community health worker refresher training (demo)",
        "solicitation_type": "eoi",
        "description": "Expressions of interest from organisations able to run refresher training for 200 workers.",
        "scope_of_work": "Design and deliver a five-day refresher course in three locations.",
        "estimated_scale": "200 community health workers",
        "currency": "USD",
        "deadline_days": 14,
    },
    {
        "key": "water-points",
        "title": "Safe-water point monitoring (demo)",
        "solicitation_type": "rfp",
        "description": "A partner to visit and monitor community water points monthly for one year.",
        "scope_of_work": "Monthly visits, chlorine residual tests and a quarterly summary.",
        "estimated_scale": "120 water points",
        "currency": "USD",
        "deadline_days": 30,
    },
]

FIRMS = [
    (
        "Amber Ridge Research (demo)",
        "Lena Moss",
        "lena@amberridge.example",
        12,
        "Eighteen enumerators, one supervisor per six.",
    ),
    (
        "Bluefinch Field Services (demo)",
        "Tomas Reyes",
        "tomas@bluefinch.example",
        7,
        "Ten enumerators with daily spot checks.",
    ),
]


def ensure_org():
    """The demo organisation: a labs-only row under a fixed key this seeder owns."""
    from connect_labs.labs.models import LABS_ONLY_ORG_PREFIX, LabsOrg

    org, _ = LabsOrg.objects.get_or_create(
        slug=f"{LABS_ONLY_ORG_PREFIX}demo-{ORG_KEY}", defaults={"name": ORG_NAME, "labs_only": True, "notes": NOTE}
    )
    if not org.labs_only or org.connect_organization_id is not None:
        raise ValueError(f"{org.slug} is not a labs-only organisation this seeder made; refusing to use it")
    return org


@transaction.atomic
def seed(*, today: date | None = None, admin=None) -> dict:
    """Create or refresh the demo organisation, its calls and responses. Returns their ids."""
    from connect_labs.marketplace.models import OrgMembership
    from connect_labs.solicitations.local_models import SOURCE_LABS, Solicitation, SolicitationResponse

    today = today or timezone.localdate()
    org = ensure_org()
    if admin is not None:
        OrgMembership.objects.get_or_create(org=org, user=admin, defaults={"role": "admin"})

    calls = {}
    for spec in CALLS:
        call, _ = Solicitation.objects.update_or_create(
            slug=f"demo-{ORG_KEY}-{spec['key']}",
            defaults={
                "source": SOURCE_LABS,
                "owner_org": org,
                "listed": True,
                "status": "active",
                "title": spec["title"],
                "description": spec["description"],
                "scope_of_work": spec["scope_of_work"],
                "solicitation_type": spec["solicitation_type"],
                "estimated_scale": spec["estimated_scale"],
                "currency": spec["currency"],
                "contact_email": "calls@kestrel.example",
                "application_deadline": today + timedelta(days=spec["deadline_days"]),
                "expected_start_date": today + timedelta(days=spec["deadline_days"] + 14),
                "expected_end_date": today + timedelta(days=spec["deadline_days"] + 120),
                "questions": QUESTIONS,
                "evaluation_criteria": CRITERIA,
                "criteria_locked_at": timezone.now(),
                "notes": NOTE,
            },
        )
        calls[spec["key"]] = call.pk

    responses = []
    survey = Solicitation.objects.get(pk=calls["household-survey"])
    for i, (firm, contact, email, years, team) in enumerate(FIRMS):
        response, _ = SolicitationResponse.objects.update_or_create(
            solicitation=survey,
            submitted_by_email=email,
            defaults={
                "org_name": firm,
                "submitted_by_name": contact,
                "status": "submitted",
                "submission_date": timezone.now() - timedelta(days=3 - i),
                "responses": {
                    "q_experience": f"{years} household surveys in the last three years (invented).",
                    "q_team": team,
                    "q_budget": str(40000 + 5000 * i),
                },
            },
        )
        responses.append(response.pk)
    return {"organisation": org.slug, "calls": calls, "responses": responses}
