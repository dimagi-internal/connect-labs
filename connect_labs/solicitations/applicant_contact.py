"""Writing to the organisations that applied to a call, one email per person.

Whoever reviews a directory round (an EOI imported from the LLO Directory)
picks which applicant organisations to write to and writes one message. Labs sends each
person at each chosen organisation their own copy, filled in for them, with
their own invitation link: opening it and accepting makes them a member of the
organisation on labs and takes them to its applications, where they can update
the one to this call while it is open.

Only this page sends: nothing else hands out the links, which are credentials.
Replies go to the address the writer gives (`reply_to`), which need not be
Dimagi's -- whoever runs the call answers its applicants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db.models import Max

from connect_labs.solicitations import access
from connect_labs.solicitations.local_models import ApplicantMessage

#: What a message may name; each is filled in per person. ``{link}`` is added at
#: the end of a message that does not place it.
PLACEHOLDERS = ("first_name", "org_name", "call_title", "deadline", "link")
LINK_SHOWN_IN_RECORD = "[invitation link]"
#: SES takes one message a second from labs; spacing a batch a little wider
#: keeps it from leaning on retries.
SEND_SPACING_SECONDS = 1.2
MAX_SUBJECT = 300
MAX_BODY = 10_000

DEFAULT_SUBJECT = "Your application to {call_title}"
DEFAULT_MESSAGE = (
    "Dear {first_name},\n\n"
    "Thank you for your application to {call_title}. We are looking forward to reviewing it.\n\n"
    "We have added a map where you can click on the regions you are offering to work in. "
    "It is optional, but it helps us. Please use the link below to reach {org_name}'s application "
    "and add the areas if you can, by {deadline}.\n\n"
    "{link}"
)


def can_contact(subject, solicitation) -> bool:
    """Write to a directory round's applicants: whoever reads its responses as its owner.

    Not a call made in labs: its owner scores responses blind, and a page
    listing who applied, by name, would undo that.
    """
    return access.is_directory_round(solicitation) and access.can_read_round_responses(subject, solicitation)


@dataclass
class Person:
    name: str
    email: str

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""


@dataclass
class Applicant:
    """One applicant organisation and the people its submissions named."""

    org: object
    response_ids: list = field(default_factory=list)
    people: list = field(default_factory=list)
    has_areas: bool = False
    last_contacted: object = None
    blocked: str = ""

    @property
    def contactable(self) -> bool:
        return not self.blocked and bool(self.people)


def _split(value) -> list[str]:
    return [p.strip() for p in str(value or "").replace(";", ",").split(",") if p.strip()]


def applicants(solicitation) -> tuple[list[Applicant], list]:
    """The call's applicant organisations, by name, and the submissions matched to none."""
    from connect_labs.labs.synthetic.org_tree import is_labs_only_org_row
    from connect_labs.supply_chain.runner import is_running_org

    contacted = dict(
        ApplicantMessage.objects.filter(solicitation=solicitation)
        .values("org_id")
        .annotate(last=Max("sent_at"))
        .values_list("org_id", "last")
    )
    by_org: dict[int, Applicant] = {}
    unmatched = []
    responses = solicitation.responses.exclude(status="draft").filter(synthetic=False).select_related("llo_entity")
    for response in responses.order_by("pk"):
        org = response.llo_entity
        if org is None:
            unmatched.append(response)
            continue
        entry = by_org.get(org.pk)
        if entry is None:
            entry = by_org[org.pk] = Applicant(org=org, last_contacted=contacted.get(org.pk))
        entry.response_ids.append(response.pk)
        entry.has_areas = entry.has_areas or bool(response.service_areas)
        names = _split(response.submitted_by_name)
        for i, email in enumerate(_split(response.submitted_by_email)):
            if "@" in email and email.lower() not in {p.email.lower() for p in entry.people}:
                entry.people.append(
                    Person(name=names[i] if i < len(names) else (names[0] if names else ""), email=email)
                )
    for entry in by_org.values():
        # Whoever accepts an invitation joins the organisation: never one that
        # runs a programme (or Dimagi) -- support adds those people -- nor an
        # invented one.
        if is_running_org(entry.org) or is_labs_only_org_row(entry.org):
            entry.blocked = "runs a programme on labs; support adds its people"
        elif not entry.people:
            entry.blocked = "no email address on its application"
    return sorted(by_org.values(), key=lambda a: a.org.name.lower()), unmatched


