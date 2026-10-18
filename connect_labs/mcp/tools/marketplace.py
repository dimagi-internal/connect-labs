"""marketplace_* — the organisation directory and the rounds it answers.

These exist so an agent looking at a marketplace page can read the rows behind
it. The page declares only a SELECTION (slugs plus the tool that resolves them),
and the agent calls in here for the substance — so one place decides what a row
says, and the copy on the screen cannot go stale between render and use.

A round's submissions are not "anyone may read it": who answered a round and
what they said are what organisations told the round's owner, so only its
members and platform support read them (``access.can_read_round_responses``).
That covers ``marketplace_round_responses``, ``include_applicants`` on
``marketplace_rounds_list`` and the ``applied_programs`` an org row carries.

Read-only, all of it. Writing to the directory is the LLO Directory sheet's job
(``marketplace_import``), and a tool that wrote here would be a second source of
truth for rows a human maintains.

**These return contact details — real people's names, addresses and phone
numbers.** That is deliberate: drafting an outreach email per organisation is
what the directory is for, and every labs user can already read the same fields
on ``/labs/marketplace/org/<slug>/``. Two consequences worth holding onto:

* They land in an agent transcript that persists. Nothing here should grow a
  field the org page does not already show.
* ``contacts`` is opt-in per call (``include_contacts``), so the common
  "what is in this round" question does not drag PII through a transcript that
  had no use for it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import programs, queries
from connect_labs.marketplace.responses import Question, detect_columns

from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

#: One page of the directory. The whole network is a few hundred rows, so this is
#: a guard against an accidental full dump into a transcript rather than a real
#: paging scheme.
MAX_ORGS = 100


def _contact(contact) -> dict[str, Any]:
    return {
        "name": contact.full_name,
        "role": contact.role_title,
        "email": contact.email,
        "is_main_poc": contact.is_main_poc,
    }


def _org(org, *, delivered_by_name, readable, include_contacts: bool) -> dict[str, Any]:
    profile = getattr(org, "marketplace_profile", None)
    out = {
        "slug": org.slug,
        "name": org.name,
        "short_name": org.short_name or "",
        "country": org.country or "",
        # What they have DELIVERED on Connect, and what they have asked to
        # deliver. The gap between the two is the thing a person scanning this
        # page is looking for, so an agent should see it the same way.
        "delivered_programs": sorted(p["label"] for p in programs.chips(delivered_by_name.get(org.name, ()))),
        "applied_programs": sorted(p["label"] for p in programs.chips(queries.applied_programs_of(org, readable))),
    }
    if profile is not None:
        out.update(
            {
                "countries": list(profile.countries or []),
                "regions": list(profile.regions or []),
                "website": profile.website or "",
                "year_established": profile.year_established,
                "team_size": profile.team_size,
                "has_used_connect": profile.has_used_connect,
                "joined_at": profile.joined_at.isoformat() if profile.joined_at else None,
                # The basis travels with the date because they are not
                # equivalent: some are an exact submission date and some a
                # cohort's publication date shared by everyone in it.
                "joined_basis": profile.joined_basis or "",
                "notes": profile.notes or "",
            }
        )
    if include_contacts:
        out["contacts"] = [_contact(c) for c in org.contacts.all()]
    return out


@register(
    name="marketplace_orgs_get",
    description=(
        "Read organisations from the labs marketplace directory (the LLO "
        "Directory), by slug. This is the tool the marketplace network page "
        "names as its `backing_tool`: when a page has declared "
        "`labs-marketplace://orgs` with a list of visible_ids, pass those same "
        "slugs here to read the rows the person is looking at. Returns each "
        "org's profile, the programs it has delivered on Connect and the ones "
        "it applied to, and — only when `include_contacts` is true — its named "
        "contacts with email addresses, for drafting outreach. Pass "
        "`include_contacts` only when you are actually going to write to them: "
        "the addresses are real people's and they persist in this transcript. "
        "Read-only; the directory is maintained in the source sheet and "
        "imported by `marketplace_import`."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slugs": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Org slugs to read, normally the `visible_ids` from the page's "
                    f"declared state. At most {MAX_ORGS} per call."
                ),
            },
            "include_contacts": {
                "type": "boolean",
                "description": (
                    "Include each org's named contacts (name, role, email). Defaults "
                    "to false — ask for them only when the task is to contact people."
                ),
            },
        },
        "required": ["slugs"],
        "additionalProperties": False,
    },
)
def marketplace_orgs_get(user, slugs: list[str], include_contacts: bool = False) -> dict[str, Any]:
    if not slugs:
        raise MCPToolError("INVALID_ARGUMENT", "Pass at least one org slug.")
    if len(slugs) > MAX_ORGS:
        raise MCPToolError(
            "INVALID_ARGUMENT",
            f"{len(slugs)} slugs is more than this tool returns at once ({MAX_ORGS}). "
            "Narrow the page's filters, or ask for the slugs you actually need.",
        )

    wanted = [str(s).strip() for s in slugs if str(s).strip()]
    rows = LabsOrg.objects.filter(slug__in=wanted).select_related("marketplace_profile").prefetch_related("contacts")
    by_slug = {org.slug: org for org in rows}
    from connect_labs.solicitations import access

    delivered_by_name = queries.delivered_programs_by_org_name()
    readable = access.readable_round_ids(user)

    # Slugs that matched nothing are reported rather than dropped: a page and the
    # directory can disagree after an import, and an agent silently drafting
    # nine emails when it was asked for ten is the bad failure here.
    found = [
        _org(by_slug[s], delivered_by_name=delivered_by_name, readable=readable, include_contacts=include_contacts)
        for s in wanted
        if s in by_slug
    ]
    missing = [s for s in wanted if s not in by_slug]
    return {"organizations": found, "count": len(found), "not_found": missing}


@register(
    name="marketplace_rounds_list",
    description=(
        "List the EOI and RFP rounds in the labs marketplace — what was asked, "
        "when it opened and closed, and how many organisations answered. Pass "
        "`slug` for one round, and `include_applicants` to get the "
        "organisations that answered it with what became of each (whether they "
        "went on to deliver). This is how 'draft something for each org in THIS "
        "EOI' resolves the round the person is looking at: the round page "
        "declares `labs-marketplace://rounds/<slug>`. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slug": {
                "type": "string",
                "description": "One round, by slug. Omit to list them all.",
            },
            "open_only": {
                "type": "boolean",
                "description": "Only rounds still open for submissions.",
            },
            "include_applicants": {
                "type": "boolean",
                "description": (
                    "Include the organisations that answered each round, with the "
                    "outcome for each. Requires `slug` — it is a lot of rows "
                    "otherwise, and no question needs every applicant of every round."
                ),
            },
        },
        "additionalProperties": False,
    },
)
def marketplace_rounds_list(
    user,
    slug: str = None,
    open_only: bool = False,
    include_applicants: bool = False,
) -> dict[str, Any]:
    if include_applicants and not slug:
        raise MCPToolError(
            "INVALID_ARGUMENT",
            "include_applicants needs a `slug`: every applicant of every round is "
            "more rows than any question wants.",
        )

    # `open_rounds()` rather than a status filter: a round's stored status is
    # typed by hand and goes stale the day its deadline passes, so the dates
    # decide. Reusing the page's own helper keeps the two from disagreeing.
    rounds = queries.open_rounds() if open_only else queries.rounds_with_counts()
    if slug:
        rounds = rounds.filter(slug=slug)
    if include_applicants:
        from connect_labs.solicitations import access

        round_ = rounds.select_related("owner_org").first()
        if round_ is not None and not access.can_read_round_responses(user, round_):
            raise MCPToolError(
                "PERMISSION_DENIED",
                f"Only members of the organisation that ran {slug!r} "
                f"({round_.owner_name or 'its owner'}) may see who applied to it.",
            )

    out = []
    first_service = queries.first_service_by_org_name() if include_applicants else {}
    for round_ in rounds:
        row = {
            "slug": round_.slug,
            "title": round_.title,
            "type": round_.get_solicitation_type_display(),
            "status": round_.status,
            "published_on": round_.published_on.isoformat() if round_.published_on else None,
            "application_deadline": (round_.application_deadline.isoformat() if round_.application_deadline else None),
            "decision_on": round_.decision_on.isoformat() if round_.decision_on else None,
            "scope_of_work": round_.scope_of_work or "",
            "description": round_.description or "",
            "contact_email": round_.contact_email or "",
            "target_countries": round_.target_countries or "",
            # `applications` counts submissions; `organisations` counts
            # applicants, counting each unattributed submission separately
            # because assuming two of them are the same org is the judgement
            # being deferred. `unresolved` says how many await that verdict.
            "applications": round_.applications,
            "organisations": round_.organisations,
            "unresolved": round_.unresolved,
            "questions": list(round_.questions or []),
        }
        if include_applicants:
            row["applicants"] = [
                {
                    "slug": a["org"].slug if a["org"] else None,
                    "name": a["name"],
                    "country": a["country"] or "",
                    # after / before / never / unresolved — what became of them,
                    # not merely that they applied.
                    "outcome": a["outcome"],
                    "submissions": a["submissions"],
                }
                for a in queries.round_applicants(round_, first_service)
            ]
        out.append(row)

    if slug and not out:
        raise MCPToolError("NOT_FOUND", f"No marketplace round with slug {slug!r}.")
    return {"rounds": out, "count": len(out)}


#: One round's submissions. The largest round (CHC 2025) has a little over a
#: hundred, so a single page covers every round there is.
MAX_RESPONSES = 200

# Personal fields the column detector does not name: a phone number, and the
# declaration's "Name, Role, and Date" signature line.
_PERSONAL_HEADER = re.compile(r"\b(phone|telephone|whatsapp|mobile)\b|^name,? (and )?(role|title)|signature", re.I)


def _personal_question_ids(questions: list[dict], column_map: dict) -> set[str]:
    """The answers that are a person's contact details rather than the organisation's."""
    parsed = [Question(id=q["id"], text=q.get("text", ""), column=q.get("column", 0)) for q in questions]
    found = detect_columns(parsed, column_map or None)
    ids = {found[f] for f in ("email", "contact_name") if f in found}
    return ids | {q.id for q in parsed if _PERSONAL_HEADER.search(q.text.strip())}


@register(
    name="marketplace_round_responses",
    description=(
        "Read every submission to one marketplace EOI/RFP round: each applicant's "
        "answers to every question, in the form's order, with the directory "
        "organisation it was matched to (slug, or null when unmatched). Use it to "
        "summarise a round, compare applicants and recommend a shortlist; pair it "
        "with `marketplace_orgs_get` on the matched slugs for each organisation's "
        "history (what it applied to before, what it delivered). Only the round's "
        "owner (Dimagi) and platform support may read it. The contact person's "
        "name, email, phone and signature are left out unless `include_contacts` "
        "is true — ask for them only when you are going to write to them. Read-only."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "The round's slug, from `marketplace_rounds_list`."},
            "include_contacts": {
                "type": "boolean",
                "description": "Include each submission's contact name, emails, phone and signature line.",
            },
        },
        "required": ["slug"],
        "additionalProperties": False,
    },
)
def marketplace_round_responses(user, slug: str, include_contacts: bool = False) -> dict[str, Any]:
    from connect_labs.solicitations import access
    from connect_labs.solicitations.local_models import Solicitation

    round_ = Solicitation.objects.directory_rounds().select_related("owner_org").filter(slug=slug).first()
    if round_ is None:
        raise MCPToolError("NOT_FOUND", f"No marketplace round with slug {slug!r}.")
    if not access.can_read_round_responses(user, round_):
        raise MCPToolError(
            "PERMISSION_DENIED",
            f"Only members of the organisation that ran {slug!r} "
            f"({round_.owner_name or 'its owner'}) may read its responses.",
        )

    questions = list(round_.questions or [])
    personal = set() if include_contacts else _personal_question_ids(questions, round_.column_map)
    shown = [q for q in questions if q["id"] not in personal]

    rows = round_.responses.select_related("llo_entity").order_by("source_row")
    total = rows.count()
    out = []
    for response in rows[:MAX_RESPONSES]:
        answers = response.responses or {}
        row = {
            # What `create_review` and `list_reviews` take.
            "response_id": response.pk,
            "source_row": response.source_row,
            "org_name": response.org_name,
            # The directory organisation, only where the import was certain;
            # an unmatched submission says so rather than borrowing a near name.
            "org_slug": response.llo_entity.slug if response.llo_entity_id else None,
            "match_state": response.match_state,
            "submitted_at": response.submission_date.isoformat() if response.submission_date else None,
            "country": response.country_as_submitted,
            "website": response.website_as_submitted,
            # `question_id` is what a review's evidence cites (`create_review` claims).
            "answers": [
                {"question_id": q["id"], "question": q["text"], "answer": answers.get(q["id"], "")} for q in shown
            ],
        }
        if include_contacts:
            row["contact"] = {"name": response.submitted_by_name, "emails": response.submitted_by_email}
        out.append(row)

    return {
        "round": {
            "slug": round_.slug,
            "title": round_.title,
            "type": round_.get_solicitation_type_display(),
            "owner": round_.owner_name,
            "application_deadline": (round_.application_deadline.isoformat() if round_.application_deadline else None),
            "target_countries": round_.target_countries or "",
            "announcement_url": round_.announcement_url or "",
            "last_ingested_at": round_.last_ingested_at.isoformat() if round_.last_ingested_at else None,
            "evaluation_criteria": round_.evaluation_criteria or [],
            "rubric_version": round_.rubric_version or "",
            "criteria_basis": round_.criteria_basis or "",
            "questions_withheld": len(questions) - len(shown),
        },
        "responses": out,
        "count": len(out),
        "total": total,
        "truncated": total > len(out),
    }


@register(
    name="marketplace_round_criteria_set",
    description=(
        "Set (replace) the evaluation criteria for one marketplace EOI/RFP round. A round's form "
        "went out without published criteria, so they are written after reading its submissions. "
        "Each criterion is {id, name, weight, scoring_guide, rationale}: `id` a short snake_case "
        "slug, `weight` a percentage (all weights sum to 100), `scoring_guide` the anchors for a "
        "1-10 score, `rationale` why it separates this pool of applicants. `basis` says how the "
        "criteria were derived and what was considered and dropped. Every replacement bumps "
        "`rubric_version`; reviews keep the version they scored against. Only members of the "
        "organisation that ran the round may set them. Get the requester's approval first."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "slug": {"type": "string", "description": "The round's slug, from `marketplace_rounds_list`."},
            "criteria": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "weight": {"type": "number"},
                        "scoring_guide": {"type": "string"},
                        "rationale": {"type": "string"},
                    },
                    "required": ["id", "name", "weight"],
                },
            },
            "basis": {"type": "string", "description": "How the criteria were arrived at."},
        },
        "required": ["slug", "criteria"],
        "additionalProperties": False,
    },
    is_write=True,
)
def marketplace_round_criteria_set(user, slug: str, criteria: list, basis: str = "") -> dict[str, Any]:
    from django.core.exceptions import PermissionDenied, ValidationError

    from connect_labs.solicitations.data_access import SolicitationsDataAccess
    from connect_labs.solicitations.local_models import Solicitation

    try:
        round_ = SolicitationsDataAccess(user=user).set_round_criteria(slug, criteria, basis)
    except Solicitation.DoesNotExist as e:
        raise MCPToolError("NOT_FOUND", str(e)) from e
    except PermissionDenied as e:
        raise MCPToolError("PERMISSION_DENIED", str(e)) from e
    except ValidationError as e:
        raise MCPToolError("BAD_REQUEST", "; ".join(e.messages)) from e
    return {
        "slug": round_.slug,
        "rubric_version": round_.rubric_version,
        "evaluation_criteria": round_.evaluation_criteria,
        "criteria_basis": round_.criteria_basis,
    }
