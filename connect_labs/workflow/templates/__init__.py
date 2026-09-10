"""
Workflow Templates Registry.

This module automatically discovers and registers workflow templates from
individual template files in this directory.

Each template file should export a TEMPLATE dict with:
- key: Unique identifier
- name: Human-readable name
- description: Brief description
- icon: Font Awesome icon class
- color: Tailwind color name
- definition: Workflow definition dict
- render_code: JSX render code string
- pipeline_schema: Optional pipeline schema dict
- pipeline_schemas: Optional list of pipeline schema dicts (for multi-source templates)
- multi_opp: Optional bool (default False). When True, the template opts in to
  multi-opportunity support: the create flow shows an opp picker, the run page
  shows an opp editor, and pipeline rows/workers are tagged with opportunity_id.

Usage:
    from connect_labs.workflow.templates import (
        TEMPLATES,
        get_template,
        list_templates,
        create_workflow_from_template,
    )
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from connect_labs.workflow.data_access import WorkflowDataAccess

logger = logging.getLogger(__name__)

# =============================================================================
# Template Registry
# =============================================================================

# Discovered templates will be stored here
TEMPLATES: dict[str, dict] = {}


def _discover_templates() -> None:
    """
    Discover and register all templates from modules in this package.

    Each module should export a TEMPLATE dict. Modules starting with '_' or
    named 'base' are skipped.

    Saved-runs opt-in (see WORKFLOW_REFERENCE.md §"Saved-runs templates"):
    - `TEMPLATE["supports_saved_runs"] = True` enables the in_progress→completed
      lifecycle for this template's runs.
    - Optional `TEMPLATE["snapshot_inputs"]` declares what the framework's
      default hook should capture: `{"pipelines": [aliases], "workers": bool,
      "state_keys": [keys]}`. Anything not listed is not captured.
    - Optional `TEMPLATE["snapshot_schema"]` documents the shape render code
      can read from `instance.snapshot` (consumed by the FE `useRunView`
      helper and the completion confirmation copy).
    - Optional module-level `build_snapshot(*, pipelines, state, opportunity_id,
      **context) -> dict` overrides the default hook entirely — use when the
      snapshot shape differs from the inputs (computed summaries, KPIs, etc.).
    """
    import connect_labs.workflow.templates as templates_package

    for _, module_name, _ in pkgutil.iter_modules(templates_package.__path__):
        # Skip private modules and base
        if module_name.startswith("_") or module_name == "base":
            continue

        try:
            module = importlib.import_module(f".{module_name}", package=__name__)
            if hasattr(module, "TEMPLATE"):
                template = module.TEMPLATE
                key = template.get("key")
                if key:
                    if hasattr(module, "build_snapshot") and callable(module.build_snapshot):
                        template["build_snapshot"] = module.build_snapshot
                    if hasattr(module, "run_default") and callable(module.run_default):
                        template["run_default"] = module.run_default
                    TEMPLATES[key] = template
                    logger.debug(f"Registered workflow template: {key}")
                else:
                    logger.warning(f"Template in {module_name} missing 'key' field")
        except Exception as e:
            logger.error(f"Failed to load template from {module_name}: {e}")


# Discover templates on module load
_discover_templates()


# =============================================================================
# Public API
# =============================================================================


def get_template(template_key: str) -> dict | None:
    """
    Get a workflow template by key.

    Args:
        template_key: Template identifier (e.g., 'performance_review')

    Returns:
        Template dict with 'name', 'description', 'definition', 'render_code'
        or None if not found
    """
    return TEMPLATES.get(template_key)


def run_default_for_definition(definition, *, access_token, request=None, **kwargs) -> dict:
    """Run a workflow with its default settings (no UI). Raises ValueError if the
    definition's template doesn't support default-run."""
    key = definition.template_type or (definition.data.get("config") or {}).get("templateType")
    template = TEMPLATES.get(key) if key else None
    if not template or not template.get("supports_default_run") or not callable(template.get("run_default")):
        raise ValueError(f"Workflow {getattr(definition, 'id', '?')} (template {key!r}) does not support default-run.")
    return template["run_default"](definition=definition, access_token=access_token, request=request, **kwargs)


def template_supports_default_run(template_key: str | None) -> bool:
    """True iff the named template supports headless default-run (schedulable)."""
    if not template_key:
        return False
    template = TEMPLATES.get(template_key)
    return bool(template and template.get("supports_default_run") and callable(template.get("run_default")))


# The multi-select option types, mapped to the coercion their stored values get. Both
# behave identically -- a set chosen from choices_from_config -- and differ ONLY in the
# type of the value, so every place that handles one handles the other through this map
# rather than through a second copy of the same branch that could drift from the first.
MULTI_OPTION_COERCERS = {"multi_int": int, "multi_str": str}

SCHEDULE_OPTION_TYPES = ("int", "bool", *MULTI_OPTION_COERCERS)

# The config key every schedulable template keeps its headless-run settings under. Named
# here rather than per template so the scheduling UI and the endpoint that saves it agree
# with run_default without importing the template module.
SCHEDULE_DEFAULTS_CONFIG_KEY = "schedule_defaults"


def template_schedule_options(template_key: str | None) -> list[dict]:
    """Settings a template lets the scheduling UI write to ``config.schedule_defaults``.

    A template's ``run_default`` reads its settings from ``config.schedule_defaults``,
    which had no editing surface at all: render code can only write RUN state, and no API
    endpoint updates a definition's config. So a cap typed into a dashboard applied to
    that one run while a schedule silently ran without it, and changing which
    opportunities a schedule covered meant an out-of-band API call. Declaring an option
    here is what puts it in the schedule dialog and makes it persist.

    Two deliberate types rather than a general config editor - that is what keeps every
    value cheap to validate and safe to merge:

    ``int``
        One bounded whole number: ``{key, label, help, min, max}``. Blank clears it.
    ``multi_int``
        A non-empty set of whole numbers chosen from a fixed list, e.g. which
        opportunities a schedule covers. ``choices_from_config`` names the config key
        holding ``{value: label}``, so the choices come from the workflow's OWN config
        and stay correct per definition instead of being frozen at import.
    ``bool``
        An on/off flag, e.g. a dry run that reports what it would do and creates
        nothing.

    Returns [] for templates that declare none, which is every template but one.
    """
    if not template_supports_default_run(template_key):
        return []
    template = TEMPLATES.get(template_key) or {}
    options = []
    for opt in template.get("schedule_options") or []:
        key = (opt or {}).get("key")
        opt_type = (opt or {}).get("type") or "int"
        if not key or opt_type not in SCHEDULE_OPTION_TYPES:
            # Skip rather than raise: a malformed declaration must not take down the
            # whole workflow list page for every other template.
            logger.warning("Ignoring schedule option %r on template %r", opt, template_key)
            continue
        resolved = {
            "key": key,
            "type": opt_type,
            "label": opt.get("label") or key,
            "help": opt.get("help") or "",
        }
        if opt_type == "int":
            resolved["min"] = int(opt.get("min", 1))
            resolved["max"] = int(opt.get("max", 1000000))
        elif opt_type in MULTI_OPTION_COERCERS:
            resolved["choices_from_config"] = opt.get("choices_from_config") or ""
            # What to tick when the config has no saved selection yet. The dialog posts
            # EVERY option it renders, so a multi-select with nothing ticked posts an
            # empty list, fails the "select at least one" rule, and blocks the whole
            # schedule from saving - on a setting the user never touched. A declared
            # default means adding a new required multi-select to a template cannot
            # break saving for schedules that predate it.
            default = opt.get("default")
            resolved["default"] = list(default) if isinstance(default, list) else []
        options.append(resolved)
    return options


def schedule_options_for_definition(definition) -> list[dict]:
    """``template_schedule_options`` with each option's choices and current value filled in.

    Both the schedule dialog and the endpoint that saves it read options through here, so
    what the UI offers and what the API accepts cannot drift apart - the endpoint
    validates against exactly the list the dialog was built from.
    """
    if definition is None:
        return []
    # Every read below treats config as UNTRUSTED. It is operator-editable JSON that this
    # template's own docs tell people to hand-patch, and this function runs while building
    # the workflow LIST page - so a scalar where a dict was expected would raise inside a
    # comprehension, get swallowed by the page's blanket handler, and render zero
    # workflows for everyone in the opportunity, including the page needed to fix it.
    data = getattr(definition, "data", None) or {}
    config = data.get("config")
    config = config if isinstance(config, dict) else {}
    defaults = config.get(SCHEDULE_DEFAULTS_CONFIG_KEY)
    defaults = defaults if isinstance(defaults, dict) else {}

    options = []
    for opt in template_schedule_options(getattr(definition, "template_type", None)):
        filled = dict(opt)
        filled["value"] = defaults.get(opt["key"])
        coerce = MULTI_OPTION_COERCERS.get(opt["type"])
        if coerce is not None:
            raw = config.get(opt.get("choices_from_config") or "")
            raw = raw if isinstance(raw, dict) else {}
            choices = []
            for value, label in raw.items():
                try:
                    choices.append({"value": coerce(value), "label": str(label)})
                except (TypeError, ValueError):
                    continue
            filled["choices"] = sorted(choices, key=lambda c: c["value"])
            # Selected set, already coerced, so the template can test membership
            # directly. A non-list value (a bare int, a string) is treated as nothing
            # selected rather than iterated - iterating a string would silently select
            # its characters. Values that will not coerce are dropped; values that
            # coerce but are not among the choices are deliberately KEPT, so a saved id
            # whose choice has since disappeared shows up still ticked and is refused by
            # _clean_schedule_defaults with a message naming it. Dropping it here
            # instead would quietly narrow a live schedule on the next save, with
            # nothing on screen to say the coverage had changed.
            saved = filled["value"] if isinstance(filled["value"], list) else []
            if not saved:
                # Nothing chosen yet -- fall back to the declared default, narrowed to
                # choices this workflow actually offers.
                offered = {c["value"] for c in filled["choices"]}
                saved = [d for d in opt.get("default") or [] if d in offered]
            selected = []
            for v in saved:
                try:
                    coerced = coerce(v)
                except (TypeError, ValueError):
                    continue
                if coerced not in selected:
                    selected.append(coerced)
            filled["selected"] = selected
            # No choices means the config key this option reads is missing or unusable.
            # Left unflagged that renders a labelled control with nothing in it, which
            # posts an empty set, fails validation, and blocks the whole schedule from
            # being saved - a dead end with no explanation. Flagged, the dialog can say
            # why and omit the key so the REST of the schedule still saves.
            filled["unavailable"] = not filled["choices"]
        options.append(filled)
    return options


# =============================================================================
# Groups — how the template picker is organised
# =============================================================================
#
# The picker used to be one flat list in module-load order, three lines per
# card, 27 cards: a long scroll to find the thing you came for. Templates are
# grouped by what they PRODUCE — the question a person has when they open the
# picker — not by the programme they were written for; a programme's name is
# still in the description where it matters (176, 217), and the filter box finds
# it. Order is by how often each family is opened: reports first, demos last.
#
# One map, one file, so reorganising the picker is an edit here and nowhere
# else: the map's ORDER is the picker's order within each group. Every registered
# template must appear (enforced by test_template_groups.py), so a new template
# cannot silently fall into "other".
TEMPLATE_GROUPS: list[dict] = [
    {"key": "reports", "label": "Programme reports", "blurb": "cross-opportunity, drillable, read-only"},
    {"key": "automatic", "label": "Automatic reports", "blurb": "computed on a schedule, nothing to decide"},
    {"key": "reviews", "label": "Worker reviews", "blurb": "one scorecard per worker, you assign a status"},
    {"key": "audits", "label": "Audits", "blurb": "decide on photos and records, or create the audits"},
    {"key": "tracking", "label": "Beneficiary tracking", "blurb": "one child across follow-up visits"},
    {"key": "other", "label": "Outreach & demos", "blurb": "talk to workers, or show the platform"},
]

TEMPLATE_GROUP_OF: dict[str, str] = {
    # Programme reports: read across opportunities and drill down.
    "kmc_programme_metrics": "reports",
    # The report's drill page. Created WITH the report as its companion, and
    # creatable on its own — opened alone it reads the newest saved report.
    "kmc_flw_review": "reports",
    "program_admin_report": "reports",
    "audit_par": "reports",
    "chc_audit_history": "reports",
    "flw_audit_trend_dashboard": "reports",
    "flw_daily_indicator_table": "reports",
    "kmc_project_metrics": "reports",
    "verified_monitoring": "reports",
    # Automatic reports: run themselves on a schedule, no statuses.
    "flw_weekly_audit_report": "automatic",
    "flw_daily_indicator_report": "automatic",
    "flw_daily_summary_report": "automatic",
    # Worker reviews: one worker per row, a decision expected.
    "performance_review": "reviews",
    "llo_weekly_review": "reviews",
    "chc_nutrition_analysis": "reviews",
    "mbw_auditing_v5": "reviews",
    # Audits: a photo or record gets a verdict, plus the creators that spawn them.
    "bulk_image_audit": "audits",
    "muac_picture_audit": "audits",
    "kmc_image_audit": "audits",
    "weekly_dual_track_audit": "audits",
    "audit_with_ai_review": "audits",
    "program_audit_creator": "audits",
    "kmc_flw_flags": "audits",
    # Beneficiary tracking: keyed on a child, not a worker.
    "kmc_longitudinal": "tracking",
    "sam_followup": "tracking",
    # Outreach & demos.
    "ocs_outreach": "other",
    "interviews_reporting_v2": "other",
    "jakusko_chlorine_dispenser": "other",
}


def template_groups() -> list[dict]:
    """The picker's groups, in display order — copies, so a caller cannot
    reorder the registry's constant by accident."""
    return [dict(g) for g in TEMPLATE_GROUPS]


