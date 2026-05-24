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


def _apply_transform(raw: float, transform: str | None, rng: random.Random) -> Any:
    if not transform:
        return raw
    if transform == "round_1":
        return round(raw, 1)
    if transform == "gender":
        return "male" if raw < 0.5 else "female"
    if transform == "boolean_yes":
        return "yes" if raw < 0.5 else "no"
    if transform == "boolean_yes_rare":
        return "yes" if raw < 0.15 else "no"
    return raw


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


def _set_nested(obj: dict, dotted_path: str, value: Any) -> None:
    """Set a value in a nested dict using a dotted path.

    "form.case.update.muac_cm" -> obj["form"]["case"]["update"]["muac_cm"] = value

    If an intermediate node already holds a non-dict value (e.g. a string set by
    a shallower question), it is replaced with a dict so the deeper path can be set.
    """
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        existing = obj.get(part)
        if not isinstance(existing, dict):
            obj[part] = {}
        obj = obj[part]
    obj[parts[-1]] = value


def fill_form_json(
    *,
    schema: FormSchema,
    cohort: BeneficiaryCohort,
    anomalies_for_visit: list[Anomaly],
    rng: random.Random,
) -> dict[str, Any]:
    anomaly_paths = {a.field_path for a in anomalies_for_visit if a.field_path}
    out: dict[str, Any] = {}
    covered_paths: set[str] = set()
    for spec in schema.questions:
        covered_paths.add(spec.json_path)
        dist = cohort.field_distributions.get(spec.json_path)
        if dist is None:
            value = _default_for_kind(spec, rng)
        else:
            raw = _outlier(dist, rng) if spec.json_path in anomaly_paths else _draw(dist, rng)
            transform = getattr(dist, "transform", None)
            if transform:
                value = _apply_transform(raw, transform, rng)
            elif spec.kind == "int":
                value = int(round(raw))
            else:
                value = round(float(raw), 3)
        _set_nested(out, spec.json_path, value)

    # Write values for manifest field_distributions not covered by the HQ schema.
    # This ensures paths like form.case.update.soliciter_muac_cm get populated
    # even when the app structure API returns them under different question IDs.
    for path, dist in cohort.field_distributions.items():
        if path in covered_paths:
            continue
        raw = _outlier(dist, rng) if path in anomaly_paths else _draw(dist, rng)
        transform = getattr(dist, "transform", None)
        value = _apply_transform(raw, transform, rng)
        if isinstance(value, float):
            value = round(value, 3)
        _set_nested(out, path, value)

    return out
