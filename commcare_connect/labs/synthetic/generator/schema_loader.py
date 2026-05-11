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

    json_path: str  # e.g., "form.weight_kg"
    kind: str  # "decimal", "int", "text", "select", "multiselect", "date", "image"
    choices: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FormSchema:
    """The set of question specs the engine can fill for one form."""

    questions: list[QuestionSpec]

    def by_path(self) -> dict[str, QuestionSpec]:
        return {q.json_path: q for q in self.questions}


class _HqApi(Protocol):
    def get_form_json_paths(self, app_id: str, form_xmlns: str) -> dict[str, Any]:
        ...


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
        cleaned = cleaned[len("data/") :]
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


def parse_form_schema_from_app_json(
    app_json: dict[str, Any],
    *,
    app_type: str = "deliver",
) -> FormSchema:
    """Translate an HQ ``/export/opportunity/<id>/app_structure/`` response into a FormSchema.

    The response shape (from production Connect) is::

        {
          "learn_app":   {"modules": [{"forms": [{"questions": [...]}, ...]}, ...]} | null,
          "deliver_app": {"modules": [...]} | null
        }

    Collects questions from ALL forms in ALL modules so that multi-module apps
    (e.g. registration + service delivery) are fully represented. Duplicate
    json_path entries are deduplicated, keeping the first occurrence.
    Returns an empty FormSchema if the app of that type is missing or has no
    forms — callers degrade gracefully.
    """
    if not isinstance(app_json, dict):
        return FormSchema(questions=[])
    app = app_json.get(f"{app_type}_app")
    if not isinstance(app, dict):
        return FormSchema(questions=[])

    seen: set[str] = set()
    questions: list[QuestionSpec] = []
    for module in app.get("modules") or []:
        if not isinstance(module, dict):
            continue
        for form in module.get("forms") or []:
            if not isinstance(form, dict):
                continue
            for q in _hq_form_to_schema(form).questions:
                if q.json_path not in seen:
                    seen.add(q.json_path)
                    questions.append(q)
    return FormSchema(questions=questions)


def _hq_form_to_schema(form: dict[str, Any]) -> FormSchema:
    """Flatten one HQ form's question tree into a FormSchema."""
    questions: list[QuestionSpec] = []
    for q in form.get("questions") or []:
        if isinstance(q, dict):
            questions.extend(_hq_walk_question(q))
    return FormSchema(questions=questions)


def _hq_walk_question(q: dict[str, Any]) -> list[QuestionSpec]:
    """Walk an HQ question dict; descend into ``children`` for groups, emit leaves."""
    children = q.get("children")
    if children:
        out: list[QuestionSpec] = []
        for child in children:
            if isinstance(child, dict):
                out.extend(_hq_walk_question(child))
        return out
    value = q.get("value", "")
    if not value:
        return []
    kind = _KIND_MAP.get(q.get("type", ""), "text")
    options = q.get("options") or []
    choices = [opt.get("value", "") for opt in options if isinstance(opt, dict) and opt.get("value")]
    return [
        QuestionSpec(
            json_path=_xpath_to_json_path(value),
            kind=kind,
            choices=choices,
        )
    ]
