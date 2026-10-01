"""Which opportunities a visit-reading tool call reads, for the "no user visit data" gate.

A restricted caller (``token_scopes``) may run a tool in ``GENERATED_ONLY_TOOLS`` only
when every opportunity the call reads holds generated data
(``connect_labs.labs.synthetic.provenance``). So each such tool has a resolver here
that names every opportunity the call would read -- not only the argument named
``opportunity_id``: a pipeline preview fans out over ``opportunity_ids``, and a workflow
run reads its definition's ``opportunity_ids`` whatever scope it was opened in.

A resolver returns the ids, or ``None`` when it cannot tell, and ``None`` refuses the
call. Resolvers check the scope before reading anything, so a restricted caller never
makes the tool's own reads against a real opportunity just to be told no.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from connect_labs.labs.synthetic.provenance import all_generated

Resolver = Callable[[Any, dict], "list[int] | None"]


def _ints(values) -> list[int] | None:
    out = []
    for value in values:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            return None
    return out


def _opportunity_id(user, arguments: dict) -> list[int] | None:
    return _ints([arguments.get("opportunity_id")])


def _pipeline_preview(user, arguments: dict) -> list[int] | None:
    return _ints([arguments.get("opportunity_id"), *(arguments.get("opportunity_ids") or [])])


def _program_opportunity_ids(program_id) -> list[int] | None:
    """Every opp filed under a labs-only program, or None for any other program."""
    from connect_labs.labs.synthetic.local_records_backend import is_labs_only_program_id
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    try:
        program_id = int(program_id)
    except (TypeError, ValueError):
        return None
    if not is_labs_only_program_id(program_id):
        return None
    from django.db.models import Q

    # The same membership is_labs_only_program_id uses: filed under the program, or
    # with no program_id set and so its own program.
    ids = SyntheticOpportunity.objects.filter(
        Q(program_id=program_id) | Q(program_id__isnull=True, opportunity_id=program_id)
    ).values_list("opportunity_id", flat=True)
    return sorted(set(ids)) or None


def _workflow(key: str) -> Resolver:
    """Resolve a workflow tool: its scope, then the definition's own opportunities."""

    def resolve(user, arguments: dict) -> list[int] | None:
        opportunity_id = arguments.get("opportunity_id")
        program_id = arguments.get("program_id")
        if (opportunity_id is None) == (program_id is None):
            return None
        scope = _ints([opportunity_id]) if opportunity_id is not None else _program_opportunity_ids(program_id)
        # Refuse on the scope alone before reading any record through it.
        if not scope or not all_generated(scope):
            return None
        definition = _read_definition(user, key, arguments, opportunity_id, program_id)
        if definition is None:
            return None
        read = set(scope)
        read |= {oid for oid in (getattr(definition, "opportunity_ids", None) or []) if oid}
        if getattr(definition, "opportunity_id", None):
            read.add(definition.opportunity_id)
        return _ints(read)

    return resolve


def _read_definition(user, key: str, arguments: dict, opportunity_id, program_id):
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from .connect_token import require_connect_token

    wda = WorkflowDataAccess(
        opportunity_id=opportunity_id, program_id=program_id, access_token=require_connect_token(user)
    )
    try:
        if key == "run_id":
            run = wda.get_run(arguments.get("run_id"))
            if run is None:
                return None
            definition_id = (run.data or {}).get("definition_id")
        else:
            definition_id = arguments.get("definition_id")
        return wda.get_definition(definition_id) if definition_id else None
    except Exception:  # noqa: BLE001 -- anything unreadable is refused, not raised
        return None
    finally:
        wda.close()


RESOLVERS: dict[str, Resolver] = {
    "pipeline_preview": _pipeline_preview,
    "custom_analysis_run": _opportunity_id,
    "synthetic_local_record_dump": _opportunity_id,
    "synthetic_reload_fixtures": _opportunity_id,
    "synthetic_disable": _opportunity_id,
    "synthetic_set_allowed_domains": _opportunity_id,
    "task_create_synthetic": _opportunity_id,
    "workflow_run_context": _workflow("run_id"),
    "workflow_run_indicators": _workflow("run_id"),
    "workflow_indicator_explain": _workflow("run_id"),
    "workflow_action_status": _workflow("run_id"),
    "workflow_preview_snapshot": _workflow("run_id"),
    "workflow_history_runs": _workflow("definition_id"),
    "workflow_preview_as_of": _workflow("definition_id"),
}


