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

from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, Field, PositiveInt, ValidationError


class EnvManifestError(ValueError):
    """Raised when an env manifest fails schema validation."""


# ---------- Timeline ----------


class Timeline(BaseModel):
    completed_weeks: PositiveInt
    include_current_week: bool = False


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


Resource = Annotated[
    Union[
        OppDataResource,
        WeeklyRunsResource,
        RunAuditsResource,
        TasksResource,
        RollupResource,
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
