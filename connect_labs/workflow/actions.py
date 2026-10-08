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
from dataclasses import dataclass, field
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
        #: Written for the person or agent that asked; safe to show them.
        self.public_message = message
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

#: What a coaching preview on synthetic data says happens instead of a conversation.
SYNTHETIC_NOTE = (
    "Synthetic data: no message is sent; the task gets a sample conversation. "
    "To send a real test conversation to yourself, enter your PersonalID username below."
)

#: What a preview calls the coach when it cannot name the bot without asking OCS.
UNNAMED_BOT = "the programme's coach"

#: Bot names seen on a real preview, so a synthetic one (which never asks OCS) can
#: name the same bot rather than show its id.
_BOT_NAME_CACHE_KEY = "workflow_action_bot_name:{}"
_BOT_NAME_TTL = 7 * 24 * 3600

#: The QA-redirect argument of ``start_ocs_outreach``: a staff member's own ConnectID
#: username, to receive a worker's conversation instead of the worker.
DELIVER_TO = "deliver_to"

#: The opt-in argument of ``start_ocs_outreach``: give each briefed worker's session a
#: picture of their own figures (``coach_image.py``) as ``coach_image_url`` and
#: ``coach_image_caption``.
INCLUDE_IMAGE = "include_image"


@dataclass(frozen=True)
class WorkerContext:
    """What one worker's execution has to work with."""

    execution: Any  # WorkflowActionExecution
    item: dict  # this worker's entry in the arguments: {"key", ...its own text}
    prior: dict  # what an earlier attempt recorded for this worker, if any
    opportunity_id: int
    username: str
    tasks: Any  # TaskDataAccess scoped to the worker's opportunity, as the person
    ocs: Any  # OCSDataAccess as the person, or None when the type needs none
    #: What to record for this worker. An executor writes into it as it goes, so a
    #: worker that fails part-way still records what was done (a task it made).
    record: dict = field(default_factory=dict)

    @property
    def arguments(self) -> dict:
        return self.execution.arguments


@dataclass(frozen=True)
class ActionType:
    """A kind of action the framework can run: what it takes and what it does.

    ``execute`` does ONE worker, writing what to record for them into
    ``ctx.record`` (``task_id``, ``session_id``, ...); raising marks that worker
    failed -- keeping what it had recorded -- and the rest carry on.
    Resolved from a workflow's declaration by ``type`` -- ``ACTION_TYPES[type]`` --
    in ``declared_actions`` (to describe it) and ``execute`` (to run it).
    """

    type: str
    default_label: str
    description: str
    parameters: dict
    execute: Any  # Callable[[WorkerContext], None]
    uses_ocs: bool = False


def _follow_up_task(ctx: WorkerContext):
    """The worker's follow-up task on this run -- the one an earlier attempt made,
    if it made one, so a retry never files a second."""
    args = ctx.arguments
    task = ctx.tasks.get_task(ctx.prior["task_id"]) if ctx.prior.get("task_id") else None
    if task is None:
        task = ctx.tasks.create_task(
            username=ctx.username,
            opportunity_id=ctx.opportunity_id,
            priority=args.get("priority") or "medium",
            title=ctx.item.get("title") or args.get("title") or "Follow-up",
            description=ctx.item.get("description")
            or args.get("description")
            or ctx.item.get("prompt")
            or args.get("prompt")
            or "",
            creator_name=ctx.execution.user.get_display_name(),
            workflow_run_id=ctx.execution.run_id,
        )
    ctx.record["task_id"] = task.id
    return task


def _execute_create_task(ctx: WorkerContext) -> None:
    _follow_up_task(ctx)


def _execute_ocs_outreach(ctx: WorkerContext) -> None:
    from connect_labs.tasks.ai_sessions import start_ai_session

    is_new = not ctx.prior.get("task_id")
    task = _follow_up_task(ctx)
    indicators = ctx.item.get("indicators")
    if is_new and indicators:
        # The coaching-progress DENOMINATOR (workflow/views.py `_coaching_indicators`):
        # written once, at creation, and never by the chatbot.
        task.data = {**(task.data or {}), "coaching_indicators": list(indicators)}
        ctx.tasks.save_task(task)
    deliver_to = ctx.arguments.get(DELIVER_TO)
    prompt_text = ctx.item.get("prompt") or ctx.arguments.get("prompt") or ""
    started = start_ai_session(
        ctx.execution.user,
        ctx.tasks,
        task,
        ocs=ctx.ocs,
        # A QA redirect (`deliver_to`): the task is still the worker's, but the
        # conversation goes to a staff member's own Connect app.
        identifier=deliver_to or ctx.username,
        on_behalf_of=ctx.username if deliver_to else None,
        experiment=ctx.arguments["bot"],
        prompt_text=prompt_text,
        start_new_session=True,
        coach_image=_image_for(ctx.arguments, prompt_text),
    )
    ctx.record["session_id"] = started.get("session_id")


