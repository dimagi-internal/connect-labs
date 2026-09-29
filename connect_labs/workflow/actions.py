"""Workflow actions: things a workflow lets you DO, defined once, run from anywhere.

A workflow declares its actions on its definition — per instance with
``workflow_update_definition``; a template may declare a default its instances
inherit on read, like every config key:

    "config": {
        "actions": [
            {
                "key": "initiate_ai_coach",          # the workflow's name for it
                "type": "start_ocs_outreach",        # which framework action it is
                "label": "Initiate AI coach",        # what its button says
                "defaults": {"bot": "<OCS bot id>", "prompt": "..."},
            }
        ]
    }

Every door runs the same code path, as the person it runs for, recorded the same way
(``WorkflowActionExecution``):

* a BUTTON on the report — render code calls ``actions.runAction(key, {workers})``;
  the runner shows the preview and the person confirms it;
* the labs MCP — ``workflow_run_action``, for a person's own agent, or for canopy
  acting as the visitor on a page that shares its run (``agent_sharing.py``).

**Preview, then commit.** Running an action is two calls. The first returns exactly
what would happen — which workers, which bot, which text — and a single-use
``confirm`` token bound to the person, the run, the action and those resolved
arguments. The second must carry that token. A caller therefore cannot act without
first producing the preview, and anything that changes between the two (a worker
added, a different prompt) invalidates it. An agent is told to show the preview to
the person and get their yes in between; the page does that with its own dialog.

**Execution is in the background** (``tasks.execute_workflow_action``), so neither
a page request nor an MCP call holds on to thirty OCS round trips. That works
because the person's Connect token (``UserConnectToken``) and OCS token
(``UserOCSToken``) are both stored server-side; nothing needs their browser.

Action TYPES are framework code (``ACTION_TYPES``), not render code: a type is a
server-side executor with an argument schema. A new one is a class here.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

from django.core import signing
from django.core.cache import cache

logger = logging.getLogger(__name__)

#: How long a preview's confirm token is good for. Long enough for a person to read
#: a list and answer; short enough that "yes" still refers to what they saw.
CONFIRM_MAX_AGE_SECONDS = 15 * 60
_CONFIRM_SALT = "connect_labs.workflow.actions.confirm"

#: The widest selection one action run may name. A preview is confirmed as one
#: decision, and a person cannot meaningfully review a longer list.
MAX_WORKERS = 200


class ActionError(ValueError):
    """An action that cannot run as asked. ``code`` is stable; the message is for a person."""

    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------


def _workers_schema(item_properties: dict) -> dict:
    return {
        "type": "array",
        "minItems": 1,
        "maxItems": MAX_WORKERS,
        "description": "Workers by key ('<opportunity_id>::<username>'); an item may carry its own text.",
        "items": {
            "type": "object",
            "properties": {"key": {"type": "string"}, **item_properties},
            "required": ["key"],
            "additionalProperties": False,
        },
    }


_PRIORITY = {"type": "string", "enum": ["low", "medium", "high"]}


@dataclass(frozen=True)
class ActionType:
    type: str
    default_label: str
    description: str
    parameters: dict
    uses_ocs: bool = False


ACTION_TYPES: dict[str, ActionType] = {
    t.type: t
    for t in (
        ActionType(
            type="create_task",
            default_label="Create follow-up task",
            description=(
                "One follow-up task per worker, attached to this run, created as the person the "
                "action runs for. `title`/`description` apply to every worker unless an item has its own."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "workers": _workers_schema(
                        {"title": {"type": "string", "maxLength": 200}, "description": {"type": "string"}}
                    ),
                    "title": {"type": "string", "maxLength": 200},
                    "description": {"type": "string"},
                    "priority": _PRIORITY,
                },
                "required": ["workers"],
                "additionalProperties": False,
            },
        ),
        ActionType(
            type="start_ocs_outreach",
            default_label="Initiate AI coach",
            description=(
                "For each worker, a follow-up task attached to this run and an Open Chat Studio "
                "conversation with them, `prompt` being the bot's instructions (an item's own `prompt` "
                "wins -- use it to name that worker's own red indicators). `bot` is an OCS bot id; "
                "without one, the preview lists the bots the person can use. On synthetic "
                "opportunities no message is sent: each task gets a sample coaching conversation."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "workers": _workers_schema(
                        {
                            "prompt": {"type": "string", "maxLength": 4000},
                            "title": {"type": "string", "maxLength": 200},
                        }
                    ),
                    "prompt": {"type": "string", "maxLength": 4000},
                    "title": {"type": "string", "maxLength": 200},
                    "bot": {"type": "string"},
                    "priority": _PRIORITY,
                },
                "required": ["workers"],
                "additionalProperties": False,
            },
            uses_ocs=True,
        ),
    )
}


# ---------------------------------------------------------------------------
# What a workflow declares
# ---------------------------------------------------------------------------


def declared_actions(definition_data: dict | None, template_type: str | None = None) -> list[dict]:
    """The actions a workflow offers, resolved: each with its type's description and
    argument schema. Entries with no known ``type``, or a duplicate ``key``, are not
    offered — a catalog entry that documents a render-code button is not an action
    this framework can run."""
    from connect_labs.workflow.templates import with_inherited_config_flags

    data = with_inherited_config_flags(definition_data or {}, template_type)
    raw = (data.get("config") or {}).get("actions")
    out: list[dict] = []
    seen: set[str] = set()
    for entry in raw if isinstance(raw, list) else []:
        if not isinstance(entry, dict):
            continue
        key, kind = entry.get("key"), entry.get("type")
        if not isinstance(key, str) or not key or key in seen or kind not in ACTION_TYPES:
            continue
        seen.add(key)
        action_type = ACTION_TYPES[kind]
        defaults = entry.get("defaults") if isinstance(entry.get("defaults"), dict) else {}
        out.append(
            {
                "key": key,
                "type": kind,
                "label": entry.get("label") or action_type.default_label,
                "description": entry.get("description") or action_type.description,
                "defaults": copy.deepcopy(defaults),
                "parameters": action_type.parameters,
            }
        )
    return out


def definition_actions(definition) -> list[dict]:
    return declared_actions(getattr(definition, "data", None) or {}, getattr(definition, "template_type", None))


def find_action(definition, key: str) -> dict:
    for action in definition_actions(definition):
        if action["key"] == key:
            return action
    offered = [a["key"] for a in definition_actions(definition)]
    raise ActionError("not_offered", f"This workflow has no action {key!r}. It offers: {offered or 'none'}.")


# ---------------------------------------------------------------------------
# Resolving arguments
# ---------------------------------------------------------------------------


def run_roster(wda, run, definition) -> dict[str, dict]:
    """Every worker this run can act on, by worker key: the workers of each
    opportunity the workflow spans, read as the caller."""
    from connect_labs.workflow.agent_sharing import worker_key

    opp_ids = list(definition.opportunity_ids or []) or ([run.opportunity_id] if run.opportunity_id else [])
    roster: dict[str, dict] = {}
    for oid in opp_ids:
        try:
            workers = wda.get_workers(oid)
        except Exception:  # noqa: BLE001 -- an opportunity this person cannot enumerate
            logger.warning("roster: could not read workers of opp %s", oid, exc_info=True)
            continue
        for w in workers:
            if w.get("username"):
                roster[worker_key(oid, w["username"])] = {
                    "username": w["username"],
                    "name": w.get("name") or w["username"],
                    "opportunity_id": oid,
                }
    return roster


def resolve_arguments(action: dict, arguments: Any, roster: dict[str, dict]) -> dict:
    """The action's defaults under the caller's arguments, validated against the
    type's schema and the run's roster, in canonical form."""
    import jsonschema

    from connect_labs.workflow.agent_sharing import split_worker_key

    if not isinstance(arguments, dict):
        raise ActionError("invalid", "arguments must be an object")
    merged = {**copy.deepcopy(action["defaults"]), **copy.deepcopy(arguments)}
    try:
        jsonschema.validate(merged, action["parameters"])
    except jsonschema.ValidationError as e:
        where = "/".join(str(p) for p in e.absolute_path) or "arguments"
        raise ActionError("invalid", f"{where}: {e.message}") from e

    seen: set[str] = set()
    for item in merged["workers"]:
        try:
            split_worker_key(item["key"])
        except ValueError as e:
            raise ActionError("invalid", str(e)) from e
        if item["key"] in seen:
            raise ActionError("invalid", f"worker {item['key']!r} is named twice")
        seen.add(item["key"])
    unknown = sorted(k for k in seen if k not in roster)
    if unknown:
        raise ActionError("invalid", f"not workers on this run: {unknown[:10]}")

    merged.setdefault("priority", "medium")
    merged.setdefault("title", action["label"])
    if action["type"] == "start_ocs_outreach":
        missing = sorted(i["key"] for i in merged["workers"] if not (i.get("prompt") or merged.get("prompt")))
        if missing:
            raise ActionError("invalid", f"no prompt for {missing[:10]}: give `prompt`, or one on each item")
        if _all_synthetic(merged):
            # A synthetic opportunity never reaches OCS: each task gets a sample
            # coaching conversation instead (tasks/ai_sessions.py). Decided HERE so a
            # preview and its commit resolve to the same arguments.
            from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT

            merged["bot"] = SYNTHETIC_BOT
    return merged


def _all_synthetic(arguments: dict) -> bool:
    from connect_labs.labs.synthetic.registry import get_synthetic_opp
    from connect_labs.workflow.agent_sharing import split_worker_key

    opps = {split_worker_key(i["key"])[0] for i in arguments.get("workers") or []}
    return bool(opps) and all(get_synthetic_opp(o) is not None for o in opps)


def _digest(user_id: int, run_id: int, key: str, arguments: dict) -> str:
    body = json.dumps(
        {"u": user_id, "r": run_id, "k": key, "a": arguments}, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(body.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Preview and commit
# ---------------------------------------------------------------------------


def preview(user, *, wda, run, definition, key: str, arguments: Any, request=None) -> dict:
    """What running ``key`` with ``arguments`` would do, and the token to confirm it.

    ``needs`` names what must be settled first — ``bot`` (choose one of
    ``bot_choices``) or ``connect_ocs`` (the person must connect Open Chat Studio at
    ``connect_url``) — and while anything is needed there is no ``confirm``.
    """
    from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT

    action = find_action(definition, key)
    roster = run_roster(wda, run, definition)
    args = resolve_arguments(action, arguments, roster)

    needs: list[str] = []
    out: dict[str, Any] = {}
    if ACTION_TYPES[action["type"]].uses_ocs:
        if args.get("bot") == SYNTHETIC_BOT:
            out["synthetic"] = True
            out["bot"] = {"id": SYNTHETIC_BOT, "name": "Sample coaching conversation (synthetic data)"}
        else:
            bots = _ocs_bots(user, request)
            if bots is None:
                needs.append("connect_ocs")
                out["connect_url"] = "/labs/ocs/initiate/"
            else:
                chosen = next((b for b in bots if b["id"] == args.get("bot")), None)
                if chosen is None:
                    if args.get("bot"):
                        out["unknown_bot"] = args.pop("bot")
                    needs.append("bot")
                    out["bot_choices"] = bots
                else:
                    out["bot"] = chosen

    workers = []
    for item in args["workers"]:
        who = roster[item["key"]]
        row = {"key": item["key"], "name": who["name"], "opportunity_id": who["opportunity_id"]}
        if action["type"] == "start_ocs_outreach":
            row["prompt"] = item.get("prompt") or args.get("prompt")
        row["title"] = item.get("title") or args.get("title")
        workers.append(row)

    n = len(workers)
    result = {
        "action": key,
        "type": action["type"],
        "label": action["label"],
        "summary": f"{action['label']} for {n} worker{'s' if n != 1 else ''}",
        "workers": workers,
        "arguments": args,
        "needs": needs,
        **out,
    }
    if not needs:
        result["confirm"] = signing.dumps(
            {"d": _digest(user.pk, run.id, key, args)}, salt=_CONFIRM_SALT, compress=True
        )
        result["confirm_expires_in"] = CONFIRM_MAX_AGE_SECONDS
    return result


def commit(
    user, *, wda, run, definition, key: str, arguments: Any, confirm: str, via: str, actor: str = "", request=None
):
    """Record and queue the action a preview described. ``arguments`` must be what
    that preview returned as ``arguments`` (or resolve to it); ``confirm`` is its
    token, and is spent."""
    from django.db import transaction

    from connect_labs.workflow.models import WorkflowActionExecution
    from connect_labs.workflow.tasks import execute_workflow_action

    action = find_action(definition, key)
    args = resolve_arguments(action, arguments, run_roster(wda, run, definition))
    try:
        signed = signing.loads(confirm or "", salt=_CONFIRM_SALT, max_age=CONFIRM_MAX_AGE_SECONDS)
    except signing.SignatureExpired as e:
        raise ActionError("confirm_expired", "That preview has expired. Preview again and confirm the new one.") from e
    except signing.BadSignature as e:
        raise ActionError("confirm_invalid", "`confirm` is not a token from a preview of this action.") from e
    if signed.get("d") != _digest(user.pk, run.id, key, args):
        raise ActionError(
            "confirm_mismatch",
            "These arguments are not the ones that were previewed. Preview again and confirm what is shown.",
        )
    # Single use: a confirmed preview runs once, however many times it is sent.
    if not cache.add(f"wf-action-confirm:{signed['d']}", 1, CONFIRM_MAX_AGE_SECONDS):
        raise ActionError("confirm_used", "That preview was already confirmed and run.")

    execution = WorkflowActionExecution.objects.create(
        user=user,
        via=via,
        actor=(actor or "")[:100],
        definition_id=definition.id,
        run_id=run.id,
        opportunity_id=run.opportunity_id,
        program_id=getattr(run, "program_id", None),
        action_key=key,
        action_type=action["type"],
        arguments=args,
    )
    transaction.on_commit(lambda: execute_workflow_action.delay(execution.pk))
    return execution


def _ocs_bots(user, request) -> list[dict] | None:
    """The OCS bots this person can use, or None when they have not connected OCS."""
    from connect_labs.labs.integrations.ocs.api_client import OCSDataAccess

    client = OCSDataAccess(request, user=user) if request is not None else OCSDataAccess(user=user)
    try:
        if not client.check_token_valid():
            return None
        return [
            {"id": e.get("public_id") or str(e.get("id")), "name": e.get("name") or ""}
            for e in client.list_experiments()
        ]
    except Exception:  # noqa: BLE001 -- an unreachable OCS reads as "connect again"
        logger.warning("Could not list OCS bots for user %s", user.pk, exc_info=True)
        return None
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Execution (background)
# ---------------------------------------------------------------------------


def execute(execution_id: int) -> None:
    """Carry out a queued action, worker by worker, recording each as it goes.

    Safe to re-run (a redelivered task): a worker already done is skipped, and a
    worker whose task was made but whose conversation failed keeps its task.
    """
    from django.utils import timezone

    from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
    from connect_labs.workflow.models import WorkflowActionExecution

    Status = WorkflowActionExecution.Status
    execution = WorkflowActionExecution.objects.select_related("user").get(pk=execution_id)
    if execution.status in (Status.COMPLETED, Status.COMPLETED_WITH_ERRORS, Status.FAILED):
        return
    execution.status = Status.RUNNING
    execution.save(update_fields=["status"])

    try:
        connect_token = get_valid_access_token(execution.user)
    except ConnectTokenError as e:
        execution.status = Status.FAILED
        execution.error = f"No usable Connect login for {execution.user.username}: {e}"
        execution.finished_at = timezone.now()
        execution.save(update_fields=["status", "error", "finished_at"])
        return

    from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT

    ocs = None
    if ACTION_TYPES[execution.action_type].uses_ocs and execution.arguments.get("bot") != SYNTHETIC_BOT:
        from connect_labs.labs.integrations.ocs.api_client import OCSDataAccess

        ocs = OCSDataAccess(user=execution.user)
    try:
        for item in execution.arguments.get("workers") or []:
            prior = (execution.results or {}).get(item["key"]) or {}
            if prior.get("status") == "ok":
                continue
            result = _execute_item(execution, item, prior, connect_token, ocs)
            execution.results = {
                **(execution.results or {}),
                item["key"]: {**result, "at": timezone.now().isoformat()},
            }
            execution.save(update_fields=["results"])
    finally:
        if ocs is not None:
            ocs.close()

    failed = any((execution.results.get(k) or {}).get("status") != "ok" for k in execution.worker_keys())
    execution.status = Status.COMPLETED_WITH_ERRORS if failed else Status.COMPLETED
    execution.finished_at = timezone.now()
    execution.save(update_fields=["status", "finished_at"])


def _execute_item(execution, item: dict, prior: dict, connect_token: str, ocs) -> dict:
    """One worker: its task, and for outreach its conversation. Never raises."""
    from connect_labs.labs.synthetic.access import labs_only_scope_denied_reason
    from connect_labs.tasks.ai_sessions import start_ai_session
    from connect_labs.tasks.data_access import TaskDataAccess
    from connect_labs.workflow.agent_sharing import split_worker_key

    args = execution.arguments
    user = execution.user
    opportunity_id, username = split_worker_key(item["key"])
    denied = labs_only_scope_denied_reason(user, opportunity_id=opportunity_id)
    if denied:
        return {"status": "failed", "error": denied}

    out: dict = {}
    tda = None
    try:
        # Scoped to the WORKER's opportunity: a program report spans several, and a
        # task belongs to the one its worker delivers in.
        tda = TaskDataAccess(user=user, access_token=connect_token, opportunity_id=opportunity_id)
        prompt = item.get("prompt") or args.get("prompt") or ""
        task = tda.get_task(prior["task_id"]) if prior.get("task_id") else None
        if task is None:
            task = tda.create_task(
                username=username,
                opportunity_id=opportunity_id,
                priority=args.get("priority") or "medium",
                title=item.get("title") or args.get("title") or "Follow-up",
                description=item.get("description") or args.get("description") or prompt,
                creator_name=user.get_display_name(),
                workflow_run_id=execution.run_id,
            )
        out["task_id"] = task.id
        if execution.action_type == "start_ocs_outreach":
            started = start_ai_session(
                user,
                tda,
                task,
                ocs=ocs,
                identifier=username,
                experiment=args["bot"],
                prompt_text=prompt,
                start_new_session=True,
            )
            out["session_id"] = started.get("session_id")
        out["status"] = "ok"
        return out
    except Exception as e:  # noqa: BLE001 -- reported per worker; the rest carry on
        logger.warning("action %s: %s failed for %s", execution.pk, execution.action_type, item["key"], exc_info=True)
        return {**out, "status": "failed", "error": _plain_error(e)}
    finally:
        if tda is not None:
            tda.close()


def _plain_error(e: Exception) -> str:
    from connect_labs.labs.integrations.ocs.api_client import OCSAPIError

    if isinstance(e, OCSAPIError):
        return "Open Chat Studio refused the request"
    if getattr(e, "status_code", None) == 404:
        return "Not found, or no access to this opportunity"
    return type(e).__name__
