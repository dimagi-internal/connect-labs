"""Pydantic-validated YAML manifest schema for the synthetic generator.

The manifest is the structured contract between ACE's planning skill and
the engine. It is deterministic given ``random_seed`` and reviewable in
isolation — any opp's fixtures can be regenerated bit-for-bit from its
saved manifest.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    model_validator,
)


class ManifestValidationError(ValueError):
    """Raised when a manifest fails schema validation."""


# ---------- Distributions ----------

class NormalDistribution(BaseModel):
    distribution: Literal["normal"] = "normal"
    mean: float
    stddev: float = Field(ge=0)
    transform: str | None = None


class UniformDistribution(BaseModel):
    distribution: Literal["uniform"]
    low: float
    high: float
    transform: str | None = None

    @model_validator(mode="after")
    def _check_bounds(self):
        if self.high < self.low:
            raise ValueError("uniform high must be >= low")
        return self


FieldDistribution = Annotated[
    NormalDistribution | UniformDistribution,
    Field(discriminator="distribution"),
]


class MeanStddev(BaseModel):
    mean: float
    stddev: float = Field(ge=0)


# ---------- FLW personas ----------

Archetype = Literal["rockstar", "steady", "struggling", "new_hire"]


class ImprovementArc(BaseModel):
    intervention_week: PositiveInt
    post_intervention_lift: float = Field(ge=-1, le=1)


class FlwPersona(BaseModel):
    id: str = Field(pattern=r"^[a-z0-9_]+$")
    display_name: str | None = None
    archetype: Archetype
    accuracy_distribution: MeanStddev
    completeness_distribution: MeanStddev
    flag_rate: float = Field(ge=0, le=1)
    improvement_arc: ImprovementArc | None = None
    notes: str | None = None


# ---------- Beneficiary cohorts ----------

Progression = Literal["improvement_curve", "flat", "regression"]


class BeneficiaryCohort(BaseModel):
    id: str
    size: PositiveInt
    field_distributions: dict[str, FieldDistribution]
    progression: Progression


# ---------- Anomalies ----------

AnomalyType = Literal["field_outlier", "missing_visits", "duplicate_submission"]


class Anomaly(BaseModel):
    id: str
    type: AnomalyType
    flw_ids: list[str]
    field_path: str | None = None
    week: int | None = None
    weeks: list[int] | None = None
    detection_path: str | None = None
    reviewer_visible_in: list[str] = Field(default_factory=list)


# ---------- KPI config ----------

KpiAggregation = Literal["validated_rate", "non_null_rate", "mean", "count"]


class KpiSpec(BaseModel):
    kpi: str
    field_path: str
    aggregation: KpiAggregation
    threshold_underperform: float
    threshold_target: float | None = None


# ---------- Coaching arcs ----------

class CoachingMessage(BaseModel):
    role: Literal["bot", "flw"]
    text: str
    ts: dt.datetime


class CoachingArc(BaseModel):
    flw_id: str
    week_triggered: PositiveInt
    persona: str
    target_behavior: str
    transcript: list[CoachingMessage]
    follow_up_outcome_week: PositiveInt | None = None


# ---------- Timeline ----------

class Timeline(BaseModel):
    start_date: dt.date
    end_date: dt.date
    weeks: PositiveInt
    visit_cadence_per_week_per_flw: MeanStddev

    @model_validator(mode="after")
    def _check_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        span_days = (self.end_date - self.start_date).days
        expected_weeks = round(span_days / 7)
        if abs(self.weeks - expected_weeks) > 1:
            raise ValueError(
                f"weeks={self.weeks} is inconsistent with date range "
                f"({span_days} days = ~{expected_weeks} weeks)"
            )
        return self


# ---------- Top-level manifest ----------

class Manifest(BaseModel):
    opportunity_id: PositiveInt
    opportunity_name: str
    random_seed: NonNegativeInt
    timeline: Timeline
    flw_personas: list[FlwPersona] = Field(min_length=1)
    beneficiary_cohorts: list[BeneficiaryCohort] = Field(min_length=1)
    anomalies: list[Anomaly] = Field(default_factory=list)
    kpi_config: list[KpiSpec] = Field(min_length=1)
    coaching_arcs: list[CoachingArc] = Field(default_factory=list)

    @classmethod
    def from_yaml(cls, source: str | bytes) -> "Manifest":
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise ManifestValidationError(f"YAML parse error: {exc}") from exc
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise ManifestValidationError(str(exc)) from exc

    @model_validator(mode="after")
    def _check_references(self):
        flw_ids = {p.id for p in self.flw_personas}
        for arc in self.coaching_arcs:
            if arc.flw_id not in flw_ids:
                raise ValueError(f"coaching_arc references unknown flw_id={arc.flw_id}")
        for anomaly in self.anomalies:
            unknown = set(anomaly.flw_ids) - flw_ids
            if unknown:
                raise ValueError(f"anomaly {anomaly.id} references unknown flw_ids={unknown}")
        return self
