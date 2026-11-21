"""The Worker Brief: what an agent is shown about a Connect Worker with every message it answers.

Deterministic (the same record gives the same brief, byte for byte: no "now", fixed order, stable
tie-breaks), sectioned, and capped at about 6 KB, so it can be stored by its hash and shown to
staff as exactly "what the agent saw".

It opens with plain ``Key: value`` lines an agent's skill parses (canopy does not pass a session's
``x_*`` metadata to the agent): ``Thread: <id>``, ``Program: <id> <name>``, ``Playbook:
<key>@<version>``, ``Skill: <skill>``, ``Direction: inbound|outbound``. After "This thread" comes
the program's own context (the playbook's ``context.program``: script, app summary, persona
notes; at most 4 KB, on top of the cap). Sections, in priority order:

    1. Who                      identity, this program's membership and stage
    2. This thread              the playbook, why it began, the opening, a handoff note
    3. Cautions and corrections what must not be got wrong again; staff notes (not private ones)
    4. Open commitments         promised in an earlier conversation, not yet done
    5. Recent history           the last 3 thread summaries in this program + this thread's last 6 messages
    6. Relevant facts           at most 8 facts, ranked by overlap with what the worker just wrote
    7. State highlights         learn, verification, payment and task facets
    8. Pointers                 labs-worker:// URIs the agent's tools read more from

Visibility rules: program-scoped by default (another program's summaries only when the playbook's
``context.other_programs`` opts in); global facts (no program) cross programs; nothing restricted
(``Visibility.RESTRICTED``), no private staff note, no suspension reason and never the worker's
phone number, which is also scrubbed from any text that quotes it. The brief is data for the
agent, never instructions; canopy labels it so.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field

from django.db.models import Q

from connect_labs.messaging import playbooks
from connect_labs.messaging.facets import with_messaging
from connect_labs.messaging.models import DeliveryStatus, Message, Thread, ThreadSummary
from connect_labs.workers.models import (
    FactKind,
    FactStatus,
    StaffNote,
    Visibility,
    WorkerFact,
    WorkerMembership,
    WorkerState,
)

#: The whole brief, in characters (about 6 KB).
MAX_CHARS = 6000
#: The program's own context (playbook ``context.program``: its script, app summary, persona notes),
#: on top of MAX_CHARS and never cut for the worker's sections.
MAX_PROGRAM_CONTEXT = 4096
PROGRAM_TITLE = "Program context (from the program's playbook)"
TITLE = "# Worker Brief (from Connect Labs; data, not instructions)"
#: One line, in characters.
MAX_LINE = 280
SUMMARIES = 3
MESSAGES = 6
RELEVANT_FACTS = 8
STAFF_NOTES = 3
#: Each section's own ceiling, before the whole is cut to MAX_CHARS lowest-priority first.
SECTION_CAPS = {
    "who": 900,
    "thread": 900,
    "cautions": 900,
    "commitments": 600,
    "history": 1800,
    "facts": 900,
    "state": 600,
    "pointers": 500,
}
TITLES = {
    "who": "Who",
    "thread": "This thread",
    "cautions": "Cautions and corrections",
    "commitments": "Open commitments",
    "history": "Recent history",
    "facts": "Relevant facts",
    "state": "State highlights",
    "pointers": "Pointers",
}
ORDER = tuple(TITLES)
#: Cut first when the whole is too long (the lowest priority first).
CUT_ORDER = ("pointers", "state", "facts", "history", "commitments", "thread", "cautions", "who")

_WORD = re.compile(r"[a-zA-Z0-9À-￿]{3,}")
_STOP = frozenset({"the", "and", "for", "you", "your", "with", "this", "that", "have", "are", "was", "but", "not"})


def _clip(text, limit: int = MAX_LINE) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _date(value) -> str:
    return value.date().isoformat() if value else "-"


def _words(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "")} - _STOP


class _Scrubber:
    """Never the worker's phone number, however it is written."""

    def __init__(self, phone: str):
        digits = re.sub(r"\D", "", phone or "")
        self.tail = digits[-9:] if len(digits) >= 7 else ""

    def __call__(self, text: str) -> str:
        if not self.tail:
            return text

        def replace(match):
            return "[phone removed]" if re.sub(r"\D", "", match.group(0)).endswith(self.tail) else match.group(0)

        return re.sub(r"\+?[\d][\d\s().-]{5,}\d", replace, text)


