"""Pydantic-validated YAML manifest schema for the synthetic generator.

The manifest is the structured contract between ACE's planning skill and
the engine. It is deterministic given ``random_seed`` and reviewable in
isolation — any opp's fixtures can be regenerated bit-for-bit from its
saved manifest.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, Field, NonNegativeInt, PositiveInt, ValidationError, model_validator


class ManifestValidationError(ValueError):
    """Raised when a manifest fails schema validation."""


# ---------- Distributions ----------


class NormalDistribution(BaseModel):
    distribution: Literal["normal"] = "normal"
    mean: float
    stddev: float = Field(ge=0)
    transform: str | None = None
    null_rate: float = Field(ge=0, le=1, default=0.0)


class UniformDistribution(BaseModel):
    distribution: Literal["uniform"]
    low: float
    high: float
    transform: str | None = None
    null_rate: float = Field(ge=0, le=1, default=0.0)

    @model_validator(mode="after")
    def _check_bounds(self):
        if self.high < self.low:
            raise ValueError("uniform high must be >= low")
        return self


class BinaryDistribution(BaseModel):
    """Draws 1 at ``rate`` (0 otherwise). ``period_rates`` overrides the rate for
    a specific period (week_index) so an outcome can vary round to round — e.g.
    vitamin-A confirmed at 0.52 in round 1 climbing to 0.68 by round 6."""

    distribution: Literal["binary"]
    rate: float = Field(ge=0, le=1)
    period_rates: dict[int, float] = Field(default_factory=dict)
    transform: str | None = None
    null_rate: float = Field(ge=0, le=1, default=0.0)

    @model_validator(mode="after")
    def _check_period_rates(self):
        for period, rate in self.period_rates.items():
            if not 0 <= rate <= 1:
                raise ValueError(f"period_rates[{period}] must be in [0, 1], got {rate}")
        return self

    def rate_for_period(self, period: int | None) -> float:
        if period is None:
            return self.rate
        return self.period_rates.get(period, self.rate)


class CategoricalDistribution(BaseModel):
    """Draw a category by observed frequency. ``values`` maps category -> rate;
    rates need not sum to exactly 1 (they are normalized at draw time)."""

    distribution: Literal["categorical"]
    values: dict[str, float]
    transform: str | None = None
    null_rate: float = Field(ge=0, le=1, default=0.0)

    @model_validator(mode="after")
    def _check_values(self):
        if not self.values:
            raise ValueError("categorical distribution needs at least one value")
        for k, v in self.values.items():
            if v < 0:
                raise ValueError(f"categorical rate for {k!r} must be >= 0, got {v}")
        if sum(self.values.values()) <= 0:
            raise ValueError("categorical rates must sum to > 0")
        return self


FieldDistribution = Annotated[
    NormalDistribution | UniformDistribution | BinaryDistribution | CategoricalDistribution,
    Field(discriminator="distribution"),
]


class CorrelationSpec(BaseModel):
    """Spearman rank-correlation over ``fields``, used to drive a Gaussian copula.
    ``matrix`` is square (len == len(fields)), symmetric, unit diagonal."""

    fields: list[str] = Field(min_length=1)
    matrix: list[list[float]]
    method: Literal["spearman"] = "spearman"

    @model_validator(mode="after")
    def _check_square(self):
        n = len(self.fields)
        if len(self.matrix) != n or any(len(row) != n for row in self.matrix):
            raise ValueError(f"correlation matrix must be {n}x{n} to match fields")
        return self


class TemporalProfile(BaseModel):
    day_of_week: list[float] = Field(min_length=7, max_length=7)
    hour_of_day: list[float] = Field(min_length=24, max_length=24)

    @model_validator(mode="after")
    def _check_nonneg(self):
        if any(w < 0 for w in self.day_of_week) or any(w < 0 for w in self.hour_of_day):
            raise ValueError("temporal weights must be >= 0")
        return self


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
    # Per-FLW overrides for cohort field distributions. The engine merges these
    # on top of ``BeneficiaryCohort.field_distributions`` before drawing a
    # visit's form_json — same field path, different distribution, only for
    # this persona. Use this when a signal should appear concentrated on a
    # single worker (e.g. gender skew that the audit "finds" on one FLW)
    # without bending the rest of the cohort.
    field_overrides: dict[str, FieldDistribution] = Field(default_factory=dict)


# ---------- Beneficiary cohorts ----------

Progression = Literal["improvement_curve", "flat", "regression"]


class BeneficiaryCohort(BaseModel):
    id: str
    size: PositiveInt
    field_distributions: dict[str, FieldDistribution]
    progression: Progression
    correlation: CorrelationSpec | None = None


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


# ---------- Tasks ----------

TaskPriority = Literal["low", "medium", "high"]
TaskStatus = Literal["pending", "in_progress", "completed"]


class TaskSpec(BaseModel):
    flw_id: str
    title: str
    priority: TaskPriority
    status: TaskStatus
    created_week: PositiveInt
    ocs_persona: str | None = None


# ---------- Image config ----------


class ImageConfig(BaseModel):
    question_path: str = "form.muac_group.muac_display_group_1.muac_photo"
    # Legacy uncategorized pool — kept so existing opps with `stock_image_count`
    # alone keep working. Maps to muac_NNN.jpg / synth-muac-NNN.
    stock_image_count: PositiveInt = 15
    probability: float = Field(ge=0, le=1, default=0.85)
    # Two-pool corpus. When good_image_count is set, visits are assigned from
    # the good or bad pool based on per-FLW bad_rate; pool entries map to
    # muac_good_NNN.jpg / muac_bad_NNN.jpg and synth-muac-good-NNN /
    # synth-muac-bad-NNN. When good_image_count is None, the legacy
    # uncategorized pool above is used.
    good_image_count: PositiveInt | None = None
    bad_image_count: PositiveInt | None = None
    default_bad_rate: float = Field(ge=0, le=1, default=0.0)
    flw_bad_rates: dict[str, float] = Field(default_factory=dict)


# ---------- Timeline ----------


class Timeline(BaseModel):
    start_date: dt.date
    end_date: dt.date
    weeks: PositiveInt
    visit_cadence_per_week_per_flw: MeanStddev
    weekly_volume_multipliers: list[float] | None = None

    @model_validator(mode="after")
    def _check_dates(self):
        if self.end_date <= self.start_date:
            raise ValueError("end_date must be after start_date")
        span_days = (self.end_date - self.start_date).days
        expected_weeks = round(span_days / 7)
        if abs(self.weeks - expected_weeks) > 1:
            raise ValueError(
                f"weeks={self.weeks} is inconsistent with date range " f"({span_days} days = ~{expected_weeks} weeks)"
            )
        return self

    @model_validator(mode="after")
    def _check_weekly_multipliers(self):
        if self.weekly_volume_multipliers is not None:
            if len(self.weekly_volume_multipliers) != self.weeks:
                raise ValueError("weekly_volume_multipliers length must equal weeks")
            if any(m < 0 for m in self.weekly_volume_multipliers):
                raise ValueError("weekly_volume_multipliers must be >= 0")
        return self


# ---------- Geography (visit GPS placement) ----------


class Geography(BaseModel):
    """Spread synthetic visit GPS across a real area so the service-delivery
    overlay renders points 'on the ground' instead of blank locations.

    Households are placed in a handful of settlement clusters inside ``polygon``;
    each beneficiary keeps a fixed household location, so repeat visits to the
    same beneficiary stack at the same point (realistic, appropriately spaced).
    The placement is deterministic given the manifest ``random_seed``.
    """

    # GeoJSON geometry (Polygon or MultiPolygon), coordinates in [lon, lat].
    polygon: dict[str, Any]
    # How many settlement clusters to scatter households across the polygon.
    settlements: PositiveInt = 6
    # Village radius (km) — households are gaussian-offset from a settlement center.
    settlement_spread_km: float = Field(gt=0, default=1.2)
    # Reported GPS altitude (m) and accuracy (m) ranges for the packed location string.
    altitude_m: MeanStddev = Field(default_factory=lambda: MeanStddev(mean=480.0, stddev=15.0))
    accuracy_m_min: float = Field(ge=0, default=4.0)
    accuracy_m_max: float = Field(ge=0, default=12.0)

    @model_validator(mode="after")
    def _check_polygon(self):
        gtype = self.polygon.get("type") if isinstance(self.polygon, dict) else None
        if gtype not in ("Polygon", "MultiPolygon"):
            raise ValueError("geography.polygon must be a GeoJSON Polygon or MultiPolygon geometry")
        if not self.polygon.get("coordinates"):
            raise ValueError("geography.polygon is missing coordinates")
        if self.accuracy_m_max < self.accuracy_m_min:
            raise ValueError("geography.accuracy_m_max must be >= accuracy_m_min")
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
    tasks: list[TaskSpec] = Field(default_factory=list)
    image_config: ImageConfig | None = None
    # Optional: place visit GPS across a real area (renders on the delivery overlay).
    geography: Geography | None = None
    temporal: TemporalProfile | None = None
    flag_reason_distribution: dict[str, float] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, source: str | bytes) -> Manifest:
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
        for task in self.tasks:
            if task.flw_id not in flw_ids:
                raise ValueError(f"task references unknown flw_id={task.flw_id}")
        return self
