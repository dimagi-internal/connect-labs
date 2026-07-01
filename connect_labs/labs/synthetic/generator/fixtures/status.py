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

Status = Literal["approved", "pending", "rejected"]
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
) -> VisitStatus:
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
    return VisitStatus(
        status="approved",
        flagged=False,
        flag_reason="",
        review_status="approved",
    )
