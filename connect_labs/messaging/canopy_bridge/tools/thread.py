"""The thread tools: what the conversation came to, handing it on, and closing it.

A handoff or a close the agent asks for mid-turn waits until its answer has gone out
(``PendingTurn.after``), so the worker reads the answer first; ``handler.run_after`` drops it if
the turn never answered and a person has the thread.
"""

from __future__ import annotations

from connect_labs.messaging import playbooks, router, service
from connect_labs.messaging.canopy_bridge.models import CommitmentStatus, PendingTurn, TurnStatus, WorkerCommitment
from connect_labs.messaging.models import AuthorKind

from .base import Ctx, ToolRefused, params, tool


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


def _summary_arg(summary) -> str:
    summary = str(summary or "").strip()
    if not summary:
        raise ToolRefused("invalid", "summary says what the conversation came to, for whoever picks it up next")
    return summary


@tool(
    "Record what this conversation has come to so far, for whoever picks the Connect Worker up next.",
    params(
        required=("summary",),
        outcome={"type": "string", "maxLength": 64},
        summary={"type": "string", "minLength": 1, "maxLength": 2000},
    ),
    write=True,
)
def thread_record_outcome(ctx: Ctx, outcome="", summary="") -> dict:
    row = service.record_summary(
        ctx.thread,
        ctx.scrub(_summary_arg(summary)),
        author_kind=AuthorKind.AGENT,
        author_ref=ctx.author_ref,
        outcome=str(outcome or "")[:64],
    )
    return {"summary_id": row.pk, "outcome": row.outcome}


@tool(
    "Hand this conversation to a person ('human') or to another of the program's "
    "playbooks, after your answer is sent.",
    params(
        required=("to", "note"),
        to={"type": "string", "minLength": 1, "maxLength": 64},
        note={"type": "string", "minLength": 1, "maxLength": 300},
    ),
    write=True,
)
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


@tool(
    "Close this conversation after your answer is sent, with a summary and anything "
    "promised that is still to happen.",
    params(
        required=("summary",),
        summary={"type": "string", "minLength": 1, "maxLength": 2000},
        commitments={"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 300}},
        outcome={"type": "string", "maxLength": 64},
    ),
    write=True,
)
def thread_close(ctx: Ctx, summary="", commitments=None, outcome="closed") -> dict:
    _require_open(ctx)
    summary = _summary_arg(summary)
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