def _companion_of(key: str) -> list[str]:
    """The templates that create ``key`` as a companion. Such a template keeps
    its own row in the picker — it is designed to work alone too — and the row
    says which template also creates it."""
    return [
        k
        for k, t in TEMPLATES.items()
        if not t.get("deprecated")
        and any(isinstance(c, dict) and c.get("template_key") == key for c in t.get("companions") or [])
    ]


def list_templates() -> list[dict]:
    """
    List available templates for creation/listing surfaces.

    Excludes templates flagged ``deprecated`` — those stay in the registry
    (so existing instances resolve via ``get_template``) but must not be
    presented as creatable starters or reference patterns.

    Returns:
        List of dicts with 'key', 'name', 'description', 'icon', 'color',
        'multi_opp', 'supports_saved_runs', 'companions' (template keys),
        'group' (a TEMPLATE_GROUPS key) and 'companion_of' (template keys).
    """
    # Picker order: the map's order, then anything unplaced (a test forbids it).
    position = {key: i for i, key in enumerate(TEMPLATE_GROUP_OF)}
    return [
        {
            "key": key,
            "name": t["name"],
            "description": t["description"],
            "icon": t.get("icon", "fa-cog"),
            "color": t.get("color", "gray"),
            "multi_opp": bool(t.get("multi_opp", False)),
            "supports_saved_runs": bool(t.get("supports_saved_runs", False)),
            # Templates created alongside this one (see "Companions" below), so a
            # creation surface can say "also creates X" instead of surprising you.
            "companions": [c["template_key"] for c in t.get("companions") or [] if isinstance(c, dict)],
            # The picker's section for this template (TEMPLATE_GROUP_OF), and
            # the templates that create it alongside themselves, if any.
            "group": TEMPLATE_GROUP_OF.get(key, "other"),
            "companion_of": _companion_of(key),
        }
        for key, t in sorted(TEMPLATES.items(), key=lambda kv: position.get(kv[0], len(position)))
        if not t.get("deprecated")
    ]


