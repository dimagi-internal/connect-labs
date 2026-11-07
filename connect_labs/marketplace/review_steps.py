"""The steps of a round's Review tab, in order: the one place that says what they are.

A round has two tabs, Overview (who applied, what became of them) and Review.
Review is a loop and then a check:

    Criteria -> AI Review -> AI Analysis -> (revise: back to Criteria) ... -> Human Review

The criteria cannot be judged until something has been scored against them,
so the first criteria are a draft, the AI Review scores everyone on them, and
the AI Analysis is where a person looks at what those scores did. A revision is
a new criteria version, and the loop goes round again. Human Review is people
checking the AI's reviews once the criteria hold.

To add, rename, reorder or drop a step, change `STEPS` and its partial under
`templates/marketplace/review/`. The view (`views.round_review`), the URL
(`rounds/<slug>/review/<step>/`), the step bar and its one-line statuses all
read from here, and `tests/test_review_tabs.py` checks every step renders. A
step's `status` reads only the page context `views.round_review` builds, so a
new step that needs new figures adds them to that context (or to its own
`extra`), not to the template.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field


def _number(version: str) -> str:
    return (version or "").lstrip("v")


def _criteria_status(ctx: dict) -> str:
    round_ = ctx["round"]
    count = len(round_.evaluation_criteria or [])
    if not count:
        return "None yet"
    return f"Version {_number(round_.rubric_version)} · {count} criteri{'on' if count == 1 else 'a'}"


def _ai_review_status(ctx: dict) -> str:
    round_, progress = ctx["round"], ctx["progress"]
    if not round_.evaluation_criteria:
        return "Waits for criteria"
    return f"{progress['ai_on_current']} of {progress['applicants']} on version {_number(round_.rubric_version)}"


def _ai_analysis_status(ctx: dict) -> str:
    if not ctx["progress"]["ai_on_current"]:
        return "Waits for the AI review"
    previous = ctx["previous_version"]
    return f"Compared with version {_number(previous)}" if previous else "First version: nothing to compare yet"


def _human_review_status(ctx: dict) -> str:
    progress = ctx["progress"]
    return f"{progress['people_checked']} of {progress['applicants']} checked"


@dataclass(frozen=True)
class Step:
    key: str  # the URL segment: rounds/<slug>/review/<key>/
    label: str
    template: str  # the partial drawn under the step bar
    status: Callable[[dict], str]  # one line under the label: where this step stands
    hint: str = ""  # the label's tooltip: what the step is for
    # Whose reviews the step's scores read: "ai" for the AI's alone, None for the
    # round's usual reading (a person's review first, else the AI's).
    reviews: str | None = None
    # Figures only this step draws, built from the page context when it is the shown step.
    extra: Callable[[dict], dict] | None = field(default=None, compare=False)


STEPS: tuple[Step, ...] = (
    Step(
        "criteria",
        "Criteria",
        "marketplace/review/criteria.html",
        _criteria_status,
        "What applicants are scored on: the current version, how it was chosen, and every earlier version.",
    ),
    Step(
        "ai-review",
        "AI Review",
        "marketplace/review/ai_review.html",
        _ai_review_status,
        "Every applicant scored on the criteria, with the AI's recommendation.",
        reviews="ai",
    ),
    Step(
        "ai-analysis",
        "AI Analysis",
        "marketplace/review/ai_analysis.html",
        _ai_analysis_status,
        "Whether the criteria are doing their job: how each one scored, and what changed from the last version.",
        reviews="ai",
    ),
    Step(
        "human-review",
        "Human Review",
        "marketplace/review/human_review.html",
        _human_review_status,
        "People checking the AI: who agreed, added to it or disagreed, and who is left.",
    ),
)

BY_KEY = {step.key: step for step in STEPS}


def default_step(ctx: dict) -> Step:
    """The step a bare /review/ opens on: the first one with something still to do."""
    if not ctx["round"].evaluation_criteria:
        return BY_KEY["criteria"]
    progress = ctx["progress"]
    if progress["ai_on_current"] < progress["applicants"] and not progress["people_checked"]:
        return BY_KEY["ai-review"]
    if not progress["people_checked"]:
        return BY_KEY["ai-analysis"]
    return BY_KEY["human-review"]


def tab_bar(ctx: dict, current: Step) -> list[dict]:
    """The step bar: each step with its number, label, status line and whether it is the one shown."""
    return [
        {
            "number": i,
            "key": step.key,
            "label": step.label,
            "hint": step.hint,
            "status": step.status(ctx),
            "current": step.key == current.key,
        }
        for i, step in enumerate(STEPS, 1)
    ]
