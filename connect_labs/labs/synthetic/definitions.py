"""A synthetic programme's definition: what regenerating it makes.

Every synthetic programme a clone builds keeps its definition here, so it can be
regenerated (rebuilt in place from fresh measurements of its real sources) with
one click or one tool call, as whoever asks, without anyone handing over a spec.

Where things live, and why:

    the definition    labs DB, the `synthetic` settings namespace, programme layer
                      (scope_config: versioned, attributed, undoable; read and
                      changed by the programme's members and Dimagi staff, on the
                      programme's settings page or with labs_config_get/set)
    the measurements  Google Drive bundles (bundle.py): large, written by each
                      generation's step 1, read by its step 2
    templates         cohorts/*.yaml in this repo: reviewed starting points for a
                      FIRST build (`synthetic_clone_opp(template="kmc")`); a
                      programme built from one keeps its own copy from then on

A definition is a cohort spec (cohort.CohortSpec) without the two things that are
not choices: the programme id (it is the scope the definition is stored on) and
the bundle root (each generation measures afresh into a new Drive folder).
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.scope_config.namespaces import Namespace, register_namespace

NAMESPACE = "synthetic"
TEMPLATES_DIR = Path(__file__).parent / "cohorts"

# Keys a cohort spec carries that a stored definition does not (see module docstring).
_NOT_STORED = ("program_id", "bundle_root")


def _check(data: dict) -> None:
    """A layer's definition must be a cohort spec that would generate."""
    definition = data.get("definition")
    if definition is None:
        return
    spec_from_definition(definition)


def spec_from_definition(definition: dict, program_id: int | None = None) -> CohortSpec:
    """The cohort spec a stored definition generates, checked. Raises ValueError for the person."""
    if not isinstance(definition, dict):
        raise ValueError("a synthetic definition is an object (a cohort spec)")
    stray = [k for k in _NOT_STORED if k in definition]
    if stray:
        raise ValueError(
            f"a definition does not carry {', '.join(stray)}: the programme it is stored on is its id, "
            "and every generation measures into a fresh Drive folder"
        )
    spec = CohortSpec.from_dict(definition)
    if spec.image_config:
        from pydantic import ValidationError

        from connect_labs.labs.synthetic.generator.fixtures.manifest import ImageConfig

        try:
            ImageConfig(**spec.image_config)
        except ValidationError as exc:
            raise ValueError(f"image_config: {exc}") from None
    spec.program_id = program_id
    spec.bundle_root = "gdrive:"
    return spec


def definition_from_spec(spec: CohortSpec) -> dict:
    """A cohort spec as a stored definition (JSON: map keys become strings, which the spec reads back)."""
    data = yaml.safe_load(spec.to_yaml())
    for key in _NOT_STORED:
        data.pop(key, None)
    return _json_keys(data)


def _json_keys(value):
    if isinstance(value, dict):
        return {str(k): _json_keys(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_keys(v) for v in value]
    return value


register_namespace(
    Namespace(
        key=NAMESPACE,
        label="Synthetic data",
        description=(
            "What regenerating this synthetic programme makes: the real opportunities it is measured from, "
            "its names and choices (curation, case timelines, invented names, demo photo cases)."
        ),
        schema={
            "type": "object",
            "properties": {
                "definition": {"type": "object"},
                # Written by the generation job: when, by whom, which job, what it made.
                "last_generation": {"type": "object"},
            },
            "additionalProperties": False,
        },
        layers=frozenset({"program"}),
        check=_check,
    )
)


# --- reading and saving ----------------------------------------------------------


def stored(program_id: int) -> dict:
    """The programme's stored layer ({} if it has none). No permission check: callers check."""
    from connect_labs.scope_config.models import ScopeConfig

    row = ScopeConfig.objects.filter(scope_type="program", scope_key=str(int(program_id)), namespace=NAMESPACE).first()
    return copy.deepcopy(row.data) if row else {}


def definition_for(program_id: int) -> dict | None:
    return stored(program_id).get("definition")


def save_definition(program_id: int, definition: dict, caller, *, via: str) -> None:
    """Replace the programme's definition, as `caller` (checked, versioned, undoable)."""
    from connect_labs.scope_config import service
    from connect_labs.scope_config.merge import diff_patch
    from connect_labs.scope_config.scopes import Scope

    old = definition_for(program_id) or {}
    patch = diff_patch(old, definition)
    if not patch and old:
        return
    service.update(NAMESPACE, Scope.of("program", program_id), {"definition": patch or definition}, caller, via=via)


def record_generation(program_id: int, info: dict, *, definition: dict | None = None) -> None:
    """The job's own record of a generation (and, for a first build, the definition it was built from)."""
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.scope_config import service
    from connect_labs.scope_config.merge import diff_patch
    from connect_labs.scope_config.scopes import Scope

    patch: dict = {"last_generation": _json_keys(info)}
    if definition is not None:
        old = definition_for(program_id) or {}
        patch["definition"] = diff_patch(old, definition) or definition
    service.update(NAMESPACE, Scope.of("program", program_id), patch, SYSTEM, via="operation:synthetic_generate")


def programmes_with_definitions() -> list[tuple[int, dict]]:
    """Every programme that has a definition, newest first, as (program_id, layer). Callers filter by access."""
    from connect_labs.scope_config.models import ScopeConfig

    rows = ScopeConfig.objects.filter(namespace=NAMESPACE, scope_type="program").order_by("-updated_at")
    return [(int(r.scope_key), copy.deepcopy(r.data)) for r in rows if (r.data or {}).get("definition")]


# --- templates -------------------------------------------------------------------


def template_names() -> list[str]:
    return sorted(p.stem for p in TEMPLATES_DIR.glob("*.yaml"))


def template_definition(name: str) -> dict:
    """A template from cohorts/ as a definition. Raises ValueError naming the templates there are."""
    if name not in template_names():
        raise ValueError(f"no synthetic template {name!r}; there are: {', '.join(template_names()) or 'none'}")
    data = yaml.safe_load((TEMPLATES_DIR / f"{name}.yaml").read_text()) or {}
    for key in _NOT_STORED:
        data.pop(key, None)
    definition = _json_keys(data)
    spec_from_definition(definition)
    return definition


def definition_from_yaml(text: str) -> dict:
    """A definition a person wrote as YAML (program_id / bundle_root dropped). Raises ValueError."""
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"definition YAML parse error: {exc}") from None
    if not isinstance(data, dict):
        raise ValueError("a definition is a YAML mapping (a cohort spec)")
    for key in _NOT_STORED:
        data.pop(key, None)
    definition = _json_keys(data)
    spec_from_definition(definition)
    return definition
