"""Per-visit form_json filling.

For each question in the schema, draws a value from the cohort's field
distribution. Anomalies that name the visit's field path override the
distribution with an outlier (>= 4 sigma).
"""

from __future__ import annotations

import random
from typing import Any

from .manifest import Anomaly, BeneficiaryCohort, NormalDistribution, UniformDistribution
from .schema_loader import FormSchema, QuestionSpec


def _draw(distribution, rng: random.Random) -> float:
    if isinstance(distribution, NormalDistribution):
        return rng.gauss(distribution.mean, distribution.stddev)
    if isinstance(distribution, UniformDistribution):
        return rng.uniform(distribution.low, distribution.high)
    raise TypeError(f"unknown distribution: {distribution!r}")


def _outlier(distribution, rng: random.Random) -> float:
    if isinstance(distribution, NormalDistribution):
        # Always at least 4 sigma off the mean, randomly above or below.
        sign = rng.choice([-1, 1])
        return distribution.mean + sign * (4 + rng.random()) * max(distribution.stddev, 0.01)
    if isinstance(distribution, UniformDistribution):
        return distribution.low - 1 if rng.random() < 0.5 else distribution.high + 1
    raise TypeError(f"unknown distribution: {distribution!r}")


def _default_for_kind(spec: QuestionSpec, rng: random.Random) -> Any:
    if spec.kind in {"select", "multiselect"} and spec.choices:
        return rng.choice(spec.choices)
    if spec.kind == "int":
        return rng.randint(0, 10)
    if spec.kind == "decimal":
        return round(rng.uniform(0, 10), 2)
    if spec.kind == "date":
        return "2026-01-01"
    if spec.kind == "image":
        return ""  # synthetic visits do not produce real images
    return f"sample-{rng.randint(0, 999)}"


def fill_form_json(
    *,
    schema: FormSchema,
    cohort: BeneficiaryCohort,
    anomalies_for_visit: list[Anomaly],
    rng: random.Random,
) -> dict[str, Any]:
    anomaly_paths = {a.field_path for a in anomalies_for_visit if a.field_path}
    out: dict[str, Any] = {}
    for spec in schema.questions:
        dist = cohort.field_distributions.get(spec.json_path)
        if dist is None:
            out[spec.json_path] = _default_for_kind(spec, rng)
            continue
        value = _outlier(dist, rng) if spec.json_path in anomaly_paths else _draw(dist, rng)
        if spec.kind == "int":
            value = int(round(value))
        else:
            value = round(float(value), 3)
        out[spec.json_path] = value
    return out
