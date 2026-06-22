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


def _clamp_normal(raw: float, distribution: NormalDistribution) -> float:
    """Clamp a draw to the distribution's observed [lo, hi] bounds (if set), so an
    unbounded Normal can't emit impossible values (negative ages, bad vitals)."""
    if distribution.lo is not None and raw < distribution.lo:
        return distribution.lo
    if distribution.hi is not None and raw > distribution.hi:
        return distribution.hi
    return raw


def _draw(distribution, rng: random.Random, period: int | None = None) -> float:
    if isinstance(distribution, NormalDistribution):
        return _clamp_normal(rng.gauss(distribution.mean, distribution.stddev), distribution)
    if isinstance(distribution, UniformDistribution):
        return rng.uniform(distribution.low, distribution.high)
    if isinstance(distribution, BinaryDistribution):
        return 1.0 if rng.random() < distribution.rate_for_period(period) else 0.0
    raise TypeError(f"unknown distribution: {distribution!r}")


def _outlier(distribution, rng: random.Random) -> float:
    if isinstance(distribution, NormalDistribution):
        # Always at least 4 sigma off the mean, randomly above or below.
        sign = rng.choice([-1, 1])
        val = distribution.mean + sign * (4 + rng.random()) * max(distribution.stddev, 0.01)
        # A seeded outlier may exceed the normal range, but must not flip sign on a
        # field whose observed minimum is non-negative (no negative-age "outliers").
        if distribution.lo is not None and distribution.lo >= 0:
            val = max(val, 0.0)
        return val
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


# Small fabrication pools for text fields the profiler can't distribution (names,
# phones, places). Deterministic given the visit rng. No PII — invented values.
# Location-ish pools are intentionally small so a group-by on a real categorical
# dimension (country/village) yields a handful of groups, not 300 unique stubs.
_FIRST_NAMES = (
    "Amina Grace John Mary Joseph Fatima Peter Esther David Ruth Samuel Aisha "
    "Daniel Sarah Ibrahim Janet Emmanuel Hawa Moses Halima"
).split()
_LAST_NAMES = (
    "Okeke Mwangi Abubakar Otieno Bello Achieng Musa Wanjiru Adeyemi Kamau Sani " "Njoroge Yusuf Auma Okafor"
).split()
_VILLAGES = (
    "Kibera Mathare Kawangware Makoko Ajegunle Bwaise Katwe Mukuru Korogocho " "Dandora Githurai Kasarani"
).split()
_COUNTRIES = "Kenya Nigeria Uganda Tanzania Ghana Ethiopia".split()
_GENERIC_TEXT = ["none", "n/a", "ok", "completed", "not recorded", "see notes"]


def _fake_date(rng: random.Random) -> str:
    return f"2026-{rng.randint(1, 6):02d}-{rng.randint(1, 28):02d}"


def _fabricate_text(leaf: str, rng: random.Random) -> str:
    """A plausible (invented, non-PII) value for a text field, keyed off its leaf.

    Replaces the old ``sample-NNN`` stub so categorical group-bys and joins land on
    realistic-looking values instead of obvious placeholders.
    """
    low = leaf.lower()
    if any(k in low for k in ("phone", "mobile", "msisdn", "contact_number")):
        return "0" + "".join(str(rng.randint(0, 9)) for _ in range(9))
    if "name" in low:
        return f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
    if "country" in low:
        return rng.choice(_COUNTRIES)
    if any(
        k in low
        for k in (
            "village",
            "ward",
            "district",
            "county",
            "settlement",
            "community",
            "location",
            "address",
            "region",
            "state",
            "province",
            "area",
        )
    ):
        return rng.choice(_VILLAGES)
    if "email" in low:
        return f"{rng.choice(_FIRST_NAMES).lower()}{rng.randint(1, 999)}@example.com"
    if any(k in low for k in ("gps", "geopoint", "geo_point", "coord")):
        lat, lon = round(rng.uniform(-4, 14), 6), round(rng.uniform(2, 42), 6)
        alt, acc = round(rng.uniform(400, 600), 1), round(rng.uniform(3, 12), 1)
        return f"{lat} {lon} {alt} {acc}"
    if (
        low == "id"
        or low.endswith("_id")
        or any(k in low for k in ("uuid", "instanceid", "case_id", "barcode", "serial"))
    ):
        return "".join(rng.choice("0123456789abcdef") for _ in range(12))
    if "time" in low:
        return f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}"
    if "date" in low or low.endswith("dob"):
        return _fake_date(rng)
    return rng.choice(_GENERIC_TEXT)


def _default_for_kind(spec: QuestionSpec, rng: random.Random) -> Any:
    if spec.kind in {"select", "multiselect"}:
        # Draw a real option when the schema lists choices; otherwise a neutral
        # yes/no rather than a stub (the profiler gives most selects a real
        # categorical distribution, so this only covers all-null selects).
        return rng.choice(spec.choices) if spec.choices else rng.choice(["yes", "no"])
    if spec.kind == "int":
        return rng.randint(0, 10)
    if spec.kind == "decimal":
        return round(rng.uniform(0, 10), 2)
    if spec.kind == "date":
        return _fake_date(rng)
    if spec.kind == "image":
        return ""  # synthetic visits do not produce real images
    return _fabricate_text(spec.json_path.rsplit(".", 1)[-1], rng)


