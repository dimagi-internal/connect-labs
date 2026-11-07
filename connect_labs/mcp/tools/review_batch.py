"""Reviews and criteria on a marketplace round, written when a person confirms them.

An agent acting for a visitor through canopy (the delegated grant) may read a round's
answers (``reviews:read``) and propose what to write on it (``marketplace:review``), but
never saves it on its own say-so. It proposes through the same card as a workflow edit
(``workflow_edit_propose`` -> ``workflow_edit_apply``, ``mcp/tools/workflow_edit.py``), naming
one of these two tools:

* ``marketplace_reviews_write``      -- a round's whole batch of reviews, written together.
* ``marketplace_round_criteria_set`` -- registered in ``marketplace.py``; proposable here
                                        so the criteria an agent drafts are saved the same way.
* ``marketplace_round_observations_set`` -- likewise: the AI Observations an agent writes on
                                        a version's scores, from the AI Analysis step.

This module holds the batch tool and what the card shows for each. None of the tools is in a
delegated scope: they run only from the person's Confirm, as the person.

Why a batch, not ``create_review`` per response: a round has dozens of responses, and
labs allows a person 30 writes a minute. The batch is one confirmation and one write
from labs' side, so nothing waits on a rate limit, and every review is checked before
anything is written.
"""

from __future__ import annotations

from django.db import transaction

from ..tool_registry import MCPToolError, get_tool, register

#: The tools an agent may propose for a round. Both are writes the person confirms.
REVIEW_TOOLS: frozenset[str] = frozenset(
    {"marketplace_reviews_write", "marketplace_round_criteria_set", "marketplace_round_observations_set"}
)

#: A round is dozens of responses; this is a ceiling against a runaway payload.
MAX_REVIEWS = 300

#: Outcome keys in the order a person reads them (labs' vocabulary).
_OUTCOME_ORDER = ("pick", "backup", "waitlist", "pass", "")
_OUTCOME_LABELS = {
    "pick": "Recommended",
    "backup": "Recommended backup",
    "waitlist": "Waitlist",
    "pass": "Not recommended",
    "": "No outcome",
}

#: Fields of one review the batch passes on to ``create_review``. Everything else is refused,
#: so a batch cannot reach a field ``create_review`` does not already take.
REVIEW_FIELDS = frozenset(
    {
        "response_id",
        "llo_entity_id",
        "criteria_scores",
        "criteria_notes",
        "claims",
        "outcome",
        "strengths",
        "concerns",
        "ask_next",
        "flags",
        "provenance",
        "notes",
        "score",
        "recommendation",
        "tags",
    }
)


class _Rollback(Exception):
    """Raised inside the dry run to undo what it wrote."""


def _round(user, slug: str):
    from connect_labs.solicitations.data_access import SolicitationsDataAccess
    from connect_labs.solicitations.local_models import Solicitation

    try:
        return SolicitationsDataAccess(user=user)._readable_round(slug)
    except Solicitation.DoesNotExist as e:
        raise MCPToolError("NOT_FOUND", str(e)) from e
    except Exception as e:  # noqa: BLE001 -- PermissionDenied and the like, said plainly
        raise MCPToolError("PERMISSION_DENIED", str(e)) from e


def _check_shape(round_, reviews) -> None:
    if not isinstance(reviews, list) or not reviews:
        raise MCPToolError("INVALID_SCHEMA", "`reviews` must be a non-empty list.")
    if len(reviews) > MAX_REVIEWS:
        raise MCPToolError("INVALID_SCHEMA", f"At most {MAX_REVIEWS} reviews in one batch.")
    seen: set[int] = set()
    mine = set(round_.responses.values_list("pk", flat=True))
    for i, review in enumerate(reviews):
        if not isinstance(review, dict) or not isinstance(review.get("response_id"), int):
            raise MCPToolError("INVALID_SCHEMA", f"reviews[{i}] needs an integer `response_id`.")
        extra = sorted(set(review) - REVIEW_FIELDS)
        if extra:
            raise MCPToolError("INVALID_SCHEMA", f"reviews[{i}] has fields a review does not take: {extra}.")
        rid = review["response_id"]
        if rid not in mine:
            raise MCPToolError("INVALID_SCHEMA", f"reviews[{i}]: response {rid} is not a response to {round_.slug}.")
        if rid in seen:
            raise MCPToolError("INVALID_SCHEMA", f"Response {rid} is reviewed twice in this batch.")
        seen.add(rid)


def _write_all(user, reviews: list[dict]) -> tuple[list[dict], list[dict]]:
    """Write each review as an AI pass by ``user``; returns (written, failed).

    Each goes through ``create_review`` itself, so labs' own checks apply unchanged: the
    reviewer must be allowed to review that response, every quote must be found in its
    answer, flags and outcomes must be labs' own words.
    """
    create = get_tool("create_review").handler
    written: list[dict] = []
    failed: list[dict] = []
    for review in reviews:
        fields = {k: v for k, v in review.items() if k != "llo_entity_id"}
        try:
            with transaction.atomic():
                out = create(
                    user=user,
                    public_record_acknowledged=True,
                    llo_entity_id=str(review.get("llo_entity_id") or ""),
                    kind="ai",
                    **fields,
                )
            written.append({"response_id": review["response_id"], "review_id": out.get("id")})
        except MCPToolError as e:
            failed.append({"response_id": review["response_id"], "error": f"{e.code}: {e.message}"[:600]})
    return written, failed


