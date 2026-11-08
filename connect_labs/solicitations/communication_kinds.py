"""What solicitations writes: a directory round's applicants, each with their own link.

Whoever reviews a directory round (an EOI imported from the LLO Directory)
writes one message to chosen applicant organisations. Each person named on an
organisation's application gets their own copy with their own invitation link
(`{link}`): accepting it makes them a member of the organisation on labs and
takes them to its applications, where they can update this one while the
round is open. The link is made only when the message is really sent, and the
stored copy says ``[invitation link]`` instead: it is a credential.

Not a call made in labs: its owner scores responses blind, and a message
listing who applied, by name, would undo that.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.core.exceptions import ValidationError
from django.urls import reverse

from connect_labs.communications import kinds
from connect_labs.communications.kinds import DraftSpec, Filled, RecipientSpec
from connect_labs.solicitations import access

LINK_SHOWN_IN_RECORD = "[invitation link]"

DEFAULT_SUBJECT = "Your application to {call_title}"
DEFAULT_BODY = (
    "Dear {first_name},\n\n"
    "Thank you for your application to {call_title}. We are looking forward to reviewing it.\n\n"
    "We have added a map where you can click on the regions you are offering to work in. "
    "It is optional, but it helps us. Please use the link below to reach {org_name}'s application "
    "and add the areas if you can, by {deadline}.\n\n"
    "{link}"
)


@dataclass
class Applicant:
    """One applicant organisation and the people its submissions named."""

    org: object
    response_ids: list = field(default_factory=list)
    people: list = field(default_factory=list)  # [(name, email)]
    has_areas: bool = False
    blocked: str = ""


def _split(value) -> list[str]:
    return [p.strip() for p in str(value or "").replace(";", ",").split(",") if p.strip()]


def applicants(solicitation) -> tuple[list[Applicant], list]:
    """The round's applicant organisations, by name, and the submissions matched to none."""
    from connect_labs.labs.synthetic.org_tree import is_labs_only_org_row
    from connect_labs.supply_chain.runner import is_running_org

    by_org: dict[int, Applicant] = {}
    unmatched = []
    responses = solicitation.responses.exclude(status="draft").filter(synthetic=False).select_related("llo_entity")
    for response in responses.order_by("pk"):
        org = response.llo_entity
        if org is None:
            unmatched.append(response)
            continue
        entry = by_org.setdefault(org.pk, Applicant(org=org))
        entry.response_ids.append(response.pk)
        entry.has_areas = entry.has_areas or bool(response.service_areas)
        names = _split(response.submitted_by_name)
        for i, email in enumerate(_split(response.submitted_by_email)):
            if "@" in email and email.lower() not in {e.lower() for _, e in entry.people}:
                entry.people.append((names[i] if i < len(names) else (names[0] if names else ""), email))
    for entry in by_org.values():
        # Whoever accepts an invitation joins the organisation: never one that
        # runs a programme (or Dimagi) -- support adds those people -- nor an
        # invented one.
        if is_running_org(entry.org) or is_labs_only_org_row(entry.org):
            entry.blocked = "runs a programme on labs; support adds its people"
    return sorted(by_org.values(), key=lambda a: a.org.name.lower()), unmatched


