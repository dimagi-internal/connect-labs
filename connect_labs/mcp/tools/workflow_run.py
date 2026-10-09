"""A workflow run, for an agent: read its grading, run its actions.

For any agent on the labs MCP — a person's own (PAT or MCP sign-in), or canopy
acting as the visitor on a run page that shares its run (``config.agent.share``,
``workflow/agent_sharing.py``). The run page hands canopy its SELECTION (the run,
its scope, the worker keys on screen); these read the substance live, as the caller:

* ``workflow_run_context``      — what the run is, the indicators it is graded on,
                                   and the actions the workflow offers;
* ``workflow_run_indicators``   — the graded cells, filterable by band ("red");
* ``workflow_indicator_explain``— how an indicator is computed, from the run's registry;
* ``workflow_run_action``       — run one of the workflow's OWN actions (the same one
                                   its button runs): a preview first, then the call
                                   that acts, carrying the preview's confirm token;
* ``workflow_action_status``    — how an action run is going.

A canopy (delegated) call is held to the workflow's opt-in to sharing; a person's
own agent is not, since it already reaches every tool.
"""

from __future__ import annotations

import logging
from typing import Any

from ..tool_registry import MCPToolError, register

logger = logging.getLogger(__name__)

_SCOPE = {
    "run_id": {"type": "integer", "description": "The workflow run (on a run page: the page state's filters.run_id)."},
    "opportunity_id": {
        "type": "integer",
        "description": "The run's scope when it is opportunity-owned. Give exactly one of this or program_id.",
    },
    "program_id": {
        "type": "integer",
        "description": "The run's scope when it is program-owned. Give exactly one of this or opportunity_id.",
    },
}


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------


def _wda_for_user(user, opportunity_id: int | None = None, program_id: int | None = None):
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    return WorkflowDataAccess(
        opportunity_id=opportunity_id, program_id=program_id, access_token=require_connect_token(user)
    )


def _delegated_token():
    """The delegated (canopy-for-a-visitor) access token of this call, or None."""
    try:
        from fastmcp.server.dependencies import get_access_token

        token = get_access_token()
    except Exception:  # noqa: BLE001 -- no MCP request context (direct calls, tests)
        return None
    # Recognised by what the verifier stamped, not by whether the call is limited:
    # a restricted ("no user visit data") call is limited too, and is not canopy.
    claims = getattr(token, "claims", None) or {}
    return token if claims.get("auth_method") == "delegated" else None


def _caller_actor() -> str:
    try:
        from fastmcp.server.dependencies import get_http_headers

        return (get_http_headers().get("canopy-actor") or "")[:100]
    except Exception:  # noqa: BLE001
        return ""


class _Run:
    """A run and its definition, read as the caller, in the run's own scope."""

    def __init__(self, user, run_id: int, opportunity_id: int | None, program_id: int | None):
        from connect_labs.workflow.agent_sharing import definition_shares_with_agent

        if (opportunity_id is None) == (program_id is None):
            raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")
        self.user = user
        self.opportunity_id = opportunity_id
        self.program_id = program_id
        self.delegated = _delegated_token() is not None
        self.wda = _wda_for_user(user, opportunity_id=opportunity_id, program_id=program_id)
        try:
            self.run = self.wda.get_run(run_id)
            if self.run is None:
                raise MCPToolError("NOT_FOUND", f"workflow run {run_id} not found in this scope")
            definition_id = (self.run.data or {}).get("definition_id")
            self.definition = self.wda.get_definition(definition_id) if definition_id else None
            if self.definition is None:
                raise MCPToolError("NOT_FOUND", f"the workflow behind run {run_id} could not be read")
            if self.delegated and not definition_shares_with_agent(self.definition):
                raise MCPToolError(
                    "PERMISSION_DENIED",
                    "This workflow does not share its runs with the embedded agent "
                    "(its definition's config.agent.share is off).",
                )
        except BaseException:
            self.wda.close()
            raise

    def close(self):
        self.wda.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @property
    def page_url(self) -> str:
        scope = f"opportunity_id={self.opportunity_id}" if self.opportunity_id else f"program_id={self.program_id}"
        return f"/labs/workflow/{self.definition.id}/run/?run_id={self.run.id}&{scope}"

    def graded(self) -> dict:
        """The run's grading, trimmed to what an agent reads, with its provenance
        (``workflow/run_grading.py``: stored for a completed run, else live and
        briefly cached per person)."""
        from connect_labs.workflow.run_grading import GradingUnavailable, NotGraded, graded_for_run

        from ..visit_access import caller_restricted

        try:
            return graded_for_run(
                self.user,
                self.wda,
                self.run,
                opportunity_id=self.opportunity_id,
                program_id=self.program_id,
                restricted=caller_restricted(),
            )
        except NotGraded as e:
            raise _not_semantic(self.run.id) from e
        except GradingUnavailable as e:
            raise MCPToolError("UPSTREAM_ERROR", e.message) from e


