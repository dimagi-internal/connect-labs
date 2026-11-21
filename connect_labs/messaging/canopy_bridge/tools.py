"""The worker tools: what a canopy agent may ask labs about the ONE Connect Worker it is talking to.

Served by ``mcp.py`` to canopy's gateway in subject mode (S4): every call carries canopy's signed
word naming the worker (``Subject.sub``) and the conversation (``Subject.session``), and labs
resolves both to its own record before any tool runs. A tool never takes a worker, a thread or a
program as an argument: they come from the subject, so an agent cannot ask about anyone else.

Every tool holds to the visibility rules of the Worker Brief (``brief.py``):

* this thread's program only (another program's thread summaries only when the playbook's
  ``context.other_programs`` opts in; global facts cross programs);
* nothing restricted, no private staff note, no suspension reason, and never the phone number
  (also scrubbed from any text that quotes it);
* only the tools the thread's playbook lists in ``tools``.

Facts an agent records need no approval (``asserted_by_kind=agent``, retractable by staff on the
Connect Worker page), but are never a judgement of the worker's performance or character.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from connect_labs.messaging import playbooks, router, service
from connect_labs.messaging.canopy_bridge.brief import _Scrubber
from connect_labs.messaging.canopy_bridge.models import CommitmentStatus, PendingTurn, TurnStatus, WorkerCommitment
from connect_labs.messaging.models import AuthorKind, DeliveryStatus, Message, Thread, ThreadSummary
from connect_labs.workers import api as workers
from connect_labs.workers.models import (
    FactKind,
    FactStatus,
    OpportunitySync,
    Visibility,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
    WorkerSnapshot,
)
from connect_labs.workers.profile import fact_dict


class ToolRefused(Exception):
    """A call labs will not answer. ``code`` is stable; the message is for the agent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Ctx:
    """Who a call is about and where it comes from: resolved by labs from canopy's subject."""

    worker: object
    thread: Thread
    subject: object
    playbook: dict
    programs: frozenset = field(default_factory=frozenset)
    other_programs: bool = False

    @property
    def author_ref(self) -> str:
        return f"{self.subject.agent}:{self.thread.handler_ref.partition(':')[2]}".rstrip(":")

    @property
    def scrub(self) -> Callable[[str], str]:
        return _Scrubber(self.worker.phone)

    def opportunity_ids(self) -> list[int]:
        return list(
            WorkerMembership.objects.filter(worker=self.worker, program_id__in=list(self.programs)).values_list(
                "opportunity_id", flat=True
            )
        )


def _iso(value):
    return value.isoformat() if value else None


def _int(value, default, low, high) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _since(value):
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        day = parse_date(str(value))
        if day is None:
            raise ToolRefused("invalid", "since is an ISO 8601 date or time, e.g. 2026-11-01")
        parsed = dt.datetime.combine(day, dt.time.min)
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed, dt.UTC)


def _who(message: Message) -> str:
    if message.direction == "in":
        return "worker"
    return f"{message.author_kind}{':' + message.author_ref if message.author_ref else ''}"


def _visible_messages(thread):
    return (
        Message.objects.filter(thread=thread)
        .exclude(delivery_status=DeliveryStatus.NOTE)
        .exclude(visibility=Visibility.RESTRICTED)
    )


def _summaries():
    return ThreadSummary.objects.exclude(visibility=Visibility.RESTRICTED)


def _snapshots(ctx: Ctx, kind: str) -> list[dict]:
    rows = WorkerSnapshot.objects.filter(
        worker=ctx.worker, kind=kind, opportunity_id__in=ctx.opportunity_ids(), gone_at__isnull=True
    ).order_by("opportunity_id", "key")
    return [{k: v for k, v in (r.data or {}).items() if k not in ("username", "key")} for r in rows]


def _facet(ctx: Ctx, name: str) -> list[dict]:
    return [
        {"opportunity_id": s["opportunity_id"], "stage": s["stage"], name: (s["facets"] or {}).get(name)}
        for s in workers.states_for(ctx.worker, ctx.thread.program_id)
    ]


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def worker_profile(ctx: Ctx) -> dict:
    from connect_labs.messaging.facets import with_messaging

    data = workers.worker_brief_data(ctx.worker, ctx.thread.program_id)
    states = workers.states_for(ctx.worker, ctx.thread.program_id)
    for state in states:
        state["facets"] = with_messaging(state["facets"], ctx.worker, ctx.thread.program_id)
    return {
        "worker": data["worker"],
        "program_id": data["program_id"],
        "memberships": data["memberships"],
        "states": states,
        "notes": [{"body": ctx.scrub(n["body"]), "created_at": n["created_at"]} for n in data["notes"]],
        "coverage": data["coverage"],
    }


