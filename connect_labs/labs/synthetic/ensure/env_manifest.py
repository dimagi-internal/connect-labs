"""Pydantic-validated composite environment manifest for ``ensure_synthetic_data``.

One env manifest per demo declares the full synthetic environment that must
exist on labs before a walkthrough/DDD recorder drives its scenes: the timeline
window, the per-opp data (as references to standard generator ``Manifest``
files), the weekly workflow runs, run-linked audits, coaching tasks, and the
cross-opp rollup. It is the single source of truth for *what must exist*; the
ensure engine (a later task) realizes it idempotently.

This is a NEW, SEPARATE schema from the per-opp generator ``Manifest`` — it
mirrors that model's validation style (pydantic v2, ``from_yaml``, a
``ValueError`` subclass for failures) but does not extend or modify it.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, Field, PositiveInt, ValidationError, field_validator


class EnvManifestError(ValueError):
    """Raised when an env manifest fails schema validation."""


# ---------- Timeline ----------


class Timeline(BaseModel):
    completed_weeks: PositiveInt
    include_current_week: bool = False
    # When set, the window is PINNED to these fixed weeks (the ``completed_weeks``
    # Mondays from here) instead of sliding off *today*. Pin demos that tell a
    # fixed calendar story so a re-seed stays idempotent — a moving window strands
    # already-seeded runs/flags/audits/tasks on the wrong week. Must be a Monday.
    start_monday: dt.date | None = None

    @field_validator("start_monday")
    @classmethod
    def _start_monday_must_be_monday(cls, v: dt.date | None) -> dt.date | None:
        if v is not None and v.weekday() != 0:
            raise ValueError(f"start_monday must be a Monday, got {v.isoformat()} ({v.strftime('%A')})")
        return v


# ---------- Shared sub-parts ----------


class ResetFlag(BaseModel):
    reset: bool = False


# ---------- Resources (discriminated by ``kind``) ----------


class OppDataResource(BaseModel):
    kind: Literal["opp_data"]
    opportunity_id: PositiveInt
    manifest: str


class WeeklyRunsResource(BaseModel):
    kind: Literal["weekly_runs"]
    opportunity_ids: list[PositiveInt]
    template: str
    missed_week_idxs: dict[int, list[int]] = Field(default_factory=dict)
    current_week: ResetFlag | None = None


class RunAuditsResource(BaseModel):
    kind: Literal["run_audits"]
    source: Literal["anomalies"] = "anomalies"


class TasksResource(BaseModel):
    kind: Literal["tasks"]
    source: Literal["coaching_arcs"] = "coaching_arcs"


class RollupResource(BaseModel):
    kind: Literal["rollup"]
    opportunity_ids: list[PositiveInt]
    template: str


class CampaignResource(BaseModel):
    """Realize a Campaign Utility Tool national synthetic campaign (worker cases on
    real Nigeria geography, served via the synthetic CommCare project space).
    Idempotent by ``code`` — the ensurer rebuilds that campaign in place."""

    kind: Literal["campaign"]
    code: str = "MR-NAT-2026"
    name: str = "Measles–Rubella Vaccination Campaign (National)"
    worker_count: PositiveInt = 5000
    states_limit: PositiveInt | None = None


Resource = Annotated[
    Union[
        OppDataResource,
        WeeklyRunsResource,
        RunAuditsResource,
        TasksResource,
        RollupResource,
        CampaignResource,
    ],
    Field(discriminator="kind"),
]


# ---------- Top-level env manifest ----------


class EnvManifest(BaseModel):
    env: str
    timeline: Timeline
    resources: list[Resource] = Field(min_length=1)

    @classmethod
    def from_yaml(cls, source: str | bytes) -> EnvManifest:
        try:
            data = yaml.safe_load(source)
        except yaml.YAMLError as exc:
            raise EnvManifestError(f"YAML parse error: {exc}") from exc
        try:
            return cls.model_validate(data)
        except ValidationError as exc:
            raise EnvManifestError(str(exc)) from exc