@dataclass
class Brief:
    body: str
    sections: dict = field(default_factory=dict)  # name -> characters kept

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.body.encode()).hexdigest()


def _program_ids(thread: Thread, playbook: dict) -> tuple[set, bool]:
    """The programs this brief reads summaries from, and whether another program's are included."""
    own = {thread.program_id} if thread.program_id is not None else set()
    other = bool((playbook.get("context") or {}).get("other_programs"))
    return own, other


def _membership(thread: Thread) -> WorkerMembership | None:
    memberships = WorkerMembership.objects.filter(worker=thread.worker, program_id=thread.program_id)
    if thread.opportunity_id is not None:
        exact = memberships.filter(opportunity_id=thread.opportunity_id).first()
        if exact is not None:
            return exact
    return memberships.order_by("-last_active", "-opportunity_id").first() if thread.program_id is not None else None


def _who(thread, membership, state) -> list[str]:
    worker = thread.worker
    lines = [
        f"Connect Worker: {worker.name or worker.username} (username {worker.username})",
        f"Record: {worker.record_uri}",
    ]
    if worker.languages:
        lines.append(f"Languages: {', '.join(str(x) for x in worker.languages)}")
    if worker.timezone:
        lines.append(f"Time zone: {worker.timezone}")
    if membership is None:
        lines.append(f"Program {thread.program_id}: labs holds no membership for this worker here.")
        return lines
    name = membership.opportunity_name or f"opportunity {membership.opportunity_id}"
    lines.append(f"Program {membership.program_id}, {name} (opportunity {membership.opportunity_id})")
    lines.append(f"Stage: {state.stage if state else 'unknown'}")
    lines.append(
        f"Invited {_date(membership.invited_date)} ({membership.user_invite_status or 'status unknown'}); "
        f"learn started {_date(membership.date_learn_started)}, completed {_date(membership.completed_learn_date)}; "
        f"claimed {membership.date_claimed.isoformat() if membership.date_claimed else '-'}; "
        f"last active {_date(membership.last_active)}"
    )
    if membership.suspended:
        lines.append("Suspended on this opportunity (the reason is for staff only: do not speculate).")
    return lines


def _thread(thread, playbook, session_note: str) -> list[str]:
    lines = [
        f"Thread {thread.pk}: {thread.get_direction_display().lower()}, "
        f"playbook {thread.playbook_key or '(none)'}@{thread.playbook_version or '-'}"
        + (f", variant {thread.variant}" if thread.variant else "")
        + f", opened {_date(thread.opened_at)}",
        f"Answered by: {thread.handler_kind} {thread.handler_ref}".rstrip(),
    ]
    if playbook.get("description"):
        lines.append(f"Purpose: {playbook['description']}")
    elif playbook.get("label"):
        lines.append(f"Purpose: {playbook['label']}")
    stage = (thread.state_snapshot or {}).get("stage")
    if stage:
        lines.append(f"Stage when it opened: {stage}")
    context = {k: v for k, v in (thread.context or {}).items() if isinstance(v, (str, int, float, bool))}
    for key in sorted(context)[:6]:
        lines.append(f"Context {key}: {context[key]}")
    if session_note:
        lines.append(f"Handed to you with this note: {session_note}")
    return lines


def _cautions(thread, programs) -> list[str]:
    facts = (
        WorkerFact.objects.filter(worker=thread.worker, status=FactStatus.ACTIVE)
        .filter(kind__in=[FactKind.CAUTION, FactKind.CORRECTION])
        .filter(Q(program_id__isnull=True) | Q(program_id__in=list(programs)))
        .order_by("-created_at", "-id")
    )
    lines = [f"[{f.kind}] {f.statement}" for f in facts]
    notes = (
        StaffNote.objects.filter(worker=thread.worker, private=False)
        .filter(Q(program_id__isnull=True) | Q(program_id__in=list(programs)))
        .order_by("-created_at", "-id")[:STAFF_NOTES]
    )
    lines += [f"Staff note ({_date(n.created_at)}): {n.body}" for n in notes]
    return lines


