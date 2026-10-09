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
* ``workflow_action_status``    — how an action run is going;
* ``workflow_action_preview_view`` — app-only (MCP Apps): the preview the coaching View
                                   shows, as the VIEWER, with its picture inline and
                                   the viewer's own confirm token.

A coaching action is sent by a person's click, never by the agent. Its preview, as
the agent gets it, carries no confirm token; the token goes only to the View canopy
renders for ``workflow_run_action`` (``ui://labs/workflow-action-preview``), which
previews again as whoever is looking at it, or to the Labs run page's own button.

A canopy (delegated) call is held to the workflow's opt-in to sharing; a person's
own agent is not, since it already reaches every tool.
"""

from __future__ import annotations

import logging
from typing import Any

from ..tool_registry import MCPToolError, register
from ..ui import WORKFLOW_ACTION_PREVIEW_URI, tool_meta

logger = logging.getLogger(__name__)

#: Action types a person sends by clicking -- the View's Send, or the run page's
#: button -- and never the agent: the agent's preview of one carries no ``confirm``.
CLICK_TO_SEND_TYPES = frozenset({"start_ocs_outreach"})

#: The pictures a View preview draws inline, at most: one per worker, for the first
#: few workers, each well under what a postMessage to a sandboxed frame copes with.
VIEW_IMAGE_WORKERS = 3
VIEW_IMAGE_MAX_BYTES = 512 * 1024

#: Where the person sends a coaching preview from, as the agent is told it.
CLICK_TO_SEND = (
    "The person sends this themselves, by clicking: in canopy, Send on the card shown "
    "with this preview (it shows the picture, the briefing and the opening message, and "
    "offers Send to the worker, Send to me (QA test) for Dimagi staff, and Not yet); on "
    "the Labs run page (`page_url`), the Start coaching button. You cannot send it: there "
    "is no `confirm` for you."
)

#: What the agent says after previewing a click-to-send action. The card carries the
#: content; a chat that restates it buries the card (owner, 2026-10-09: "extremely
#: minimal beyond the card").
CHAT_AFTER_PREVIEW = (
    "Say nothing before the preview (no narration of who the worker is or what you are "
    "looking up). In canopy the card shows everything -- picture, topics, opening, coach, the synthetic note "
    "and the Send buttons -- so after previewing reply in ONE short line (e.g. 'Here's the "
    "coaching card for Ibrahim.') and do not restate anything the card shows. Only outside "
    "canopy, where there is no card, give the topics briefly and point to Start coaching on "
    "the Labs page (`page_url`)."
)

#: How an agent runs each kind of action, given beside the action in workflow_run_context --
#: the tool an agent reads first. (A tool's own description is not enough: clients that
#: load tool schemas on demand never show it until the agent has already chosen the tool.
#: Seen live 2026-10-09: asked to start coaching, the agent read the context, never opened
#: workflow_run_action, and sent the person to the page's button instead of previewing.)
HOW_TO_RUN_CLICK_TO_SEND = (
    "Sent only by the person's click, never by you. As soon as they want to coach someone -- "
    "including when they ask for a summary of a worker's data to start coaching -- PREVIEW it "
    "in that same reply, beside any summary: workflow_run_action with this action's key, "
    "`arguments.workers: [{key}]`, and no `confirm`. Never end on 'say so and I'll start it'. "
    "In canopy that preview appears to them as a card with the worker's picture, "
    "the briefing and the opening message, and the buttons Send to <worker>, Send to me (QA "
    "test) and Not yet. " + CHAT_AFTER_PREVIEW + " Don't ask for a yes in chat and "
    "don't offer to send it. Labs writes the briefing and always sends it to this workflow's "
    "coach; to change what the coach is told, add text in a worker's `prompt` (or the "
    "top-level `prompt` for all) -- it stays inside the briefing as a note. To choose what "
    "the picture shows, see `picture` and `picture_types` beside this."
)
HOW_TO_RUN_CONFIRMED = (
    "Preview with workflow_run_action (no `confirm`), show the person exactly what it will do, "
    "and on their explicit yes call again with the preview's `arguments` and `confirm`."
)


#: What the agent is told about the coaching picture: it chooses WHAT the chart shows,
#: Labs supplies every number and Connect's look (workflow/coach_charts/).
PICTURE_GUIDE = (
    "THE PICTURE is a chart Labs draws for the card and the worker; you choose what it shows "
    "with `arguments.picture` (top-level, or on one worker's item), default {type: 'topic_bars'}. "
    "Use a named type (`picture_types`): topic_bars -- the worker's own figures per topic; "
    "peer_comparison -- beside each other worker on this run; trend -- week by week "
    "(`params.weeks`; `params.peers: true` adds peers' lines). `params.topics` (indicator ids from "
    "`indicators`) picks or adds topics, e.g. 'add the unreadable-tests topic'. Only when no type "
    "fits, use {type: 'custom', spec: <Vega-Lite>} reading Labs' datasets by name -- "
    "{data: {name: 'worker_topics' | 'peers' | 'history'}} -- with NO numbers of your own (inline "
    "data is stripped) and NO styling (Connect's theme is always applied; colours outside it are "
    "dropped). Peers are ALWAYS anonymous -- Peer A, Peer B, ... -- so never name another worker "
    "in a chart title or a note: Labs refuses it. To change the picture, preview again with a "
    "new `picture`; the card then shows exactly what will be sent."
)


def _picture_types() -> dict:
    """The picture catalog an agent reads in workflow_run_context: each named type's
    description and params, the custom path, and the datasets a custom spec may read."""
    from connect_labs.workflow.coach_charts import types
    from connect_labs.workflow.coach_charts.chart import _CUSTOM_PARAMS

    return {
        "types": {t.name: {"description": t.description, "params": t.params} for t in types.TYPES.values()},
        "custom": {
            "use_when": "no named type fits",
            "shape": {"type": "custom", "spec": "<Vega-Lite reading Labs' datasets by name>", "params": "see below"},
            "params": _CUSTOM_PARAMS,
            "datasets": {
                "worker_topics": "the worker's own figures, one row per topic: i, key, label, unit, band, value "
                "(a fraction for unit '%'), n, numerator, denominator, pct, figure_text, who ('You')",
                "peers": "other workers on this run, one row per peer and topic: i, key, label, unit, who "
                "('Peer A', ...), value, n, band",
                "history": "week by week up to this run: week (date), t, i, key, label, unit, who, value, n "
                "(peers' rows only with params.peers)",
            },
        },
    }


def _with_how_to_run(actions: list[dict]) -> list[dict]:
    for action in actions:
        action["how_to_run"] = (
            HOW_TO_RUN_CLICK_TO_SEND if action.get("type") in CLICK_TO_SEND_TYPES else HOW_TO_RUN_CONFIRMED
        )
        if action.get("type") == "start_ocs_outreach":
            action["picture"] = PICTURE_GUIDE
            action["picture_types"] = _picture_types()
    return actions


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
            "actions": _with_how_to_run(definition_actions(r.definition)),
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


def _for_the_agent(out: dict, r: _Run) -> dict:
    """A click-to-send preview as the agent may see it: everything it shows, minus the
    token that would let the agent send it."""
    out.pop("confirm", None)
    out.pop("confirm_expires_in", None)
    out["sent_by"] = "click"
    out["page_url"] = r.page_url
    if out["needs"]:
        out["next"] = "Nothing has been done. Settle `needs`, then preview again."
    else:
        out["next"] = "Nothing has been done. " + CHAT_AFTER_PREVIEW + " " + CLICK_TO_SEND + _picture_next(out)
    return out


def _connect_ocs_url(page_url: str | None) -> str:
    """Labs' Open Chat Studio OAuth start, returning the person to the run page when it
    is done (the callback accepts only a relative ``next``) rather than to the overview."""
    from urllib.parse import urlencode

    path = "/labs/ocs/initiate/"
    if page_url and page_url.startswith("/"):
        path += "?" + urlencode({"next": page_url})
    return _absolute(path) or path


def _picture_next(out: dict) -> str:
    """One line on the picture the preview made: which chart, what Labs stripped, and
    how to change it -- for the agent, not to repeat in chat."""
    charts = [
        w["image"]["chart"]
        for w in out.get("workers") or []
        if isinstance(w.get("image"), dict) and w["image"].get("chart")
    ]
    if not charts:
        return ""
    kinds = sorted({c.get("type") for c in charts if c.get("type")})
    line = f" Picture: {', '.join(kinds)} (Labs' figures, Connect's theme, peers anonymous)."
    notes = [n for c in charts for n in c.get("notes") or []]
    if notes:
        line += " " + notes[0] + "."
    return (
        line
        + " To show something else, preview again with `arguments.picture` (workflow_run_context -> picture_types)."
    )


def _absolute(path: str | None) -> str | None:
    """A Labs path as a link a View can open (``ui/open-link`` takes https only)."""
    from django.conf import settings

    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    if not path or not base or not path.startswith("/"):
        return None
    return base + path


def _inline_image(prompt: str | None, image: dict | None = None) -> dict | None:
    """The worker's picture as a ``data:`` URI, for a View: ``{"data_uri", "caption",
    "bytes"}`` (plus the chart's ``chart`` info), or None when there is none. The frozen
    chart the preview built (``image["chart"]["id"]``) -- the same one the worker's
    conversation links to; else the briefing's own figures."""
    import base64

    from connect_labs.workflow import coach_image

    chart_info = (image or {}).get("chart") if isinstance(image, dict) else None
    if chart_info and chart_info.get("id"):
        from connect_labs.workflow.coach_charts import chart as charts
        from connect_labs.workflow.coach_charts import store

        record = store.get(chart_info["id"])
        if record is None:
            return None
        png, caption = charts.png(record.chart), record.chart.get("caption") or ""
    else:
        payload = coach_image.payload_from_briefing(prompt or "")
        if payload is None:
            return None
        png, caption = coach_image.render_png(payload), coach_image.caption(payload)
    extra = {"chart": chart_info} if chart_info else {}
    if len(png) > VIEW_IMAGE_MAX_BYTES:
        return {"omitted": f"too large to show inline ({len(png)} bytes)", "caption": caption, **extra}
    return {
        "data_uri": "data:image/png;base64," + base64.b64encode(png).decode("ascii"),
        "caption": caption,
        "bytes": len(png),
        **extra,
    }


def _view_text(out: dict) -> str:
    """The text content of a View preview: what it says, without the inline pictures
    (they are for the View) and without the viewer's confirm token."""
    import json

    def strip(worker):
        image = worker.get("image")
        if isinstance(image, dict) and image.get("data_uri"):
            shown = {"caption": image.get("caption"), "inline_png_bytes": image.get("bytes")}
            worker = {**worker, "image": {**shown, **({"chart": image["chart"]} if image.get("chart") else {})}}
        return worker

    shown = {k: v for k, v in out.items() if k not in ("confirm", "confirm_expires_in")}
    shown["workers"] = [strip(w) for w in out.get("workers") or []]
    return json.dumps(shown, default=str)


@register(
    name="workflow_run_action",
    description=(
        "Run one of the workflow's own actions (workflow_run_context -> actions), e.g. "
        "'Initiate AI coach' -- the same action its button runs, as the person you act for.\n\n"
        "PREVIEW FIRST. Without `confirm` you get a PREVIEW of exactly what would happen -- "
        "which workers, which bot, which text; nothing is done. If it lists `needs`, settle "
        "them and preview again: `bot` -- ask which of `bot_choices` to use, then preview with "
        "`bot`; `connect_ocs` -- the person must connect Open Chat Studio at `connect_url`.\n\n"
        "COACHING ('Initiate AI coach', type start_ocs_outreach) IS SENT BY THE PERSON'S CLICK, "
        "NEVER BY YOU. Its preview has no `confirm` (`sent_by: click`). In canopy the preview "
        "is shown to the person as a card with the picture, the briefing and the opening message, "
        "and the buttons Send to <worker>, Send to me (QA test) -- Dimagi staff, who type their "
        "own PersonalID username -- and Not yet. On the Labs run page the same send is its Start "
        "coaching button. So when the person wants to coach or start coaching someone, PREVIEW "
        "STRAIGHT AWAY -- the preview is what puts the card in front of them. "
        + CHAT_AFTER_PREVIEW
        + " Never ask them to confirm in "
        "chat, and never offer to send it yourself. Once they have sent it, "
        "workflow_action_status shows how it is going.\n"
        "Labs writes each worker's briefing from this run's grading (topics: the worker's "
        "off-target indicators, worst first) and always sends it to the workflow's own coach, "
        "with the fixed opening to the worker. To change what the coach is told, give text: a "
        "worker's own `prompt` for one worker, the top-level `prompt` for all. Labs keeps it "
        "inside the briefing as the programme team's note -- the coach, the picture and the "
        "opening stay -- so edit freely. `bot` cannot name another coach. "
        + PICTURE_GUIDE
        + " On a synthetic opportunity the "
        "preview's `arguments.bot` is Labs' sample stand-in (no message goes out) while `bot` "
        "names the coach a real run uses: that is expected, not a mismatch. A synthetic preview "
        "also has no `opening`, because no message goes to the worker; a QA send (Send to me) "
        "opens with Labs' fixed opening, which the card shows. The card explains all this: don't.\n\n"
        "OTHER ACTIONS (e.g. create_task): the preview carries a single-use `confirm`. Show the "
        "preview, get the person's explicit yes, then call again with the preview's `arguments` "
        "and its `confirm`; the action is queued and an execution id returned. Changing anything "
        "between the two calls invalidates the token.\n\n"
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
    # Rendered as the coaching View; callable by the View too, which is how its Send
    # commits (with the confirm only the View's own preview gets).
    meta=tool_meta(WORKFLOW_ACTION_PREVIEW_URI, "model", "app"),
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
                from connect_labs.workflow.actions import INCLUDE_IMAGE, find_action

                if find_action(r.definition, action)["type"] in CLICK_TO_SEND_TYPES and isinstance(arguments, dict):
                    # The card always sends a picture (workflow_action_preview_view): the
                    # agent's preview pictures the same, so it can say what is shown.
                    arguments = {**arguments, INCLUDE_IMAGE: True}
                out = preview(
                    user,
                    wda=r.wda,
                    run=r.run,
                    definition=r.definition,
                    key=action,
                    arguments=arguments,
                    briefing=briefing,
                    restricted=caller_restricted(),
                )
                if out["type"] in CLICK_TO_SEND_TYPES:
                    return _for_the_agent(out, r)
                out["next"] = (
                    "Nothing has been done. Settle `needs`, then preview again."
                    if out["needs"]
                    else "Nothing has been done. Show this to the person; on their yes, call again with "
                    "these `arguments` and `confirm`."
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
                restricted=caller_restricted(),
            )
        except ActionError as e:
            raise _action_error(e) from e
        return {
            "execution": execution.as_dict(),
            "page_url": r.page_url,
            "next": "Queued. Follow it with workflow_action_status.",
        }


@register(
    name="workflow_action_preview_view",
    description=(
        "For the coaching View only (MCP Apps, app-only): the preview of a workflow action as "
        "the person LOOKING AT the View, with each worker's picture inline as a data: URI and "
        "that person's own single-use `confirm`, which the View's Send hands to "
        "workflow_run_action. `deliver_to` (Dimagi staff only, one worker) previews a QA send "
        "to that PersonalID username instead of the worker. Hosts hide this tool from the agent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE,
            "action": {"type": "string", "description": "An action key the workflow offers."},
            "arguments": {"type": "object"},
            "deliver_to": {
                "type": "string",
                "maxLength": 150,
                "description": "QA only, Dimagi staff only: a PersonalID username to send to "
                "instead of the worker. Empty clears it.",
            },
        },
        "required": ["run_id", "action", "arguments"],
        "additionalProperties": False,
    },
    meta=tool_meta(WORKFLOW_ACTION_PREVIEW_URI, "app"),
    text=_view_text,
)
def workflow_action_preview_view(
    user,
    *,
    run_id: int,
    action: str,
    arguments: dict,
    opportunity_id=None,
    program_id=None,
    deliver_to: str | None = None,
) -> dict[str, Any]:
    from connect_labs.workflow.actions import DELIVER_TO, INCLUDE_IMAGE, ActionError, briefing_source, preview
    from connect_labs.workflow.models import WorkflowActionExecution

    from ..visit_access import caller_restricted

    if not isinstance(arguments, dict):
        raise MCPToolError("INVALID_SCHEMA", "`arguments` must be an object.")
    args = dict(arguments)
    if deliver_to is not None:
        if deliver_to.strip():
            args[DELIVER_TO] = deliver_to.strip()
        else:
            args.pop(DELIVER_TO, None)
    with _Run(user, run_id, opportunity_id, program_id) as r:
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
            from connect_labs.workflow.actions import find_action

            kind = find_action(r.definition, action)["type"]
            if kind in CLICK_TO_SEND_TYPES and not r.delegated and not args.get(DELIVER_TO):
                # Hosts hide this tool from the agent, but a client on a person's own token
                # (an agent's direct MCP connection) can name it anyway. Off canopy it may only
                # preview a QA send: reaching a WORKER takes a click, on canopy's card or the
                # Labs page's own button.
                raise MCPToolError(
                    "PERMISSION_DENIED",
                    "A coaching conversation reaches a worker only by a person's click: the Send "
                    "button on canopy's card, or Start coaching on the Labs page. Off canopy, this "
                    "tool previews a QA send only (`deliver_to`).",
                )
            if kind in CLICK_TO_SEND_TYPES:
                # What the View shows is what is sent: the picture goes with the conversation.
                args[INCLUDE_IMAGE] = True
            out = preview(
                user,
                wda=r.wda,
                run=r.run,
                definition=r.definition,
                key=action,
                arguments=args,
                briefing=briefing,
                restricted=caller_restricted(),
            )
        except ActionError as e:
            raise _action_error(e) from e
        if out["type"] not in CLICK_TO_SEND_TYPES:
            # The View only sends what is sent by a click; anything else the agent
            # confirms with the person, so the View gets no token for it.
            out.pop("confirm", None)
            out.pop("confirm_expires_in", None)
        out["sent_by"] = "click" if out["type"] in CLICK_TO_SEND_TYPES else "agent"
        for i, worker in enumerate(out["workers"]):
            linked = worker.pop("image", None)  # the Labs link: no use to an opaque-origin View
            if out["type"] in CLICK_TO_SEND_TYPES and i < VIEW_IMAGE_WORKERS:
                image = _inline_image(worker.get("prompt"), linked)
                if image is not None:
                    worker["image"] = image
        out["page_url"] = _absolute(r.page_url) or r.page_url
        if out["type"] in CLICK_TO_SEND_TYPES:
            # Every coaching send runs on the sender's own OCS connection -- on synthetic
            # data too, for a test send -- so the View checks it FIRST and asks for it
            # before showing anything else.
            from connect_labs.workflow.actions import ocs_connected

            out["ocs"] = {
                "connected": ocs_connected(user),
                "connect_url": _connect_ocs_url(r.page_url),
            }
        if out.get("connect_url"):
            out["connect_url"] = _connect_ocs_url(r.page_url)
        out["executions"] = [
            e.as_dict()
            for e in WorkflowActionExecution.objects.filter(user=user, run_id=r.run.id, action_key=action)[:5]
        ]
        return out


@register(
    name="workflow_action_status",
    description=(
        "How an action run is going: status (queued, running, completed, "
        "completed_with_errors, failed), progress, and per-worker results (task_id, "
        "session_id, or error). Give execution_id, or none for your recent runs on this run. "
        "Dimagi staff see every person's runs on this run, each with `by` (who ran it), so a "
        "send someone else made can be debugged."
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
        from connect_labs.utils.dimagi_user import is_dimagi_user

        from ..visit_access import caller_restricted

        # Dimagi staff (people, and ACE as the support agent) see everyone's runs on a run
        # they can open, to debug a send another person made (2026-10-09: ACE could not
        # see the owner's execution 13). Everyone else sees only their own.
        staff = is_dimagi_user(user)
        qs = WorkflowActionExecution.objects.filter(run_id=r.run.id)
        if not staff:
            qs = qs.filter(user=user)
        if caller_restricted():
            # Labs-local and production run ids are separate sequences and overlap, so
            # without visit access an execution must also match the run's own
            # opportunity/program (what actions.py records), or a real run's
            # executions -- worker keys, statuses, errors -- could come back.
            qs = qs.filter(opportunity_id=r.run.opportunity_id, program_id=getattr(r.run, "program_id", None))
        if execution_id is not None:
            qs = qs.filter(pk=execution_id)
        executions = []
        for e in qs.select_related("user")[:20]:
            row = e.as_dict()
            if staff:
                row["by"] = getattr(e.user, "email", "") or getattr(e.user, "username", "")
            executions.append(row)
        return {"run_id": r.run.id, "executions": executions}
