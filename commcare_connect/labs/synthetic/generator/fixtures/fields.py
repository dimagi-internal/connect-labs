"""Per-visit form_json filling.

For each question in the schema, draws a value from the cohort's field
distribution. Anomalies that name the visit's field path override the
distribution with an outlier (>= 4 sigma).
"""

from __future__ import annotations

import random
from typing import Any

from .manifest import (
    Anomaly,
    BeneficiaryCohort,
    BinaryDistribution,
    CategoricalDistribution,
    FlwPersona,
    NormalDistribution,
    UniformDistribution,
)
from .schema_loader import FormSchema, QuestionSpec

_OMIT = object()  # sentinel: field rolled its null_rate and should be omitted


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


def _draw(distribution, rng: random.Random, period: int | None = None) -> float:
    if isinstance(distribution, NormalDistribution):
        return rng.gauss(distribution.mean, distribution.stddev)
    if isinstance(distribution, UniformDistribution):
        return rng.uniform(distribution.low, distribution.high)
    if isinstance(distribution, BinaryDistribution):
        return 1.0 if rng.random() < distribution.rate_for_period(period) else 0.0
    raise TypeError(f"unknown distribution: {distribution!r}")


def _outlier(distribution, rng: random.Random) -> float:
    if isinstance(distribution, NormalDistribution):
        # Always at least 4 sigma off the mean, randomly above or below.
        sign = rng.choice([-1, 1])
        return distribution.mean + sign * (4 + rng.random()) * max(distribution.stddev, 0.01)
    if isinstance(distribution, UniformDistribution):
        return distribution.low - 1 if rng.random() < 0.5 else distribution.high + 1
    if isinstance(distribution, BinaryDistribution):
        # An outlier on a binary field is the rare/failed outcome: return the
        # value opposite the expected majority. If success (1.0) is the majority
        # (rate >= 0.5), the outlier is the failure (0.0), and vice versa.
        return 0.0 if distribution.rate >= 0.5 else 1.0
    raise TypeError(f"unknown distribution: {distribution!r}")


def _categorical_value(dist: CategoricalDistribution, rng: random.Random) -> str:
    names = sorted(dist.values)
    weights = [dist.values[n] for n in names]
    return rng.choices(names, weights=weights, k=1)[0]


def _binary_choice(spec: QuestionSpec, raw: float) -> Any:
    """Render a no-transform binary draw on a choice/text question as yes/no.

    A ``BinaryDistribution`` draws 1.0/0.0; on a ``select``/``multiselect``/``text``
    question those floats are the wrong type for the field. Map ``raw >= 0.5`` to
    the affirmative outcome and below to the negative — no inversion, so rate 0.72
    yields ~72% affirmative. Prefer the question's own 2-valued ``choices`` (first
    = affirmative, second = negative); otherwise emit the strings "yes"/"no".
    """
    if spec.choices and len(spec.choices) == 2:
        affirmative, negative = spec.choices[0], spec.choices[1]
    else:
        affirmative, negative = "yes", "no"
    return affirmative if raw >= 0.5 else negative