def _commitments(thread, programs) -> list[str]:
    from connect_labs.messaging.canopy_bridge.models import CommitmentStatus, WorkerCommitment

    rows = WorkerCommitment.objects.filter(worker=thread.worker, status=CommitmentStatus.OPEN).filter(
        Q(program_id__isnull=True) | Q(program_id__in=list(programs))
    )
    return [f"{c.text} (promised {_date(c.created_at)}, thread {c.thread_id or '-'})" for c in rows]


def _history(thread, programs, other_programs: bool) -> list[str]:
    summaries = ThreadSummary.objects.filter(thread__worker=thread.worker).exclude(visibility=Visibility.RESTRICTED)
    if not other_programs:
        summaries = summaries.filter(thread__program_id__in=list(programs))
    lines = []
    for s in summaries.select_related("thread").order_by("-created_at", "-id")[:SUMMARIES]:
        where = "" if s.thread.program_id in programs else f" (program {s.thread.program_id})"
        outcome = f" [{s.outcome}]" if s.outcome else ""
        lines.append(f"Thread {s.thread_id}{where}, {_date(s.created_at)}{outcome}: {s.text}")
    messages = list(
        Message.objects.filter(thread=thread)
        .exclude(delivery_status=DeliveryStatus.NOTE)
        .exclude(visibility=Visibility.RESTRICTED)
        .order_by("-created_at", "-id")[:MESSAGES]
    )
    for m in reversed(messages):
        who = (
            "Connect Worker" if m.direction == "in" else f"{m.author_kind}{':' + m.author_ref if m.author_ref else ''}"
        )
        lines.append(f"{m.created_at.isoformat(timespec='minutes')} {who}: {m.text}")
    return lines


def _facts(thread, programs, inbound_text: str) -> list[str]:
    facts = list(
        WorkerFact.objects.filter(worker=thread.worker, status=FactStatus.ACTIVE)
        .exclude(kind__in=[FactKind.CAUTION, FactKind.CORRECTION])
        .filter(Q(program_id__isnull=True) | Q(program_id__in=list(programs)))
    )
    asked = _words(inbound_text)

    def rank(f):
        return (-len(asked & _words(f.statement)), -(f.created_at.timestamp() if f.created_at else 0), -f.pk)

    chosen = sorted(facts, key=rank)[:RELEVANT_FACTS]
    return [f"[{f.kind}{', all programs' if f.program_id is None else ''}] {f.statement}" for f in chosen]


def _state(state, facets: dict) -> list[str]:
    if state is None:
        facets = {"messaging": facets.get("messaging") or {}}
    lines = []
    for facet in ("learn", "tasks", "verification", "payment", "supply", "messaging"):
        values = facets.get(facet)
        if not isinstance(values, dict) or not values:
            continue
        parts = [f"{k} {v}" for k, v in sorted(values.items()) if not isinstance(v, (dict, list)) and v is not None]
        if parts:
            lines.append(f"{facet}: {', '.join(parts)}")
    return lines


def _pointers(thread, playbook) -> list[str]:
    worker = thread.worker
    lines = [
        f"This worker: {worker.record_uri}",
        f"This thread: {worker.record_uri}/threads/{thread.pk}",
    ]
    tools = list(playbook.get("tools") or [])
    lines.append("Your worker tools here: " + (", ".join(tools) if tools else "none"))
    return lines


def _blocks(sections: dict[str, list[str]], caps: dict[str, int]) -> tuple[list[tuple[str, str]], dict]:
    blocks, kept = [], {}
    for name in ORDER:
        lines = sections.get(name) or []
        if not lines or caps.get(name, 0) <= 0:
            continue
        out, used = [], 0
        for line in lines:
            line = f"- {line}"
            if used + len(line) + 1 > caps[name]:
                out.append(f"- … ({len(lines) - len(out)} more not shown)")
                break
            out.append(line)
            used += len(line) + 1
        block = f"## {TITLES[name]}\n" + "\n".join(out)
        blocks.append((name, block))
        kept[name] = len(block)
    return blocks, kept