# Size guards on snapshot blobs. JSON-serialized size is a reasonable
# proxy for what ends up in LabsRecord.data. Warn at 1 MB; reject at 5 MB.
# The hard cap really rejects (SnapshotTooLargeError): a 112 MB snapshot
# built from a 102k-visit opp's verbatim pipeline capture OOM-killed a web
# worker before the log-only version of this guard could help anyone.
_SNAPSHOT_SIZE_WARN_BYTES = 1 * 1024 * 1024
_SNAPSHOT_SIZE_HARD_BYTES = 5 * 1024 * 1024


class SnapshotStateNotStagedError(Exception):
    """A declarative snapshot would capture NONE of the REQUIRED state its manifest names.

    Only raised for templates that set `snapshot_inputs.require_state_keys`.

    `snapshot_inputs.state_keys` is a promise that the run's state carries those
    keys by completion time. When a template computes them in its RENDER (the
    browser stages them via onUpdateState, then completes), an API/MCP caller that
    completes the run without ever opening the page satisfies every other part of
    the contract and captures `{}` — producing a completed run whose snapshot is
    empty, which cannot be re-opened and reads as a real published artifact.

    That is silent, permanent and exactly the shape a caller cannot debug: the
    call returns 200. So refuse instead, and name the fix in the message.
    """

    def __init__(self, template_key: str, missing: list[str]):
        self.template_key = template_key
        self.missing = list(missing)
        super().__init__(
            f"snapshot for {template_key!r} declares state_keys {self.missing} but the run's "
            "state carries none of them, so completing now would freeze an EMPTY snapshot "
            "onto a run that cannot be re-opened. This template computes its snapshot in the "
            "render: stage it first via POST /labs/workflow/api/run/<run_id>/state/ with those "
            "keys, or give the template a server-side `build_snapshot` hook so an API caller "
            "can complete a run without opening the page."
        )


class SnapshotTooLargeError(Exception):
    """The built snapshot exceeds the hard size cap and must not be persisted."""

    def __init__(self, template_key: str, size_bytes: int):
        self.template_key = template_key
        self.size_bytes = size_bytes
        super().__init__(
            f"Snapshot for {template_key!r} is {size_bytes / 1024 / 1024:.1f} MB "
            f"(cap {_SNAPSHOT_SIZE_HARD_BYTES / 1024 / 1024:.0f} MB). Trim the workflow's "
            "snapshot_inputs manifest — capture derived aggregates in state keys instead "
            "of raw pipeline rows."
        )


