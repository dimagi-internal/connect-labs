# ACE Phase 6 — Plan A: connect-labs side

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land the connect-labs infrastructure that ACE Phase 6 will drive — a synthetic data generator engine, two new SEED workflow templates, and five new MCP tools — as three independently shippable PRs that together let a human manually mint a story-coherent synthetic opportunity from a YAML manifest.

**Architecture:** Manifest YAML → Pydantic-validated → deterministic Python engine → 5 fixture JSON files → existing GDrive uploader → existing `SyntheticOpportunity` registry. Templates are scaffolds that take KPI config from definition data; render code is opp-agnostic until the ACE-side polish skill (Plan B) layers on per-opp JSX. MCP tools wrap engine + templates + task creation + snapshot save into a clean RPC surface for ACE.

**Tech Stack:** Python 3.11+ with Django 5, Pydantic v2 for the manifest schema, pytest + django-pytest for tests, existing `DriveClient` for GDrive, existing `@register` decorator for MCP tools, existing template auto-discovery registry.

**Companion design:** [`2026-05-05-ace-synthetic-data-phase-design.md`](./2026-05-05-ace-synthetic-data-phase-design.md). Plan B (ACE plugin side) is a follow-up plan that depends on this one being deployed to labs prod.

**Spec coverage map (design § → plan task):**

| Design section | Plan task(s) |
|---|---|
| §5.1 manifest schema | 1.2 |
| §5.2 generator engine | 1.1 – 1.11 |
| §5.3 SEED templates | 2.1 – 2.6 |
| §5.4 MCP tools | 3.1 – 3.7 |
| §11 testing strategy | every task's test step + 1.10 (golden) + 3.7 (e2e) |

---

## Phase 1 — Generator Engine (PR 1)

Pure Python package at `commcare_connect/labs/synthetic/generator/`. No Django ORM dependency in the engine itself; only the `uploader` composes with `gdrive.py` and the `SyntheticOpportunity` model. Every module is independently testable. The package's public entry is `engine.generate(manifest, opportunity_detail, form_schema) -> dict[str, list | dict]`, fully deterministic given `manifest.random_seed`.

### Task 1.1: Package skeleton

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/__init__.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/__init__.py`

- [ ] **Step 1: Create the package init**

```python
# commcare_connect/labs/synthetic/generator/__init__.py
"""Deterministic synthetic data generator for labs synthetic opportunities.

Public entry: ``engine.generate(manifest, opportunity_detail, form_schema)``
which returns the five fixture dicts the labs synthetic system serves.
"""
```

- [ ] **Step 2: Create the tests package init**

```python
# commcare_connect/labs/synthetic/generator/tests/__init__.py
```

- [ ] **Step 3: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/
git commit -m "feat(synthetic): scaffold generator package"
```

---

### Task 1.2: Manifest schema (Pydantic v2)

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/manifest.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_manifest.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_manifest.py
import datetime as dt

import pytest

from commcare_connect.labs.synthetic.generator.manifest import (
    Manifest,
    ManifestValidationError,
)


VALID_MANIFEST_YAML = """
opportunity_id: 1237
opportunity_name: Demo
random_seed: 42
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw:
    mean: 8
    stddev: 2
flw_personas:
  - id: asha
    display_name: Asha M.
    archetype: rockstar
    accuracy_distribution: { mean: 0.92, stddev: 0.04 }
    completeness_distribution: { mean: 0.95, stddev: 0.03 }
    flag_rate: 0.02
beneficiary_cohorts:
  - id: primary
    size: 100
    field_distributions:
      "form.weight_kg":
        distribution: normal
        mean: 12.4
        stddev: 2.1
    progression: improvement_curve
anomalies: []
kpi_config:
  - kpi: accuracy
    field_path: form.weight_kg
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
coaching_arcs: []
"""


def test_manifest_parses_valid_yaml():
    m = Manifest.from_yaml(VALID_MANIFEST_YAML)
    assert m.opportunity_id == 1237
    assert m.random_seed == 42
    assert m.timeline.start_date == dt.date(2026, 2, 1)
    assert m.timeline.weeks == 4
    assert m.flw_personas[0].id == "asha"
    assert m.flw_personas[0].archetype == "rockstar"
    assert m.beneficiary_cohorts[0].size == 100
    assert m.kpi_config[0].kpi == "accuracy"


def test_manifest_rejects_unknown_archetype():
    bad = VALID_MANIFEST_YAML.replace("archetype: rockstar", "archetype: wizard")
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)


def test_manifest_rejects_negative_seed():
    bad = VALID_MANIFEST_YAML.replace("random_seed: 42", "random_seed: -1")
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)


