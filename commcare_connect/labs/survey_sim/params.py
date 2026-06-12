"""Parameter contract for the plan-grounded synthetic survey generator.

``SimParams`` is everything the generator needs to turn one plan's sampled work
areas into a representative run of household survey records — quality rates, the
coverage curve, the per-surveyor primary-vs-alternate behaviour, and the GPS
offset model. It is plain data (no I/O), so a caller assembles it from a JSON
config (see ``from_dict``) and the generator stays pure.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PrimaryRate:
    """How often a surveyor completes the **primary** (first-choice) unit rather
    than substituting a ranked **alternate**.

    The rate is drawn once per surveyor (stable within a plan): a flagged
    surveyor uses ``flagged_mean`` (heavy substitution is part of why they're
    flagged); everyone else draws ``clamp(Normal(mean, variance), 0, 1)``.
    """

    mean: float = 0.85
    variance: float = 0.05  # stddev of the per-surveyor normal draw
    flagged_mean: float | None = None
    flagged_id: str | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> PrimaryRate:
        d = d or {}
        return cls(
            mean=float(d.get("mean", 0.85)),
            variance=float(d.get("variance", 0.05)),
            flagged_mean=(None if d.get("flagged_mean") is None else float(d["flagged_mean"])),
            flagged_id=d.get("flagged_id"),
        )


@dataclass
class SimParams:
    """Generation knobs for one plan-arm-round.

    ``arm`` is the arm key stamped on records (e.g. ``"treatment"`` /
    ``"comparison"``) — decided by the caller from the study's arm assignment,
    independent of the plan's own geo arm tag.
    """

    enumerators: list[str]
    coverage_start: float
    coverage_end: float
    round_idx: int = 0
    n_rounds: int = 1
    coverage_noise: float = 0.0
    arm: str = "treatment"
    n_surveys: int | None = None  # cap on completed surveys; default = all primary slots

    primary_rate: PrimaryRate = field(default_factory=PrimaryRate)

    gps_within_15m: float = 0.96
    gps_near_m: tuple = (1.0, 13.0)
    gps_far_m: tuple = (16.0, 55.0)

    evidence_complete: float = 0.97
    field_complete: float = 0.99

    duration: dict = field(default_factory=lambda: {"mean": 18, "sd": 5, "floor": 4, "short_rate": 0.0})
    eligibility: dict = field(
        default_factory=lambda: {"present_rate": 0.99, "age_min_months": 6, "age_max_months": 59}
    )

    # Per-surveyor quality degradation for the flagged surveyor (evidence/gps).
    # The flagged identity is shared with ``primary_rate.flagged_id``.
    flagged: dict | None = None

    @classmethod
    def from_dict(cls, d: dict) -> SimParams:
        """Build from a flat-ish config dict (the demo_config ``quality`` / ``arms``
        / ``eligibility`` shape, plus a ``primary_rate`` block)."""
        return cls(
            enumerators=list(d["enumerators"]),
            coverage_start=float(d["coverage_start"]),
            coverage_end=float(d["coverage_end"]),
            round_idx=int(d.get("round_idx", 0)),
            n_rounds=int(d.get("n_rounds", 1)),
            coverage_noise=float(d.get("coverage_noise", 0.0)),
            arm=d.get("arm", "treatment"),
            n_surveys=d.get("n_surveys"),
            primary_rate=PrimaryRate.from_dict(d.get("primary_rate")),
            gps_within_15m=float(d.get("gps_within_15m", 0.96)),
            gps_near_m=tuple(d.get("gps_near_m", (1.0, 13.0))),
            gps_far_m=tuple(d.get("gps_far_m", (16.0, 55.0))),
            evidence_complete=float(d.get("evidence_complete", 0.97)),
            field_complete=float(d.get("field_complete", 0.99)),
            duration=dict(d.get("duration") or {"mean": 18, "sd": 5, "floor": 4, "short_rate": 0.0}),
            eligibility=dict(
                d.get("eligibility") or {"present_rate": 0.99, "age_min_months": 6, "age_max_months": 59}
            ),
            flagged=d.get("flagged"),
        )

    def surveyor_primary_rate(self, surveyor: str, rng) -> float:
        """Stable per-surveyor primary rate. Call once per surveyor."""
        pr = self.primary_rate
        if surveyor == pr.flagged_id and pr.flagged_mean is not None:
            return _clamp(pr.flagged_mean, 0.0, 1.0)
        return _clamp(rng.gauss(pr.mean, pr.variance), 0.0, 1.0)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