#: Arguments naming where a profile bundle is written or read. Anything but a Drive
#: folder ("gdrive:...") is a path on the server's own disk.
_BUNDLE_PATH_ARGS = ("out_dir", "bundle_dir", "bundle_root")


def synthetic_denied_reason(tool_name: str, arguments: dict) -> str | None:
    """Why a restricted caller may not make this synthetic call, or None.

    * Bundles live in Drive; a local path is the server's own filesystem.
    * ``synthetic_env_ensure(fresh=true)`` deletes shared demo data for everyone.
    * A manifest from the RETIRED mirror mode replays real cases near-verbatim
      (``provenance.replays_real_cases``). Profiling with ``case_timelines`` (or its
      old name ``mirror``) is allowed: it ships cases sampled from models, never real
      ones (generator/fixtures/case_model.py).
    """
    if not tool_name.startswith("synthetic_"):
        return None
    no_visit = "without access to user visit data"
    paths = {name: arguments.get(name) for name in _BUNDLE_PATH_ARGS if arguments.get(name) is not None}
    spec_yaml = arguments.get("spec_yaml")
    if spec_yaml:
        from connect_labs.labs.synthetic.cohort import CohortSpec

        try:
            spec = CohortSpec.from_yaml(spec_yaml)
        except ValueError:
            return None  # the tool reports the malformed spec itself
        paths["bundle_root"] = spec.bundle_root
    for name, value in paths.items():
        if not str(value).startswith("gdrive:"):
            return f"{name} must be a Drive folder ('gdrive:' or 'gdrive:<folder_id>') {no_visit}; got {value!r}."
    if tool_name == "synthetic_env_ensure" and arguments.get("fresh"):
        return f"fresh=true deletes a shared demo environment for everyone, so it is not available {no_visit}."
    if tool_name == "synthetic_generate_from_manifest" and arguments.get("manifest_yaml"):
        from connect_labs.labs.synthetic.generator.fixtures.manifest import Manifest
        from connect_labs.labs.synthetic.provenance import replays_real_cases

        try:
            manifest = Manifest.from_yaml(arguments["manifest_yaml"])
        except Exception:  # noqa: BLE001 -- the tool reports the malformed manifest itself
            return None
        if replays_real_cases(manifest):
            return (
                f"This manifest is from the retired mirror mode, which replays real cases; not available "
                f"{no_visit}. Re-profile with case_timelines=true for a pool of modelled cases."
            )
    return None


def caller_restricted() -> bool:
    """True when the current MCP call must keep to "no user visit data".

    For tools that hold STORED visit-derived data (a completed run's snapshot). The gate
    above checks an opportunity's provenance as it is now; a snapshot stored before the
    opportunity was (re)generated, or while its workflow also spanned a real one, was
    built from whatever it read then. So a restricted caller is never served stored run
    data -- it gets a live build over today's (generated) data, or a refusal.
    """
    from fastmcp.server.dependencies import get_access_token

    from .server import restricted_call

    # Outside an MCP request get_access_token() is None and there is no endpoint mark,
    # so this is False there -- and an unexpected error raises rather than failing open.
    return restricted_call(get_access_token())


def denied_reason(user, tool_name: str, arguments: dict) -> str | None:
    """Why a restricted caller may not run this generated-only call, or None to allow it."""
    resolver = RESOLVERS.get(tool_name)
    if resolver is None:
        return f"{tool_name} is not available without access to user visit data."
    opportunity_ids = resolver(user, arguments)
    if not opportunity_ids or not all_generated(opportunity_ids):
        return (
            f"{tool_name} reads visit data, so without access to user visit data it runs only on "
            "synthetic opportunities whose data was generated. At least one opportunity this call "
            "reads is not one of those."
        )
    return None
