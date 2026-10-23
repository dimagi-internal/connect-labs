"""Edits an agent proposes through canopy, saved when a person confirms them in chat.

canopy, acting for a visitor (the delegated grant), may change a workflow, its
pipelines and its semantic registry -- but never on its own say-so. Its scope
(``workflow:edit``) does not include the edit tools themselves, only these:

* ``workflow_edit_propose``    -- the agent names an edit tool and its arguments. Labs
                                  checks them, works out what would change, and holds
                                  the edit unsaved (``StagedEdit``). The result links the
                                  confirm card (``ui://labs/workflow-edit-confirm``).
* ``workflow_edit_review_view``-- app-only: the card reads the held edit back AS THE
                                  VIEWER, with the diff and that person's own single-use
                                  ``confirm`` token.
* ``workflow_edit_apply``      -- app-only: the card's Confirm. Runs the edit tool as the
                                  person who clicked, and the change saves straight to the
                                  workflow (its version history is the undo).
* ``workflow_edit_decline``    -- app-only: the card's Decline.

The agent never holds a confirm token, so it cannot confirm its own edit; the host
hides app-only tools from it and refuses it a call to one. That is the host's rule to
enforce, so each app-only tool still checks its caller itself.

What an edit through canopy may NOT do, whatever the person confirms, is send data
anywhere else: ``refusal`` refuses the definition changes that would (a workflow's
own outbound actions, its agent settings, who it shares to), and render code may be
changed only on a workflow that runs in the page sandbox, whose enforced
``connect-src`` names no host the code could post rows to (``workflow/sandbox.py``).
Sharing, publishing, cloning and the like are simply not proposable.
Design: canopy-web ``docs/superpowers/specs/2026-10-22-sensitive-data-agents-design.md`` §7.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import secrets
import uuid

from django.core import signing
from django.utils import timezone

from ..tool_registry import MCPToolError, get_tool, register
from ..ui import WORKFLOW_EDIT_CONFIRM_URI, tool_meta

#: The edit tools an agent may propose. Each changes one workflow, pipeline or
#: registry in place; none shares, publishes, copies or deletes anything.
EDIT_TOOLS: frozenset[str] = frozenset(
    {
        "workflow_update_definition",
        "workflow_update_render_code",
        "workflow_patch_render_code",
        "workflow_add_pipeline_source",
        "workflow_remove_pipeline_source",
        "pipeline_create",
        "pipeline_update_schema",
        "semantic_registry_create",
        "semantic_registry_update",
        "semantic_registry_set_indicator_meta",
    }
)

RENDER_TOOLS = frozenset({"workflow_update_render_code", "workflow_patch_render_code"})

#: Definition keys an edit through canopy may not set: they change where the
#: workflow's data goes or who sees it, not what the workflow computes.
REFUSED_DEFINITION_KEYS = frozenset({"kind", "page", "workflow_sources", "config_reads"})
#: ``config`` keys likewise: its outbound actions (a message to a worker), its
#: embedded-agent settings, and its sandbox switch.
REFUSED_CONFIG_KEYS = frozenset({"actions", "agent", "sandbox"})

_CONFIRM_SALT = "labs.mcp.workflow_edit.confirm"
CONFIRM_MAX_AGE_SECONDS = 30 * 60
#: A diff longer than this is cut, with a note; the card is a review, not an editor.
MAX_DIFF_LINES = 600


def digest(user_pk: int, tool: str, arguments: dict) -> str:
    raw = json.dumps([user_pk, tool, arguments], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _scope(arguments: dict) -> dict:
    return {k: arguments[k] for k in ("opportunity_id", "program_id") if arguments.get(k) is not None}


def _wda(user, arguments: dict):
    from connect_labs.workflow.data_access import WorkflowDataAccess

    from ..connect_token import require_connect_token

    scope = _scope(arguments)
    if len(scope) != 1:
        raise MCPToolError("INVALID_SCHEMA", "Provide exactly one of opportunity_id / program_id.")
    return WorkflowDataAccess(access_token=require_connect_token(user), **scope)


def _definition(user, arguments: dict):
    wda = _wda(user, arguments)
    try:
        definition = wda.get_definition(arguments.get("workflow_id"))
    finally:
        if hasattr(wda, "close"):
            wda.close()
    if definition is None:
        raise MCPToolError("NOT_FOUND", f"No workflow with id {arguments.get('workflow_id')}")
    return definition


def refusal(user, tool: str, arguments: dict) -> str | None:
    """Why this edit may not be made through canopy, or None. Checked at proposal and again at apply."""
    if tool not in EDIT_TOOLS:
        return (
            f"{tool} is not an edit canopy can propose. Proposable: {', '.join(sorted(EDIT_TOOLS))}. "
            "Sharing, publishing, cloning and deleting are made in Labs by a person."
        )
    if tool == "workflow_update_definition":
        patch = arguments.get("patch") or {}
        refused = sorted(set(patch) & REFUSED_DEFINITION_KEYS)
        config = patch.get("config")
        if isinstance(config, dict):
            refused += [f"config.{k}" for k in sorted(set(config) & REFUSED_CONFIG_KEYS)]
        if refused:
            return (
                f"Through canopy a definition edit may not change {', '.join(refused)}: those decide where "
                "the workflow's data goes or who sees it. A person changes them in Labs."
            )
    if tool in RENDER_TOOLS:
        from connect_labs.workflow.sandbox import should_sandbox

        if not should_sandbox(_definition(user, arguments)):
            return (
                "This workflow's render code runs outside the page sandbox, where it could send what it "
                "shows to another site, so canopy may not change it. Move the workflow into the sandbox "
                "(config.sandbox: true, set by a person in Labs) first."
            )
    return None


def _unified(before: str, after: str, label: str) -> dict:
    lines = list(
        difflib.unified_diff(
            (before or "").splitlines(),
            (after or "").splitlines(),
            f"{label} (now)",
            f"{label} (proposed)",
            lineterm="",
        )
    )
    cut = len(lines) > MAX_DIFF_LINES
    return {"label": label, "diff": "\n".join(lines[:MAX_DIFF_LINES]), "truncated": cut}


def _json(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=str)


def _render_preview(user, tool: str, arguments: dict) -> dict:
    from connect_labs.workflow.sandbox_scan import scan_render_code

    wda = _wda(user, arguments)
    try:
        current = wda.get_render_code(arguments.get("workflow_id"))
    finally:
        if hasattr(wda, "close"):
            wda.close()
    if current is None:
        raise MCPToolError("NOT_FOUND", f"No render_code for workflow {arguments.get('workflow_id')}.")
    before = current.component_code or ""
    if tool == "workflow_update_render_code":
        after = arguments.get("component_code") or ""
    else:
        search = arguments.get("search") or ""
        count = before.count(search) if search else 0
        if count != 1:
            raise MCPToolError(
                "INVALID_SCHEMA",
                f"`search` must match the current render code exactly once; it matches {count} times.",
            )
        after = before.replace(search, arguments.get("replace") or "", 1)
    findings = [f.as_dict() for f in scan_render_code(after)]
    return {
        "summary": f"Change the render code of workflow {arguments.get('workflow_id')}",
        "changes": [_unified(before, after, "render code")],
        "sandbox_findings": findings,
    }


def _definition_preview(user, arguments: dict) -> dict:
    data = _definition(user, arguments).data or {}
    patch = arguments.get("patch") or {}
    changes = []
    for key in sorted(patch):
        before = data.get(key)
        after = patch[key]
        if key == "config" and isinstance(after, dict):
            after = {**(before if isinstance(before, dict) else {}), **after}  # config shallow-merges
        changes.append(_unified(_json(before), _json(after), key))
    return {
        "summary": f"Change {', '.join(sorted(patch)) or 'nothing'} on workflow {arguments.get('workflow_id')}",
        "changes": changes,
    }


def preview(user, tool: str, arguments: dict) -> dict:
    """What the card shows: a summary and, per thing changed, a unified diff."""
    if tool in RENDER_TOOLS:
        return _render_preview(user, tool, arguments)
    if tool == "workflow_update_definition":
        return _definition_preview(user, arguments)
    shown = {k: v for k, v in arguments.items() if k not in ("expected_version",)}
    return {
        "summary": tool.replace("_", " "),
        "changes": [_unified("", _json(shown), tool)],
    }


def _delegation() -> dict:
    from ..server import _delegation_audit

    found = _delegation_audit()
    return {"client_id": found.get("client_id", ""), "actor": found.get("actor", "")}


_PROPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "tool": {
            "type": "string",
            "enum": sorted(EDIT_TOOLS),
            "description": "The edit tool to run once the person confirms.",
        },
        "arguments": {
            "type": "object",
            "description": "Exactly the arguments that tool takes (see its own schema), including expected_version.",
        },
    },
    "required": ["tool", "arguments"],
    "additionalProperties": False,
}


def _propose_text(result: dict) -> str:
    return (
        f"{result.get('summary', 'Edit')} -- proposed, NOT saved. The person sees the change in the chat and "
        "saves it with Confirm. Do not call the edit again; wait for them."
    )


@register(
    name="workflow_edit_propose",
    description=(
        "Propose a change to a workflow, its pipelines or its semantic registry: name the edit tool "
        f"({', '.join(sorted(EDIT_TOOLS))}) and its exact arguments. Labs checks it and shows the person a "
        "confirm card in the chat with the diff; it is saved only when THEY click Confirm, and then straight "
        "to the workflow. Read the current version first (workflow_get, pipeline_get), propose one coherent "
        "change at a time, and say in your reply what it does. Refused: anything that would send data "
        "outside the workflow (sharing, publishing, cloning, the workflow's actions or agent settings), and "
        "render code on a workflow that is not sandboxed."
    ),
    input_schema=_PROPOSE_SCHEMA,
    meta=tool_meta(WORKFLOW_EDIT_CONFIRM_URI, "model", "app"),
    text=_propose_text,
)
def workflow_edit_propose(user, tool: str, arguments: dict):
    from ..models import StagedEdit
    from ..server import policy_denial, restricted_call

    if not isinstance(arguments, dict):
        raise MCPToolError("INVALID_SCHEMA", "`arguments` must be an object.")
    if get_tool(tool) is None:
        raise MCPToolError("INVALID_SCHEMA", f"No tool named {tool!r}.")
    reason = refusal(user, tool, arguments)
    if reason:
        raise MCPToolError("PERMISSION_DENIED", reason)
    denied = _policy(user, tool, arguments, policy_denial, restricted_call)
    if denied:
        raise MCPToolError("PERMISSION_DENIED", denied)
    shown = preview(user, tool, arguments)
    edit = StagedEdit.objects.create(
        id=uuid.uuid4(),
        user=user,
        tool_name=tool,
        arguments=arguments,
        digest=digest(user.pk, tool, arguments),
        preview=shown,
        **_delegation(),
    )
    return {"change_id": str(edit.id), "state": edit.state, "tool": tool, "summary": shown["summary"]}


def _policy(user, tool: str, arguments: dict, policy_denial, restricted_call) -> str | None:
    """The edit tool's own policy checks, as if it were called directly."""
    from fastmcp.server.dependencies import get_access_token

    try:
        access = get_access_token()
    except Exception:  # noqa: BLE001 -- no MCP request context (direct calls, tests)
        access = None
    claims = getattr(access, "claims", None) or {}
    restricted = restricted_call(access) if access is not None else False
    return policy_denial(tool, arguments, user, restricted, delegated=claims.get("auth_method") == "delegated")