def _not_semantic(run_id) -> MCPToolError:
    return MCPToolError(
        "INVALID_SCHEMA",
        f"run {run_id} is not graded by the semantic layer (its snapshot has no per-worker "
        "indicator grading), so there is nothing to read by band.",
    )


def _action_error(e) -> MCPToolError:
    code = "VERSION_CONFLICT" if e.code.startswith("confirm") else "INVALID_SCHEMA"
    if e.code in ("not_offered", "forbidden"):
        code = "PERMISSION_DENIED"
    return MCPToolError(code, str(e), {"reason": e.code})


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@register(
    name="workflow_run_context",
    description=(
        "Start here for a workflow run. What the run is, the indicators it is graded on "
        "(label, plain meaning, direction, thresholds, target, unit -- the thresholds these "
        "bands were computed with), what each band means, and the ACTIONS the workflow offers "
        "(the same ones its buttons run) with their argument schemas and defaults. On a run "
        "page, read the run and scope from the page state's filters (run_id and "
        "opportunity_id or program_id)."
    ),
    input_schema={"type": "object", "properties": dict(_SCOPE), "required": ["run_id"], "additionalProperties": False},
)
def workflow_run_context(user, *, run_id: int, opportunity_id=None, program_id=None) -> dict[str, Any]:
    from connect_labs.workflow.actions import definition_actions
    from connect_labs.workflow.agent_sharing import BAND_MEANING, indicator_catalog

    with _Run(user, run_id, opportunity_id, program_id) as r:
        run = r.run
        try:
            graded = r.graded()
            indicators = indicator_catalog(graded)
            display = graded.get("display") or {}
            grading = {
                "source": graded["source"],
                "generated_at": graded.get("generated_at"),
                "cache": graded["cache"],
            }
        except MCPToolError as e:
            indicators, display, grading = None, {}, {"unavailable": e.message}
        return {
            "workflow": {
                "id": r.definition.id,
                "name": r.definition.name,
                "template_type": r.definition.template_type,
            },
            "run": {
                "id": run.id,
                "name": run.name,
                "status": run.status,
                "period_start": run.period_start,
                "period_end": run.period_end,
                "completed_at": run.completed_at,
            },
            "page_url": r.page_url,
            "nouns": {k: display.get(k) for k in ("entity", "worker", "organisation") if display.get(k)},
            "indicators": indicators,
            "bands": BAND_MEANING,
            "grading": grading,
            "actions": definition_actions(r.definition),
        }


@register(
    name="workflow_run_indicators",
    description=(
        "The run's graded indicators -- the same cells the report colours. scope='worker' "
        "(default) lists workers with their worker `key` (what workflow_run_action takes), "
        "organisation, case count, red/yellow counts and a cell per indicator {band, value, n}. "
        "`band='red'` keeps only workers with at least one red indicator (among `indicators` "
        "if given) and names them in `matched`, most-matched first. Bands come from the "
        "server's grading; never infer them from values. scope='organisation' | 'opportunity' "
        "| 'programme' returns those rows instead. `cache.cold` true means the live figures "
        "are all zero because no visits are loaded -- say so rather than reporting zeros."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE,
            "scope": {"type": "string", "enum": ["worker", "organisation", "opportunity", "programme"]},
            "band": {
                "type": "string",
                "enum": [
                    "red",
                    "yellow",
                    "green",
                    "insufficient",
                    "notcredible",
                    "notinapp",
                    "unrecorded",
                    "unbanded",
                    "nodata",
                ],
            },
            "indicators": {"type": "array", "items": {"type": "string"}},
            "worker_keys": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 500},
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
)
def workflow_run_indicators(
    user,
    *,
    run_id: int,
    opportunity_id=None,
    program_id=None,
    scope: str = "worker",
    band: str | None = None,
    indicators: list[str] | None = None,
    worker_keys: list[str] | None = None,
    limit: int = 200,
) -> dict[str, Any]:
    from connect_labs.workflow.agent_sharing import BAND_MEANING, GradingError, scope_rows, select_workers

    with _Run(user, run_id, opportunity_id, program_id) as r:
        graded = r.graded()
        known = {m.get("indicator") or m.get("id") for m in graded.get("cMeasures") or []}
        unknown = sorted(set(indicators or []) - known)
        if unknown:
            raise MCPToolError("NOT_FOUND", f"not indicators of this run: {unknown}; known: {sorted(known)}")
        head = {
            "run_id": r.run.id,
            "source": graded["source"],
            "generated_at": graded.get("generated_at"),
            "cache": graded["cache"],
            "legend": BAND_MEANING,
        }
        try:
            if scope == "worker":
                return {
                    **head,
                    **select_workers(graded, band=band, indicators=indicators, worker_keys=worker_keys, limit=limit),
                }
            rows = scope_rows(graded, scope, indicators)
        except GradingError as e:
            raise MCPToolError("INVALID_SCHEMA", str(e)) from e
        return {**head, "rows": rows, "opportunity_labels": graded.get("opportunity_labels")}