def _default_snapshot_from_inputs(
    *,
    snapshot_inputs: dict,
    pipelines: dict,
    state: dict,
    context: dict,
    opportunity_id: int,
    template_key: str = "instance",
) -> dict:
    """Build the default snapshot honoring a template's declarative manifest.

    `snapshot_inputs` keys (all optional):
      - `builder`: name of a framework snapshot builder (see
        workflow/snapshot_builders.py). When set, that builder OWNS the payload and
        every key below is ignored — the manifest becomes the builder's spec. This
        is how a computed snapshot stays declarative instead of needing a hook.
      - `pipelines`: list of alias strings to capture verbatim. None/missing
        means "all"; an empty list means "none."
      - `workers`: bool (default True) — capture worker list if present.
      - `state_keys`: list of state keys to capture. None/missing means "all
        of state"; an empty list means "no state."
      - `require_state_keys`: bool (default False) — when True, completing with
        NONE of the declared `state_keys` populated raises
        `SnapshotStateNotStagedError` instead of freezing an empty snapshot.
        Opt in for templates whose snapshot is computed by their RENDER and
        staged into run state, where an empty capture is meaningless rather than
        merely early. Default False, so no existing template changes behaviour.
    Anything not listed is not captured.
    """
    # A DECLARED BUILDER, before anything is copied. Everything below this point can
    # only copy — pipeline rows, state keys, workers, verbatim — so a template whose
    # snapshot is a COMPUTATION had no declarative route and had to ship a Python
    # hook, which is a deploy for every change. `builder` names a framework builder
    # and the rest of this manifest is its spec, so the computation becomes editable
    # through `workflow_update_definition`. See workflow/snapshot_builders.py.
    builder_name = snapshot_inputs.get("builder")
    if builder_name:
        from connect_labs.workflow.snapshot_builders import BUILDERS, SnapshotBuilderError

        builder = BUILDERS.get(str(builder_name))
        if builder is None:
            raise SnapshotBuilderError(
                f"{template_key}: snapshot_inputs.builder is {builder_name!r}, which is not a "
                f"registered builder. Known: {sorted(BUILDERS)}"
            )
        return builder(
            spec=snapshot_inputs,
            pipelines=pipelines,
            opportunity_id=opportunity_id,
            context=context,
        )

    out: dict = {"schema_version": 1}

    pipelines_filter = snapshot_inputs.get("pipelines")
    if pipelines_filter is None:
        out["pipelines"] = pipelines
    else:
        # A declared alias missing from the live result is contract drift.
        missing = [alias for alias in pipelines_filter if alias not in pipelines]
        if missing:
            logger.warning("snapshot_inputs declared pipeline aliases not present at completion: %s", missing)
        out["pipelines"] = {alias: pipelines[alias] for alias in pipelines_filter if alias in pipelines}

    if snapshot_inputs.get("workers", True):
        out["workers"] = context.get("workers", [])

    state_keys = snapshot_inputs.get("state_keys")
    if state_keys is None:
        out["state"] = state
    else:
        captured = {k: state.get(k) for k in state_keys if k in state}
        # Refuse ONLY when the template says these keys are load-bearing.
        #
        # An empty capture is legitimate for most templates and is covered by
        # tests that predate this guard: performance_review completes a run with
        # no decisions recorded yet ("A run with no decisions yet still produces
        # a valid snapshot"), and an instance manifest declaring `decisions`
        # completes at 200 with `state == {}`. Refusing those broke real,
        # intended behaviour.
        #
        # The distinction is per-template intent, not a property the framework
        # can infer: empty `worker_states` means "nobody decided anything yet",
        # while empty `frozen` means "this dashboard has no numbers in it". So
        # the template declares which it is, and the default preserves today's
        # behaviour exactly.
        if snapshot_inputs.get("require_state_keys") and state_keys and not any(captured.get(k) for k in state_keys):
            raise SnapshotStateNotStagedError(template_key, list(state_keys))
        out["state"] = captured

    out["opportunity_ids"] = context.get("opportunity_ids", [opportunity_id])
    return out


def _check_snapshot_size(template_key: str, snapshot: dict) -> None:
    """Warn above the soft cap; raise SnapshotTooLargeError at the hard cap."""
    import json as _json

    try:
        size = len(_json.dumps(snapshot, default=str).encode("utf-8"))
    except Exception:
        logger.exception("Could not measure snapshot size for %s", template_key)
        return
    if size >= _SNAPSHOT_SIZE_HARD_BYTES:
        logger.error(
            "Snapshot for template %r is %.1f MB (>= %.0f MB hard cap) — rejecting.",
            template_key,
            size / 1024 / 1024,
            _SNAPSHOT_SIZE_HARD_BYTES / 1024 / 1024,
        )
        raise SnapshotTooLargeError(template_key, size)
    elif size >= _SNAPSHOT_SIZE_WARN_BYTES:
        logger.warning(
            "Snapshot for template %r is %.1f MB (>= %.0f MB soft cap).",
            template_key,
            size / 1024 / 1024,
            _SNAPSHOT_SIZE_WARN_BYTES / 1024 / 1024,
        )


def detect_template_key_from_name(definition_name: str) -> str | None:
    """Strict name→template-key match: key equals the snake_cased name, or the
    template's display name equals the definition name (case-insensitive).
    Workflows created outside the from-template flow (blank MCP create,
    wholesale definition overwrites) can lack config.templateType; this is the
    shared recovery used by template sync and run completion."""
    if not definition_name:
        return None
    name_lower = definition_name.lower().replace(" ", "_")
    for key, template in TEMPLATES.items():
        if key == name_lower or template.get("name", "").lower() == definition_name.lower():
            return key
    return None