def worker_timeline(ctx: Ctx, since=None, kinds=None, limit=50) -> dict:
    kinds = [str(k) for k in (kinds or []) if k] or None
    events = workers.timeline(
        ctx.worker,
        program_id=ctx.thread.program_id,
        kinds=kinds,
        since=_since(since),
        limit=_int(limit, 50, 1, 200),
    )
    return {"events": events, "kinds": list(workers.EVENT_KINDS)}


def _thread_row(ctx: Ctx, thread: Thread) -> dict:
    summary = _summaries().filter(thread=thread).order_by("-created_at", "-id").first()
    return {
        "id": thread.pk,
        "program_id": thread.program_id,
        "this_program": thread.program_id in ctx.programs,
        "playbook": thread.playbook_key,
        "direction": "inbound" if thread.direction == "in" else "outbound",
        "state": thread.state,
        "handler": f"{thread.handler_kind}:{thread.handler_ref}".rstrip(":"),
        "opened_at": _iso(thread.opened_at),
        "closed_at": _iso(thread.closed_at),
        "summary": ctx.scrub(summary.text) if summary else None,
        "outcome": summary.outcome if summary else None,
        "current": thread.pk == ctx.thread.pk,
    }


def _threads(ctx: Ctx):
    qs = Thread.objects.filter(worker=ctx.worker)
    if not ctx.other_programs:
        qs = qs.filter(program_id__in=list(ctx.programs))
    return qs


def worker_threads(ctx: Ctx, limit=20) -> dict:
    rows = _threads(ctx).order_by("-opened_at", "-id")[: _int(limit, 20, 1, 50)]
    return {"threads": [_thread_row(ctx, t) for t in rows]}


def worker_thread(ctx: Ctx, id=None) -> dict:  # noqa: A002 -- the tool's argument name
    thread = _threads(ctx).filter(pk=_int(id, 0, 0, 2**62)).first()
    if thread is None:
        raise ToolRefused("not_found", "No such thread for this Connect Worker.")
    out = _thread_row(ctx, thread)
    if thread.program_id in ctx.programs:
        out["messages"] = [
            {"id": m.pk, "at": _iso(m.created_at), "from": _who(m), "text": ctx.scrub(m.text)}
            for m in _visible_messages(thread).order_by("created_at", "id")[:200]
        ]
    else:
        out["messages"] = None
        out["note"] = "Another program's thread: only its summary is shared."
    out["summaries"] = [
        {"at": _iso(s.created_at), "outcome": s.outcome, "text": ctx.scrub(s.text), "by": s.author_kind}
        for s in _summaries().filter(thread=thread).order_by("created_at", "id")
    ]
    return out


_TOKEN = re.compile(r"[\w]{2,}", re.UNICODE)


def worker_history_search(ctx: Ctx, query="", limit=10) -> dict:
    """Messages and summaries of this worker's that match ``query``, best first.

    Messages are encrypted at rest (``EncryptedTextField``), so Postgres full-text (and pgvector)
    cannot index them; the worker's own history is small, so it is searched here, decrypted, after
    the visibility rules have narrowed it."""
    words = {w.lower() for w in _TOKEN.findall(query or "")}
    if not words:
        raise ToolRefused("invalid", "query needs at least one word")
    hits = []
    messages = (
        Message.objects.filter(worker=ctx.worker, thread__program_id__in=list(ctx.programs))
        .exclude(delivery_status=DeliveryStatus.NOTE)
        .exclude(visibility=Visibility.RESTRICTED)
        .order_by("-created_at", "-id")[:2000]
    )
    for m in messages:
        found = words & {w.lower() for w in _TOKEN.findall(m.text or "")}
        if found:
            hits.append((len(found), m.created_at, "message", m.thread_id, m.pk, _who(m), m.text))
    summaries = _summaries().filter(thread__worker=ctx.worker)
    if not ctx.other_programs:
        summaries = summaries.filter(thread__program_id__in=list(ctx.programs))
    for s in summaries.order_by("-created_at", "-id")[:500]:
        found = words & {w.lower() for w in _TOKEN.findall(s.text or "")}
        if found:
            hits.append((len(found), s.created_at, "summary", s.thread_id, s.pk, s.author_kind, s.text))
    hits.sort(key=lambda h: (-h[0], -h[1].timestamp(), -h[4]))
    return {
        "query": query,
        "results": [
            {"kind": kind, "thread_id": tid, "id": pk, "at": _iso(at), "from": who, "text": ctx.scrub(text)[:500]}
            for _, at, kind, tid, pk, who, text in hits[: _int(limit, 10, 1, 25)]
        ],
    }


