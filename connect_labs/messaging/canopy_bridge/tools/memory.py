"""The memory tools: facts an agent records about the Connect Worker, and retracts.

A fact needs no approval (``asserted_by_kind=agent``, retractable by staff on the Connect
Worker page), but is never a judgement of the worker's performance or character, never holds
their phone number, and an agent retracts only what an agent recorded.
"""

from __future__ import annotations

import re

from django.utils import timezone

from connect_labs.messaging.models import Message
from connect_labs.workers import api as workers
from connect_labs.workers.models import FactKind, FactStatus, WorkerFact
from connect_labs.workers.profile import fact_dict

from .base import Ctx, ToolRefused, bounded_int, params, tool

#: A fact is never a judgement of the worker. These words mark the obvious ones, refused outright.
_JUDGEMENT = re.compile(
    r"\b(lazy|laziness|dishonest|liar|lying|lies|cheat(s|ed|er|ing)?|fraud(ulent|ster)?|untrustworthy|"
    r"incompetent|useless|stupid|dumb|unreliable|careless|rude|arrogant|bad worker|poor worker|"
    r"poor performer|low performer|underperform\w*|not trustworthy|can't be trusted|cannot be trusted|"
    r"slacker|troublemaker|difficult person|bad attitude)\b",
    re.IGNORECASE,
)
GLOBAL_KINDS = frozenset({FactKind.LANGUAGE})


@tool(
    "Record something learned that will help the next conversation (a "
    "preference, a constraint, a correction). Never a judgement of the worker's performance or "
    "character. Language facts hold across programs.",
    params(
        required=("kind", "statement"),
        kind={"enum": list(FactKind.values)},
        statement={"type": "string", "minLength": 1, "maxLength": 300},
        source_message={"type": "integer"},
        scope={"enum": ["program", "global"]},
    ),
    write=True,
)
def worker_fact_record(ctx: Ctx, kind="", statement="", source_message=None, scope=None) -> dict:
    if kind not in FactKind.values:
        raise ToolRefused("invalid", f"kind is one of: {', '.join(FactKind.values)}")
    statement = " ".join(str(statement or "").split())
    if not statement or len(statement) > 300:
        raise ToolRefused("invalid", "a fact is one statement of 1 to 300 characters")
    if _JUDGEMENT.search(statement):
        raise ToolRefused(
            "judgement",
            "Facts record what helps the next conversation (a preference, a constraint, a correction), never a "
            "judgement of the Connect Worker's performance or character. Not recorded.",
        )
    if ctx.scrub(statement) != statement:
        raise ToolRefused("invalid", "a fact never holds the Connect Worker's phone number")
    scope = scope or ("global" if kind in GLOBAL_KINDS else "program")
    if scope not in ("program", "global"):
        raise ToolRefused("invalid", "scope is program (default) or global")
    message_id = None
    if source_message not in (None, ""):
        message = Message.objects.filter(pk=bounded_int(source_message, 0, 0, 2**62), worker=ctx.worker).first()
        if message is None or message.thread.program_id not in ctx.programs:
            raise ToolRefused("not_found", "source_message is not one of this Connect Worker's messages here")
        message_id = message.pk
    fact = WorkerFact.objects.create(
        worker=ctx.worker,
        program_id=None if scope == "global" else ctx.thread.program_id,
        kind=kind,
        statement=statement,
        asserted_by_kind="agent",
        asserted_by=ctx.author_ref[:200],
        source_thread_id=ctx.thread.pk,
        source_message_id=message_id,
    )
    workers.record_event(
        ctx.worker,
        "fact_recorded",
        source="labs",
        dedupe_key=f"labs:-:fact_recorded:{fact.pk}",
        program_id=fact.program_id,
        ref={"fact_id": fact.pk, "thread_id": ctx.thread.pk},
        payload={"kind": kind, "by": ctx.author_ref},
    )
    return {"fact": fact_dict(fact)}


@tool(
    "Retract a fact an agent recorded that no longer holds.",
    params(
        required=("id", "why"),
        id={"type": "integer"},
        why={"type": "string", "minLength": 1, "maxLength": 300},
    ),
    write=True,
)
def worker_fact_retract(ctx: Ctx, id=None, why="") -> dict:  # noqa: A002 -- the tool's argument name
    why = " ".join(str(why or "").split())
    if not why:
        raise ToolRefused("invalid", "say why the fact no longer holds")
    fact = ctx.scope.facts(active_only=False).filter(pk=bounded_int(id, 0, 0, 2**62)).first()
    if fact is None:
        raise ToolRefused("not_found", "No such fact for this Connect Worker.")
    if fact.asserted_by_kind != "agent":
        raise ToolRefused(
            "staff_fact", "A fact a person recorded is retracted by a person, on the Connect Worker page."
        )
    if fact.status == FactStatus.ACTIVE:
        fact.status = FactStatus.RETRACTED
        fact.retracted_at = timezone.now()
        fact.retracted_by = ctx.author_ref[:200]
        fact.retraction_reason = why[:300]
        fact.save(update_fields=["status", "retracted_at", "retracted_by", "retraction_reason"])
    return {"fact": fact_dict(fact)}