class RoundApplicantsKind(kinds.Kind):
    key = "solicitations.round_applicants"
    label = "Message to a round's applicants"
    about_model = "solicitations.solicitation"
    placeholders = ("first_name", "name", "org_name", "call_title", "deadline", "link")
    # Each copy carries an invitation into the recipient's organisation: only
    # the people the application names, at the addresses it gives.
    recipients_editable = False
    emails_editable = False
    description = (
        "A message to the organisations that applied to a directory EOI round, each person with their own "
        "link to sign in, join their organisation on labs and update their application while the round is "
        "open. `about_id` is the round's id. Pass `org_slugs` in params to address only those organisations; "
        "otherwise every applicant organisation that can be invited is included. Only the round's reviewers "
        "prepare and send it."
    )

    def resolve(self, about_id):
        from connect_labs.solicitations.data_access import by_public_id
        from connect_labs.solicitations.local_models import Solicitation

        return by_public_id(Solicitation.objects.directory_rounds().select_related("owner_org"), about_id)

    def about_label(self, about) -> str:
        return about.title if about is not None else ""

    def about_url(self, about) -> str:
        return reverse("marketplace:round", args=[about.slug]) if about is not None else ""

    def can_send(self, caller, about, communication=None) -> bool:
        return (
            about is not None and access.is_directory_round(about) and access.can_read_round_responses(caller, about)
        )

    def new_draft(self, caller, about, params: dict) -> DraftSpec:
        wanted = set(params.get("org_slugs") or [])
        orgs, unmatched = applicants(about)
        unknown = wanted - {a.org.slug for a in orgs}
        if unknown:
            raise ValidationError({"org_slugs": f"not applicants to this round: {', '.join(sorted(unknown))}"})
        recipients = []
        for applicant in orgs:
            chosen = (not wanted or applicant.org.slug in wanted) and not applicant.blocked
            if not applicant.people:
                recipients.append(
                    RecipientSpec(
                        org=applicant.org,
                        label=applicant.org.name,
                        included=False,
                        ref={"note": "no email address on its application"},
                    )
                )
            for name, email in applicant.people:
                recipients.append(
                    RecipientSpec(
                        email=email,
                        name=name,
                        org=applicant.org,
                        label=applicant.org.name,
                        included=chosen,
                        ref=(
                            {"blocked": applicant.blocked} if applicant.blocked else {"has_areas": applicant.has_areas}
                        ),
                    )
                )
        return DraftSpec(
            title=f"To applicants: {about.title}",
            subject=DEFAULT_SUBJECT,
            body=DEFAULT_BODY,
            recipients=recipients,
            owner_org=about.owner_org,
            context={"unmatched": [r.org_name or r.llo_entity_name for r in unmatched]},
        )

    def _values(self, recipient, about) -> dict:
        org_name = recipient.org.name if recipient.org_id else ""
        deadline = about.application_deadline.strftime("%-d %B %Y") if about.application_deadline else ""
        return {
            "first_name": recipient.first_name or (f"{org_name} team" if org_name else "colleague"),
            "name": recipient.name or org_name,
            "org_name": org_name,
            "call_title": about.title,
            "deadline": deadline or "the deadline",
        }

    def fill(self, communication, recipient, about, *, sending: bool, caller) -> Filled:
        values = self._values(recipient, about)
        recorded = {**values, "link": LINK_SHOWN_IN_RECORD}
        if not sending:
            return Filled(values=recorded, recorded=recorded)
        from connect_labs.marketplace import membership

        invite, raw = membership.issue_invite(recipient.org, email=recipient.email[:254], issued_by=caller.user)
        path = reverse("solicitations:join_link", args=[raw])
        link = caller.request.build_absolute_uri(path) if caller.request is not None else path
        return Filled(values={**values, "link": link}, recorded=recorded, effects={"invite_id": invite.pk})

    def before_send(self, communication, about, caller) -> None:
        if not about.can_accept_responses:
            raise ValidationError({"__all__": "This round has closed, so its applications can no longer be updated."})
        included = list(communication.recipients.filter(included=True))
        # Checked again now, from the applications themselves, not from the draft:
        # an invitation goes only to an address an applicant organisation gave.
        orgs, _ = applicants(about)
        allowed = {(a.org.pk, email.lower()): a for a in orgs for _, email in a.people}
        refused = []
        for r in included:
            applicant = allowed.get((r.org_id, (r.email or "").lower()))
            if applicant is None or applicant.blocked:
                refused.append(r.label or r.email or r.name)
            elif r.cc:
                # The copy carries an invitation link: nobody else may read it.
                refused.append(f"{r.label or r.email} (copied to {', '.join(r.cc)})")
        if refused:
            raise ValidationError(
                {
                    "recipients": "These can't be invited from here (not an address an applicant gave, or an "
                    "organisation whose people support adds): " + ", ".join(refused) + "."
                }
            )
        if "{link}" not in communication.body and not all("{link}" in (r.body or "") for r in included):
            communication.body = communication.body.rstrip() + "\n\n{link}"
            communication.save(update_fields=["body"])


kinds.register(RoundApplicantsKind())


class SolicitationNoticeKind(kinds.Kind):
    """Mail labs sends on its own about a call: a submission receipt, an award notice.

    Recorded (`service.record_automatic`), never drafted or sent from the outbox.
    """

    key = "solicitations.notice"
    label = "Automatic notice about a call"
    about_model = "solicitations.solicitation"

    def can_send(self, caller, about, communication=None) -> bool:
        return False


kinds.register(SolicitationNoticeKind())