def _image_for(arguments: dict, prompt: str | None) -> dict | None:
    """The picture to attach to one worker's conversation: ``{"url", "caption"}`` when
    the action asked for pictures and the worker's text is a Labs briefing, else None.
    Drawn from the briefing text itself, so it shows exactly the topics the coach is
    briefed on; a worker given their own prompt gets no picture."""
    from connect_labs.workflow import coach_image

    if not arguments.get(INCLUDE_IMAGE):
        return None
    return coach_image.attachment(prompt or "")


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
            execute=_execute_create_task,
        ),
        ActionType(
            type="start_ocs_outreach",
            default_label="Initiate AI coach",
            description=(
                "For each worker, a follow-up task attached to this run and an Open Chat Studio "
                "conversation with them, `prompt` being the bot's instructions (an item's own `prompt` "
                "wins -- use it to name that worker's own red indicators; its `indicators` lists the "
                "indicator keys the conversation covers, recorded on the task as the coaching-progress "
                "denominator). `bot` is an OCS bot id; without one, the preview lists the bots the "
                "person can use. On synthetic opportunities no message is sent: each task gets a "
                "sample coaching conversation. `deliver_to` (Dimagi staff only, one worker at a time) "
                "is a QA redirect: the conversation goes to that ConnectID username instead of the "
                "worker -- on a synthetic opportunity too, where it is then a real OCS conversation. "
                "`include_image` (default false) also gives each worker whose briefing Labs writes a "
                "picture of their own figures for those topics, linked from the session state."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "workers": _workers_schema(
                        {
                            "prompt": {"type": "string", "maxLength": 4000},
                            "title": {"type": "string", "maxLength": 200},
                            "indicators": {
                                "type": "array",
                                "maxItems": 50,
                                "items": {"type": "string", "minLength": 1, "maxLength": 100},
                                "description": "Indicator keys (e.g. 'SF_P1') this worker is coached on, worst first.",
                            },
                        }
                    ),
                    "prompt": {"type": "string", "maxLength": 4000},
                    "title": {"type": "string", "maxLength": 200},
                    "bot": {"type": "string"},
                    "priority": _PRIORITY,
                    INCLUDE_IMAGE: {
                        "type": "boolean",
                        "description": (
                            "Attach a picture of each briefed worker's own figures (bar per topic) to their "
                            "conversation. Only workers whose briefing Labs writes get one."
                        ),
                    },
                    DELIVER_TO: {
                        "type": "string",
                        "minLength": 1,
                        "maxLength": 150,
                        "description": (
                            "QA only, Dimagi staff only: a ConnectID username to deliver the conversation "
                            "to instead of the worker. One worker at a time."
                        ),
                    },
                },
                "required": ["workers"],
                "additionalProperties": False,
            },
            execute=_execute_ocs_outreach,
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


def declaration_problems(raw: Any) -> list[str]:
    """What is wrong with a ``config.actions`` value, for whoever is saving it.

    ``declared_actions`` quietly skips what it cannot run, so a page never breaks
    on a bad entry; this is the loud half, called where a definition is written
    (``workflow_update_definition``) so an author learns at save time rather than
    from a button that never appears.
    """
    import jsonschema

    if raw is None:
        return []
    if not isinstance(raw, list):
        return ["config.actions must be a list"]
    problems, seen = [], set()
    for n, entry in enumerate(raw):
        where = f"config.actions[{n}]"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        key, kind = entry.get("key"), entry.get("type")
        if not isinstance(key, str) or not key.replace("_", "").replace("-", "").isalnum():
            problems.append(f"{where}.key must be a slug (letters, digits, _ and -)")
        elif key in seen:
            problems.append(f"{where}.key {key!r} is used twice")
        seen.add(key)
        if kind not in ACTION_TYPES:
            problems.append(f"{where}.type {kind!r} is not an action type; known: {sorted(ACTION_TYPES)}")
            continue
        defaults = entry.get("defaults", {})
        if not isinstance(defaults, dict):
            problems.append(f"{where}.defaults must be an object")
            continue
        # Defaults are arguments minus the workers: check them against the schema.
        schema = dict(ACTION_TYPES[kind].parameters)
        schema = {**schema, "required": [r for r in schema.get("required", []) if r != "workers"]}
        try:
            jsonschema.validate(defaults, schema)
        except jsonschema.ValidationError as e:
            problems.append(f"{where}.defaults: {e.message}")
        if "workers" in defaults:
            problems.append(f"{where}.defaults cannot name workers; the button or caller does")
        if DELIVER_TO in defaults:
            problems.append(f"{where}.defaults cannot set {DELIVER_TO}; it is a QA choice made per run")
    return problems


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


