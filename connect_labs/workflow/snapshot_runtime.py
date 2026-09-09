"""Build a run's snapshot: ONE function behind completion and preview, web and MCP.

The build step -- resolve the definition and its contract, work out the opportunity
scope, read the cached pipelines, gather workers, hand everything to the contract's
builder -- existed twice: once in `views.complete_run_api` and once in the MCP tool
`workflow_save_snapshot`, the second annotated "mirrors the canonical run-completion
endpoint". Mirrors drift. When the MCP path gained `program_id` and `access_token`
in its builder context, the web path did not; when the web path gained `run_id`,
the MCP path did not.

It now exists once, and it is also what a LIVE run renders from. The dashboard
render used to carry its own JavaScript implementation of grading, pooling and the
monthly trend for in-progress runs, with the Python builder used only for saved
ones -- and every saved-run defect the KMC dashboard has had was those two copies
disagreeing about a shape. `preview` builds the payload without persisting it, so a
live run fetches exactly what completing it would store and the render is a view
over one shape.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SnapshotBuildError(Exception):
    """A snapshot could not be built.

    `code` is stable so each caller can map it onto its own error shape (an HTTP
    status, an MCP error class); `message` is reportable as-is. `contract` rides
    along for the contract-resolution codes so a caller can say WHICH template or
    manifest was at fault, and `missing` for `not_staged`.

    codes: no_definition_id, definition_not_found, no_contract, unknown_template,
           template_not_saved_runs, no_opportunity, cache_miss, not_staged,
           too_large, non_dict
    """

    def __init__(self, code: str, message: str, *, contract: dict | None = None, missing: list | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.contract = contract
        self.missing = missing


def contract_error_message(contract: dict) -> str:
    """The reportable reason a definition has no usable completion contract."""
    if contract.get("error") == "unknown_template":
        return f"Unknown template: {contract.get('template_key')}"
    if contract.get("error") == "template_not_saved_runs":
        return (
            f"Template {contract.get('template_key')!r} does not declare supports_saved_runs=True; "
            "this template's runs cannot be marked complete. To opt this workflow in anyway, set "
            "snapshot_inputs on its definition."
        )
    return (
        "Workflow has no snapshot_inputs manifest, no template_type, and its name does not match "
        "a known template; cannot resolve a completion contract. Set snapshot_inputs on the "
        "workflow definition (e.g. via the workflow_update_definition MCP tool) to declare what "
        "the snapshot should capture -- or set config.templateType to the key of the template "
        "this workflow was built from."
    )


def build_snapshot_for_run(
    data_access,
    run,
    *,
    requested_opportunity_id: int | None = None,
    request=None,
    program_id: int | None = None,
) -> dict[str, Any]:
    """Build -- and do NOT persist -- the snapshot that completing `run` would store.

    Returns ``{"payload", "contract", "definition", "opportunity_id", "opportunity_ids"}``.
    Raises `SnapshotBuildError` with a stable code for every way this can fail.

    Pipelines come from the processed cache the runner page populated -- never
    re-executed here. A snapshot freezes what was reviewed; re-running pipelines in
    this request both captured the wrong data and turned the button into a
    multi-minute batch job (102k visits, an OOM-killed worker). Only the aliases the
    contract captures are read; hook contracts get all of them.
    """
    from connect_labs.workflow.data_access import PipelineCacheMiss
    from connect_labs.workflow.templates import (
        SnapshotStateNotStagedError,
        SnapshotTooLargeError,
        build_snapshot_for_contract,
        resolve_snapshot_contract,
        resolve_snapshot_opp_scope,
    )

    definition_id = run.data.get("definition_id")
    if not definition_id:
        raise SnapshotBuildError("no_definition_id", f"run {run.id} has no definition_id")

    definition = data_access.get_definition(definition_id)
    if definition is None:
        raise SnapshotBuildError("definition_not_found", f"workflow definition {definition_id} not found")

    contract = resolve_snapshot_contract(definition)
    if not contract["ok"]:
        raise SnapshotBuildError(contract["error"], contract_error_message(contract), contract=contract)

    # A program-owned multi-opp definition has neither a run-level nor a
    # definition-level opportunity_id -- its opps are in opportunity_ids -- so
    # resolving only the singular fields made such a run impossible to conclude
    # (#1182).
    opportunity_id, effective_opp_ids = resolve_snapshot_opp_scope(run, definition, requested_opportunity_id)
    if not opportunity_id:
        raise SnapshotBuildError(
            "no_opportunity",
            f"run {run.id} has no opportunity: neither the run, the definition, nor its "
            "opportunity_ids name one, so there is nothing to snapshot against",
            contract=contract,
        )

    contract_inputs = contract.get("snapshot_inputs")
    aliases = None if contract["source"] == "template_hook" else (contract_inputs or {}).get("pipelines")
    if aliases == []:
        pipelines: dict = {}
    else:
        try:
            pipelines = data_access.get_cached_pipeline_data(
                definition_id,
                opportunity_id,
                aliases=aliases,
                # Period-scope opted-in pipelines to the run's window so each saved
                # run freezes its own period, not the all-time aggregate (ace#764).
                period_start=run.period_start,
                period_end=run.period_end,
            )
        except PipelineCacheMiss as e:
            raise SnapshotBuildError(
                "cache_miss",
                f"no cached data for pipeline {e.pipeline_name or e.alias!r} (opp {e.opportunity_id}); "
                "load the workflow's pipeline data first (open the run page, or run the pipelines), "
                "then retry",
                contract=contract,
            ) from e

    workers: list[dict] = []
    for oid in effective_opp_ids:
        try:
            for w in data_access.get_workers(oid):
                workers.append({**w, "opportunity_id": oid})
        except Exception:  # noqa: BLE001 -- an opp the user cannot enumerate must not block the rest
            logger.exception("Failed to load workers for opp %s", oid)

    try:
        payload = build_snapshot_for_contract(
            contract,
            pipelines=pipelines,
            state=run.data.get("state", {}),
            opportunity_id=opportunity_id,
            workers=workers,
            opportunity_ids=effective_opp_ids,
            # The full context contract, on BOTH paths. A builder that constructs
            # its own data access needs `access_token` and `program_id` where there
            # is no `request` (MCP), and `request` where there is (web); a gate hook
            # may read the run's audit sessions by `run_id`. Templates that use none
            # of these absorb them via **_.
            definition_id=definition_id,
            request=request,
            access_token=getattr(data_access, "access_token", None),
            program_id=program_id,
            run_id=run.id,
            # The run's own window. A builder that evaluates AS OF a date reads
            # `period_end`; without it a weekly run saved on Tuesday computed
            # "as of Tuesday" and called it last week's figures.
            period_start=run.period_start,
            period_end=run.period_end,
        )
    except SnapshotStateNotStagedError as e:
        # The run stays in_progress, which is the whole point: an empty snapshot
        # on a completed run is unrecoverable, an un-completed run is not.
        raise SnapshotBuildError("not_staged", str(e), contract=contract, missing=list(e.missing)) from e
    except SnapshotTooLargeError as e:
        raise SnapshotBuildError("too_large", str(e), contract=contract) from e
    if not isinstance(payload, dict):
        raise SnapshotBuildError("non_dict", "snapshot builder returned non-dict", contract=contract)

    return {
        "payload": payload,
        "contract": contract,
        "definition": definition,
        "opportunity_id": opportunity_id,
        "opportunity_ids": effective_opp_ids,
    }


def cache_state(opportunity_ids) -> dict[str, Any]:
    """Whether the visit cache behind a build was cold or partial.

    Two lies the numbers cannot tell you about themselves. COLD: every count is
    zero, which reads as a programme with no babies. PARTIAL is worse and was
    silent -- the total is a real number computed over only the opportunities
    that happen to be cached, entirely credible and understated. A live preview
    carries this beside the payload so the render can say so.
    """
    from django.utils import timezone as dj_timezone

    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

    requested = [int(o) for o in (opportunity_ids or [])]
    present = set(
        RawVisitCache.objects.filter(
            opportunity_id__in=requested,
            visit_count__gt=0,
            expires_at__gt=dj_timezone.now(),
        ).values_list("opportunity_id", flat=True)
    )
    with_data = [o for o in requested if o in present]
    missing = [o for o in requested if o not in present]
    cold = not with_data
    partial = bool(with_data) and bool(missing)
    if cold:
        hint = (
            "No cached visits for these opportunities, so every metric is zero rather than "
            "genuinely zero. Reload the run page to load the data, then try again."
        )
    elif partial:
        hint = (
            f"Computed from {len(with_data)} of {len(requested)} opportunities: "
            f"{', '.join(str(o) for o in missing)} {'has' if len(missing) == 1 else 'have'} no cached "
            "visits, so these totals are understated. Reload the run page to load the rest."
        )
    else:
        hint = ""
    return {
        "cold_cache": cold,
        "partial_cache": partial,
        "opportunities_with_data": with_data,
        "opportunities_missing": missing,
        "cold_cache_hint": hint,
    }