def test_manifest_rejects_end_before_start():
    bad = VALID_MANIFEST_YAML.replace(
        "end_date: 2026-02-28", "end_date: 2026-01-01"
    )
    with pytest.raises(ManifestValidationError):
        Manifest.from_yaml(bad)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_manifest.py -v`
Expected: 4 errors — `ImportError: cannot import name 'Manifest'`.

- [ ] **Step 3: Implement the manifest module**

```python
# commcare_connect/labs/synthetic/generator/manifest.py
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
from pydantic import (
    BaseModel,
    Field,
    NonNegativeInt,
    PositiveInt,
    ValidationError,
    field_validator,
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

    @field_validator("coaching_arcs")
    @classmethod
    def _arcs_reference_known_flws(cls, arcs, info):
        # Cross-field check happens in _check_references below; keep field validator
        # as a no-op for ordering.
        return arcs

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_manifest.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/manifest.py commcare_connect/labs/synthetic/generator/tests/test_manifest.py
git commit -m "feat(synthetic): manifest schema for generator"
```

---

### Task 1.3: Schema loader (CommCare HQ form structure)

The engine needs the form's JSON paths to fill `form_json` correctly. Reuse the existing CommCare HQ API client; this module just resolves a flat list of `(json_path, question_type, choices)` tuples per form.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/schema_loader.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_schema_loader.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_schema_loader.py
from unittest.mock import MagicMock

from commcare_connect.labs.synthetic.generator.schema_loader import (
    FormSchema,
    QuestionSpec,
    load_form_schema,
)


def test_load_form_schema_collects_question_specs():
    """Schema loader returns one QuestionSpec per leaf question with a JSON path."""
    fake_hq_response = {
        "forms": [
            {
                "name": "Visit",
                "questions": [
                    {"value": "/data/weight_kg", "type": "Decimal", "options": []},
                    {
                        "value": "/data/kmc_status",
                        "type": "Select",
                        "options": [
                            {"value": "active"},
                            {"value": "inactive"},
                        ],
                    },
                ],
            }
        ]
    }
    api = MagicMock()
    api.get_form_json_paths.return_value = fake_hq_response

    schema = load_form_schema(api, app_id="app-123", form_xmlns="form-456")

    assert isinstance(schema, FormSchema)
    assert len(schema.questions) == 2
    weight = schema.questions[0]
    assert weight.json_path == "form.weight_kg"
    assert weight.kind == "decimal"
    assert weight.choices == []
    status = schema.questions[1]
    assert status.choices == ["active", "inactive"]
    assert status.kind == "select"


def test_load_form_schema_handles_empty_response():
    api = MagicMock()
    api.get_form_json_paths.return_value = {"forms": []}
    schema = load_form_schema(api, app_id="x", form_xmlns="y")
    assert schema.questions == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_schema_loader.py -v`
Expected: ImportError on schema_loader.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/schema_loader.py
"""Form schema discovery for the generator.

Wraps the existing CommCare HQ API client (via ``tools/commcare_hq_mcp``)
to produce a flat list of ``QuestionSpec`` instances — the inputs the
field filler needs to know which paths exist and what values are valid.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class QuestionSpec:
    """One leaf question on a form, normalized for the generator."""

    json_path: str          # e.g., "form.weight_kg"
    kind: str               # "decimal", "int", "text", "select", "multiselect", "date", "image"
    choices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FormSchema:
    """The set of question specs the engine can fill for one form."""

    questions: list[QuestionSpec]

    def by_path(self) -> dict[str, QuestionSpec]:
        return {q.json_path: q for q in self.questions}


class _HqApi(Protocol):
    def get_form_json_paths(self, app_id: str, form_xmlns: str) -> dict[str, Any]: ...


_KIND_MAP = {
    "Decimal": "decimal",
    "Int": "int",
    "Text": "text",
    "Select": "select",
    "MSelect": "multiselect",
    "Date": "date",
    "Image": "image",
}


def _xpath_to_json_path(xpath: str) -> str:
    # /data/foo/bar -> form.foo.bar
    cleaned = xpath.lstrip("/")
    if cleaned.startswith("data/"):
        cleaned = cleaned[len("data/"):]
    return "form." + cleaned.replace("/", ".")


def load_form_schema(api: _HqApi, *, app_id: str, form_xmlns: str) -> FormSchema:
    response = api.get_form_json_paths(app_id=app_id, form_xmlns=form_xmlns)
    questions: list[QuestionSpec] = []
    for form in response.get("forms", []):
        for q in form.get("questions", []):
            kind = _KIND_MAP.get(q.get("type", ""), "text")
            choices = [opt["value"] for opt in q.get("options", []) if "value" in opt]
            questions.append(
                QuestionSpec(
                    json_path=_xpath_to_json_path(q["value"]),
                    kind=kind,
                    choices=choices,
                )
            )
    return FormSchema(questions=questions)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_schema_loader.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/schema_loader.py commcare_connect/labs/synthetic/generator/tests/test_schema_loader.py
git commit -m "feat(synthetic): form schema loader for generator"
```

---

### Task 1.4: Timeline expander

Expands the timeline + cadence rules into a deterministic list of `(flw_id, visit_date)` pairs per week, modulated by FLW persona archetype. This module owns all date math.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/timeline.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_timeline.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_timeline.py
import datetime as dt

from commcare_connect.labs.synthetic.generator.manifest import (
    FlwPersona,
    MeanStddev,
    Timeline,
)
from commcare_connect.labs.synthetic.generator.timeline import (
    VisitSlot,
    expand_visit_schedule,
)


def _persona(pid, archetype):
    return FlwPersona(
        id=pid,
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=0.05,
    )


def _timeline():
    return Timeline(
        start_date=dt.date(2026, 2, 1),
        end_date=dt.date(2026, 2, 28),
        weeks=4,
        visit_cadence_per_week_per_flw=MeanStddev(mean=8, stddev=0),
    )


def test_expand_visit_schedule_is_deterministic():
    personas = [_persona("asha", "rockstar"), _persona("ravi", "struggling")]
    a = expand_visit_schedule(_timeline(), personas, random_seed=42)
    b = expand_visit_schedule(_timeline(), personas, random_seed=42)
    assert a == b


def test_expand_visit_schedule_archetype_modulates_count():
    """Rockstars produce more visits than strugglers given the same cadence."""
    rockstars = [_persona("asha", "rockstar")]
    strugglers = [_persona("ravi", "struggling")]
    rs = expand_visit_schedule(_timeline(), rockstars, random_seed=42)
    st = expand_visit_schedule(_timeline(), strugglers, random_seed=42)
    assert len(rs) > len(st)


def test_visit_slots_are_within_timeline():
    personas = [_persona("asha", "rockstar")]
    slots = expand_visit_schedule(_timeline(), personas, random_seed=42)
    for slot in slots:
        assert isinstance(slot, VisitSlot)
        assert dt.date(2026, 2, 1) <= slot.visit_date <= dt.date(2026, 2, 28)
        assert 1 <= slot.week_index <= 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_timeline.py -v`
Expected: ImportError on timeline.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/timeline.py
"""Timeline → per-FLW visit schedule expansion.

Deterministic given the manifest's ``random_seed``. Archetype controls
how many visits an FLW produces relative to the cadence mean.
"""

from __future__ import annotations

import datetime as dt
import random
from dataclasses import dataclass

from .manifest import FlwPersona, Timeline


@dataclass(frozen=True)
class VisitSlot:
    flw_id: str
    visit_date: dt.date
    week_index: int  # 1-based


_ARCHETYPE_MULTIPLIER = {
    "rockstar": 1.20,
    "steady": 1.00,
    "struggling": 0.65,
    "new_hire": 0.55,
}


def expand_visit_schedule(
    timeline: Timeline,
    personas: list[FlwPersona],
    *,
    random_seed: int,
) -> list[VisitSlot]:
    rng = random.Random(random_seed)
    slots: list[VisitSlot] = []
    cadence_mean = timeline.visit_cadence_per_week_per_flw.mean
    cadence_std = timeline.visit_cadence_per_week_per_flw.stddev

    for week in range(1, timeline.weeks + 1):
        week_start = timeline.start_date + dt.timedelta(days=(week - 1) * 7)
        for persona in personas:
            mult = _ARCHETYPE_MULTIPLIER.get(persona.archetype, 1.0)
            mean = max(0.0, cadence_mean * mult)
            count = max(0, int(round(rng.gauss(mean, cadence_std))))
            for _ in range(count):
                day_offset = rng.randint(0, 6)
                slots.append(
                    VisitSlot(
                        flw_id=persona.id,
                        visit_date=week_start + dt.timedelta(days=day_offset),
                        week_index=week,
                    )
                )
    return slots
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_timeline.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/timeline.py commcare_connect/labs/synthetic/generator/tests/test_timeline.py
git commit -m "feat(synthetic): timeline expander for visit schedules"
```

---

### Task 1.5: Field filler

Walks the form schema and fills `form_json` per visit using cohort distributions. Owns anomaly injection (a visit that lands on a scheduled anomaly week pulls its outlier values from the anomaly catalog instead of the cohort's normal distribution).

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/fields.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_fields.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_fields.py
import datetime as dt
import random

from commcare_connect.labs.synthetic.generator.fields import fill_form_json
from commcare_connect.labs.synthetic.generator.manifest import (
    Anomaly,
    BeneficiaryCohort,
    NormalDistribution,
)
from commcare_connect.labs.synthetic.generator.schema_loader import (
    FormSchema,
    QuestionSpec,
)


def _schema():
    return FormSchema(
        questions=[
            QuestionSpec("form.weight_kg", "decimal"),
            QuestionSpec("form.muac_cm", "decimal"),
            QuestionSpec("form.kmc_status", "select", choices=["active", "inactive"]),
        ]
    )


def _cohort():
    return BeneficiaryCohort(
        id="primary",
        size=100,
        field_distributions={
            "form.weight_kg": NormalDistribution(mean=12.4, stddev=0.5),
            "form.muac_cm": NormalDistribution(mean=13.2, stddev=0.3),
        },
        progression="flat",
    )


def test_fill_form_json_returns_a_value_for_every_question():
    rng = random.Random(7)
    out = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[],
        rng=rng,
    )
    assert "form.weight_kg" in out
    assert "form.muac_cm" in out
    assert out["form.kmc_status"] in ("active", "inactive")


def test_fill_form_json_applies_anomaly_outlier():
    rng = random.Random(7)
    anomaly = Anomaly(
        id="weight_outlier",
        type="field_outlier",
        flw_ids=["ravi"],
        field_path="form.weight_kg",
        week=5,
    )
    out = fill_form_json(
        schema=_schema(),
        cohort=_cohort(),
        anomalies_for_visit=[anomaly],
        rng=rng,
    )
    # Anomaly outliers are >= 4 stddevs from cohort mean
    assert abs(out["form.weight_kg"] - 12.4) >= 4 * 0.5


def test_fill_form_json_is_deterministic():
    a = fill_form_json(
        schema=_schema(), cohort=_cohort(), anomalies_for_visit=[], rng=random.Random(7)
    )
    b = fill_form_json(
        schema=_schema(), cohort=_cohort(), anomalies_for_visit=[], rng=random.Random(7)
    )
    assert a == b
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_fields.py -v`
Expected: ImportError on fields.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/fields.py
"""Per-visit form_json filling.

For each question in the schema, draws a value from the cohort's field
distribution. Anomalies that name the visit's field path override the
distribution with an outlier (>= 4 sigma).
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Any

from .manifest import (
    Anomaly,
    BeneficiaryCohort,
    NormalDistribution,
    UniformDistribution,
)
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
        return dt.date.today().isoformat()
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_fields.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/fields.py commcare_connect/labs/synthetic/generator/tests/test_fields.py
git commit -m "feat(synthetic): field filler with anomaly injection"
```

---

### Task 1.6: Status / flag distributor

For each visit, decides `status`, `flagged`, `flag_reason`, `review_status` based on the FLW persona's flag rate, the Connect verification rules from the opp config, and any anomaly that touches that visit.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/status.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_status.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_status.py
import random

from commcare_connect.labs.synthetic.generator.manifest import (
    FlwPersona,
    MeanStddev,
)
from commcare_connect.labs.synthetic.generator.status import (
    VisitStatus,
    decide_visit_status,
)


def _p(flag_rate, archetype="steady"):
    return FlwPersona(
        id="x",
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=flag_rate,
    )


def test_zero_flag_rate_never_flags():
    rng = random.Random(0)
    persona = _p(0.0)
    for _ in range(200):
        s = decide_visit_status(persona=persona, has_anomaly=False, rng=rng)
        assert s.flagged is False
        assert s.status == "approved"


def test_high_flag_rate_eventually_flags():
    rng = random.Random(0)
    persona = _p(1.0)
    s = decide_visit_status(persona=persona, has_anomaly=False, rng=rng)
    assert s.flagged is True
    assert s.flag_reason  # non-empty string
    assert s.status in {"pending", "rejected"}


def test_anomaly_forces_flag_and_review():
    rng = random.Random(0)
    persona = _p(0.0)  # would never flag without anomaly
    s = decide_visit_status(persona=persona, has_anomaly=True, rng=rng)
    assert s.flagged is True
    assert s.review_status in {"pending", "rejected"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_status.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/status.py
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_status.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/status.py commcare_connect/labs/synthetic/generator/tests/test_status.py
git commit -m "feat(synthetic): visit status / flag distributor"
```

---

### Task 1.7: Completed-works + completed-module minter

Builds `completed_works.json` and `completed_module.json` from the visit list + payment unit definitions on the opportunity.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/works.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_works.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_works.py
import datetime as dt

from commcare_connect.labs.synthetic.generator.works import build_works_and_modules


def test_build_works_one_per_approved_visit():
    visits = [
        {"id": "v1", "username": "asha", "status": "approved", "deliver_unit_id": 1, "visit_date": "2026-02-05"},
        {"id": "v2", "username": "asha", "status": "rejected", "deliver_unit_id": 1, "visit_date": "2026-02-06"},
        {"id": "v3", "username": "ravi", "status": "approved", "deliver_unit_id": 2, "visit_date": "2026-02-06"},
    ]
    payment_units = [
        {"id": 1, "name": "PU1", "deliver_units": [1, 2]},
    ]
    works, modules = build_works_and_modules(visits, payment_units)
    # one completed work per approved visit
    work_ids = {w["id"] for w in works}
    assert {"v1-cw", "v3-cw"}.issubset(work_ids)
    assert "v2-cw" not in work_ids
    # modules: one per (username, payment unit)
    assert {(m["username"], m["payment_unit_id"]) for m in modules} == {
        ("asha", 1),
        ("ravi", 1),
    }


def test_build_works_returns_lists():
    works, modules = build_works_and_modules([], [])
    assert works == []
    assert modules == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_works.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/works.py
"""Build completed_works.json and completed_module.json from synthetic visits."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _payment_unit_for_deliver(deliver_unit_id: int, payment_units: list[dict]) -> int | None:
    for pu in payment_units:
        if deliver_unit_id in pu.get("deliver_units", []):
            return pu["id"]
    return None


def build_works_and_modules(
    visits: list[dict[str, Any]],
    payment_units: list[dict[str, Any]],
) -> tuple[list[dict], list[dict]]:
    works: list[dict] = []
    seen_modules: set[tuple[str, int]] = set()
    modules: list[dict] = []
    counts: dict[tuple[str, int], int] = defaultdict(int)

    for v in visits:
        if v.get("status") != "approved":
            continue
        deliver_unit_id = v.get("deliver_unit_id")
        if deliver_unit_id is None:
            continue
        pu_id = _payment_unit_for_deliver(deliver_unit_id, payment_units)
        if pu_id is None:
            continue
        works.append(
            {
                "id": f"{v['id']}-cw",
                "username": v["username"],
                "payment_unit_id": pu_id,
                "completed_at": v["visit_date"],
                "approved": True,
            }
        )
        key = (v["username"], pu_id)
        counts[key] += 1
        if key not in seen_modules:
            seen_modules.add(key)
            modules.append(
                {
                    "id": f"{v['username']}-{pu_id}-cm",
                    "username": v["username"],
                    "payment_unit_id": pu_id,
                    "completed": True,
                }
            )

    for m in modules:
        m["count"] = counts[(m["username"], m["payment_unit_id"])]

    return works, modules
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_works.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/works.py commcare_connect/labs/synthetic/generator/tests/test_works.py
git commit -m "feat(synthetic): completed works + module minter"
```

---

### Task 1.8: User_data builder (FLW roster)

Mints `user_data.json` from the manifest's FLW personas — one row per persona.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/user_data.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_user_data.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_user_data.py
import datetime as dt

from commcare_connect.labs.synthetic.generator.manifest import FlwPersona, MeanStddev
from commcare_connect.labs.synthetic.generator.user_data import build_user_data


def _p(pid, name, archetype="steady"):
    return FlwPersona(
        id=pid,
        display_name=name,
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=0.05,
    )


def test_build_user_data_one_row_per_persona():
    visits = [
        {"username": "asha", "visit_date": "2026-02-15"},
        {"username": "asha", "visit_date": "2026-02-20"},
        {"username": "ravi", "visit_date": "2026-02-12"},
    ]
    rows = build_user_data([_p("asha", "Asha M."), _p("ravi", None)], visits)
    by_user = {r["username"]: r for r in rows}
    assert by_user["asha"]["name"] == "Asha M."
    assert by_user["ravi"]["name"] == "ravi"  # falls back to id
    assert by_user["asha"]["last_active"] == "2026-02-20"


def test_build_user_data_handles_no_visits():
    rows = build_user_data([_p("asha", "Asha M.")], [])
    assert len(rows) == 1
    assert rows[0]["last_active"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_user_data.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/user_data.py
"""Build user_data.json (the FLW roster) from manifest personas."""

from __future__ import annotations

from typing import Any

from .manifest import FlwPersona


def build_user_data(
    personas: list[FlwPersona],
    visits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    last_active: dict[str, str] = {}
    for v in visits:
        u = v["username"]
        d = v["visit_date"]
        if u not in last_active or d > last_active[u]:
            last_active[u] = d

    rows: list[dict[str, Any]] = []
    for p in personas:
        rows.append(
            {
                "username": p.id,
                "name": p.display_name or p.id,
                "last_active": last_active.get(p.id),
                "archetype": p.archetype,
            }
        )
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_user_data.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/user_data.py commcare_connect/labs/synthetic/generator/tests/test_user_data.py
git commit -m "feat(synthetic): FLW user_data builder"
```

---

### Task 1.9: Opportunity detail builder

Builds `opportunity.json` from the live Connect opportunity detail. Mostly a passthrough that fills in any missing keys with sensible defaults.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/opportunity.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_opportunity.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_opportunity.py
from commcare_connect.labs.synthetic.generator.opportunity import build_opportunity


def test_build_opportunity_passes_through_known_keys():
    detail = {
        "id": 1237,
        "name": "Demo Opportunity",
        "organization": "Acme",
        "currency": "USD",
    }
    out = build_opportunity(detail, opportunity_name_override="Pretty Name")
    assert out["id"] == 1237
    assert out["name"] == "Pretty Name"
    assert out["organization"] == "Acme"
    assert out["currency"] == "USD"


def test_build_opportunity_defaults_missing_fields():
    out = build_opportunity({"id": 1, "name": "X"})
    assert "currency" in out
    assert "organization" in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_opportunity.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/opportunity.py
"""Build opportunity.json from live Connect opp detail."""

from __future__ import annotations

from typing import Any


_DEFAULTS = {
    "organization": "",
    "currency": "USD",
    "is_active": True,
}


def build_opportunity(
    detail: dict[str, Any],
    *,
    opportunity_name_override: str | None = None,
) -> dict[str, Any]:
    out = {**_DEFAULTS, **detail}
    if opportunity_name_override:
        out["name"] = opportunity_name_override
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_opportunity.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/opportunity.py commcare_connect/labs/synthetic/generator/tests/test_opportunity.py
git commit -m "feat(synthetic): opportunity.json builder"
```

---

### Task 1.10: Engine orchestrator + golden integration test

Composes the modules above into one entry point. Writes a golden-manifest test that asserts byte-stable outputs across runs given the same seed.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/engine.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/golden/manifest.yaml`
- Create: `commcare_connect/labs/synthetic/generator/tests/golden/opportunity_detail.json`
- Create: `commcare_connect/labs/synthetic/generator/tests/golden/form_schema.json`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_engine.py`

- [ ] **Step 1: Write the golden manifest fixture**

```yaml
# commcare_connect/labs/synthetic/generator/tests/golden/manifest.yaml
opportunity_id: 9999
opportunity_name: Golden Demo
random_seed: 1234
timeline:
  start_date: 2026-02-01
  end_date: 2026-02-28
  weeks: 4
  visit_cadence_per_week_per_flw: { mean: 4, stddev: 0 }
flw_personas:
  - id: asha
    display_name: Asha M.
    archetype: rockstar
    accuracy_distribution: { mean: 0.92, stddev: 0.0 }
    completeness_distribution: { mean: 0.95, stddev: 0.0 }
    flag_rate: 0.0
  - id: ravi
    archetype: struggling
    accuracy_distribution: { mean: 0.62, stddev: 0.0 }
    completeness_distribution: { mean: 0.80, stddev: 0.0 }
    flag_rate: 0.5
beneficiary_cohorts:
  - id: primary
    size: 50
    field_distributions:
      "form.weight_kg": { distribution: normal, mean: 12.4, stddev: 0.5 }
    progression: flat
anomalies: []
kpi_config:
  - kpi: accuracy
    field_path: form.weight_kg
    aggregation: validated_rate
    threshold_underperform: 0.75
    threshold_target: 0.90
coaching_arcs: []
```

- [ ] **Step 2: Write the opportunity_detail and form_schema fixtures**

```json
// commcare_connect/labs/synthetic/generator/tests/golden/opportunity_detail.json
{
    "id": 9999,
    "name": "Golden Demo",
    "organization": "Golden Org",
    "payment_units": [
        {"id": 1, "name": "PU1", "deliver_units": [1]}
    ],
    "deliver_units": [{"id": 1, "name": "DU1"}]
}
```

```json
// commcare_connect/labs/synthetic/generator/tests/golden/form_schema.json
{"questions": [
    {"json_path": "form.weight_kg", "kind": "decimal", "choices": []}
]}
```

- [ ] **Step 3: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_engine.py
import json
from pathlib import Path

import pytest

from commcare_connect.labs.synthetic.generator.engine import generate
from commcare_connect.labs.synthetic.generator.manifest import Manifest
from commcare_connect.labs.synthetic.generator.schema_loader import (
    FormSchema,
    QuestionSpec,
)


GOLDEN = Path(__file__).parent / "golden"


def _load_inputs():
    manifest = Manifest.from_yaml((GOLDEN / "manifest.yaml").read_text())
    detail = json.loads((GOLDEN / "opportunity_detail.json").read_text())
    schema_data = json.loads((GOLDEN / "form_schema.json").read_text())
    schema = FormSchema(
        questions=[QuestionSpec(**q) for q in schema_data["questions"]]
    )
    return manifest, detail, schema


def test_generate_returns_all_five_endpoints():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert set(out.keys()) == {
        "opportunity",
        "user_visits",
        "user_data",
        "completed_works",
        "completed_module",
    }


def test_generate_is_deterministic_under_seed():
    manifest, detail, schema = _load_inputs()
    a = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    b = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_generate_visits_carry_required_fields():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    visits = out["user_visits"]
    assert len(visits) > 0
    v = visits[0]
    for key in (
        "id",
        "username",
        "visit_date",
        "status",
        "form_json",
        "deliver_unit_id",
        "opportunity_id",
    ):
        assert key in v, f"missing key {key} in visit"


def test_generate_user_data_matches_personas():
    manifest, detail, schema = _load_inputs()
    out = generate(manifest=manifest, opportunity_detail=detail, form_schema=schema)
    usernames = {u["username"] for u in out["user_data"]}
    assert usernames == {"asha", "ravi"}
```

- [ ] **Step 4: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_engine.py -v`
Expected: ImportError on engine.

- [ ] **Step 5: Implement the engine**

```python
# commcare_connect/labs/synthetic/generator/engine.py
"""Top-level generator orchestrator.

Composes manifest → timeline → fields → status → user_data → works
into the five fixture dicts the labs synthetic system serves.
"""

from __future__ import annotations

import datetime as dt
import random
import uuid
from typing import Any

from .fields import fill_form_json
from .manifest import Manifest
from .opportunity import build_opportunity
from .schema_loader import FormSchema
from .status import decide_visit_status
from .timeline import expand_visit_schedule
from .user_data import build_user_data
from .works import build_works_and_modules


def _anomalies_at(week_index: int, flw_id: str, manifest: Manifest):
    out = []
    for a in manifest.anomalies:
        if flw_id not in a.flw_ids:
            continue
        if a.week and a.week == week_index:
            out.append(a)
        elif a.weeks and week_index in a.weeks:
            out.append(a)
    return out


def _persona_index(manifest: Manifest):
    return {p.id: p for p in manifest.flw_personas}


def _payment_units(detail: dict[str, Any]) -> list[dict[str, Any]]:
    return detail.get("payment_units", [])


def _default_deliver_unit(detail: dict[str, Any]) -> int | None:
    units = detail.get("deliver_units") or []
    return units[0]["id"] if units else None


def generate(
    *,
    manifest: Manifest,
    opportunity_detail: dict[str, Any],
    form_schema: FormSchema,
) -> dict[str, Any]:
    rng = random.Random(manifest.random_seed)
    personas = manifest.flw_personas
    persona_index = _persona_index(manifest)
    cohort = manifest.beneficiary_cohorts[0]  # v1 supports the primary cohort
    deliver_unit_id = _default_deliver_unit(opportunity_detail)
    payment_units = _payment_units(opportunity_detail)

    slots = expand_visit_schedule(manifest.timeline, personas, random_seed=manifest.random_seed)
    slots.sort(key=lambda s: (s.visit_date, s.flw_id))

    visits: list[dict[str, Any]] = []
    for slot in slots:
        persona = persona_index[slot.flw_id]
        anomalies = _anomalies_at(slot.week_index, slot.flw_id, manifest)
        form_json = fill_form_json(
            schema=form_schema, cohort=cohort, anomalies_for_visit=anomalies, rng=rng
        )
        status = decide_visit_status(persona=persona, has_anomaly=bool(anomalies), rng=rng)
        visits.append(
            {
                "id": str(uuid.UUID(int=rng.getrandbits(128))),
                "xform_id": str(uuid.UUID(int=rng.getrandbits(128))),
                "opportunity_id": manifest.opportunity_id,
                "username": persona.id,
                "deliver_unit": str(deliver_unit_id) if deliver_unit_id is not None else "",
                "deliver_unit_id": deliver_unit_id,
                "entity_id": str(uuid.UUID(int=rng.getrandbits(128))),
                "entity_name": f"Beneficiary {rng.randint(1, cohort.size)}",
                "visit_date": slot.visit_date.isoformat(),
                "status": status.status,
                "reason": None,
                "location": "",
                "flagged": status.flagged,
                "flag_reason": status.flag_reason,
                "form_json": form_json,
                "completed_work": "",
                "status_modified_date": dt.datetime.combine(
                    slot.visit_date, dt.time(12, 0)
                ).isoformat(),
                "review_status": status.review_status,
                "review_created_on": dt.datetime.combine(
                    slot.visit_date, dt.time(12, 30)
                ).isoformat(),
                "justification": None,
                "date_created": dt.datetime.combine(
                    slot.visit_date, dt.time(11, 0)
                ).isoformat(),
                "completed_work_id": None,
                "images": [],
            }
        )

    user_data = build_user_data(personas, visits)
    works, modules = build_works_and_modules(visits, payment_units)
    opportunity = build_opportunity(
        opportunity_detail, opportunity_name_override=manifest.opportunity_name
    )

    return {
        "opportunity": opportunity,
        "user_visits": visits,
        "user_data": user_data,
        "completed_works": works,
        "completed_module": modules,
    }
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_engine.py -v`
Expected: 4 passed.

- [ ] **Step 7: Run the entire generator package test suite to confirm nothing regressed**

Run: `pytest commcare_connect/labs/synthetic/generator/ -v`
Expected: all tests pass.

- [ ] **Step 8: Commit**

```bash
git add commcare_connect/labs/synthetic/generator/engine.py commcare_connect/labs/synthetic/generator/tests/golden/ commcare_connect/labs/synthetic/generator/tests/test_engine.py
git commit -m "feat(synthetic): engine orchestrator + golden integration test"
```

---

### Task 1.11: Uploader

Composes the engine output with the existing `DriveClient` and `SyntheticOpportunity` model. This is the only generator module that touches Django state.

**Files:**
- Create: `commcare_connect/labs/synthetic/generator/uploader.py`
- Create: `commcare_connect/labs/synthetic/generator/tests/test_uploader.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/labs/synthetic/generator/tests/test_uploader.py
import json

import pytest
from django.test import override_settings

from commcare_connect.labs.synthetic.generator.uploader import (
    UploadResult,
    upload_and_register,
)
from commcare_connect.labs.synthetic.models import SyntheticOpportunity


class _FakeDrive:
    def __init__(self):
        self.created_folder = None
        self.uploads: list[tuple[str, str, bytes]] = []

    def create_folder(self, name, parent_id):
        self.created_folder = (name, parent_id)
        return f"folder-{name}"

    def upload_file(self, folder_id, filename, content):
        self.uploads.append((folder_id, filename, content))
        return f"file-{filename}"


@pytest.mark.django_db
@override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="parent-abc")
def test_upload_and_register_uploads_five_files_and_creates_row():
    drive = _FakeDrive()
    fixtures = {
        "opportunity": {"id": 1237, "name": "X"},
        "user_visits": [{"id": "v1"}],
        "user_data": [{"username": "asha"}],
        "completed_works": [],
        "completed_module": [],
    }
    result = upload_and_register(
        drive=drive,
        opportunity_id=1237,
        opportunity_name="X",
        fixtures=fixtures,
    )
    assert isinstance(result, UploadResult)
    assert result.folder_id.startswith("folder-")
    filenames = sorted(name for _, name, _ in drive.uploads)
    assert filenames == sorted([
        "opportunity.json",
        "user_visits.json",
        "user_data.json",
        "completed_works.json",
        "completed_module.json",
    ])
    row = SyntheticOpportunity.objects.get(opportunity_id=1237)
    assert row.enabled is True
    assert row.gdrive_folder_id == result.folder_id


@pytest.mark.django_db
@override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="parent-abc")
def test_upload_and_register_updates_existing_row():
    SyntheticOpportunity.objects.create(
        opportunity_id=1237,
        gdrive_folder_id="old-folder",
        enabled=False,
    )
    drive = _FakeDrive()
    fixtures = {
        "opportunity": {}, "user_visits": [], "user_data": [],
        "completed_works": [], "completed_module": [],
    }
    upload_and_register(
        drive=drive, opportunity_id=1237, opportunity_name="X", fixtures=fixtures,
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=1237)
    assert row.gdrive_folder_id != "old-folder"
    assert row.enabled is True


@override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="")
def test_upload_and_register_requires_parent_folder_setting():
    drive = _FakeDrive()
    with pytest.raises(RuntimeError, match="LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID"):
        upload_and_register(
            drive=drive,
            opportunity_id=1,
            opportunity_name="X",
            fixtures={k: [] for k in ("opportunity", "user_visits", "user_data", "completed_works", "completed_module")},
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_uploader.py -v`
Expected: ImportError on uploader.

- [ ] **Step 3: Implement**

```python
# commcare_connect/labs/synthetic/generator/uploader.py
"""Compose engine output with the GDrive uploader and SyntheticOpportunity registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from django.conf import settings
from django.utils import timezone

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache


_FILES = (
    ("opportunity", "opportunity.json"),
    ("user_visits", "user_visits.json"),
    ("user_data", "user_data.json"),
    ("completed_works", "completed_works.json"),
    ("completed_module", "completed_module.json"),
)


class _Drive(Protocol):
    def create_folder(self, name: str, parent_id: str) -> str: ...
    def upload_file(self, folder_id: str, filename: str, content: bytes) -> str: ...


@dataclass(frozen=True)
class UploadResult:
    folder_id: str
    record_counts: dict[str, int]


def upload_and_register(
    *,
    drive: _Drive,
    opportunity_id: int,
    opportunity_name: str,
    fixtures: dict[str, Any],
) -> UploadResult:
    parent_id = getattr(settings, "LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID", "")
    if not parent_id:
        raise RuntimeError("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID is not set.")

    folder_name = f"opp-{opportunity_id}-{timezone.now():%Y%m%d-%H%M%S}-generated"
    folder_id = drive.create_folder(folder_name, parent_id=parent_id)

    counts: dict[str, int] = {}
    for key, filename in _FILES:
        payload = fixtures[key]
        drive.upload_file(folder_id, filename, json.dumps(payload).encode())
        counts[key] = len(payload) if isinstance(payload, list) else 1

    SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults={
            "label": opportunity_name,
            "gdrive_folder_id": folder_id,
            "enabled": True,
        },
    )
    invalidate_cache()

    return UploadResult(folder_id=folder_id, record_counts=counts)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/labs/synthetic/generator/tests/test_uploader.py -v`
Expected: 3 passed.

- [ ] **Step 5: Run the full generator suite**

Run: `pytest commcare_connect/labs/synthetic/generator/ -v`
Expected: all tests pass.

- [ ] **Step 6: Commit + open PR 1**

```bash
git add commcare_connect/labs/synthetic/generator/uploader.py commcare_connect/labs/synthetic/generator/tests/test_uploader.py
git commit -m "feat(synthetic): uploader composing GDrive + SyntheticOpportunity registry"
```

PR 1 is now ready. Title: `feat(synthetic): generator engine for fixture data`. Follow `.github/PULL_REQUEST_TEMPLATE.md` — include `## Product Description` ("Labs adds a deterministic generator that mints synthetic-opportunity fixture data from a YAML manifest. Operators can now produce demo datasets without dumping from prod.").