@register(
    name="workflow_indicator_explain",
    description=(
        "How this run's indicators are computed, from the semantic registry the workflow is "
        "bound to. With no `indicators`: an index -- id, title, unit, category, the authored "
        "plain meaning and a definition rendered from the SQL. With ids: the full chain "
        "(measure, components, properties, filters) and the compiled statement once."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE,
            "indicators": {"type": "array", "items": {"type": "string"}},
            "scope": {"type": "string", "enum": ["programme", "llo", "opportunity", "flw"]},
        },
        "required": ["run_id"],
        "additionalProperties": False,
    },
)
def workflow_indicator_explain(
    user,
    *,
    run_id: int,
    opportunity_id=None,
    program_id=None,
    indicators: list[str] | None = None,
    scope: str = "programme",
) -> dict[str, Any]:
    from connect_labs.semantic.explain import UnknownIndicator, english, explain
    from connect_labs.semantic.runtime import SemanticRuntimeError
    from connect_labs.semantic.workflow_binding import resolve_registry_for
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    with _Run(user, run_id, opportunity_id, program_id) as r:
        # Read by the workflow's OWNER scope, which is the scope the run was opened
        # with (see snapshot_builders.semantic_snapshot).
        try:
            props_doc, full_registry, llo_map, reg_settings, _deployment, source = resolve_registry_for(
                r.definition,
                registry_access_factory=lambda: SemanticRegistryDataAccess(
                    access_token=r.wda.access_token, opportunity_id=opportunity_id, program_id=program_id
                ),
            )
        except SemanticRuntimeError as e:
            raise MCPToolError("INVALID_SCHEMA", str(e)) from e

        if not indicators:
            index = []
            for measure in full_registry.get("measures") or []:
                meta = measure.get("meta") or {}
                if not meta.get("indicator"):
                    continue
                index.append(
                    {
                        "indicator": meta["indicator"],
                        "title": measure.get("title"),
                        "unit": meta.get("unit"),
                        "category": meta.get("category"),
                        "plain": meta.get("plain"),
                        "definition": (english(full_registry, props_doc, measure["name"]) or {}).get("definition"),
                    }
                )
            return {"registry": source, "indicators": index}

        out = []
        for ind in indicators:
            try:
                out.append(
                    explain(
                        props_doc,
                        full_registry,
                        ind,
                        scope=scope,
                        llo_map=llo_map or None,
                        settings=reg_settings or None,
                    )
                )
            except UnknownIndicator:
                raise MCPToolError("NOT_FOUND", f"No indicator or measure named {ind!r} in this run's registry")
        compiled_sql = out[0].pop("compiled_sql", None) if out else None
        for explanation in out:
            explanation.pop("compiled_sql", None)
            explanation.pop("layer1", None)
        return {"registry": source, "scope": scope, "indicators": out, "compiled_sql": compiled_sql}


def _preview_choices(out: dict) -> list[dict] | None:
    """What the person can do with a ONE-worker coaching preview, for an agent to offer as
    buttons: send to the worker, send to themselves as a QA test (Dimagi staff --
    ``qa_redirect``), or not yet. Labs owns this list so every agent offers the same
    choices; None for anything else, or while ``needs`` are open."""
    if out.get("type") != "start_ocs_outreach" or out.get("needs") or len(out.get("workers") or []) != 1:
        return None
    if (out.get("arguments") or {}).get("deliver_to"):
        return None  # already a QA preview: the person chose; send it or not
    name = out["workers"][0].get("name") or "the worker"
    send = "Call again with this preview's `arguments` and `confirm`."
    if out.get("synthetic"):
        send += " Synthetic data: the task gets a sample conversation and no message is sent."
    choices = [{"label": f"Send to {name}", "do": send}]
    if out.get("qa_redirect"):
        choices.append(
            {
                "label": "Send to me (QA test)",
                "do": "Ask the person for their own PersonalID username, preview again with it as "
                "`arguments.deliver_to`, and send THAT preview's `confirm`. The conversation reaches "
                f"their Connect app, on {name}'s behalf.",
            }
        )
    choices.append({"label": "Not yet", "do": "Do nothing."})
    return choices


