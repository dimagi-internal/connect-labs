"""The reading tools: the worker's profile, timeline, conversations, history, tasks, learning,
visits, payments and facts, each through the call's `visibility.Scope`."""

from __future__ import annotations

import re

from connect_labs.messaging.canopy_bridge import visibility
from connect_labs.messaging.models import Thread
from connect_labs.workers import api as workers
from connect_labs.workers.models import FactKind, OpportunitySync, WorkerMembership, WorkerSnapshot
from connect_labs.workers.profile import fact_dict

from .base import Ctx, ToolRefused, author_of, bounded_int, iso, params
from .base import since as parse_since
from .base import tool


def _snapshots(ctx: Ctx, kind: str) -> list[dict]:
    rows = WorkerSnapshot.objects.filter(
        worker=ctx.worker, kind=kind, opportunity_id__in=ctx.scope.opportunity_ids(), gone_at__isnull=True
    ).order_by("opportunity_id", "key")
    return [{k: v for k, v in (r.data or {}).items() if k not in ("username", "key")} for r in rows]


def _facet(ctx: Ctx, name: str) -> list[dict]:
    return [
        {"opportunity_id": s["opportunity_id"], "stage": s["stage"], name: (s["facets"] or {}).get(name)}
        for s in workers.states_for(ctx.worker, ctx.thread.program_id)
    ]


@tool(
    "The Connect Worker you are talking to: identity, this program's memberships, "
    "stage and facets, staff notes (never private ones), and what labs' record covers."
)
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


@tool(
    "This Connect Worker's timeline in this program, newest first: invites, "
    "learning, visits, tasks, payments, stage changes, messages.",
    params(
        since={"type": "string", "description": "ISO 8601 date or time"},
        kinds={"type": "array", "items": {"type": "string"}},
        limit={"type": "integer", "minimum": 1, "maximum": 200},
    ),
)
def worker_timeline(ctx: Ctx, since=None, kinds=None, limit=50) -> dict:
    kinds = [str(k) for k in (kinds or []) if k] or None
    events = workers.timeline(
        ctx.worker,
        program_id=ctx.thread.program_id,
        kinds=kinds,
        since=parse_since(since),
        limit=bounded_int(limit, 50, 1, 200),
    )
    return {"events": events, "kinds": list(workers.EVENT_KINDS)}


# --- conversations ------------------------------------------------------------------------------


def _thread_row(ctx: Ctx, thread: Thread) -> dict:
    summary = visibility.summaries().filter(thread=thread).order_by("-created_at", "-id").first()
    return {
        "id": thread.pk,
        "program_id": thread.program_id,
        "this_program": thread.program_id in ctx.programs,
        "playbook": thread.playbook_key,
        "direction": "inbound" if thread.direction == "in" else "outbound",
        "state": thread.state,
        "handler": f"{thread.handler_kind}:{thread.handler_ref}".rstrip(":"),
        "opened_at": iso(thread.opened_at),
        "closed_at": iso(thread.closed_at),
        "summary": ctx.scrub(summary.text) if summary else None,
        "outcome": summary.outcome if summary else None,
        "current": thread.pk == ctx.thread.pk,
    }


@tool(
    "This Connect Worker's conversations with labs in this program (and others, "
    "summaries only, when the playbook allows), newest first, each with its summary.",
    params(limit={"type": "integer", "minimum": 1, "maximum": 50}),
)
def worker_threads(ctx: Ctx, limit=20) -> dict:
    rows = ctx.scope.threads().order_by("-opened_at", "-id")[: bounded_int(limit, 20, 1, 50)]
    return {"threads": [_thread_row(ctx, t) for t in rows]}


@tool(
    "One of this Connect Worker's conversations: its messages and summaries.",
    params(required=("id",), id={"type": "integer"}),
)
def worker_thread(ctx: Ctx, id=None) -> dict:  # noqa: A002 -- the tool's argument name
    thread = ctx.scope.threads().filter(pk=bounded_int(id, 0, 0, 2**62)).first()
    if thread is None:
        raise ToolRefused("not_found", "No such thread for this Connect Worker.")
    out = _thread_row(ctx, thread)
    if thread.program_id in ctx.programs:
        out["messages"] = [
            {"id": m.pk, "at": iso(m.created_at), "from": author_of(m), "text": ctx.scrub(m.text)}
            for m in visibility.messages(thread).order_by("created_at", "id")[:200]
        ]
    else:
        out["messages"] = None
        out["note"] = "Another program's thread: only its summary is shared."
    out["summaries"] = [
        {"at": iso(s.created_at), "outcome": s.outcome, "text": ctx.scrub(s.text), "by": s.author_kind}
        for s in visibility.summaries().filter(thread=thread).order_by("created_at", "id")
    ]
    return out


_TOKEN = re.compile(r"[\w]{2,}", re.UNICODE)