---

## Phase 2 — SEED Workflow Templates (PR 2)

Two new templates ship as repo code at `commcare_connect/workflow/templates/`. Both are scaffolds — config-driven, opp-agnostic. Per-opp visual polish lands in Plan B (ACE polish skill, layered via `workflow_update_render_code`). Tests follow the existing template-test convention.

### Task 2.1: `llo_weekly_review` skeleton — DEFINITION + PIPELINE_SCHEMAS

**Files:**
- Create: `commcare_connect/workflow/templates/llo_weekly_review.py`
- Create: `commcare_connect/workflow/tests/templates/test_llo_weekly_review.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/workflow/tests/templates/test_llo_weekly_review.py
def test_llo_weekly_review_template_registered():
    from commcare_connect.workflow.templates import list_templates

    keys = {t["key"] for t in list_templates()}
    assert "llo_weekly_review" in keys


def test_llo_weekly_review_supports_saved_runs():
    from commcare_connect.workflow.templates.llo_weekly_review import TEMPLATE

    assert TEMPLATE["supports_saved_runs"] is True
    assert TEMPLATE["snapshot_inputs"] == {
        "pipelines": ["flw_kpis"],
        "state_keys": ["worker_states", "spawned_tasks"],
    }


def test_llo_weekly_review_definition_has_kpi_config_slot():
    from commcare_connect.workflow.templates.llo_weekly_review import DEFINITION

    assert "kpi_config" in DEFINITION["config"]
    assert "coaching_task_template" in DEFINITION["config"]


def test_llo_weekly_review_pipeline_schema_aggregates_per_flw():
    from commcare_connect.workflow.templates.llo_weekly_review import PIPELINE_SCHEMA

    assert PIPELINE_SCHEMA["grouping_key"] == "username"
    assert PIPELINE_SCHEMA["terminal_stage"] == "aggregated"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/workflow/tests/templates/test_llo_weekly_review.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement skeleton**

```python
# commcare_connect/workflow/templates/llo_weekly_review.py
"""LLO weekly FLW performance review (synthetic-data demo scaffold).

A config-driven template the ACE Phase 6 synthetic generator instantiates.
The KPI list and coaching-task template are filled in via the workflow
definition's ``config`` dict; ACE's polish skill rewrites the JSX to feature
specific FLWs and seeded anomalies. Out of the box this template is
opp-agnostic and renders a generic per-FLW KPI table with a "spawn coaching
task" button and a chat-styled task drawer.
"""