def _resolve_dist(
    effective: dict[str, Any],
    spec: QuestionSpec,
    *,
    leaf_question_counts: dict[str, int],
) -> tuple[Any, str | None]:
    """Resolve the distribution for a question, exact-match first then leaf-match.

    Returns ``(dist, consumed_key)``. ``consumed_key`` is the effective-map key
    that should be excluded from the trailing orphan-write loop (so a leaf-keyed
    distribution drives its matching question instead of being double-written as
    an orphan float). An exact json_path match is the question's own path and is
    already covered, so it returns ``consumed_key=None``.

    Leaf resolution: if no exact match, look for effective keys whose bare leaf
    equals this question's leaf. Use it ONLY if the mapping is unambiguous on BOTH
    sides — EXACTLY ONE effective key has that leaf AND EXACTLY ONE schema question
    (lacking its own exact key) has that leaf. If either side is ambiguous (zero or
    more than one match), leave ``dist=None`` (unchanged fall-through to default);
    an ambiguous leaf must not guess.
    """
    dist = effective.get(spec.json_path)
    if dist is not None:
        return dist, None

    leaf = spec.json_path.rsplit(".", 1)[-1]
    if leaf_question_counts.get(leaf, 0) != 1:
        # More than one question shares this leaf — ambiguous on the question side.
        return None, None
    matches = [k for k in effective if k.rsplit(".", 1)[-1] == leaf]
    if len(matches) == 1:
        key = matches[0]
        return effective[key], key
    return None, None


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
    persona: FlwPersona | None = None,
    period: int | None = None,
    correlated_values: dict[str, Any] | None = None,
) -> dict[str, Any]:
    anomaly_paths = {a.field_path for a in anomalies_for_visit if a.field_path}
    # Persona overrides take precedence over cohort distributions. Building a
    # merged map once avoids re-checking the persona on every field.
    overrides = persona.field_overrides if persona else {}
    effective: dict[str, Any] = {**cohort.field_distributions, **overrides}

    # Count, per leaf, how many questions would rely on leaf-resolution (i.e. lack
    # their own exact effective-map key). A leaf shared by 2+ such questions is
    # ambiguous and must not be leaf-resolved by any of them.
    leaf_question_counts: dict[str, int] = {}
    for spec in schema.questions:
        if spec.json_path in effective:
            continue
        leaf = spec.json_path.rsplit(".", 1)[-1]
        leaf_question_counts[leaf] = leaf_question_counts.get(leaf, 0) + 1

    correlated = correlated_values or {}
    out: dict[str, Any] = {}
    covered_paths: set[str] = set()
    consumed_keys: set[str] = set()
    for spec in schema.questions:
        covered_paths.add(spec.json_path)
        if spec.json_path in correlated:
            dist = effective.get(spec.json_path)
            if dist is not None and getattr(dist, "null_rate", 0.0) and rng.random() < dist.null_rate:
                continue
            _set_nested(out, spec.json_path, correlated[spec.json_path])
            continue
        dist, consumed_key = _resolve_dist(effective, spec, leaf_question_counts=leaf_question_counts)
        if consumed_key is not None:
            consumed_keys.add(consumed_key)
        if dist is None:
            value = _default_for_kind(spec, rng)
        elif getattr(dist, "null_rate", 0.0) and rng.random() < dist.null_rate:
            continue  # omit this field — matches real missing-data rate
        elif isinstance(dist, CategoricalDistribution):
            value = _categorical_value(dist, rng)
        else:
            raw = _outlier(dist, rng) if spec.json_path in anomaly_paths else _draw(dist, rng, period)
            transform = getattr(dist, "transform", None)
            if transform:
                value = _apply_transform(raw, transform, rng)
            elif spec.kind == "int":
                value = int(round(raw))
            elif isinstance(dist, BinaryDistribution) and spec.kind in {"select", "multiselect", "text"}:
                # A no-transform binary on a choice/text question renders as a
                # yes/no choice, not a float 1.0/0.0 (ace#773).
                value = _binary_choice(spec, raw)
            else:
                value = round(float(raw), 3)
        _set_nested(out, spec.json_path, value)

    # Write values for manifest field_distributions not covered by the HQ schema.
    # This ensures paths like form.case.update.soliciter_muac_cm get populated
    # even when the app structure API returns them under different question IDs.
    # Keys consumed by leaf-resolution above already drive a schema question and
    # must NOT be orphan-written (that would double-write the field).
    for path, dist in effective.items():
        if path in covered_paths or path in consumed_keys:
            continue
        if path in correlated:
            if getattr(dist, "null_rate", 0.0) and rng.random() < dist.null_rate:
                continue
            _set_nested(out, path, correlated[path])
            continue
        if getattr(dist, "null_rate", 0.0) and rng.random() < dist.null_rate:
            continue
        if isinstance(dist, CategoricalDistribution):
            _set_nested(out, path, _categorical_value(dist, rng))
            continue
        raw = _outlier(dist, rng) if path in anomaly_paths else _draw(dist, rng, period)
        transform = getattr(dist, "transform", None)
        value = _apply_transform(raw, transform, rng)
        if isinstance(value, float):
            value = round(value, 3)
        _set_nested(out, path, value)

    return out
