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