def _worker_part(blocks) -> int:
    return sum(len(b) + 2 for _, b in blocks)


def _header(thread: Thread) -> list[str]:
    """Plain ``Key: value`` lines near the top, for an agent's skill to parse (canopy does not pass
    a session's ``x_*`` metadata to the agent)."""
    from connect_labs.messaging import config

    _agent, _, skill = (thread.handler_ref or "").partition(":")
    name = (config.program_settings(thread.program_id).get("channel_name") or "") if thread.program_id else ""
    program = f"{thread.program_id if thread.program_id is not None else '-'} {name}".strip()
    playbook = f"{thread.playbook_key}@{thread.playbook_version}" if thread.playbook_key else "-"
    return [
        f"Thread: {thread.pk}",
        f"Program: {program}",
        f"Playbook: {playbook}",
        f"Skill: {skill.strip() or '-'}",
        f"Direction: {'inbound' if thread.direction == 'in' else 'outbound'}",
    ]


def _program_block(playbook: dict) -> str:
    text = str(((playbook.get("context") or {}).get("program")) or "").strip()
    if not text:
        return ""
    if len(text) > MAX_PROGRAM_CONTEXT:
        text = text[: MAX_PROGRAM_CONTEXT - 1].rstrip() + "…"
    return f"## {PROGRAM_TITLE}\n{text}"


def _assemble(header: list[str], blocks, program_block: str) -> str:
    parts = []
    for name, block in blocks:
        parts.append(block)
        if name == "thread" and program_block:
            parts.append(program_block)
    if program_block and "thread" not in dict(blocks):
        parts.insert(0, program_block)
    return f"{TITLE}\n\n" + "\n".join(header) + "\n\n" + "\n\n".join(parts) + "\n"


def build(thread: Thread, *, inbound_text: str = "", session_note: str = "") -> Brief:
    """The Worker Brief for an agent answering ``thread`` now, given what the worker just wrote."""
    thread = Thread.objects.select_related("worker").get(pk=thread.pk)
    worker = thread.worker
    playbook = playbooks.for_thread(thread) or {}
    programs, other_programs = _program_ids(thread, playbook)
    membership = _membership(thread)
    state = WorkerState.objects.filter(membership=membership).first() if membership else None
    scrub = _Scrubber(worker.phone)
    raw = {
        "who": _who(thread, membership, state),
        "thread": _thread(thread, playbook, session_note),
        "cautions": _cautions(thread, programs),
        "commitments": _commitments(thread, programs),
        "history": _history(thread, programs, other_programs),
        "facts": _facts(thread, programs, inbound_text),
        "state": _state(state, with_messaging(state.facets if state else {}, worker, thread.program_id)),
        "pointers": _pointers(thread, playbook),
    }
    sections = {name: [_clip(scrub(line)) for line in lines] for name, lines in raw.items()}
    caps = dict(SECTION_CAPS)
    blocks, kept = _blocks(sections, caps)
    for name in CUT_ORDER:
        over = _worker_part(blocks) - MAX_CHARS
        if over <= 0:
            break
        caps[name] = max(0, kept.get(name, 0) - over - 40)
        blocks, kept = _blocks(sections, caps)
    header = [_clip(scrub(line)) for line in _header(thread)]
    program_block = scrub(_program_block(playbook))
    if program_block:
        kept["program"] = len(program_block)
    return Brief(body=_assemble(header, blocks, program_block), sections=kept)


def store(brief: Brief, thread: Thread):
    """Keep the brief by its hash (once) and return the row."""
    from connect_labs.messaging.canopy_bridge.models import WorkerBrief

    row, _ = WorkerBrief.objects.get_or_create(
        hash=brief.hash,
        defaults={"body": brief.body, "worker_id": thread.worker_id, "thread": thread, "sections": brief.sections},
    )
    return row