def worker_tasks(ctx: Ctx) -> dict:
    return {"tasks": _snapshots(ctx, "assigned_tasks"), "facets": _facet(ctx, "tasks")}


def worker_learn(ctx: Ctx) -> dict:
    names = {}
    for sync in OpportunitySync.objects.filter(opportunity_id__in=ctx.opportunity_ids()):
        names[sync.opportunity_id] = (sync.meta or {}).get("modules") or {}
    modules = _snapshots(ctx, "completed_module")
    for m in modules:
        m["module_name"] = names.get(m.get("opportunity_id"), {}).get(str(m.get("module")), "")
    memberships = WorkerMembership.objects.filter(worker=ctx.worker, program_id__in=list(ctx.programs))
    return {
        "memberships": [
            {
                "opportunity_id": m.opportunity_id,
                "learn_started": _iso(m.date_learn_started),
                "learn_completed": _iso(m.completed_learn_date),
            }
            for m in memberships.order_by("opportunity_id")
        ],
        "modules_completed": modules,
        "assessments": _snapshots(ctx, "assessment"),
        "facets": _facet(ctx, "learn"),
    }


def worker_visits(ctx: Ctx, limit=50) -> dict:
    events = WorkerEvent.objects.filter(
        worker=ctx.worker, opportunity_id__in=ctx.opportunity_ids(), kind__in=("visit", "visit_reviewed")
    ).exclude(visibility=Visibility.RESTRICTED)
    rows = events.order_by("-occurred_at", "-observed_at", "-id")[: _int(limit, 50, 1, 200)]
    return {
        "visits": [
            {"kind": e.kind, "at": _iso(e.at), "opportunity_id": e.opportunity_id, **(e.payload or {})} for e in rows
        ],
        "facets": _facet(ctx, "verification"),
        "note": "A thin view: status, review and flags, never the form's contents.",
    }


def worker_payments(ctx: Ctx) -> dict:
    works = _snapshots(ctx, "completed_works")
    by_status: dict[str, int] = {}
    for w in works:
        by_status[w.get("status") or "unknown"] = by_status.get(w.get("status") or "unknown", 0) + 1
    return {"payments": _snapshots(ctx, "payment"), "work_by_status": by_status, "facets": _facet(ctx, "payment")}


def worker_facts(ctx: Ctx, include_retracted=False) -> dict:
    qs = WorkerFact.objects.filter(worker=ctx.worker).filter(
        Q(program_id__isnull=True) | Q(program_id__in=list(ctx.programs))
    )
    if not include_retracted:
        qs = qs.filter(status=FactStatus.ACTIVE)
    out = []
    for fact in qs.order_by("-created_at", "-id")[:100]:
        row = fact_dict(fact)
        row["statement"] = ctx.scrub(row["statement"])
        out.append(row)
    return {"facts": out, "kinds": list(FactKind.values)}


# ---------------------------------------------------------------------------
# Writing memory
# ---------------------------------------------------------------------------

#: A fact is never a judgement of the worker. These words mark the obvious ones, refused outright.
_JUDGEMENT = re.compile(
    r"\b(lazy|laziness|dishonest|liar|lying|lies|cheat(s|ed|er|ing)?|fraud(ulent|ster)?|untrustworthy|"
    r"incompetent|useless|stupid|dumb|unreliable|careless|rude|arrogant|bad worker|poor worker|"
    r"poor performer|low performer|underperform\w*|not trustworthy|can't be trusted|cannot be trusted|"
    r"slacker|troublemaker|difficult person|bad attitude)\b",
    re.IGNORECASE,
)
GLOBAL_KINDS = frozenset({FactKind.LANGUAGE})


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
        message = Message.objects.filter(pk=_int(source_message, 0, 0, 2**62), worker=ctx.worker).first()
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