def _format_forced(value: Any, kind: str | None) -> Any:
    """Format a transplanted/longitudinal numeric value for its field kind."""
    if kind == "int":
        return int(round(float(value)))
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return value


def _sample_count(count: dict[int, float], rng: random.Random) -> int:
    keys = sorted(count)
    weights = [count[k] for k in keys]
    return rng.choices(keys, weights=weights, k=1)[0]


def _draw_repeat_value(dist, rng: random.Random) -> Any:
    """Draw one scalar value for a repeat-group child (no schema kind available, so
    type is inferred from the distribution: categorical -> label, binary -> yes/no,
    otherwise a rounded number)."""
    if isinstance(dist, CategoricalDistribution):
        return _categorical_value(dist, rng)
    raw = _draw(dist, rng)
    transform = getattr(dist, "transform", None)
    if transform:
        return _apply_transform(raw, transform, rng)
    if isinstance(dist, BinaryDistribution):
        return "yes" if raw >= 0.5 else "no"
    return round(float(raw), 3)


def _build_repeat_element(field_distributions: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Fill one repeat-group instance from child distributions keyed by relative path."""
    element: dict[str, Any] = {}
    for rel_path, dist in field_distributions.items():
        if getattr(dist, "null_rate", 0.0) and rng.random() < dist.null_rate:
            continue  # this instance legitimately omits the field
        _set_nested(element, rel_path, _draw_repeat_value(dist, rng))
    return element


def _under_repeat(path: str, bases: set[str]) -> bool:
    """True if ``path`` is a repeat base or a scalar leaf nested under one — those are
    owned by the repeat array, not the flat scalar fill."""
    return any(path == b or path.startswith(b + ".") for b in bases)


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
    # Never let a scalar overwrite an already-populated group. CommCare app structures
    # sometimes expose a group node as its own (text) question (e.g. "form.anthropometric"
    # alongside "form.anthropometric.child_weight_visit"); filling that group as a scalar
    # would wipe out children already written (notably transplanted values in mirror mode).
    if isinstance(obj.get(parts[-1]), dict) and not isinstance(value, dict):
        return
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
    forced_values: dict[str, Any] | None = None,
    mirror: bool = False,
) -> dict[str, Any]:
    anomaly_paths = {a.field_path for a in anomalies_for_visit if a.field_path}
    # Persona overrides take precedence over cohort distributions. Building a
    # merged map once avoids re-checking the persona on every field.
    overrides = persona.field_overrides if persona else {}
    effective: dict[str, Any] = {**cohort.field_distributions, **overrides}
    # Repeat-group bases own their subtree — their scalar leaves are filled as array
    # elements below, not as flat fields here.
    repeat_bases = set(getattr(cohort, "repeat_groups", {}) or {})

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

    # Forced (transplanted/longitudinal) values win outright: they were really
    # observed for this entity at this visit, so they bypass distribution draws and
    # null-omission. Written first; both fill loops then skip these paths.
    forced = forced_values or {}
    by_path = schema.by_path()
    written_forced: set[str] = set()
    for path, val in forced.items():
        if _under_repeat(path, repeat_bases):
            continue
        kind = by_path[path].kind if path in by_path else None
        _set_nested(out, path, _format_forced(val, kind))
        written_forced.add(path)
    covered_paths |= written_forced

    for spec in schema.questions:
        if _under_repeat(spec.json_path, repeat_bases):
            continue  # owned by a repeat array, emitted below
        if spec.json_path in written_forced:
            continue  # already set from the transplanted series
        covered_paths.add(spec.json_path)
        if mirror and spec.kind in {"int", "decimal"}:
            # Faithful sparsity (mirror): numeric fields come ONLY from the transplant
            # (forced). The real visit left this one blank, so leave it blank — don't
            # invent a value. Inventing at duplicate/sibling numeric paths buries the
            # real per-entity trajectory when an analysis coalesces by field name (#713).
            continue
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
        if path in covered_paths or path in consumed_keys or _under_repeat(path, repeat_bases):
            continue
        if mirror and isinstance(dist, (NormalDistribution, UniformDistribution)):
            continue  # mirror: numeric orphans come only from the transplant (forced)
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

    # Repeat groups: emit a JSON array of 0–N filled sub-records at each base path.
    # Done last so the array authoritatively owns the base path.
    for base_path, rg in (getattr(cohort, "repeat_groups", {}) or {}).items():
        n = _sample_count(rg.count, rng)
        elements = [_build_repeat_element(rg.field_distributions, rng) for _ in range(n)]
        _set_nested(out, base_path, elements)

    return out