def dry_run(user, reviews: list[dict]) -> list[dict]:
    """Every review written for real and then undone: what a save would say, with nothing kept.

    Returns the failures. Used by the proposal, so the person is shown only a batch that
    will write -- and re-run by the apply, because the data may have moved since.
    """
    failures: list[dict] = []
    try:
        with transaction.atomic():
            _, failures = _write_all(user, reviews)
            raise _Rollback
    except _Rollback:
        pass
    return failures


@register(
    name="marketplace_reviews_write",
    description=(
        "Write a marketplace round's AI reviews in one go: `reviews` is a list of create_review inputs "
        "(response_id, criteria_scores, claims, outcome, flags, ask_next, provenance), each saved as an AI "
        "review by you, replacing your earlier AI review of that response. Every review is checked as the "
        "save would (quotes found in the answer, labs' flags and outcomes) and a batch with a failure writes "
        "nothing. `summary_url` (https) also links the round's written summary. Through canopy this is not "
        "called directly: propose it with workflow_edit_propose and the person confirms."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "round_slug": {"type": "string", "description": "The round's slug."},
            "reviews": {
                "type": "array",
                "description": "One create_review input per response (public_record_acknowledged is implied).",
                "items": {"type": "object", "additionalProperties": True},
            },
            "summary_url": {"type": "string", "description": "Optional https link to the round's summary report."},
        },
        "required": ["round_slug", "reviews"],
        "additionalProperties": False,
    },
    is_write=True,
)
def marketplace_reviews_write(user, round_slug: str, reviews: list, summary_url: str = "") -> dict:
    round_ = _round(user, round_slug)
    _check_shape(round_, reviews)
    failures = dry_run(user, reviews)
    if failures:
        first = failures[0]
        raise MCPToolError(
            "BAD_REQUEST",
            f"{len(failures)} of {len(reviews)} reviews would be refused, so nothing was written. "
            f"First: response {first['response_id']}: {first['error']}",
        )
    written, failed = _write_all(user, reviews)
    if failed:  # the data moved between the check and the write
        raise MCPToolError("CONFLICT", f"{len(failed)} reviews failed on the write: {failed[0]['error']}")
    out = {"round": round_.slug, "written": len(written)}
    if summary_url:
        link = get_tool("marketplace_round_summary_set").handler(user=user, slug=round_.slug, url=summary_url)
        out["summary_url"] = link["summary_url"]
    return out


def _label(review: dict, names: dict[int, str]) -> str:
    scores = review.get("criteria_scores") or {}
    flags = review.get("flags") or []
    name = names.get(review["response_id"], f"response {review['response_id']}")
    bits = [f"{len(scores)} criteria scored"]
    if flags:
        bits.append(f"{len(flags)} flag{'s' if len(flags) != 1 else ''}")
    bits.append(f"{len(review.get('claims') or [])} claims")
    return f"+ {name} -- " + ", ".join(bits)


def _plus(text: str) -> str:
    """Text as the added lines of a diff."""
    return "\n".join(f"+ {line}" for line in (text or "").strip().splitlines())


def preview(user, tool: str, arguments: dict) -> dict:
    """What the confirm card shows for a proposed round write: a summary and lines per group."""
    if tool == "marketplace_round_criteria_set":
        criteria = arguments.get("criteria") or []
        lines = [f"+ {c.get('name') or c.get('id')} -- weight {c.get('weight')}" for c in criteria]
        return {
            "summary": f"Set {len(criteria)} evaluation criteria on {arguments.get('slug')}",
            "changes": [{"label": "criteria", "diff": "\n".join(lines), "truncated": False}]
            + (
                [{"label": "outcome rules", "diff": _plus(arguments["outcome_rules"]), "truncated": False}]
                if arguments.get("outcome_rules")
                else []
            ),
        }
    if tool == "marketplace_round_observations_set":
        version, against = arguments.get("version", ""), arguments.get("compared_with", "")
        text = arguments.get("observations") or ""
        return {
            "summary": (
                f"{'Write' if text.strip() else 'Clear'} the AI Observations on {arguments.get('slug')} "
                f"version {version.lstrip('v')}"
                + (f", compared with version {against.lstrip('v')}" if against else "")
            ),
            "changes": [{"label": "AI Observations", "diff": _plus(text), "truncated": False}],
        }
    slug = arguments.get("round_slug", "")
    reviews = arguments.get("reviews")
    round_ = _round(user, slug)
    _check_shape(round_, reviews)
    failures = dry_run(user, reviews)
    if failures:
        shown = "; ".join(f"response {f['response_id']}: {f['error']}" for f in failures[:3])
        raise MCPToolError(
            "BAD_REQUEST",
            f"{len(failures)} of {len(reviews)} reviews would be refused, so nothing was proposed. {shown}",
        )
    # A response can arrive with no name of its own (the form's name column left empty); the
    # directory organisation it was matched to names it, else say which response it is.
    names = {
        r.pk: r.org_name or (r.llo_entity.name if r.llo_entity else "") or f"response {r.pk}"
        for r in round_.responses.filter(pk__in=[r["response_id"] for r in reviews]).select_related("llo_entity")
    }
    changes = []
    for key in _OUTCOME_ORDER:
        group = [r for r in reviews if (r.get("outcome") or "") == key]
        if group:
            changes.append(
                {
                    "label": f"{_OUTCOME_LABELS[key]} ({len(group)})",
                    "diff": "\n".join(_label(r, names) for r in group),
                    "truncated": False,
                }
            )
    if arguments.get("summary_url"):
        changes.append({"label": "summary report", "diff": f"+ {arguments['summary_url']}", "truncated": False})
    return {
        "summary": (
            f"Write {len(reviews)} AI reviews on {round_.slug} as you, replacing your earlier AI review of each; "
            "people's reviews stay"
        ),
        "changes": changes,
    }
