"""What an agent may see of a Connect Worker: the one place the rules live.

The Worker Brief (``brief.py``), the worker tools (``tools/``) and the fallback summaries
(``summaries.py``) all read a worker's record through here, so the rules cannot drift between
what an agent is shown and what it can ask for:

* **program-scoped**: the thread's own program. Another program's thread summaries only when
  the playbook's ``context.other_programs`` opts in (never its messages). Staff notes and
  commitments with no program are the worker's own and go with every program; so do facts with
  no program that a person recorded, but of what an AGENT recorded only a language fact
  (`GLOBAL_KINDS`) crosses programs, so nothing a worker talks one agent into writing follows
  them into every other program.
* **never restricted**: nothing ``Visibility.RESTRICTED``, no staff note left on a thread
  (``DeliveryStatus.NOTE``), no private staff note, no suspension reason.
* **never the phone number**, however it is written, scrubbed from any text that quotes it
  (`Scrubber`).

A `Scope` is those rules for one thread: build it once (`Scope.of`) and read through it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db.models import Q

from connect_labs.messaging.models import DeliveryStatus, Message, Thread, ThreadSummary
from connect_labs.workers.models import FactKind, FactStatus, StaffNote, Visibility, WorkerFact, WorkerMembership

#: One line of text an agent is shown, in characters.
MAX_LINE = 280
#: The only kinds of fact an agent's record holds across programs.
GLOBAL_KINDS = frozenset({FactKind.LANGUAGE})
#: Who recorded a fact (`WorkerFact.asserted_by_kind`) when it was an agent.
AGENT = "agent"


class Scrubber:
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


def clip(text, limit: int = MAX_LINE) -> str:
    """One line, at most `limit` characters (whitespace collapsed, an ellipsis where cut)."""
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def messages(thread: Thread | None = None):
    """Messages an agent may read: never a staff note on the thread, never a restricted one."""
    qs = Message.objects.exclude(delivery_status=DeliveryStatus.NOTE).exclude(visibility=Visibility.RESTRICTED)
    return qs.filter(thread=thread) if thread is not None else qs


def summaries():
    """Thread summaries an agent may read: never a restricted one."""
    return ThreadSummary.objects.exclude(visibility=Visibility.RESTRICTED)


@dataclass(frozen=True)
class Scope:
    """The visibility rules for an agent answering one thread."""

    worker: object
    thread: Thread
    programs: frozenset
    other_programs: bool = False

    @classmethod
    def of(cls, thread: Thread, playbook: dict) -> Scope:
        return cls(
            worker=thread.worker,
            thread=thread,
            programs=frozenset({thread.program_id}) if thread.program_id is not None else frozenset(),
            other_programs=bool((playbook.get("context") or {}).get("other_programs")),
        )

    @property
    def scrub(self) -> Scrubber:
        return Scrubber(self.worker.phone)

    def _own(self) -> Q:
        """This program's, or the worker's own (no program): for facts, notes and commitments."""
        return Q(program_id__isnull=True) | Q(program_id__in=list(self.programs))

    # --- conversations ------------------------------------------------------------------------

    def threads(self):
        """The worker's threads: this program's, and others' only when the playbook opts in."""
        qs = Thread.objects.filter(worker=self.worker)
        return qs if self.other_programs else qs.filter(program_id__in=list(self.programs))

    def thread_summaries(self):
        """Summaries of `threads()`, restricted ones left out."""
        qs = summaries().filter(thread__worker=self.worker)
        return qs if self.other_programs else qs.filter(thread__program_id__in=list(self.programs))

    def worker_messages(self):
        """The worker's messages in this program only (another program's are never shared)."""
        return messages().filter(worker=self.worker, thread__program_id__in=list(self.programs))

    # --- memory -------------------------------------------------------------------------------

    def facts(self, *, active_only: bool = True):
        """This program's facts, and the worker's own: a person's, or an agent's language fact."""
        crosses = Q(program_id__isnull=True) & (~Q(asserted_by_kind=AGENT) | Q(kind__in=list(GLOBAL_KINDS)))
        qs = WorkerFact.objects.filter(worker=self.worker).filter(Q(program_id__in=list(self.programs)) | crosses)
        return qs.filter(status=FactStatus.ACTIVE) if active_only else qs

    def staff_notes(self):
        """Staff notes an agent may read: never a private one."""
        return StaffNote.objects.filter(worker=self.worker, private=False).filter(self._own())

    def open_commitments(self):
        from connect_labs.messaging.canopy_bridge.models import CommitmentStatus, WorkerCommitment

        return WorkerCommitment.objects.filter(worker=self.worker, status=CommitmentStatus.OPEN).filter(self._own())

    # --- the program's record -----------------------------------------------------------------

    def opportunity_ids(self) -> list[int]:
        return list(
            WorkerMembership.objects.filter(worker=self.worker, program_id__in=list(self.programs)).values_list(
                "opportunity_id", flat=True
            )
        )

    def events(self, kinds):
        """The worker's timeline events of `kinds` on this program's opportunities, restricted ones left out."""
        from connect_labs.workers.models import WorkerEvent

        return WorkerEvent.objects.filter(
            worker=self.worker, opportunity_id__in=self.opportunity_ids(), kind__in=list(kinds)
        ).exclude(visibility=Visibility.RESTRICTED)
