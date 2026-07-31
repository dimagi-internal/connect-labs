"""EOI round / submission / review lifecycle and the qualification registry.

Business rules live here; the API modules stay thin.
"""
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from ..models import Category, EOIReview, EOIRound, EOISubmission, Qualification
from ..serializers import org_dict
from .org_actions import ActionError

QUALIFICATION_TERM_DAYS = 540  # ~18 months

ROUND_TRANSITIONS = {
    EOIRound.Status.DRAFT: {EOIRound.Status.OPEN},
    EOIRound.Status.OPEN: {EOIRound.Status.CLOSED},
    EOIRound.Status.CLOSED: set(),
}

VALID_CATEGORIES = {c.value for c in Category}


def parse_iso_date(data, field):
    """A date off the JSON wire, or None. Passing the raw string through to
    objects.create() leaves a str on the in-memory instance, and the
    serializer's .isoformat() then 500s on the very request that created the
    row — the row lands in the DB and the caller sees only "Request failed"."""
    value = data.get(field) or None
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise ActionError(f"{field} must be ISO format (YYYY-MM-DD)")


def create_round(user, data):
    title = (data.get("title") or "").strip()
    if not title:
        raise ActionError("title is required")
    categories = data.get("categories") or []
    unknown = set(categories) - VALID_CATEGORIES
    if unknown:
        raise ActionError(f"unknown categories: {sorted(unknown)}")
    return EOIRound.objects.create(
        title=title,
        brief=(data.get("brief") or "").strip(),
        categories=categories,
        opens_at=parse_iso_date(data, "opens_at"),
        closes_at=parse_iso_date(data, "closes_at"),
        created_by=user,
    )


def transition_round(rnd, new_status):
    if new_status not in ROUND_TRANSITIONS.get(rnd.status, set()):
        raise ActionError(f"cannot move a {rnd.status} round to {new_status}")
    rnd.status = new_status
    rnd.save(update_fields=["status"])
    return rnd


def save_submission(org, data):
    """Create or update the org's draft submission for a round."""
    round_id = data.get("round_id")
    try:
        rnd = EOIRound.objects.get(id=round_id)
    except EOIRound.DoesNotExist:
        raise ActionError("round not found")
    if rnd.status != EOIRound.Status.OPEN:
        raise ActionError("round is not open")

    categories = data.get("categories") or []
    unknown = set(categories) - set(rnd.categories)
    if unknown:
        raise ActionError(f"categories not open in this round: {sorted(unknown)}")

    sub, _created = EOISubmission.objects.get_or_create(org=org, round=rnd)
    if sub.status != EOISubmission.Status.DRAFT:
        raise ActionError("submission has already been submitted")
    sub.categories = categories
    sub.commitments = data.get("commitments") or {}
    sub.save(update_fields=["categories", "commitments"])
    return sub


def submit_submission(sub):
    """Freeze the profile and hand the submission to reviewers.

    The snapshot is what reviewers evaluate — later profile edits must never
    change what was assessed.
    """
    if sub.status != EOISubmission.Status.DRAFT:
        raise ActionError("submission has already been submitted")
    if sub.round.status != EOIRound.Status.OPEN:
        raise ActionError("round is not open")
    if not sub.categories:
        raise ActionError("select at least one category before submitting")

    sub.profile_snapshot = org_dict(sub.org, include_qualifications=False)
    sub.status = EOISubmission.Status.SUBMITTED
    sub.submitted_at = timezone.now()
    sub.save(update_fields=["profile_snapshot", "status", "submitted_at"])
    return sub


@transaction.atomic
def review_submission(reviewer, sub, decisions, notes=""):
    """Record per-category decisions and stamp qualifications onto the org."""
    if sub.status != EOISubmission.Status.SUBMITTED:
        raise ActionError("only submitted applications can be reviewed")
    if not decisions:
        raise ActionError("no decisions supplied")
    outside = set(decisions) - set(sub.categories)
    if outside:
        raise ActionError(f"categories not in this submission: {sorted(outside)}")
    bad_values = {v for v in decisions.values()} - {"qualify", "reject"}
    if bad_values:
        raise ActionError(f"invalid decision values: {sorted(bad_values)}")

    EOIReview.objects.create(submission=sub, reviewer=reviewer, decisions=decisions, notes=notes)

    granted = date.today()
    qualified = [cat for cat, verdict in decisions.items() if verdict == "qualify"]
    for category in sorted(qualified):
        Qualification.objects.update_or_create(
            org=sub.org,
            category=category,
            defaults={
                "source_submission": sub,
                "granted_at": granted,
                "expires_at": granted + timedelta(days=QUALIFICATION_TERM_DAYS),
                "status": Qualification.Status.ACTIVE,
            },
        )

    sub.status = EOISubmission.Status.QUALIFIED if qualified else EOISubmission.Status.REJECTED
    sub.save(update_fields=["status"])
    return sub


def live_qualifications(category=None, country=None, expiring_within_days=None):
    """The registry query — only live qualifications ever count."""
    today = date.today()
    qs = Qualification.objects.select_related("org").filter(status=Qualification.Status.ACTIVE, expires_at__gte=today)
    if category:
        qs = qs.filter(category=category)
    if country:
        qs = qs.filter(org__country=country.upper())
    if expiring_within_days:
        qs = qs.filter(expires_at__lte=today + timedelta(days=int(expiring_within_days)))
    return qs.order_by("org__legal_name", "category")