PIPELINE_SCHEMA = {
    "name": "FLW KPI Aggregates",
    "description": (
        "Per-FLW aggregates of the KPIs declared in the workflow's "
        "kpi_config. One row per worker."
    ),
    "version": 1,
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        # Real fields are injected by the seeding step using kpi_config —
        # the scaffold ships an empty list because field paths depend on the
        # opportunity's form schema.
    ],
}

DEFINITION = {
    "name": "LLO Weekly FLW Review",
    "description": (
        "Operational weekly view: each FLW's KPI scorecard, an "
        "underperforming-only filter, and a one-click coaching task spawn."
    ),
    "version": 1,
    "templateType": "llo_weekly_review",
    "statuses": [
        {"id": "pending", "label": "Pending Review", "color": "gray"},
        {"id": "ok", "label": "On Track", "color": "green"},
        {"id": "underperforming", "label": "Underperforming", "color": "yellow"},
        {"id": "task_created", "label": "Coaching Task Created", "color": "blue"},
    ],
    "config": {
        "showSummaryCards": True,
        "showFilters": True,
        # Filled in by ACE Phase 6 synthetic-workflow-seed:
        "kpi_config": [],            # list of KpiSpec dicts
        "coaching_task_template": {  # task-spawn template
            "subject_template": "Coaching feedback — week {week} for {flw_name}",
            "ocs_persona": "supportive_coach",
        },
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, links, actions, onUpdateState, view }) {
    // Scaffold render — ACE Phase 6 polish skill layers per-opp visuals on top.
    const workers = view.workers || [];
    const kpis = (definition.config && definition.config.kpi_config) || [];
    const states = view.state.worker_states || {};
    const tasks = view.state.spawned_tasks || {};
    const isCompleted = view.isCompleted;
    const [showOnlyUnderperforming, setShowOnlyUnderperforming] = React.useState(false);

    const rowsByUser = (view.pipelines.flw_kpis || []).reduce((acc, r) => {
        acc[r.username] = r;
        return acc;
    }, {});

    const filtered = workers.filter(w => {
        if (!showOnlyUnderperforming) return true;
        const row = rowsByUser[w.username] || {};
        return kpis.some(k => row[k.kpi] !== undefined && row[k.kpi] < k.threshold_underperform);
    });

    return (
        <div className="llo-weekly-review">
            <header>
                <h1>{definition.name}</h1>
                {!isCompleted && (
                    <label>
                        <input
                            type="checkbox"
                            checked={showOnlyUnderperforming}
                            onChange={e => setShowOnlyUnderperforming(e.target.checked)}
                        />
                        Show underperforming only
                    </label>
                )}
            </header>
            <table>
                <thead>
                    <tr>
                        <th>FLW</th>
                        {kpis.map(k => <th key={k.kpi}>{k.kpi}</th>)}
                        <th>Status</th>
                        <th>Action</th>
                    </tr>
                </thead>
                <tbody>
                    {filtered.map(w => {
                        const row = rowsByUser[w.username] || {};
                        const status = states[w.username] || "pending";
                        const task = tasks[w.username];
                        return (
                            <tr key={w.username}>
                                <td>{w.name || w.username}</td>
                                {kpis.map(k => <td key={k.kpi}>{row[k.kpi] != null ? row[k.kpi].toFixed(2) : "-"}</td>)}
                                <td>{status}</td>
                                <td>
                                    {task ? (
                                        <button onClick={() => actions.openTaskDrawer(task.id)}>
                                            View coaching chat
                                        </button>
                                    ) : !isCompleted ? (
                                        <button onClick={() => actions.spawnCoachingTask(w.username)}>
                                            Spawn coaching task
                                        </button>
                                    ) : (
                                        <span>—</span>
                                    )}
                                </td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}
"""

TEMPLATE = {
    "key": "llo_weekly_review",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "multi_opp": False,
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": ["flw_kpis"],
        "state_keys": ["worker_states", "spawned_tasks"],
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": PIPELINE_SCHEMA,
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/workflow/tests/templates/test_llo_weekly_review.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/workflow/templates/llo_weekly_review.py commcare_connect/workflow/tests/templates/test_llo_weekly_review.py
git commit -m "feat(workflow): llo_weekly_review SEED template scaffold"
```

---

### Task 2.2: `program_admin_audit` skeleton

**Files:**
- Create: `commcare_connect/workflow/templates/program_admin_audit.py`
- Create: `commcare_connect/workflow/tests/templates/test_program_admin_audit.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/workflow/tests/templates/test_program_admin_audit.py
def test_program_admin_audit_registered():
    from commcare_connect.workflow.templates import list_templates

    keys = {t["key"] for t in list_templates()}
    assert "program_admin_audit" in keys


def test_program_admin_audit_definition_has_watched_workflow_slot():
    from commcare_connect.workflow.templates.program_admin_audit import DEFINITION

    assert "watched_workflow_id" in DEFINITION["config"]


def test_program_admin_audit_supports_saved_runs():
    from commcare_connect.workflow.templates.program_admin_audit import TEMPLATE

    assert TEMPLATE["supports_saved_runs"] is True


def test_program_admin_audit_is_multi_opp_capable():
    from commcare_connect.workflow.templates.program_admin_audit import TEMPLATE

    assert TEMPLATE["multi_opp"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/workflow/tests/templates/test_program_admin_audit.py -v`
Expected: ImportError.

- [ ] **Step 3: Implement**

```python
# commcare_connect/workflow/templates/program_admin_audit.py
"""Program admin audit of the LLO's weekly review process.

Reads the saved runs of an ``llo_weekly_review`` instance and renders a
week-over-week compliance dashboard: did the LLO save a snapshot, did
they spawn coaching tasks for everyone they should have, did flagged
FLWs improve. Multi-opp capable so a regional admin can roll up several
opportunities into one audit.
"""

DEFINITION = {
    "name": "Program Admin LLO Audit",
    "description": (
        "Week-by-week meta view of how well the LLO is performing the "
        "operational weekly review."
    ),
    "version": 1,
    "templateType": "program_admin_audit",
    "statuses": [
        {"id": "pending", "label": "Pending", "color": "gray"},
        {"id": "compliant", "label": "Compliant", "color": "green"},
        {"id": "gap", "label": "Process Gap", "color": "yellow"},
        {"id": "intervention_needed", "label": "Needs Intervention", "color": "red"},
    ],
    "config": {
        "showSummaryCards": True,
        # Set by ACE Phase 6 synthetic-workflow-seed:
        "watched_workflow_id": None,
    },
    "pipeline_sources": [],
}

RENDER_CODE = """function WorkflowUI({ definition, instance, links, actions, onUpdateState, view }) {
    const watchedId = definition.config && definition.config.watched_workflow_id;
    const snapshots = view.watchedSnapshots || [];

    if (!watchedId) {
        return <div>Set <code>watched_workflow_id</code> in this workflow's config.</div>;
    }

    return (
        <div className="program-admin-audit">
            <h1>{definition.name}</h1>
            <p>Watching workflow #{watchedId}</p>
            <table>
                <thead>
                    <tr>
                        <th>Snapshot</th>
                        <th>Captured</th>
                        <th>FLWs reviewed</th>
                        <th>Underperformers flagged</th>
                        <th>Coaching tasks spawned</th>
                        <th>Compliance</th>
                    </tr>
                </thead>
                <tbody>
                    {snapshots.map(s => {
                        const flagged = (s.metrics && s.metrics.flagged) || 0;
                        const spawned = (s.metrics && s.metrics.tasks_spawned) || 0;
                        const compliant = flagged === 0 || spawned >= flagged;
                        return (
                            <tr key={s.name}>
                                <td>{s.name}</td>
                                <td>{s.captured_at}</td>
                                <td>{(s.metrics && s.metrics.workers_reviewed) || 0}</td>
                                <td>{flagged}</td>
                                <td>{spawned}</td>
                                <td>{compliant ? "✓" : "gap"}</td>
                            </tr>
                        );
                    })}
                </tbody>
            </table>
        </div>
    );
}
"""

TEMPLATE = {
    "key": "program_admin_audit",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": {
        "pipelines": [],
        "state_keys": ["audit_decisions"],
    },
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schema": None,  # this template reads the watched workflow's snapshots, not a pipeline
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/workflow/tests/templates/test_program_admin_audit.py -v`
Expected: 4 passed.

- [ ] **Step 5: Run all template tests as a regression check**

Run: `pytest commcare_connect/workflow/tests/templates/ -v`
Expected: all template tests pass (existing + 8 new).

- [ ] **Step 6: Commit + open PR 2**

```bash
git add commcare_connect/workflow/templates/program_admin_audit.py commcare_connect/workflow/tests/templates/test_program_admin_audit.py
git commit -m "feat(workflow): program_admin_audit SEED template scaffold"
```

PR 2 is now ready. Title: `feat(workflow): two SEED templates for ACE Phase 6 synthetic demos`. Product description: "Two new workflow templates ship: an LLO weekly FLW review with embedded coaching tasks, and an admin audit of the LLO's weekly process. They are scaffolds — config-driven and intentionally generic — that ACE's synthetic-data phase populates per-opp."

---

## Phase 3 — MCP Tools (PR 3)

Five new MCP tools wrap the engine + templates + task creation + snapshot save into a clean RPC surface ACE will drive in Plan B. Each tool follows the existing `@register(...)` pattern with `is_write=True/False` per the side effects.

### Task 3.1: `synthetic_register` MCP tool

**Files:**
- Create: `commcare_connect/mcp/tools/synthetic.py`
- Create: `commcare_connect/mcp/tests/test_synthetic_tools.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/mcp/tests/test_synthetic_tools.py
import pytest
from django.contrib.auth import get_user_model

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.mcp.tool_registry import get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_synthetic_register_creates_row(user):
    tool = get_tool("synthetic_register")
    result = tool.handler(
        user=user,
        opportunity_id=4242,
        gdrive_folder_id="folder-x",
        enabled=True,
        label="My Demo",
    )
    assert result["opportunity_id"] == 4242
    assert result["enabled"] is True
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.gdrive_folder_id == "folder-x"
    assert row.label == "My Demo"


@pytest.mark.django_db
def test_synthetic_register_updates_existing_row(user):
    SyntheticOpportunity.objects.create(
        opportunity_id=4242, gdrive_folder_id="old", enabled=False
    )
    tool = get_tool("synthetic_register")
    tool.handler(
        user=user,
        opportunity_id=4242,
        gdrive_folder_id="new",
        enabled=True,
        label=None,
    )
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.gdrive_folder_id == "new"
    assert row.enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tools.py -v`
Expected: KeyError on `synthetic_register`.

- [ ] **Step 3: Implement**

```python
# commcare_connect/mcp/tools/synthetic.py
"""MCP tools for the labs synthetic-data system."""

from __future__ import annotations

from typing import Any

from commcare_connect.labs.synthetic.models import SyntheticOpportunity
from commcare_connect.labs.synthetic.registry import invalidate_cache

from ..tool_registry import register


@register(
    name="synthetic_register",
    description=(
        "Register or update a synthetic-opportunity entry. Set enabled=True "
        "to make labs serve fixtures from the given GDrive folder for this "
        "opportunity_id; set enabled=False to disable without deleting."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "gdrive_folder_id": {"type": "string"},
            "enabled": {"type": "boolean", "default": True},
            "label": {"type": ["string", "null"], "default": None},
        },
        "required": ["opportunity_id", "gdrive_folder_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_register(
    user,
    *,
    opportunity_id: int,
    gdrive_folder_id: str,
    enabled: bool = True,
    label: str | None = None,
) -> dict[str, Any]:
    defaults = {
        "gdrive_folder_id": gdrive_folder_id,
        "enabled": enabled,
        "created_by": user,
    }
    if label is not None:
        defaults["label"] = label
    row, _created = SyntheticOpportunity.objects.update_or_create(
        opportunity_id=opportunity_id,
        defaults=defaults,
    )
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
        "label": row.label,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tools.py::test_synthetic_register_creates_row commcare_connect/mcp/tests/test_synthetic_tools.py::test_synthetic_register_updates_existing_row -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/mcp/tools/synthetic.py commcare_connect/mcp/tests/test_synthetic_tools.py
git commit -m "feat(mcp): synthetic_register tool"
```

---

### Task 3.2: `synthetic_disable` MCP tool

**Files:**
- Modify: `commcare_connect/mcp/tools/synthetic.py`
- Modify: `commcare_connect/mcp/tests/test_synthetic_tools.py`

- [ ] **Step 1: Append the failing test**

```python
# add to commcare_connect/mcp/tests/test_synthetic_tools.py
@pytest.mark.django_db
def test_synthetic_disable_clears_enabled_flag(user):
    SyntheticOpportunity.objects.create(
        opportunity_id=4242, gdrive_folder_id="x", enabled=True
    )
    tool = get_tool("synthetic_disable")
    result = tool.handler(user=user, opportunity_id=4242)
    assert result["enabled"] is False
    row = SyntheticOpportunity.objects.get(opportunity_id=4242)
    assert row.enabled is False
    # folder retained
    assert row.gdrive_folder_id == "x"


@pytest.mark.django_db
def test_synthetic_disable_404s_on_missing_row(user):
    from commcare_connect.mcp.errors import MCPToolError
    tool = get_tool("synthetic_disable")
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=99999)
    assert exc.value.code == "NOT_FOUND"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tools.py::test_synthetic_disable_clears_enabled_flag commcare_connect/mcp/tests/test_synthetic_tools.py::test_synthetic_disable_404s_on_missing_row -v`
Expected: KeyError on `synthetic_disable`.

- [ ] **Step 3: Append the tool to `synthetic.py`**

```python
# add to commcare_connect/mcp/tools/synthetic.py

from commcare_connect.mcp.errors import MCPToolError


@register(
    name="synthetic_disable",
    description=(
        "Disable a synthetic-opportunity entry without deleting it. The "
        "GDrive folder is retained for forensics; labs reverts to real "
        "export reads for this opportunity_id on next request."
    ),
    input_schema={
        "type": "object",
        "properties": {"opportunity_id": {"type": "integer"}},
        "required": ["opportunity_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_disable(user, *, opportunity_id: int) -> dict[str, Any]:
    try:
        row = SyntheticOpportunity.objects.get(opportunity_id=opportunity_id)
    except SyntheticOpportunity.DoesNotExist:
        raise MCPToolError("NOT_FOUND", f"No synthetic entry for opportunity_id={opportunity_id}")
    row.enabled = False
    row.save(update_fields=["enabled", "updated_at"])
    invalidate_cache()
    return {
        "opportunity_id": row.opportunity_id,
        "gdrive_folder_id": row.gdrive_folder_id,
        "enabled": row.enabled,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tools.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/mcp/tools/synthetic.py commcare_connect/mcp/tests/test_synthetic_tools.py
git commit -m "feat(mcp): synthetic_disable tool"
```

---

### Task 3.3: `synthetic_generate_from_manifest` MCP tool

**Files:**
- Modify: `commcare_connect/mcp/tools/synthetic.py`
- Modify: `commcare_connect/mcp/tests/test_synthetic_tools.py`

- [ ] **Step 1: Append the failing test**

```python
# add to commcare_connect/mcp/tests/test_synthetic_tools.py
@pytest.mark.django_db
def test_synthetic_generate_from_manifest_creates_folder_and_row(user, monkeypatch):
    """Tool wires manifest -> engine -> uploader and returns folder_id + counts."""
    from commcare_connect.mcp.tools import synthetic as syn_tools

    manifest_yaml = (
        "opportunity_id: 4242\n"
        "opportunity_name: Demo\n"
        "random_seed: 7\n"
        "timeline:\n"
        "  start_date: 2026-02-01\n"
        "  end_date: 2026-02-14\n"
        "  weeks: 2\n"
        "  visit_cadence_per_week_per_flw: { mean: 2, stddev: 0 }\n"
        "flw_personas:\n"
        "  - id: a\n"
        "    archetype: steady\n"
        "    accuracy_distribution: { mean: 0.9, stddev: 0 }\n"
        "    completeness_distribution: { mean: 0.95, stddev: 0 }\n"
        "    flag_rate: 0\n"
        "beneficiary_cohorts:\n"
        "  - id: primary\n"
        "    size: 5\n"
        "    field_distributions: {}\n"
        "    progression: flat\n"
        "anomalies: []\n"
        "kpi_config:\n"
        "  - kpi: accuracy\n"
        "    field_path: form.weight_kg\n"
        "    aggregation: validated_rate\n"
        "    threshold_underperform: 0.75\n"
        "coaching_arcs: []\n"
    )

    class _FakeDrive:
        def create_folder(self, name, parent_id): return f"folder-{name}"
        def upload_file(self, fid, fname, content): return f"file-{fname}"

    monkeypatch.setattr(syn_tools, "DriveClient", lambda: _FakeDrive())
    monkeypatch.setattr(
        syn_tools, "_load_opportunity_detail",
        lambda opp_id, user: {"id": opp_id, "name": "X", "payment_units": [], "deliver_units": []},
    )
    monkeypatch.setattr(
        syn_tools, "_load_form_schema_for_opp",
        lambda opp_id, user: __import__(
            "commcare_connect.labs.synthetic.generator.schema_loader",
            fromlist=["FormSchema"],
        ).FormSchema(questions=[]),
    )
    monkeypatch.setenv("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID", "p")
    from django.test import override_settings
    with override_settings(LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID="p"):
        tool = get_tool("synthetic_generate_from_manifest")
        result = tool.handler(user=user, opportunity_id=4242, manifest_yaml=manifest_yaml)

    assert result["folder_id"].startswith("folder-")
    assert "user_visits" in result["record_counts"]
    assert SyntheticOpportunity.objects.get(opportunity_id=4242).enabled is True


@pytest.mark.django_db
def test_synthetic_generate_rejects_invalid_manifest(user):
    tool = get_tool("synthetic_generate_from_manifest")
    from commcare_connect.mcp.errors import MCPToolError
    with pytest.raises(MCPToolError) as exc:
        tool.handler(user=user, opportunity_id=1, manifest_yaml="not: valid: yaml: at all: :")
    assert exc.value.code == "INVALID_SCHEMA"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tools.py -k synthetic_generate -v`
Expected: KeyError on `synthetic_generate_from_manifest`.

- [ ] **Step 3: Append the tool to `synthetic.py`**

```python
# add to commcare_connect/mcp/tools/synthetic.py

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.labs.integrations.commcare_hq import HqApi
from commcare_connect.labs.synthetic.gdrive import DriveClient
from commcare_connect.labs.synthetic.generator.engine import generate as _generate
from commcare_connect.labs.synthetic.generator.manifest import (
    Manifest,
    ManifestValidationError,
)
from commcare_connect.labs.synthetic.generator.schema_loader import (
    FormSchema,
    QuestionSpec,
    load_form_schema,
)
from commcare_connect.labs.synthetic.generator.uploader import upload_and_register


def _load_opportunity_detail(opportunity_id: int, user) -> dict:
    """Pull live opp detail (payment units, deliver units) from prod via OAuth."""
    client = LabsRecordAPIClient.for_user(user)
    return client.get_opportunity_detail(opportunity_id)


def _load_form_schema_for_opp(opportunity_id: int, user) -> FormSchema:
    """Resolve the opp's primary form schema. Returns empty schema if HQ unreachable."""
    try:
        api = HqApi.for_user(user)
        app_id, form_xmlns = api.primary_form_for_opportunity(opportunity_id)
        return load_form_schema(api, app_id=app_id, form_xmlns=form_xmlns)
    except Exception:
        return FormSchema(questions=[])


@register(
    name="synthetic_generate_from_manifest",
    description=(
        "Generate the five fixture JSON files from a YAML manifest, upload "
        "them to a fresh GDrive folder, and register the opportunity as "
        "synthetic. Returns the new folder_id and per-endpoint record counts."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "manifest_yaml": {"type": "string"},
        },
        "required": ["opportunity_id", "manifest_yaml"],
        "additionalProperties": False,
    },
    is_write=True,
)
def synthetic_generate_from_manifest(
    user,
    *,
    opportunity_id: int,
    manifest_yaml: str,
) -> dict[str, Any]:
    try:
        manifest = Manifest.from_yaml(manifest_yaml)
    except ManifestValidationError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc))

    if manifest.opportunity_id != opportunity_id:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"manifest.opportunity_id ({manifest.opportunity_id}) != "
            f"tool arg opportunity_id ({opportunity_id})",
        )

    detail = _load_opportunity_detail(opportunity_id, user)
    form_schema = _load_form_schema_for_opp(opportunity_id, user)
    fixtures = _generate(
        manifest=manifest, opportunity_detail=detail, form_schema=form_schema
    )
    drive = DriveClient()
    result = upload_and_register(
        drive=drive,
        opportunity_id=opportunity_id,
        opportunity_name=manifest.opportunity_name,
        fixtures=fixtures,
    )
    return {
        "folder_id": result.folder_id,
        "record_counts": result.record_counts,
        "form_schema_questions": len(form_schema.questions),
    }
```

> **Note for the engineer:** The two helpers `_load_opportunity_detail` and `_load_form_schema_for_opp` assume `LabsRecordAPIClient.for_user` and `HqApi.for_user` exist. If those constructors don't match exactly, follow the patterns in the actual files (`commcare_connect/labs/integrations/connect/api_client.py` and the equivalent HQ client) — the goal is to reach `get_opportunity_detail` and `get_form_json_paths` for the authenticated user. Adjust signatures to match what's there. Do NOT invent new auth surfaces.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tools.py -k synthetic_generate -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/mcp/tools/synthetic.py commcare_connect/mcp/tests/test_synthetic_tools.py
git commit -m "feat(mcp): synthetic_generate_from_manifest tool"
```

---

### Task 3.4: `task_create_synthetic` MCP tool

Creates a labs Task LabsRecord with an embedded OCS conversation transcript.

**Files:**
- Create: `commcare_connect/mcp/tools/synthetic_tasks.py`
- Create: `commcare_connect/mcp/tests/test_synthetic_tasks_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/mcp/tests/test_synthetic_tasks_tool.py
import pytest
from django.contrib.auth import get_user_model
from unittest.mock import MagicMock

from commcare_connect.mcp.tool_registry import get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_task_create_synthetic_persists_via_labs_api(user, monkeypatch):
    fake_record = MagicMock()
    fake_record.id = 5001
    fake_record.experiment = "task"
    fake_record.type = "synthetic_task"
    fake_record.data = {
        "title": "Coaching feedback for asha",
        "assigned_to": "asha",
        "ocs_conversation": [{"role": "bot", "text": "Hi", "ts": "2026-03-01T09:00:00Z"}],
        "status": "completed",
    }

    fake_client = MagicMock()
    fake_client.create_record.return_value = fake_record

    from commcare_connect.mcp.tools import synthetic_tasks
    monkeypatch.setattr(synthetic_tasks, "_labs_api_for_user", lambda u: fake_client)

    tool = get_tool("task_create_synthetic")
    result = tool.handler(
        user=user,
        opportunity_id=4242,
        assigned_to="asha",
        subject="Coaching feedback for asha",
        ocs_conversation=[{"role": "bot", "text": "Hi", "ts": "2026-03-01T09:00:00Z"}],
    )
    assert result["id"] == 5001
    fake_client.create_record.assert_called_once()
    call_kwargs = fake_client.create_record.call_args.kwargs
    assert call_kwargs["experiment"] == "task"
    assert call_kwargs["type"] == "synthetic_task"
    assert call_kwargs["data"]["assigned_to"] == "asha"
    assert call_kwargs["data"]["ocs_conversation"][0]["role"] == "bot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tasks_tool.py -v`
Expected: KeyError on `task_create_synthetic`.

- [ ] **Step 3: Implement**

```python
# commcare_connect/mcp/tools/synthetic_tasks.py
"""MCP tool to create a synthetic labs Task with embedded OCS conversation."""

from __future__ import annotations

from typing import Any

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

from ..tool_registry import register


def _labs_api_for_user(user) -> LabsRecordAPIClient:
    return LabsRecordAPIClient.for_user(user)


@register(
    name="task_create_synthetic",
    description=(
        "Create a labs Task LabsRecord with an embedded synthetic OCS "
        "coaching conversation. Used by ACE Phase 6 synthetic-workflow-seed "
        "to spawn coaching tasks attached to underperforming FLWs."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_id": {"type": "integer"},
            "assigned_to": {"type": "string"},
            "subject": {"type": "string"},
            "ocs_conversation": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "role": {"enum": ["bot", "flw"]},
                        "text": {"type": "string"},
                        "ts": {"type": "string"},
                    },
                    "required": ["role", "text", "ts"],
                },
            },
            "status": {"type": "string", "default": "completed"},
        },
        "required": ["opportunity_id", "assigned_to", "subject", "ocs_conversation"],
        "additionalProperties": False,
    },
    is_write=True,
)
def task_create_synthetic(
    user,
    *,
    opportunity_id: int,
    assigned_to: str,
    subject: str,
    ocs_conversation: list[dict[str, Any]],
    status: str = "completed",
) -> dict[str, Any]:
    client = _labs_api_for_user(user)
    record = client.create_record(
        experiment="task",
        type="synthetic_task",
        opportunity_id=opportunity_id,
        data={
            "title": subject,
            "assigned_to": assigned_to,
            "ocs_conversation": ocs_conversation,
            "status": status,
            "synthetic": True,
        },
    )
    return {
        "id": record.id,
        "assigned_to": record.data.get("assigned_to"),
        "title": record.data.get("title"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/mcp/tests/test_synthetic_tasks_tool.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/mcp/tools/synthetic_tasks.py commcare_connect/mcp/tests/test_synthetic_tasks_tool.py
git commit -m "feat(mcp): task_create_synthetic tool"
```

---

### Task 3.5: `workflow_save_snapshot` MCP tool

Calls the existing `build_snapshot` hook on the template and persists the snapshot to the workflow definition's `saved_runs[]` list.

**Files:**
- Create: `commcare_connect/mcp/tools/workflow_snapshots.py`
- Create: `commcare_connect/mcp/tests/test_workflow_snapshot_tool.py`

- [ ] **Step 1: Write the failing test**

```python
# commcare_connect/mcp/tests/test_workflow_snapshot_tool.py
from unittest.mock import MagicMock

import pytest
from django.contrib.auth import get_user_model

from commcare_connect.mcp.tool_registry import get_tool


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="t", password="p")


@pytest.mark.django_db
def test_workflow_save_snapshot_appends_to_saved_runs(user, monkeypatch):
    from commcare_connect.mcp.tools import workflow_snapshots as ws

    fake_workflow = MagicMock()
    fake_workflow.id = 100
    fake_workflow.template_key = "llo_weekly_review"
    fake_workflow.data = {"saved_runs": [], "state": {"worker_states": {"asha": "ok"}}}

    fake_client = MagicMock()
    fake_client.get_workflow.return_value = fake_workflow
    fake_client.update_workflow.return_value = fake_workflow

    monkeypatch.setattr(ws, "_workflow_data_access_for_user", lambda u: fake_client)
    monkeypatch.setattr(
        ws, "_build_snapshot",
        lambda template_key, workflow: {"name": "ignored",
                                        "metrics": {"workers_reviewed": 1}},
    )

    tool = get_tool("workflow_save_snapshot")
    result = tool.handler(
        user=user,
        workflow_id=100,
        snapshot_name="Week 1",
        captured_at="2026-02-07T12:00:00Z",
    )
    assert result["workflow_id"] == 100
    assert result["snapshot_name"] == "Week 1"
    fake_client.update_workflow.assert_called_once()
    saved_payload = fake_client.update_workflow.call_args.kwargs["data"]
    saved_runs = saved_payload["saved_runs"]
    assert saved_runs[-1]["name"] == "Week 1"
    assert saved_runs[-1]["captured_at"] == "2026-02-07T12:00:00Z"
    assert saved_runs[-1]["metrics"]["workers_reviewed"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest commcare_connect/mcp/tests/test_workflow_snapshot_tool.py -v`
Expected: KeyError on `workflow_save_snapshot`.

- [ ] **Step 3: Implement**

```python
# commcare_connect/mcp/tools/workflow_snapshots.py
"""MCP tool to save a snapshot of a saved-runs-capable workflow."""

from __future__ import annotations

from typing import Any

from commcare_connect.workflow.data_access import WorkflowDataAccess
from commcare_connect.workflow.templates import get_template

from ..errors import MCPToolError
from ..tool_registry import register


def _workflow_data_access_for_user(user) -> WorkflowDataAccess:
    return WorkflowDataAccess.for_user(user)


def _build_snapshot(template_key: str, workflow) -> dict[str, Any]:
    """Call the template's build_snapshot hook if it has one; else best-effort."""
    template = get_template(template_key)
    if template and hasattr(template, "build_snapshot"):
        return template.build_snapshot(workflow)
    return {
        "state_keys": list((workflow.data.get("state") or {}).keys()),
        "metrics": {},
    }


@register(
    name="workflow_save_snapshot",
    description=(
        "Capture a saved-run snapshot of a workflow that supports them. "
        "Calls the template's build_snapshot hook (when present) and appends "
        "the result to the workflow's saved_runs[] list."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "snapshot_name": {"type": "string"},
            "captured_at": {"type": "string"},  # ISO 8601
        },
        "required": ["workflow_id", "snapshot_name", "captured_at"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_save_snapshot(
    user,
    *,
    workflow_id: int,
    snapshot_name: str,
    captured_at: str,
) -> dict[str, Any]:
    client = _workflow_data_access_for_user(user)
    workflow = client.get_workflow(workflow_id)
    if workflow is None:
        raise MCPToolError("NOT_FOUND", f"workflow {workflow_id} not found")

    snapshot_payload = _build_snapshot(workflow.template_key, workflow)
    snapshot_payload["name"] = snapshot_name
    snapshot_payload["captured_at"] = captured_at

    data = dict(workflow.data)
    saved_runs = list(data.get("saved_runs") or [])
    saved_runs.append(snapshot_payload)
    data["saved_runs"] = saved_runs

    client.update_workflow(workflow_id, data=data)

    return {
        "workflow_id": workflow_id,
        "snapshot_name": snapshot_name,
        "captured_at": captured_at,
        "snapshot_count": len(saved_runs),
    }
```

> **Note for the engineer:** `WorkflowDataAccess.for_user`, `get_workflow`, and `update_workflow` are presumed-existing patterns. Verify against the actual `commcare_connect/workflow/data_access.py` and adjust the helper to match its real shape (it may go through `LabsRecordAPIClient` rather than a dedicated workflow access class). Same for `get_template` — adjust to whatever the real registry call is.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest commcare_connect/mcp/tests/test_workflow_snapshot_tool.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/mcp/tools/workflow_snapshots.py commcare_connect/mcp/tests/test_workflow_snapshot_tool.py
git commit -m "feat(mcp): workflow_save_snapshot tool"
```

---

### Task 3.6: Register all five tools in the registry

**Files:**
- Modify: `commcare_connect/mcp/tool_registry.py` (or `commcare_connect/mcp/tools/__init__.py` — whichever owns the import-side-effect registration)

- [ ] **Step 1: Inspect how existing tools are registered**

Run: `grep -rn "from .tools" commcare_connect/mcp/ | head -20`
Read the registration entry point and confirm where to add imports.

- [ ] **Step 2: Add imports for the new tool modules**

The existing pattern imports each tool module from `tools/__init__.py` to trigger `@register` side effects. Add:

```python
# in commcare_connect/mcp/tools/__init__.py — add to existing imports
from . import synthetic        # noqa: F401  -- registers synthetic_register, synthetic_disable, synthetic_generate_from_manifest
from . import synthetic_tasks   # noqa: F401  -- registers task_create_synthetic
from . import workflow_snapshots  # noqa: F401  -- registers workflow_save_snapshot
```

- [ ] **Step 3: Write the registration test**

```python
# add to commcare_connect/mcp/tests/test_synthetic_tools.py
def test_all_phase6_tools_are_registered():
    from commcare_connect.mcp.tool_registry import list_tool_names
    names = set(list_tool_names())
    assert {
        "synthetic_register",
        "synthetic_disable",
        "synthetic_generate_from_manifest",
        "task_create_synthetic",
        "workflow_save_snapshot",
    }.issubset(names)
```

- [ ] **Step 4: Run all MCP tests to verify**

Run: `pytest commcare_connect/mcp/ -v`
Expected: every existing test passes + the new registration test passes.

- [ ] **Step 5: Commit**

```bash
git add commcare_connect/mcp/tools/__init__.py commcare_connect/mcp/tests/test_synthetic_tools.py
git commit -m "feat(mcp): register Phase 6 synthetic tools in registry"
```

---

### Task 3.7: End-to-end smoke test against a dev opportunity

This is a **manual** verification step — it confirms the whole stack works against a real GDrive folder and a real labs dev opp.

**Files:** none

- [ ] **Step 1: Pick a dev opportunity**

Use a non-production opportunity_id you have access to (ask the user if unsure — never test against a live/active opp).

- [ ] **Step 2: Invoke the engine through the labs MCP**

From a Claude Code session with the `connect_labs` MCP wired up (or the equivalent shell tool that calls into the MCP), call `synthetic_generate_from_manifest` with a small manifest (5 personas, 2 weeks, 50 beneficiaries). Expected response:
- `folder_id` non-empty
- `record_counts.user_visits` > 0
- `record_counts.user_data` == number of personas

- [ ] **Step 3: Verify the GDrive folder**

Open the folder in GDrive (or via `drive_list_folder`). Expected: 5 JSON files matching `ENDPOINT_FILES`.

- [ ] **Step 4: Verify the synthetic-opportunity registry entry**

Run a small script via Django shell:
```python
from commcare_connect.labs.synthetic.models import SyntheticOpportunity
SyntheticOpportunity.objects.get(opportunity_id=<id>).gdrive_folder_id
```
Expected: matches the folder_id from step 2.

- [ ] **Step 5: Verify labs serves synthetic data**

Open the opp in labs and check that an existing dashboard (e.g., the basic "user visits" view) shows your synthetic FLW names — not empty / not real prod data.

- [ ] **Step 6: Tear down**

Call `synthetic_disable(opportunity_id)`. Confirm labs returns to empty (since the opp has no real production data).

- [ ] **Step 7: Open PR 3**

PR 3 title: `feat(mcp): synthetic data + workflow snapshot tools for ACE Phase 6`. Product description: "Five new MCP tools so the ACE plugin can drive end-to-end synthetic-data generation: register a synthetic opp, disable one, generate fixtures from a YAML manifest, create a synthetic coaching task, and save a workflow snapshot. With these, an operator can mint a story-coherent synthetic opportunity from a manifest in one MCP call."

---

## Final spec coverage check

Before declaring plan A done, walk through `2026-05-05-ace-synthetic-data-phase-design.md` and confirm:

- [ ] §5.1 Manifest schema → Task 1.2
- [ ] §5.2 Generator engine package layout → Tasks 1.1–1.11
- [ ] §5.2 Engine determinism via `random_seed` → Task 1.10 golden test
- [ ] §5.2 Uploader composes engine + GDrive + `SyntheticOpportunity` → Task 1.11
- [ ] §5.3 `llo_weekly_review` SEED template (saved-runs, KPI config slot) → Task 2.1
- [ ] §5.3 `program_admin_audit` SEED template (multi-opp, watched workflow) → Task 2.2
- [ ] §5.4 `synthetic_generate_from_manifest` tool → Task 3.3
- [ ] §5.4 `synthetic_register` tool → Task 3.1
- [ ] §5.4 `synthetic_disable` tool → Task 3.2
- [ ] §5.4 `task_create_synthetic` tool → Task 3.4
- [ ] §5.4 `workflow_save_snapshot` tool → Task 3.5
- [ ] §5.4 All five tools registered → Task 3.6
- [ ] §11 Testing strategy: unit + golden integration test → covered per-task + Task 1.10
- [ ] §13 Rollout PRs 1, 2, 3 each independently shippable → ✓ (each phase ends at a commit/PR)

If any spec section above isn't covered when you reach this point, file the gap and fix before merging.

## Out of scope for plan A — handled in plan B

- ACE plugin: agent file + 7 skills + 4 evals + persona catalog + phase renumbering
- Eval calibration extensions (vision-judge bootstrap)
- ACE-side opp.yaml `synthetic` block updates

Plan B will be drafted in a separate document once plan A's PRs are merged and deployed to labs prod.