def _find(user, change_id: str | None = None, tool: str | None = None, arguments: dict | None = None):
    from ..models import StagedEdit

    found = StagedEdit.objects.filter(user=user)
    if change_id:
        try:
            found = found.filter(id=uuid.UUID(str(change_id)))
        except ValueError as e:
            raise MCPToolError("INVALID_SCHEMA", "`change_id` is not a proposal id.") from e
    else:
        found = found.filter(digest=digest(user.pk, tool or "", arguments or {}))
    edit = found.first()
    if edit is None:
        raise MCPToolError("NOT_FOUND", "No such proposed edit for you. The agent may need to propose it again.")
    return edit


def _card(edit, *, with_token: bool) -> dict:
    out = {
        "change_id": str(edit.id),
        "state": edit.state,
        "tool": edit.tool_name,
        **(edit.preview or {}),
        "proposed_at": edit.created_at.isoformat(),
        "decided_at": edit.decided_at.isoformat() if edit.decided_at else None,
        **({"error": edit.error} if edit.error else {}),
        **({"result": edit.result} if edit.result else {}),
    }
    if with_token and edit.state == edit.PENDING:
        out["confirm"] = signing.dumps(
            {"id": str(edit.id), "u": edit.user_id, "n": secrets.token_urlsafe(9)}, salt=_CONFIRM_SALT, compress=True
        )
        out["confirm_expires_in"] = CONFIRM_MAX_AGE_SECONDS
    return out