def resolve_arguments(action: dict, arguments: Any, roster: dict[str, dict], *, user=None, briefing=None) -> dict:
    """The action's defaults under the caller's arguments, validated against the
    type's schema and the run's roster, in canonical form. ``user`` is the person
    the action runs for; ``deliver_to`` is refused unless they are Dimagi staff.
    ``briefing`` (see ``briefing_source``) lets a coaching action brief each worker
    from the run's grading."""
    return _resolve(action, arguments, roster, user=user, briefing=briefing)[0]


def _resolve(action: dict, arguments: Any, roster: dict[str, dict], *, user=None, briefing=None) -> tuple[dict, list]:
    """``resolve_arguments``, plus the workers it left out: ``[{key, name, reason}]``."""
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
    skipped: list[dict] = []
    if action["type"] == "start_ocs_outreach":
        skipped = _brief_workers(merged, roster, briefing)
        missing = sorted(i["key"] for i in merged["workers"] if not (i.get("prompt") or merged.get("prompt")))
        if missing:
            raise ActionError("invalid", f"no prompt for {missing[:10]}: give `prompt`, or one on each item")
        if merged.get(DELIVER_TO):
            _check_deliver_to(user, merged)
        elif _all_synthetic(merged):
            # A synthetic opportunity never reaches OCS: each task gets a sample
            # coaching conversation instead (tasks/ai_sessions.py). Decided HERE so a
            # preview and its commit resolve to the same arguments.
            from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT

            merged["bot"] = SYNTHETIC_BOT
    return merged, skipped


#: The longest text one worker's conversation may open with (the item ``prompt`` schema).
_PROMPT_MAX = 4000


def _brief_workers(merged: dict, roster: dict[str, dict], briefing) -> list[dict]:
    """Give each worker with no text of their own a briefing from the run's grading
    (``coach_briefing.py``), the action's own ``prompt`` appended as the programme
    team's note. Workers with nothing red or yellow to raise are taken out of the
    run and returned, with the reason, for the preview to show.

    Does nothing when every worker already has a prompt (so a commit of a preview's
    own arguments never re-grades), or when the workflow is not an indicator report.
    """
    from connect_labs.workflow import coach_briefing

    if briefing is None or all(i.get("prompt") for i in merged["workers"]):
        return []
    source = briefing()
    if source is None:
        return []
    graded, programme = source
    by_key = {f.get("key"): f for f in graded.get("byFLW") or []}
    note = merged.get("prompt")
    kept, skipped = [], []
    for item in merged["workers"]:
        if item.get("prompt"):
            kept.append(item)
            continue
        who = roster[item["key"]]
        row = by_key.get(item["key"])
        topics = coach_briefing.coachable_topics(graded, row.get("ind") or {}) if row else []
        if not topics:
            reason = coach_briefing.SKIP_NOTHING_OFF_TARGET if row else coach_briefing.SKIP_NOT_GRADED
            skipped.append({"key": item["key"], "name": who["name"], "reason": reason})
            continue
        text, used = coach_briefing.fit_briefing(
            programme=programme,
            worker=(row.get("name") or who["name"] or who["username"]),
            topics=topics,
            note=note,
            limit=_PROMPT_MAX,
        )
        item["prompt"] = text
        item.setdefault("indicators", [t["key"] for t in used])
        kept.append(item)
    merged["workers"] = kept
    return skipped