@tool(
    "Search this Connect Worker's past messages and conversation summaries.",
    params(
        required=("query",),
        query={"type": "string", "minLength": 1, "maxLength": 200},
        limit={"type": "integer", "minimum": 1, "maximum": 25},
    ),
)
def worker_history_search(ctx: Ctx, query="", limit=10) -> dict:
    """Messages and summaries of this worker's that match ``query``, best first.

    Messages are encrypted at rest (``EncryptedTextField``), so Postgres full-text (and pgvector)
    cannot index them; the worker's own history is small, so it is searched here, decrypted, after
    the visibility rules have narrowed it."""
    words = {w.lower() for w in _TOKEN.findall(query or "")}
    if not words:
        raise ToolRefused("invalid", "query needs at least one word")
    hits = []
    for m in ctx.scope.worker_messages().order_by("-created_at", "-id")[:2000]:
        found = words & {w.lower() for w in _TOKEN.findall(m.text or "")}
        if found:
            hits.append((len(found), m.created_at, "message", m.thread_id, m.pk, author_of(m), m.text))
    for s in ctx.scope.thread_summaries().order_by("-created_at", "-id")[:500]:
        found = words & {w.lower() for w in _TOKEN.findall(s.text or "")}
        if found:
            hits.append((len(found), s.created_at, "summary", s.thread_id, s.pk, s.author_kind, s.text))
    hits.sort(key=lambda h: (-h[0], -h[1].timestamp(), -h[4]))
    return {
        "query": query,
        "results": [
            {"kind": kind, "thread_id": tid, "id": pk, "at": iso(at), "from": who, "text": ctx.scrub(text)[:500]}
            for _, at, kind, tid, pk, who, text in hits[: bounded_int(limit, 10, 1, 25)]
        ],
    }


# --- the program's record -----------------------------------------------------------------------


@tool("Tasks assigned to this Connect Worker in this program (type, status, due date).")
def worker_tasks(ctx: Ctx) -> dict:
    return {"tasks": _snapshots(ctx, "assigned_tasks"), "facets": _facet(ctx, "tasks")}


@tool("This Connect Worker's learning: modules completed and assessments.")
def worker_learn(ctx: Ctx) -> dict:
    names = {}
    for sync in OpportunitySync.objects.filter(opportunity_id__in=ctx.scope.opportunity_ids()):
        names[sync.opportunity_id] = (sync.meta or {}).get("modules") or {}
    modules = _snapshots(ctx, "completed_module")
    for m in modules:
        m["module_name"] = names.get(m.get("opportunity_id"), {}).get(str(m.get("module")), "")
    memberships = WorkerMembership.objects.filter(worker=ctx.worker, program_id__in=list(ctx.programs))
    return {
        "memberships": [
            {
                "opportunity_id": m.opportunity_id,
                "learn_started": iso(m.date_learn_started),
                "learn_completed": iso(m.completed_learn_date),
            }
            for m in memberships.order_by("opportunity_id")
        ],
        "modules_completed": modules,
        "assessments": _snapshots(ctx, "assessment"),
        "facets": _facet(ctx, "learn"),
    }


@tool(
    "This Connect Worker's recent visits, thin: status, review and flags.",
    params(limit={"type": "integer", "minimum": 1, "maximum": 200}),
)
def worker_visits(ctx: Ctx, limit=50) -> dict:
    rows = ctx.scope.events(("visit", "visit_reviewed")).order_by("-occurred_at", "-observed_at", "-id")[
        : bounded_int(limit, 50, 1, 200)
    ]
    return {
        "visits": [
            {"kind": e.kind, "at": iso(e.at), "opportunity_id": e.opportunity_id, **(e.payload or {})} for e in rows
        ],
        "facets": _facet(ctx, "verification"),
        "note": "A thin view: status, review and flags, never the form's contents.",
    }


@tool("This Connect Worker's payments and work by status in this program.")
def worker_payments(ctx: Ctx) -> dict:
    works = _snapshots(ctx, "completed_works")
    by_status: dict[str, int] = {}
    for w in works:
        by_status[w.get("status") or "unknown"] = by_status.get(w.get("status") or "unknown", 0) + 1
    return {"payments": _snapshots(ctx, "payment"), "work_by_status": by_status, "facets": _facet(ctx, "payment")}


@tool(
    "What has been learned about this Connect Worker (preferences, constraints, "
    "corrections, cautions), with who recorded each.",
    params(include_retracted={"type": "boolean"}),
)
def worker_facts(ctx: Ctx, include_retracted=False) -> dict:
    out = []
    for fact in ctx.scope.facts(active_only=not include_retracted).order_by("-created_at", "-id")[:100]:
        row = fact_dict(fact)
        row["statement"] = ctx.scrub(row["statement"])
        out.append(row)
    return {"facts": out, "kinds": list(FactKind.values)}