def resolve_snapshot_opp_scope(run, definition, requested_opportunity_id=None):
    """Work out which opportunity a snapshot is being taken *for*, and over which.

    Returns ``(primary_opp_id, effective_opp_ids)``.

    Both callers used to resolve this as ``run.opportunity_id or
    definition.opportunity_id`` and give up if that was falsy. For a
    program-owned multi-opp definition both are null by design — the opps live
    in ``definition.opportunity_ids`` — so such a run could be created and never
    concluded, from the runner page or the MCP (#1182). The list is the fallback,
    and its first entry is the primary-opp convention the rest of the multi-opp
    contract already uses (WORKFLOW_REFERENCE §8).

    ``requested_opportunity_id`` wins when a caller names one explicitly, so an
    opp-scoped save is unaffected.
    """
    effective = [oid for oid in (getattr(definition, "opportunity_ids", None) or []) if oid]
    primary = (
        requested_opportunity_id
        or getattr(run, "opportunity_id", None)
        or getattr(definition, "opportunity_id", None)
        or (effective[0] if effective else None)
    )
    if not effective and primary:
        effective = [primary]
    return primary, effective


# Instance manifests are stamped at create-from-template time and never migrate, so
# a template that gains a field later cannot reach the workflows already created
# from it. That is correct for WHAT to capture — `state_keys`, `pipelines`,
# `workers` describe what this workflow is doing and the instance owns them.
#
# It is wrong for `require_state_keys`, which is not a choice about content: it
# records that the template computes its snapshot in the RENDER, so an unstaged
# completion is empty rather than early. That is a property of the template's
# code, and no instance record can make it untrue.
#
# Measured: shipping the flag on kmc_programme_metrics protected nothing, because
# live workflow 5456 resolves `source: "definition"` from a manifest stamped
# before the flag existed. The guard was deployed and inert on the one workflow it
# was written for.
_INHERITED_SAFETY_FLAGS = ("require_state_keys",)


def _with_inherited_safety_flags(instance_inputs: dict, template_key: str | None) -> dict:
    """Let an instance manifest own its content, but not drop a template safety flag."""
    template = TEMPLATES.get(template_key) if template_key else None
    template_inputs = (template or {}).get("snapshot_inputs") or {}
    missing = {
        flag: template_inputs[flag]
        for flag in _INHERITED_SAFETY_FLAGS
        if flag in template_inputs and flag not in instance_inputs
    }
    if not missing:
        return instance_inputs
    return {**instance_inputs, **missing}


def resolve_snapshot_contract(definition) -> dict:
    """Resolve which snapshot contract governs run completion for a workflow.

    The workflow definition is the source of truth: an instance-owned
    `data["snapshot_inputs"]` manifest (stamped at create-from-template time,
    editable per-instance) wins over the template registry — the snapshot
    captures what the workflow *is doing*, not what its template originally
    declared. The registry is consulted only as a fallback for definitions
    that predate instance manifests, or for templates whose snapshot is
    computed by a Python `build_snapshot` hook (code can't live on the
    record, so hooks stay registry-resolved unless the instance overrides
    them with its own manifest).

    Returns a dict. On success:
        {"ok": True,
         "source": "definition" | "template_hook" | "template_inputs",
         "template_key": str | None,
         "snapshot_inputs": dict | None,        # None for template_hook
         "recovered_template_key": bool}        # key came from a name match
    On failure:
        {"ok": False,
         "error": "no_contract" | "unknown_template" | "template_not_saved_runs",
         "template_key": str | None}
    """
    data = definition.data or {}
    instance_inputs = data.get("snapshot_inputs")
    if isinstance(instance_inputs, dict):
        return {
            "ok": True,
            "source": "definition",
            "template_key": definition.template_type or None,
            "snapshot_inputs": _with_inherited_safety_flags(instance_inputs, definition.template_type),
            "recovered_template_key": False,
        }

    template_key = definition.template_type
    recovered = False
    if not template_key:
        template_key = detect_template_key_from_name(data.get("name", ""))
        recovered = template_key is not None
        if not template_key:
            return {"ok": False, "error": "no_contract", "template_key": None}

    template = TEMPLATES.get(template_key)
    if not template:
        return {"ok": False, "error": "unknown_template", "template_key": template_key}
    if not template.get("supports_saved_runs"):
        return {"ok": False, "error": "template_not_saved_runs", "template_key": template_key}

    if callable(template.get("build_snapshot")):
        return {
            "ok": True,
            "source": "template_hook",
            "template_key": template_key,
            "snapshot_inputs": None,
            "recovered_template_key": recovered,
        }

    snapshot_inputs = template.get("snapshot_inputs")
    return {
        "ok": True,
        "source": "template_inputs",
        "template_key": template_key,
        "snapshot_inputs": dict(snapshot_inputs) if isinstance(snapshot_inputs, dict) else {},
        "recovered_template_key": recovered,
    }


def build_snapshot_for_contract(
    contract: dict,
    *,
    pipelines: dict,
    state: dict,
    opportunity_id: int,
    **context,
) -> dict | None:
    """Build the completion snapshot for a resolved contract.

    `contract` is the success shape from `resolve_snapshot_contract`. The
    declarative sources ("definition", "template_inputs") run through the
    framework's default manifest builder; "template_hook" calls the
    template's Python hook with the same context contract as
    `build_snapshot_for_template`.
    """
    label = contract.get("template_key") or "instance"
    if contract["source"] == "template_hook":
        template = TEMPLATES.get(contract["template_key"]) if contract.get("template_key") else None
        builder = template.get("build_snapshot") if template else None
        if not callable(builder):
            return None
        snapshot = builder(pipelines=pipelines, state=state, opportunity_id=opportunity_id, **context)
    else:
        snapshot = _default_snapshot_from_inputs(
            snapshot_inputs=contract.get("snapshot_inputs") or {},
            pipelines=pipelines,
            state=state,
            context=context,
            opportunity_id=opportunity_id,
            template_key=label,
        )
    if isinstance(snapshot, dict):
        _check_snapshot_size(label, snapshot)
    return snapshot