@register(
    name="workflow_run_action",
    description=(
        "Run one of the workflow's own actions (workflow_run_context -> actions), e.g. "
        "'Initiate AI coach' -- the same action its button runs, as the person you act for.\n\n"
        "TWO CALLS, ALWAYS. (1) Without `confirm`: a PREVIEW of exactly what would happen -- "
        "which workers, which bot, which text -- and a single-use `confirm` token; nothing is "
        "done. SHOW the preview to the person and get their explicit yes. (2) Call again with "
        "the preview's `arguments` and its `confirm`: the action is queued and an execution id "
        "returned; follow it with workflow_action_status. Changing anything between the two "
        "invalidates the token. If the preview lists `needs`, settle them first: `bot` -- ask "
        "which of `bot_choices` to use, then preview again with `bot`; `connect_ocs` -- the "
        "person must connect Open Chat Studio at `connect_url` first.\n\n"
        "SHOWING A PREVIEW: give each worker's `briefing` topics and `opening` (the worker's first "
        "message, verbatim), and a worker's `image` inline as markdown `![<caption>](<url>)` -- it "
        "opens for a signed-in Labs user who can see that opportunity. When the preview carries "
        "`choices`, offer exactly those -- as buttons if your client can (a multiple-choice "
        "question) -- and do what the chosen one's `do` says; never pick for the person. A "
        "`synthetic: true` preview sends no message (`synthetic_note`); say so.\n\n"
        "`arguments.workers[].key` are worker keys from workflow_run_indicators; an item's own "
        "`prompt` is how to address that worker's own red indicators."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE,
            "action": {"type": "string", "description": "An action key the workflow offers."},
            "arguments": {"type": "object"},
            "confirm": {"type": "string", "description": "The token from this action's preview. Omit to preview."},
        },
        "required": ["run_id", "action", "arguments"],
        "additionalProperties": False,
    },
    is_write=True,
)
def workflow_run_action(
    user,
    *,
    run_id: int,
    action: str,
    arguments: dict,
    opportunity_id=None,
    program_id=None,
    confirm: str | None = None,
) -> dict[str, Any]:
    from connect_labs.workflow.actions import ActionError, briefing_source, commit, preview

    from ..visit_access import caller_restricted

    with _Run(user, run_id, opportunity_id, program_id) as r:
        # A coaching action briefs each worker from the run's grading, read in the
        # caller's own scope and restriction -- the same cells workflow_run_indicators returns.
        briefing = briefing_source(
            user,
            r.wda,
            r.run,
            r.definition,
            opportunity_id=opportunity_id,
            program_id=program_id,
            restricted=caller_restricted(),
        )
        try:
            if not confirm:
                out = preview(
                    user,
                    wda=r.wda,
                    run=r.run,
                    definition=r.definition,
                    key=action,
                    arguments=arguments,
                    briefing=briefing,
                )
                choices = _preview_choices(out)
                if choices:
                    out["choices"] = choices
                out["next"] = (
                    "Nothing has been done. Settle `needs`, then preview again."
                    if out["needs"]
                    else (
                        "Nothing has been done. Show this to the person and offer `choices`; act only on "
                        "the one they pick."
                        if choices
                        else "Nothing has been done. Show this to the person; on their yes, call again with "
                        "these `arguments` and `confirm`."
                    )
                )
                return out
            execution = commit(
                user,
                wda=r.wda,
                run=r.run,
                definition=r.definition,
                key=action,
                arguments=arguments,
                confirm=confirm,
                via="canopy" if r.delegated else "mcp",
                actor=_caller_actor(),
                briefing=briefing,
            )
        except ActionError as e:
            raise _action_error(e) from e
        return {
            "execution": execution.as_dict(),
            "page_url": r.page_url,
            "next": "Queued. Follow it with workflow_action_status.",
        }


@register(
    name="workflow_action_status",
    description=(
        "How an action run is going: status (queued, running, completed, "
        "completed_with_errors, failed), progress, and per-worker results (task_id, "
        "session_id, or error). Give execution_id, or none for your recent runs on this run."
    ),
    input_schema={
        "type": "object",
        "properties": {**_SCOPE, "execution_id": {"type": "integer"}},
        "required": ["run_id"],
        "additionalProperties": False,
    },
)
def workflow_action_status(
    user, *, run_id: int, opportunity_id=None, program_id=None, execution_id: int | None = None
) -> dict[str, Any]:
    from connect_labs.workflow.models import WorkflowActionExecution

    with _Run(user, run_id, opportunity_id, program_id) as r:
        from ..visit_access import caller_restricted

        qs = WorkflowActionExecution.objects.filter(user=user, run_id=r.run.id)
        if caller_restricted():
            # Labs-local and production run ids are separate sequences and overlap, so
            # without visit access an execution must also match the run's own
            # opportunity/program (what actions.py records), or a real run's
            # executions -- worker keys, statuses, errors -- could come back.
            qs = qs.filter(opportunity_id=r.run.opportunity_id, program_id=getattr(r.run, "program_id", None))
        if execution_id is not None:
            qs = qs.filter(pk=execution_id)
        return {"run_id": r.run.id, "executions": [e.as_dict() for e in qs[:20]]}
