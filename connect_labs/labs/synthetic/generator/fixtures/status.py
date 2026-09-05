"""Visit status / flag / review_status distribution.

Inputs:
- the FLW persona's flag rate (baseline likelihood of any visit being flagged)
- whether the visit overlaps a scheduled anomaly (forces a flag)

Outputs a small, JSON-serializable VisitStatus dataclass.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Literal

from .manifest import FlwPersona

# `over_limit` is APPROVED-EQUIVALENT work, not a rejection state: legitimate paid
# visits that a platform accounting glitch mislabels once an opportunity's budget cap
# is hit. Connect's own metrics spec therefore defines valid data as
# `status IN ('approved','over_limit')` and excluding it undercounts visits, started
# cases and weight series by 40-130% depending on the programme. It has to exist in the
# synthetic vocabulary or a clone cannot express that rule at all — every generated
# visit collapses to `approved` and a demo can neither show the rule nor what it fixes.
Status = Literal["approved", "pending", "rejected", "over_limit"]
# NOT a review outcome — a reviewer never sees "over_limit". The work reviewed clean;
# only the payment ledger disagreed, so review_status stays "approved".
ReviewStatus = Literal["approved", "pending", "rejected"]


@dataclass(frozen=True)
class VisitStatus:
    status: Status
    flagged: bool
    flag_reason: str
    review_status: ReviewStatus


_FLAG_REASONS = (
    "GPS outside service area",
    "Form completed in under 30s",
    "Identical photo to previous visit",
    "Beneficiary already visited this week",
    "Anthropometric value outside expected range",
)


def _pick_reason(rng: random.Random, flag_reason_distribution: dict[str, float] | None) -> str:
    """Sample a flag reason from a distribution, or fall back to _FLAG_REASONS if empty/None."""
    if flag_reason_distribution:
        names = sorted(flag_reason_distribution)
        weights = [flag_reason_distribution[n] for n in names]
        return rng.choices(names, weights=weights, k=1)[0]
    return rng.choice(_FLAG_REASONS)


def decide_visit_status(
    *,
    persona: FlwPersona,
    has_anomaly: bool,
    rng: random.Random,
    flag_reason_distribution: dict[str, float] | None = None,
    over_limit_rate: float = 0.0,
) -> VisitStatus:
    """Draw a visit's status.

    `over_limit_rate` relabels a share of the visits that would otherwise be `approved`.
    It is deliberately applied to the APPROVED branch only, and leaves `flagged` False
    and `review_status` "approved": over_limit is good work with a billing-side label,
    not a quality signal, so a flag-driven archetype must not shift when it rises. Many
    opportunities never hit their cap, so 0.0 (no over_limit at all) is the default and
    a legitimate steady state.
    """
    if has_anomaly:
        return VisitStatus(
            status="pending",
            flagged=True,
            flag_reason=_pick_reason(rng, flag_reason_distribution),
            review_status="pending",
        )
    if rng.random() < persona.flag_rate:
        rejected = rng.random() < 0.4
        return VisitStatus(
            status="rejected" if rejected else "pending",
            flagged=True,
            flag_reason=_pick_reason(rng, flag_reason_distribution),
            review_status="rejected" if rejected else "pending",
        )
    over_limit = over_limit_rate > 0 and rng.random() < over_limit_rate
    return VisitStatus(
        status="over_limit" if over_limit else "approved",
        flagged=False,
        flag_reason="",
        review_status="approved",
    )
