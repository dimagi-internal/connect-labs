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


def decide_visit_status(
    *,
    persona: FlwPersona,
    has_anomaly: bool,
    rng: random.Random,
) -> VisitStatus:
    if has_anomaly:
        return VisitStatus(
            status="pending",
            flagged=True,
            flag_reason=rng.choice(_FLAG_REASONS),
            review_status="pending",
        )
    if rng.random() < persona.flag_rate:
        rejected = rng.random() < 0.4
        return VisitStatus(
            status="rejected" if rejected else "pending",
            flagged=True,
            flag_reason=rng.choice(_FLAG_REASONS),
            review_status="rejected" if rejected else "pending",
        )
    return VisitStatus(
        status="approved",
        flagged=False,
        flag_reason="",
        review_status="approved",
    )