def build_snapshot_for_template(
    template_key: str,
    *,
    pipelines: dict,
    state: dict,
    opportunity_id: int,
    **context,
) -> dict | None:
    """Build the snapshot for a saved-runs template.

    Resolution order:
      1. If the template isn't registered or doesn't declare
         `supports_saved_runs: True`, return `None`.
      2. If the template defines a module-level `build_snapshot` hook, call
         it. The hook owns the shape entirely; use this when the snapshot
         shape differs from the raw inputs (computed summaries, KPIs).
      3. Otherwise, use the framework's default hook, which respects the
         template's `snapshot_inputs` manifest. Templates that just need
         "capture these inputs verbatim" can opt in with one line plus the
         manifest and never write Python.

    Hook contract: `build_snapshot(*, pipelines, state, opportunity_id,
    **context) -> dict`. Context keys may grow over time (currently
    `workers`, `opportunity_ids`); hooks should accept `**context` to stay
    forward-compatible.

    Hooks run server-side at completion time, so they have full Python access.
    """
    template = TEMPLATES.get(template_key)
    if not template:
        return None
    if not template.get("supports_saved_runs"):
        return None

    builder = template.get("build_snapshot")
    if callable(builder):
        snapshot = builder(pipelines=pipelines, state=state, opportunity_id=opportunity_id, **context)
    else:
        snapshot_inputs = template.get("snapshot_inputs")
        if snapshot_inputs is None:
            # Permissive fallback: dump everything. Logged because templates
            # should declare what they capture for clarity and size discipline.
            logger.warning(
                "Template %r declares supports_saved_runs but no build_snapshot hook "
                "and no snapshot_inputs manifest — falling back to dump-everything. "
                "Add a `snapshot_inputs` block to the template to make the contract explicit.",
                template_key,
            )
            snapshot_inputs = {}
        snapshot = _default_snapshot_from_inputs(
            snapshot_inputs=snapshot_inputs,
            pipelines=pipelines,
            state=state,
            context=context,
            opportunity_id=opportunity_id,
            template_key=template_key,
        )

    if isinstance(snapshot, dict):
        _check_snapshot_size(template_key, snapshot)
    return snapshot


def create_workflow_from_template(
    data_access: WorkflowDataAccess,
    template_key: str,
    request=None,
    opportunity_ids: list[int] | None = None,
    program_id: int | None = None,
) -> tuple:
    """
    Create a workflow from a template using the data access layer.

    If the template includes a pipeline_schema, a pipeline will also be created
    and linked to the workflow.

    Record ownership is exactly one of opportunity or program:

    - **Opportunity-owned** (default): the passed ``data_access`` is opp-scoped
      and the created definition carries that opportunity's FK. This is the
      per-opp case and is unchanged.
    - **Program-owned** (``program_id`` given): the created definition carries
      the program FK and no owning opportunity — used for a program's Creator /
      Report workflows. If the passed ``data_access`` is not already scoped to
      that program, a program-scoped one is constructed (reusing its token) and
      used for the writes.

    ``opportunity_ids`` is orthogonal to ownership: it is the DATA field listing
    the opportunities the workflow spans (e.g. a program's member opps), stored
    on the definition regardless of who owns the record.

    Args:
        data_access: WorkflowDataAccess instance with valid OAuth
        template_key: Template key (e.g., 'performance_review')
        request: Optional HttpRequest for creating pipelines (needed for PipelineDataAccess)
        opportunity_ids: Optional list of opp IDs this workflow should pull data from
            (multi-opp templates only; ignored for single-opp templates).
        program_id: When given, the workflow is program-owned (no owning opp).

    Returns:
        Tuple of (definition_record, render_code_record, pipeline_record or None)

    Raises:
        ValueError: If template not found, or ownership scope is ambiguous
            (neither or both of opportunity / program).
    """
    template = get_template(template_key)
    if not template:
        raise ValueError(f"Unknown template: {template_key}")
    if template.get("deprecated"):
        raise ValueError(f"Template '{template_key}' is deprecated and can no longer be instantiated.")

    # Resolve record ownership scope: exactly one of opportunity / program.
    dao_opp_id = getattr(data_access, "opportunity_id", None)
    dao_program_id = getattr(data_access, "program_id", None)
    effective_program_id = program_id if program_id is not None else dao_program_id
    effective_opp_id = None if effective_program_id is not None else dao_opp_id
    if (effective_program_id is None) == (effective_opp_id is None):
        raise ValueError(
            "create_workflow_from_template requires exactly one of opportunity_id / program_id ownership."
        )

    # If program ownership was requested but the DAO isn't already program-scoped,
    # re-scope so the created definition carries the program FK (no owning opp).
    owns_data_access = False
    if effective_program_id is not None and dao_program_id != effective_program_id:
        from connect_labs.workflow.data_access import WorkflowDataAccess as _WDA

        data_access = _WDA(
            access_token=getattr(data_access, "access_token", None),
            program_id=effective_program_id,
        )
        owns_data_access = True
    try:
        return _create_workflow_from_template_scoped(
            data_access=data_access,
            template=template,
            template_key=template_key,
            request=request,
            opportunity_ids=opportunity_ids,
        )
    finally:
        if owns_data_access:
            data_access.close()