def render(template: str, *, person: Person, org_name: str, solicitation, link: str) -> str:
    """The message as one person reads it. Unknown braces are left as they are."""
    deadline = solicitation.application_deadline.strftime("%-d %B %Y") if solicitation.application_deadline else ""
    values = {
        "first_name": person.first_name or f"{org_name} team",
        "org_name": org_name,
        "call_title": solicitation.title,
        "deadline": deadline or "the deadline",
        "link": link,
    }
    text = template
    for key, value in values.items():
        text = text.replace("{" + key + "}", value)
    return text


def with_link(message: str) -> str:
    """The message, with the link at the end unless it already places it."""
    return message if "{link}" in message else message.rstrip() + "\n\n{link}"


def check(*, subject: str, message: str, reply_to: str) -> dict:
    """Problems with what was written, by field ({} when it is fine)."""
    errors = {}
    if not subject.strip():
        errors["subject"] = "Write a subject."
    elif len(subject) > MAX_SUBJECT:
        errors["subject"] = f"Keep the subject under {MAX_SUBJECT} characters."
    if not message.strip():
        errors["message"] = "Write a message."
    elif len(message) > MAX_BODY:
        errors["message"] = f"Keep the message under {MAX_BODY:,} characters."
    try:
        validate_email(reply_to.strip())
    except ValidationError:
        errors["reply_to"] = "Give the address replies should go to."
    return errors


def send(caller, solicitation, *, org_ids, subject: str, message: str, reply_to: str, link_for) -> dict:
    """Write to everyone named at each chosen applicant organisation.

    `link_for(raw_token)` makes the absolute invitation URL. Returns how many
    messages were queued and to which organisations; refuses outright when
    outbound mail is off here, before any invitation is made.
    """
    from connect_labs.marketplace import membership
    from connect_labs.utils.email import email_enabled, send_labs_email

    caller = access.caller_for(caller)
    if not can_contact(caller, solicitation):
        raise PermissionDenied("Only the organisation running this call can write to its applicants.")
    if not solicitation.can_accept_responses:
        raise ValidationError({"__all__": "This call has closed, so its applications can no longer be updated."})
    errors = check(subject=subject, message=message, reply_to=reply_to)
    if errors:
        raise ValidationError(errors)
    if not email_enabled():
        raise ValidationError({"__all__": "Outbound email is not set up on this labs, so nothing was sent."})
    wanted = {int(i) for i in org_ids}
    chosen = [a for a in applicants(solicitation)[0] if a.org.pk in wanted and a.contactable]
    if not chosen:
        raise ValidationError({"__all__": "Choose at least one organisation to write to."})

    body_template = with_link(message)
    sent, queued_count = [], 0
    for applicant in chosen:
        for person in applicant.people:
            invite, raw = membership.issue_invite(applicant.org, email=person.email[:254], issued_by=caller.user)
            fill = {"person": person, "org_name": applicant.org.name, "solicitation": solicitation}
            queued = (
                send_labs_email(
                    render(subject, link="", **fill).strip(),
                    render(body_template, link=link_for(raw), **fill),
                    [person.email],
                    reply_to=[reply_to.strip()],
                    countdown=queued_count * SEND_SPACING_SECONDS or None,
                )
                is not None
            )
            queued_count += int(queued)
            ApplicantMessage.objects.create(
                solicitation=solicitation,
                org=applicant.org,
                invite=invite,
                to_email=person.email,
                to_name=person.name,
                reply_to=reply_to.strip(),
                subject=render(subject, link="", **fill).strip(),
                body=render(body_template, link=LINK_SHOWN_IN_RECORD, **fill),
                queued=queued,
                sent_by=caller.user,
            )
            sent.append({"org": applicant.org.name, "email": person.email, "queued": queued})
    return {"messages": sent, "queued": queued_count, "orgs": len(chosen)}