@register(
    name="workflow_edit_review_view",
    description=(
        "For the edit confirm card only (MCP Apps, app-only): a proposed edit as the person LOOKING AT the "
        "card -- its summary, the diff and that person's own single-use `confirm`. Found by `change_id`, or "
        "by the proposal's own `tool` and `arguments`. Hosts hide this tool from the agent."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "change_id": {"type": "string"},
            "tool": {"type": "string"},
            "arguments": {"type": "object"},
        },
        "additionalProperties": False,
    },
    meta=tool_meta(WORKFLOW_EDIT_CONFIRM_URI, "app"),
)
def workflow_edit_review_view(user, change_id: str | None = None, tool: str | None = None, arguments=None):
    if not change_id and not tool:
        raise MCPToolError("INVALID_SCHEMA", "Give `change_id`, or the proposal's `tool` and `arguments`.")
    return _card(_find(user, change_id, tool, arguments), with_token=True)


def _spend(user, change_id: str, confirm: str, to_state: str):
    from ..models import StagedEdit

    try:
        signed = signing.loads(confirm or "", salt=_CONFIRM_SALT, max_age=CONFIRM_MAX_AGE_SECONDS)
    except signing.SignatureExpired as e:
        raise MCPToolError("CONFLICT", "This confirmation has expired. Reload the card and confirm again.") from e
    except signing.BadSignature as e:
        raise MCPToolError("PERMISSION_DENIED", "`confirm` is not a token from this card.") from e
    if signed.get("id") != str(change_id) or signed.get("u") != user.pk:
        raise MCPToolError("PERMISSION_DENIED", "`confirm` was issued for another edit or another person.")
    edit = _find(user, change_id)
    # Single use: only one call moves a pending edit on.
    moved = StagedEdit.objects.filter(pk=edit.pk, state=StagedEdit.PENDING).update(
        state=to_state, decided_at=timezone.now()
    )
    if not moved:
        raise MCPToolError("CONFLICT", f"This edit was already {edit.state}.")
    edit.refresh_from_db()
    return edit