def _create_workflow_from_template_scoped(
    data_access: WorkflowDataAccess,
    template: dict,
    template_key: str,
    request=None,
    opportunity_ids: list[int] | None = None,
    pipeline_sources_override: list[dict] | None = None,
    config_overrides: dict | None = None,
    _ancestry: tuple[str, ...] = (),
) -> tuple:
    """Inner body of ``create_workflow_from_template`` — runs against an
    already-ownership-scoped ``data_access``. Kept separate so the public
    function can own/close a re-scoped DAO in a ``finally``.

    ``pipeline_sources_override`` and ``config_overrides`` exist for
    companions (see ``_create_companions``): a companion that shares its
    primary's pipelines is handed the primary's ``pipeline_sources`` verbatim
    and creates none of its own, and its config is stamped with the back
    reference to the primary at create time rather than by a second write.
    """

    _validate_companions(template, template_key, _ancestry)

    template_def = template["definition"]
    pipeline_schema = template.get("pipeline_schema")
    pipeline_record = None
    pipeline_sources = []

    # PipelineDataAccess can be constructed from either an HttpRequest (web
    # view path) or a direct access_token (MCP/CLI path). We reuse whatever
    # token ``data_access`` already has so the MCP can create pipelines too.
    # We also forward the scope IDs so the new pipeline record is scoped to
    # the same opp/program/org as the workflow — otherwise the record is
    # created unscoped and subsequent scoped reads (`pipeline_get`, list views)
    # can't see it. The web path gets this for free via
    # ``request.labs_context``; the MCP path has to pass them explicitly.
    #
    # Pipelines are ALWAYS opportunity-owned in this codebase — every reader
    # (pipeline_get, pipeline_preview, and the runtime PipelineDataAccess
    # constructed by WorkflowDataAccess.get_pipeline_data) looks a pipeline up
    # by opportunity_id, never by program_id. A program-owned workflow's
    # ``data_access`` has no opportunity_id of its own (opportunity_id=None,
    # program_id=<X>) — copying that scope straight onto a newly-created
    # pipeline produces a program-scoped-only record that every one of those
    # readers then reports as "not found", including at real run time. Fall
    # back to the first spanned opportunity (the same anchor opportunity the
    # manual pipeline-bootstrap pattern elsewhere in this codebase already
    # uses for program-owned workflows) so the pipeline lands somewhere every
    # reader can actually find it.
    dao_opportunity_id = getattr(data_access, "opportunity_id", None)
    dao_program_id = getattr(data_access, "program_id", None)
    pipeline_opportunity_id = dao_opportunity_id or (opportunity_ids[0] if opportunity_ids else None)
    pipeline_access_token = getattr(data_access, "access_token", None)
    pipeline_scope_kwargs = {
        "opportunity_id": pipeline_opportunity_id,
        "program_id": None if pipeline_opportunity_id else dao_program_id,
        "organization_id": getattr(data_access, "organization_id", None),
    }
    can_create_pipelines = bool(request) or bool(pipeline_access_token)
    if pipeline_sources_override is not None:
        # A companion sharing its primary's pipelines: the same two records,
        # so the two workflows share one cache. Nothing is created here.
        can_create_pipelines = False
        pipeline_sources = [dict(src) for src in pipeline_sources_override]

    # Create pipeline if template has one (singular schema)
    if pipeline_schema and can_create_pipelines:
        from connect_labs.workflow.data_access import PipelineDataAccess

        pipeline_data_access = PipelineDataAccess(
            request=request,
            access_token=pipeline_access_token,
            **pipeline_scope_kwargs,
        )
        pipeline_record = pipeline_data_access.create_definition(
            name=pipeline_schema["name"],
            description=pipeline_schema["description"],
            schema=pipeline_schema,
        )
        pipeline_data_access.close()

        # Determine the source alias for this pipeline. A template may declare
        # its own ``pipeline_alias`` — this is the contract its render code
        # (``view.pipelines.<alias>``) and ``snapshot_inputs.pipelines`` both
        # reference, so it lives with the template, not in this far-away map.
        # Fall back to the legacy per-key map, then to ``"data"``.
        #
        # Mismatch is silent and nasty (see #464): if the source alias doesn't
        # match what the render reads, live KPI cells render as dashes AND the
        # completion snapshot filters to an empty pipelines dict.
        alias_map = {
            "performance_review": "performance_data",
        }
        pipeline_alias = template.get("pipeline_alias") or alias_map.get(template_key, "data")

        # Add pipeline as a source with a default alias
        pipeline_sources = [
            {
                "pipeline_id": pipeline_record.id,
                "alias": pipeline_alias,
            }
        ]

    # Handle multiple pipeline schemas (e.g., MBW with 3 sources)
    pipeline_schemas = template.get("pipeline_schemas", [])
    if pipeline_schemas and can_create_pipelines:
        from connect_labs.workflow.data_access import PipelineDataAccess

        pipeline_data_access = PipelineDataAccess(
            request=request,
            access_token=pipeline_access_token,
            **pipeline_scope_kwargs,
        )
        for ps in pipeline_schemas:
            record = pipeline_data_access.create_definition(
                name=ps["name"],
                description=ps.get("description", ""),
                schema=ps["schema"],
            )
            pipeline_sources.append(
                {
                    "pipeline_id": record.id,
                    "alias": ps["alias"],
                }
            )
        pipeline_data_access.close()

    # Create the workflow definition with pipeline source if created. A COPY:
    # the template's DEFINITION is module state shared by every create, and a
    # companion's back reference must not leak into the next instance.
    config = dict(template_def.get("config", {}))
    config["templateType"] = template_key  # Store template type for filtering
    config["multi_opp"] = bool(template.get("multi_opp", False))
    config.update(config_overrides or {})
    extra_definition_kwargs = {}
    if template.get("supports_saved_runs") and not callable(template.get("build_snapshot")):
        # Stamp the snapshot manifest onto the instance: the definition — not
        # the registry — owns the completion contract from here on, so edits
        # to the workflow (new pipelines, new state keys) can be captured by
        # editing the instance manifest. Hook templates stay registry-resolved
        # (their snapshot is computed Python, which can't live on the record).
        extra_definition_kwargs["snapshot_inputs"] = dict(template.get("snapshot_inputs") or {})
    definition = data_access.create_definition(
        name=template_def["name"],
        description=template_def["description"],
        statuses=template_def.get("statuses", []),
        config=config,
        pipeline_sources=pipeline_sources,
        opportunity_ids=list(opportunity_ids or []),
        **extra_definition_kwargs,
    )

    # Create the render code
    render_code = data_access.save_render_code(
        definition_id=definition.id,
        component_code=template["render_code"],
        version=1,
    )

    definition = _create_companions(
        data_access=data_access,
        template=template,
        template_key=template_key,
        definition=definition,
        pipeline_sources=pipeline_sources,
        request=request,
        opportunity_ids=opportunity_ids,
        _ancestry=_ancestry,
    )

    return definition, render_code, pipeline_record


