"""The words a round's reviews are written in: outcomes and flags, each with what it means.

One place, so the review form, the round page, the Scores page and the MCP tools
all say the same thing, and every word a reviewer sees has a definition a click
away. Stored values stay short keys (`pick`, `padded`); what a person reads is
the label and the definition.
"""

from __future__ import annotations

# What a reviewer recommends for one applicant in one round. The keys predate the
# labels ("pick" was the label too) and stay as they are: renaming stored values
# would rewrite every review for a word change.
OUTCOMES = {
    "pick": {
        "label": "Recommended",
        "definition": "Proposed for one of the round's places now.",
    },
    "backup": {
        "label": "Recommended backup",
        "definition": "Ready to step in for a named place if a recommended applicant falls through.",
    },
    "waitlist": {
        "label": "Waitlist",
        "definition": "Credible, but not needed for this round's places. Worth asking again in a later round.",
    },
    "pass": {
        "label": "Not recommended",
        "definition": "Not a fit for this round, for the reasons the review gives.",
    },
}
NO_OUTCOME = {"label": "No recommendation yet", "definition": "Nobody has reviewed it, or the review set no outcome."}
SHORTLIST = ("pick", "backup")

# Things a reviewer should look at before relying on a response. A flag is not a
# verdict and costs no points by itself: the criterion scores do that. Each one
# names something checkable in the response, never a guess about how it was written
# (whether an applicant used AI is not a flag; what the answer contains is).
FLAGS = {
    "unsupported": {
        "label": "Claims to check",
        "definition": "Numbers, partners, licences or past work that look inflated or that nothing in the response or "
        "labs history backs up.",
    },
    "inconsistent": {
        "label": "Answers disagree",
        "definition": "Two answers in the same response contradict each other (dates, numbers, places, staff).",
    },
    "templated": {
        "label": "Shared or template text",
        "definition": "Placeholders left in, text near-identical to another applicant's, or the same response "
        "submitted twice.",
    },
    "padded": {
        "label": "Long but thin",
        "definition": "Answers the question at length with few checkable facts: restates the question, describes "
        "intentions rather than what they know or have done.",
    },
    "incomplete": {
        "label": "Incomplete",
        "definition": "A key answer is missing, cut off or does not address the question asked.",
    },
    "out-of-scope": {
        "label": "Outside the brief",
        "definition": "Proposes places, activities or a scale the call did not ask for.",
    },
    "capacity": {
        "label": "Thin capacity",
        "definition": "The work rests on one person, or on a team already committed elsewhere at the same time.",
    },
    "cost": {
        "label": "Cost out of line",
        "definition": "The quote is far from the others or from the call's budget, or cannot be compared with them.",
    },
    "early-start": {
        "label": "Starts before contract",
        "definition": "Plans to begin work before a contract is in place.",
    },
    "safety": {
        "label": "Safety",
        "definition": "The plan puts staff or respondents at risk (e.g. visits to smelting or scrap sites) without "
        "saying how.",
    },
}

# Free-text flags written before this list existed, read as the flag they meant.
# Reviews keep what was written; pages and tools show the defined flag.
LEGACY_FLAGS = {
    "inflated-numbers": "unsupported",
    "unverified-citations": "unsupported",
    "unsupported-dimagi-claim": "unsupported",
    "inconsistent-facts": "inconsistent",
    "inconsistent-details": "inconsistent",
    "inconsistent-numbers": "inconsistent",
    "template-placeholders": "templated",
    "near-identical-answers": "templated",
    "duplicate": "templated",
    "generic-or-ai-drafted": "padded",
    "truncated-answer": "incomplete",
    "outside-priority-area": "out-of-scope",
    "outside-priority-areas": "out-of-scope",
    "site-outside-priority": "out-of-scope",
    "scope-drift": "out-of-scope",
    "scope-drift-collection": "out-of-scope",
    "multi-site-scope": "out-of-scope",
    "single-person-capacity": "capacity",
    "concurrent-work-order": "capacity",
    "thin-sampling-plan": "incomplete",
    "cost-outlier": "cost",
    "start-before-contract": "early-start",
    "start-before-award": "early-start",
    "concurrent-dimagi-work": "capacity",
    "timeline-cost-mismatch": "inconsistent",
    "registration-year-mismatch": "inconsistent",
    "unverified-connect-claim": "unsupported",
    "multi-state-spread": "out-of-scope",
    "cost-no-currency": "cost",
    "cost-unclear": "cost",
    "no-cost": "cost",
    "no-cost-figure": "cost",
    "incomplete-answers": "incomplete",
    "thin-answers": "incomplete",
    "no-geography": "incomplete",
    "no-named-site": "incomplete",
}


def flag_key(raw: str) -> str | None:
    """The defined flag a written one means, or None when it is not one of them."""
    value = str(raw or "").strip().lower()
    if value in FLAGS:
        return value
    return LEGACY_FLAGS.get(value)


def outcome_label(key: str) -> str:
    return OUTCOMES.get(key, NO_OUTCOME)["label"]