def briefing_source(user, wda, run, definition, *, opportunity_id=None, program_id=None, restricted=False):
    """A lazy reader of the run's grading for ``_brief_workers``: ``(graded, programme
    name)``, or None when the workflow is not an indicator report. Read only when a
    worker needs a briefing; the scope defaults to the run's own."""
    from connect_labs.workflow import coach_briefing, run_grading

    if opportunity_id is None and program_id is None:
        opportunity_id = getattr(run, "opportunity_id", None) or None
        program_id = None if opportunity_id else getattr(run, "program_id", None)

    def load():
        if not run_grading.is_semantic_report(definition):
            return None
        try:
            graded = run_grading.graded_for_run(
                user, wda, run, opportunity_id=opportunity_id, program_id=program_id, restricted=restricted
            )
        except run_grading.NotGraded:
            return None
        except run_grading.GradingUnavailable as e:
            raise ActionError(
                "grading_unavailable",
                f"Could not read this run's indicators to brief the coach: {e.message}. Try again shortly.",
            ) from e
        return graded, coach_briefing.programme_name(graded, definition)

    return load


def _check_deliver_to(user, arguments: dict) -> None:
    """A QA redirect is a Dimagi-staff tool, for one worker at a time, to a real bot."""
    from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT
    from connect_labs.utils.dimagi_user import is_dimagi_user

    if user is None or not is_dimagi_user(user):
        raise ActionError(
            "forbidden", f"`{DELIVER_TO}` (a QA redirect of the conversation) is only available to Dimagi staff."
        )
    if len(arguments["workers"]) > 1:
        # One QA recipient is one OCS participant per bot: several workers' conversations
        # would land in, and overwrite, the same one.
        raise ActionError(
            "invalid",
            f"`{DELIVER_TO}` supports one worker at a time: several workers redirected to the same "
            "person would share one Open Chat Studio participant. Run it once per worker.",
        )
    if arguments.get("bot") == SYNTHETIC_BOT:
        # The sample conversation is not a bot anyone can receive: ask for a real one.
        arguments.pop("bot")


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


def preview(user, *, wda, run, definition, key: str, arguments: Any, request=None, briefing=None) -> dict:
    """What running ``key`` with ``arguments`` would do, and the token to confirm it.

    ``needs`` names what must be settled first — ``bot`` (choose one of
    ``bot_choices``) or ``connect_ocs`` (the person must connect Open Chat Studio at
    ``connect_url``) — and while anything is needed there is no ``confirm``.
    """
    from connect_labs.tasks.ai_sessions import SYNTHETIC_BOT
    from connect_labs.workflow import coach_briefing

    action = find_action(definition, key)
    roster = run_roster(wda, run, definition)
    if briefing is None:
        briefing = briefing_source(user, wda, run, definition)
    args, skipped = _resolve(action, arguments, roster, user=user, briefing=briefing)

    needs: list[str] = []
    out: dict[str, Any] = {}
    if ACTION_TYPES[action["type"]].uses_ocs:
        if args.get("bot") == SYNTHETIC_BOT:
            # Synthetic data never reaches OCS. Name the bot the workflow DECLARES --
            # the one a real run would use -- never the sample stand-in, and say plainly
            # what happens instead.
            out["synthetic"] = True
            out["synthetic_note"] = SYNTHETIC_NOTE
            declared = (arguments.get("bot") if isinstance(arguments, dict) else None) or action["defaults"].get("bot")
            # A synthetic preview never asks OCS anything: the name is the one a real
            # preview last saw for this bot, else a plain description -- never the id.
            if declared and declared != SYNTHETIC_BOT:
                out["bot"] = {"id": declared, "name": _cached_bot_name(declared) or UNNAMED_BOT}
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
            if coach_briefing.is_briefing(row["prompt"]):
                # Shown to the person confirming in plain words; ``prompt`` stays the
                # exact text the bot receives.
                row["briefing"] = coach_briefing.briefing_summary(row["prompt"])
                if args.get("bot") != SYNTHETIC_BOT:
                    # What the worker actually receives first (the briefing itself goes
                    # into the session state, never to the worker -- tasks/ai_sessions.py).
                    row["opening"] = coach_briefing.opening_message(row["prompt"])
                image = _image_for(args, row["prompt"])
                if image is not None:
                    # Shown before confirming, so the person knows a picture goes too.
                    row["image"] = image
            if item.get("indicators"):
                row["indicators"] = item["indicators"]
            if args.get(DELIVER_TO):
                row["sending_to"] = f"sending to {args[DELIVER_TO]} — a test, on behalf of {who['name']}"
        row["title"] = item.get("title") or args.get("title")
        workers.append(row)

    n = len(workers)
    summary = f"{action['label']} for {n} worker{'s' if n != 1 else ''}"
    if skipped:
        summary += f" ({len(skipped)} left out: nothing to coach)"
    if not workers:
        # Every worker asked for had nothing red or yellow: there is nothing to run.
        needs.append("workers")
        summary = f"{action['label']}: nothing to coach -- no indicator is off target or on watch"
    if action["type"] == "start_ocs_outreach" and len(workers) == 1:
        from connect_labs.utils.dimagi_user import is_dimagi_user

        # Whether the page may offer "send to me instead": the QA redirect is for
        # Dimagi staff, one worker at a time (``_check_deliver_to``).
        out["qa_redirect"] = is_dimagi_user(user)
    if args.get(DELIVER_TO):
        # Said once, in the dialog's banner -- not repeated in the title.
        out[DELIVER_TO] = args[DELIVER_TO]
    result = {
        "action": key,
        "type": action["type"],
        "label": action["label"],
        "summary": summary,
        "workers": workers,
        "arguments": args,
        "needs": needs,
        **({"skipped": skipped} if skipped else {}),
        **out,
    }
    if not needs:
        result["confirm"] = signing.dumps(
            {"d": _digest(user.pk, run.id, key, args)}, salt=_CONFIRM_SALT, compress=True
        )
        result["confirm_expires_in"] = CONFIRM_MAX_AGE_SECONDS
    return result