# =============================================================================
# Companions — a template that is only useful alongside another one
# =============================================================================
#
# A drill has two pages. The KMC programme report drills programme -> LLO ->
# opportunity -> worker, and a worker row opens the KMC Worker Review — a second
# workflow, linked by configuration: the report's ``config.flw_review`` names the
# review workflow and its long-lived run; the review's ``config.source_workflow_id``
# names the report. Two templates, one feature.
#
# Until this existed, creating the report from its template gave you HALF the
# feature: the review had to be created separately, its run minted, both configs
# patched — four API calls that the "Create" button could not make, so a workflow
# created by hand had worker rows that were not links. The link was a runbook.
#
# ``TEMPLATE["companions"]`` moves that runbook into the registry. Each entry is
# created right after the primary, in the same ownership scope and over the same
# ``opportunity_ids``, and the two are cross-linked before ``create`` returns:
#
#     {
#         "template_key": "kmc_flw_review",     # the companion's registered template
#         "config_key": "flw_review",           # primary.config[key] = {workflow_id, run_id?}
#         "share_pipelines": True,              # companion reuses the primary's pipeline records
#         "mint_run": True,                     # create one long-lived run; its id joins the link
#         "back_reference": "source_workflow_id",  # companion.config[key] = primary's id
#     }
#
# The MCP tool and the web view both go through ``create_workflow_from_template``,
# so both get the whole feature from one call.

_COMPANION_KEYS = {"template_key", "config_key", "share_pipelines", "mint_run", "back_reference"}


def _validate_companions(template: dict, template_key: str, ancestry: tuple[str, ...]) -> None:
    """Refuse a malformed or cyclic companion chain BEFORE anything is created —
    a failure halfway through leaves a primary with no link and a companion with
    no owner, which is exactly the half-built state companions exist to end."""
    if template_key in ancestry:
        chain = " -> ".join((*ancestry, template_key))
        raise ValueError(f"Template companions form a cycle: {chain}")
    for spec in template.get("companions") or []:
        if not isinstance(spec, dict):
            raise ValueError(f"Template '{template_key}': each companion must be a dict, got {spec!r}")
        unknown = set(spec) - _COMPANION_KEYS
        if unknown:
            raise ValueError(f"Template '{template_key}': unknown companion keys {sorted(unknown)}")
        if not spec.get("template_key") or not spec.get("config_key"):
            raise ValueError(f"Template '{template_key}': a companion needs template_key and config_key")
        companion = get_template(spec["template_key"])
        if not companion:
            raise ValueError(f"Template '{template_key}': unknown companion template '{spec['template_key']}'")
        if companion.get("deprecated"):
            raise ValueError(f"Template '{template_key}': companion '{spec['template_key']}' is deprecated")
        _validate_companions(companion, spec["template_key"], (*ancestry, template_key))


def _create_companions(
    *,
    data_access: WorkflowDataAccess,
    template: dict,
    template_key: str,
    definition,
    pipeline_sources: list[dict],
    request,
    opportunity_ids: list[int] | None,
    _ancestry: tuple[str, ...],
):
    """Create each declared companion and write the links. Returns the primary
    definition — re-read after the link write when there was one, so a caller
    that inspects ``definition.config`` sees the links."""
    specs = template.get("companions") or []
    if not specs:
        return definition

    from datetime import date

    dao_opportunity_id = getattr(data_access, "opportunity_id", None)
    dao_program_id = getattr(data_access, "program_id", None)
    links: dict[str, dict] = {}
    for spec in specs:
        companion_key = spec["template_key"]
        companion_template = get_template(companion_key)
        overrides = {}
        if spec.get("back_reference"):
            overrides[spec["back_reference"]] = definition.id
        companion_def, _companion_render, _ = _create_workflow_from_template_scoped(
            data_access=data_access,
            template=companion_template,
            template_key=companion_key,
            request=request,
            opportunity_ids=opportunity_ids,
            pipeline_sources_override=pipeline_sources if spec.get("share_pipelines") else None,
            config_overrides=overrides,
            _ancestry=(*_ancestry, template_key),
        )
        link = {"workflow_id": companion_def.id}
        if spec.get("mint_run"):
            # One long-lived run: the drill opens it with ?run_id=, carrying the
            # worker and the source report as query parameters. No run per click.
            today = date.today().isoformat()
            run = data_access.create_run(
                companion_def.id,
                opportunity_id=dao_opportunity_id,
                program_id=None if dao_opportunity_id else dao_program_id,
                period_start=today,
                period_end=today,
                initial_state={},
            )
            link["run_id"] = run.id
        links[spec["config_key"]] = link

    # ``update_definition`` replaces the whole data blob, so the payload is the
    # record as created plus the links — never a partial config.
    new_data = dict(getattr(definition, "data", None) or {})
    config = dict(new_data.get("config") or {})
    config.update(links)
    new_data["config"] = config
    updated = data_access.update_definition(definition_id=definition.id, data=new_data)
    return updated or definition


def companion_links(definition) -> dict[str, dict]:
    """The companion links a created definition carries, keyed by config key —
    for callers that report what ``create`` produced (the MCP tool, the view)."""
    data = getattr(definition, "data", None)
    if not isinstance(data, dict):
        return {}
    template = get_template((data.get("config") or {}).get("templateType") or "") or {}
    config = data.get("config") or {}
    return {
        spec["config_key"]: config[spec["config_key"]]
        for spec in template.get("companions") or []
        if isinstance(config.get(spec["config_key"]), dict)
    }


# =============================================================================
# Re-export for backwards compatibility
# =============================================================================

# Re-export individual template modules for direct access if needed
from . import (  # noqa: E402
    audit_with_ai_review,
    kmc_flw_flags,
    kmc_longitudinal,
    kmc_project_metrics,
    llo_weekly_review,
    ocs_outreach,
    performance_review,
    program_admin_report,
)

__all__ = [
    "TEMPLATES",
    "get_template",
    "list_templates",
    "create_workflow_from_template",
    "companion_links",
    "template_groups",
    "run_default_for_definition",
    "resolve_snapshot_contract",
    "resolve_snapshot_opp_scope",
    "build_snapshot_for_contract",
    "SnapshotStateNotStagedError",
    "SnapshotTooLargeError",
    # Individual template modules
    "performance_review",
    "ocs_outreach",
    "audit_with_ai_review",
    "bulk_image_audit",
    "kmc_longitudinal",
    "kmc_flw_flags",
    "kmc_project_metrics",
    "llo_weekly_review",
    "program_admin_report",
]