def worker_fact_retract(ctx: Ctx, id=None, why="") -> dict:  # noqa: A002 -- the tool's argument name
    why = " ".join(str(why or "").split())
    if not why:
        raise ToolRefused("invalid", "say why the fact no longer holds")
    fact = (
        WorkerFact.objects.filter(worker=ctx.worker, pk=_int(id, 0, 0, 2**62))
        .filter(Q(program_id__isnull=True) | Q(program_id__in=list(ctx.programs)))
        .first()
    )
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


# ---------------------------------------------------------------------------
# The thread
# ---------------------------------------------------------------------------


def _pending(ctx: Ctx) -> PendingTurn | None:
    """The turn this call is part of, while its answer has not gone out."""
    return PendingTurn.objects.filter(turn_id=ctx.subject.turn, thread=ctx.thread, status=TurnStatus.PENDING).first()


def _after_turn(ctx: Ctx, action: dict) -> bool:
    """Run ``action`` once this turn's answer is out (so the worker reads it first); False when no
    turn is waiting, and the caller acts now."""
    pending = _pending(ctx)
    if pending is None:
        return False
    PendingTurn.objects.filter(pk=pending.pk).update(after=[*(pending.after or []), action])
    return True


def _require_open(ctx: Ctx) -> None:
    ctx.thread.refresh_from_db()
    if not ctx.thread.is_open:
        raise ToolRefused("thread_closed", "This conversation is closed.")


def thread_record_outcome(ctx: Ctx, outcome="", summary="") -> dict:
    summary = str(summary or "").strip()
    if not summary:
        raise ToolRefused("invalid", "summary says what the conversation came to, for whoever picks it up next")
    row = service.record_summary(
        ctx.thread,
        ctx.scrub(summary),
        author_kind=AuthorKind.AGENT,
        author_ref=ctx.author_ref,
        outcome=str(outcome or "")[:64],
    )
    return {"summary_id": row.pk, "outcome": row.outcome}


def thread_handoff(ctx: Ctx, to="", note="") -> dict:
    _require_open(ctx)
    to = str(to or "").strip()
    note = " ".join(str(note or "").split())[:300]
    if not note:
        raise ToolRefused("invalid", "note says why, for the person or agent who picks it up")
    if to != "human":
        target = playbooks.get(ctx.thread.program_id, to)
        if target is None or not playbooks.is_enabled(target) or to == ctx.thread.playbook_key:
            allowed = [
                k
                for k, p in playbooks.all_for(ctx.thread.program_id).items()
                if playbooks.is_enabled(p) and k != ctx.thread.playbook_key
            ]
            raise ToolRefused("invalid", f"to is 'human' or one of this program's playbooks: {', '.join(allowed)}")
    action = {"action": "handoff", "to": to, "note": note, "by": f"agent:{ctx.subject.agent}"}
    if _after_turn(ctx, action):
        return {"handoff": to, "when": "after your answer is sent"}
    router.handoff_to(ctx.thread, to, note=note, by=action["by"])
    return {"handoff": to, "when": "now"}


def thread_close(ctx: Ctx, summary="", commitments=None, outcome="closed") -> dict:
    _require_open(ctx)
    summary = str(summary or "").strip()
    if not summary:
        raise ToolRefused("invalid", "summary says what the conversation came to, for whoever picks it up next")
    promised = [" ".join(str(c or "").split())[:300] for c in (commitments or []) if str(c or "").strip()]
    if len(promised) > 5:
        raise ToolRefused("invalid", "at most 5 commitments")
    service.record_summary(
        ctx.thread,
        ctx.scrub(summary),
        author_kind=AuthorKind.AGENT,
        author_ref=ctx.author_ref,
        outcome=str(outcome or "closed")[:64],
    )
    for text in promised:
        WorkerCommitment.objects.create(
            worker=ctx.worker,
            thread=ctx.thread,
            program_id=ctx.thread.program_id,
            text=ctx.scrub(text),
            by=ctx.author_ref[:200],
            status=CommitmentStatus.OPEN,
        )
    action = {"action": "close", "reason": f"closed by {ctx.subject.agent}", "by": f"agent:{ctx.subject.agent}"}
    if _after_turn(ctx, action):
        return {"closed": "after your answer is sent", "commitments": promised}
    ctx.thread.close(action["reason"], by=action["by"])
    return {"closed": "now", "commitments": promised}


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

_NONE = {"type": "object", "properties": {}, "additionalProperties": False}


