"""EOI round / submission / review lifecycle and the qualification registry.

Business rules live here; the API modules stay thin.
"""
from datetime import date, timedelta

from django.db import transaction
from django.utils import timezone

from ..models import Category, EOIReview, EOIRound, EOISubmission, Qualification
from ..serializers import org_dict
from .org_actions import ActionError

# The term a pass carries, in CALENDAR months.
#
# It used to be 540 days, described everywhere — including on screen — as "18
# months". Those are different dates: 18 x 30 days from 22 July 2026 is 22
# January 2028, the calendar answer is 31 January. A rule stated in months and
# computed in days is a rule a supplier can dispute, on the one figure that
# decides whether they are in the registry.
QUALIFICATION_TERM_MONTHS = 18

ROUND_TRANSITIONS = {
    EOIRound.Status.DRAFT: {EOIRound.Status.OPEN},
    EOIRound.Status.OPEN: {EOIRound.Status.CLOSED},
    EOIRound.Status.CLOSED: set(),
}

VALID_CATEGORIES = {c.value for c in Category}


def add_months(start, months):
    """``start`` plus ``months`` calendar months, clamped to the month's length.

    31 August + 6 months is 28/29 February, not 3 March. Python has no stdlib
    month arithmetic and this is the only place the app needs it.
    """
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    # The last day of the target month, so 31 -> 30 or 28/29 rather than rolling
    # into the next month.
    if month == 12:
        next_month_first = date(year + 1, 1, 1)
    else:
        next_month_first = date(year, month + 1, 1)
    last_day = (next_month_first - timedelta(days=1)).day
    return date(year, month, min(start.day, last_day))


def earliest_certificate_expiry(snapshot):
    """The soonest certificate expiry in an assessed profile snapshot, or None.

    Read off the SNAPSHOT rather than the live profile: the question is what the
    reviewer was looking at when they granted the pass, and the live profile can
    have moved since (that being the property the freeze exists to protect).
    """
    expiries = []
    for cert in (snapshot or {}).get("certifications") or []:
        raw = cert.get("expiry_date")
        if not raw:
            continue
        try:
            expiries.append(date.fromisoformat(str(raw)[:10]))
        except (TypeError, ValueError):
            continue
    return min(expiries) if expiries else None


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
    expires = add_months(granted, QUALIFICATION_TERM_MONTHS)
    # A pass that outlives a certificate it was granted against carries the date
    # that certificate lapses, so the registry can say so instead of quietly
    # answering "qualified" off expired evidence.
    cert_expiry = earliest_certificate_expiry(sub.profile_snapshot)
    verify_at = cert_expiry if (cert_expiry and cert_expiry < expires) else None
    qualified = [cat for cat, verdict in decisions.items() if verdict == "qualify"]
    for category in sorted(qualified):
        Qualification.objects.update_or_create(
            org=sub.org,
            category=category,
            defaults={
                "source_submission": sub,
                "granted_at": granted,
                "expires_at": expires,
                "verify_at": verify_at,
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