_DECIDE_SCHEMA = {
    "type": "object",
    "properties": {"change_id": {"type": "string"}, "confirm": {"type": "string"}},
    "required": ["change_id", "confirm"],
    "additionalProperties": False,
}


@register(
    name="workflow_edit_apply",
    description=(
        "For the edit confirm card only (MCP Apps, app-only): the person's Confirm. Saves the proposed edit "
        "by running its tool as that person, with the `confirm` token workflow_edit_review_view issued them. "
        "Hosts hide this tool from the agent."
    ),
    input_schema=_DECIDE_SCHEMA,
    is_write=True,
    meta=tool_meta(WORKFLOW_EDIT_CONFIRM_URI, "app"),
)
def workflow_edit_apply(user, change_id: str, confirm: str):
    from ..models import StagedEdit
    from ..rate_limit import enforce_write_limit
    from ..server import _write_audit, policy_denial, restricted_call

    edit = _spend(user, change_id, confirm, StagedEdit.APPLIED)
    tool, arguments = edit.tool_name, dict(edit.arguments)

    def fail(message: str, code: str):
        StagedEdit.objects.filter(pk=edit.pk).update(state=StagedEdit.FAILED, error=message[:2000])
        _write_audit(user, tool, arguments, success=False, is_write=True, error_code=code, **_delegation())
        raise MCPToolError(code, message)

    # Held to the rules as they are now, not as they were at proposal.
    reason = refusal(user, tool, arguments) or _policy(user, tool, arguments, policy_denial, restricted_call)
    if reason:
        fail(reason, "PERMISSION_DENIED")
    enforce_write_limit(user)
    spec = get_tool(tool)
    try:
        result = spec.handler(user=user, **arguments)
    except MCPToolError as e:
        fail(e.message, e.code)
    result = result if isinstance(result, dict) else {"value": result}
    before, after = result.get("_version_before"), result.get("_version_after")
    public = {k: v for k, v in result.items() if not k.startswith("_")}
    StagedEdit.objects.filter(pk=edit.pk).update(result=public)
    _write_audit(
        user, tool, arguments, success=True, is_write=True, version_before=before, version_after=after, **_delegation()
    )
    return {
        "change_id": str(edit.id),
        "state": StagedEdit.APPLIED,
        "result": public,
        "_version_before": before,
        "_version_after": after,
    }


@register(
    name="workflow_edit_decline",
    description=(
        "For the edit confirm card only (MCP Apps, app-only): the person's Decline. Nothing is saved. "
        "Hosts hide this tool from the agent."
    ),
    input_schema=_DECIDE_SCHEMA,
    meta=tool_meta(WORKFLOW_EDIT_CONFIRM_URI, "app"),
)
def workflow_edit_decline(user, change_id: str, confirm: str):
    from ..models import StagedEdit

    edit = _spend(user, change_id, confirm, StagedEdit.DECLINED)
    return {"change_id": str(edit.id), "state": StagedEdit.DECLINED}