@dataclass(frozen=True)
class WorkerTool:
    name: str
    description: str
    input_schema: dict
    handler: Callable
    is_write: bool = False


TOOLS: dict[str, WorkerTool] = {
    t.name: t
    for t in (
        WorkerTool(
            "worker_profile",
            "The Connect Worker you are talking to: identity, this program's memberships, "
            "stage and facets, staff notes (never private ones), and what labs' record covers.",
            _NONE,
            worker_profile,
        ),
        WorkerTool(
            "worker_timeline",
            "This Connect Worker's timeline in this program, newest first: invites, "
            "learning, visits, tasks, payments, stage changes, messages.",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "since": {"type": "string", "description": "ISO 8601 date or time"},
                    "kinds": {"type": "array", "items": {"type": "string"}},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                },
            },
            worker_timeline,
        ),
        WorkerTool(
            "worker_threads",
            "This Connect Worker's conversations with labs in this program (and others, "
            "summaries only, when the playbook allows), newest first, each with its summary.",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            },
            worker_threads,
        ),
        WorkerTool(
            "worker_thread",
            "One of this Connect Worker's conversations: its messages and summaries.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["id"],
                "properties": {"id": {"type": "integer"}},
            },
            worker_thread,
        ),
        WorkerTool(
            "worker_history_search",
            "Search this Connect Worker's past messages and conversation summaries.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["query"],
                "properties": {
                    "query": {"type": "string", "minLength": 1, "maxLength": 200},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                },
            },
            worker_history_search,
        ),
        WorkerTool(
            "worker_tasks",
            "Tasks assigned to this Connect Worker in this program (type, status, due date).",
            _NONE,
            worker_tasks,
        ),
        WorkerTool(
            "worker_learn", "This Connect Worker's learning: modules completed and assessments.", _NONE, worker_learn
        ),
        WorkerTool(
            "worker_visits",
            "This Connect Worker's recent visits, thin: status, review and flags.",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
            },
            worker_visits,
        ),
        WorkerTool(
            "worker_payments",
            "This Connect Worker's payments and work by status in this program.",
            _NONE,
            worker_payments,
        ),
        WorkerTool(
            "worker_facts",
            "What has been learned about this Connect Worker (preferences, constraints, "
            "corrections, cautions), with who recorded each.",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {"include_retracted": {"type": "boolean"}},
            },
            worker_facts,
        ),
        WorkerTool(
            "worker_fact_record",
            "Record something learned that will help the next conversation (a "
            "preference, a constraint, a correction). Never a judgement of the worker's performance or "
            "character. Language facts hold across programs.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["kind", "statement"],
                "properties": {
                    "kind": {"enum": list(FactKind.values)},
                    "statement": {"type": "string", "minLength": 1, "maxLength": 300},
                    "source_message": {"type": "integer"},
                    "scope": {"enum": ["program", "global"]},
                },
            },
            worker_fact_record,
            True,
        ),
        WorkerTool(
            "worker_fact_retract",
            "Retract a fact an agent recorded that no longer holds.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "why"],
                "properties": {"id": {"type": "integer"}, "why": {"type": "string", "minLength": 1, "maxLength": 300}},
            },
            worker_fact_retract,
            True,
        ),
        WorkerTool(
            "thread_record_outcome",
            "Record what this conversation has come to so far, for whoever picks " "the Connect Worker up next.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary"],
                "properties": {
                    "outcome": {"type": "string", "maxLength": 64},
                    "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                },
            },
            thread_record_outcome,
            True,
        ),
        WorkerTool(
            "thread_handoff",
            "Hand this conversation to a person ('human') or to another of the program's "
            "playbooks, after your answer is sent.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["to", "note"],
                "properties": {
                    "to": {"type": "string", "minLength": 1, "maxLength": 64},
                    "note": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
            thread_handoff,
            True,
        ),
        WorkerTool(
            "thread_close",
            "Close this conversation after your answer is sent, with a summary and anything "
            "promised that is still to happen.",
            {
                "type": "object",
                "additionalProperties": False,
                "required": ["summary"],
                "properties": {
                    "summary": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "commitments": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 300}},
                    "outcome": {"type": "string", "maxLength": 64},
                },
            },
            thread_close,
            True,
        ),
    )
}

assert set(TOOLS) == set(playbooks.WORKER_TOOLS), "every worker tool a playbook may grant is served here"