def commit(
    user,
    *,
    wda,
    run,
    definition,
    key: str,
    arguments: Any,
    confirm: str,
    via: str,
    actor: str = "",
    request=None,
    briefing=None,
):
    """Record and queue the action a preview described. ``arguments`` must be what
    that preview returned as ``arguments`` (or resolve to it); ``confirm`` is its
    token, and is spent."""
    from django.db import transaction

    from connect_labs.workflow.models import WorkflowActionExecution
    from connect_labs.workflow.tasks import execute_workflow_action

    action = find_action(definition, key)
    if briefing is None:
        briefing = briefing_source(user, wda, run, definition)
    args = resolve_arguments(action, arguments, run_roster(wda, run, definition), user=user, briefing=briefing)
    if not args["workers"]:
        raise ActionError("nothing_to_do", "None of these workers has an indicator off target or on watch.")
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
        bots = [
            {"id": e.get("public_id") or str(e.get("id")), "name": e.get("name") or ""}
            for e in client.list_experiments()
        ]
    except Exception:  # noqa: BLE001 -- an unreachable OCS reads as "connect again"
        logger.warning("Could not list OCS bots for user %s", user.pk, exc_info=True)
        return None
    finally:
        client.close()
    _remember_bot_names(bots)
    return bots


def _remember_bot_names(bots: list[dict]) -> None:
    from django.core.cache import cache

    try:
        cache.set_many({_BOT_NAME_CACHE_KEY.format(b["id"]): b["name"] for b in bots if b["name"]}, _BOT_NAME_TTL)
    except Exception:  # noqa: BLE001 -- a name for display only
        logger.warning("Could not cache OCS bot names", exc_info=True)


def _cached_bot_name(bot_id: str) -> str | None:
    from django.core.cache import cache

    try:
        return cache.get(_BOT_NAME_CACHE_KEY.format(bot_id))
    except Exception:  # noqa: BLE001 -- a name for display only
        return None


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
    """One worker, by the action's TYPE (``ACTION_TYPES[type].execute``). Never raises."""
    from connect_labs.labs.synthetic.access import labs_only_scope_denied_reason
    from connect_labs.tasks.data_access import TaskDataAccess
    from connect_labs.workflow.agent_sharing import split_worker_key

    user = execution.user
    opportunity_id, username = split_worker_key(item["key"])
    denied = labs_only_scope_denied_reason(user, opportunity_id=opportunity_id)
    if denied:
        return {"status": "failed", "error": denied}

    tda = None
    record: dict = {}
    try:
        # Scoped to the WORKER's opportunity: a program report spans several, and a
        # task belongs to the one its worker delivers in.
        tda = TaskDataAccess(user=user, access_token=connect_token, opportunity_id=opportunity_id)
        ACTION_TYPES[execution.action_type].execute(
            WorkerContext(
                execution=execution,
                item=item,
                prior=prior,
                opportunity_id=opportunity_id,
                username=username,
                tasks=tda,
                ocs=ocs,
                record=record,
            )
        )
        return {**record, "status": "ok"}
    except Exception as e:  # noqa: BLE001 -- reported per worker; the rest carry on
        logger.warning("action %s: %s failed for %s", execution.pk, execution.action_type, item["key"], exc_info=True)
        return {**record, "status": "failed", "error": _plain_error(e)}
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
