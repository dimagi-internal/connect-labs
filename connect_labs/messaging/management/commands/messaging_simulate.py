"""Run one staff test conversation through Messaging with nothing sent to a phone.

    python manage.py messaging_simulate --username <test worker> --opportunity-id <labs-only opp>
        [--text "..."] [--wait 600]
    python manage.py messaging_simulate --username <test worker> --report <thread id>

The test worker's message goes in as connect-id would deliver it, on a channel of the recorder
transport (transport/simulated.py), and from there takes the real path: the router picks the
program's inbound playbook, its handler (a canopy agent) gets the Worker Brief through the canopy
relay, the agent answers, and labs sends the answer back on the same channel. The recorder stores
it and calls nothing, so no request reaches PersonalID.

It refuses:
- any username not in MESSAGING_SIMULATION_WORKERS (test users only);
- any opportunity that is not a labs-only synthetic one, so the program is a test program;
- a program whose messaging is off (it says which switch).

The report prints the thread's transcript and checks the agent replied, the brief was stored,
the worker has a canopy contact, and the agent called a worker tool (MCPAuditLog, subject mode).
"""

from __future__ import annotations

import time
import uuid

from django.core.management.base import BaseCommand, CommandError

from connect_labs.messaging import crypto, service
from connect_labs.messaging.models import Address, AuthorKind, Direction, Message, Thread, TransportKind
from connect_labs.messaging.transport import get_transport
from connect_labs.messaging.transport.simulated import allowed

DEFAULT_TEXT = "Hello, how much have I been paid so far, and is there anything I still need to finish?"
POLL_SECONDS = 5


class Command(BaseCommand):
    help = "Run one test conversation through Messaging on the recorder transport (test workers only)."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True, help="the test worker's PersonalID username")
        parser.add_argument("--opportunity-id", type=int, help="a labs-only synthetic opportunity")
        parser.add_argument("--text", default=DEFAULT_TEXT, help="what the test worker writes")
        parser.add_argument("--wait", type=int, default=600, help="seconds to wait for the agent's answer")
        parser.add_argument("--report", type=int, help="only report on this thread")

    def handle(self, *args, username, opportunity_id, text, wait, report, **options):
        if not allowed(username):
            raise CommandError(f"refusing: {username!r} is not in MESSAGING_SIMULATION_WORKERS (test workers only)")
        if report:
            thread = Thread.objects.filter(pk=report, worker__username=username).first()
            if thread is None:
                raise CommandError(f"no thread {report} for {username}")
            return self._report(thread)
        if not opportunity_id:
            raise CommandError("--opportunity-id is required to start a conversation")
        program_id = self._test_program(opportunity_id)
        if not service.enabled():
            raise CommandError("MESSAGING_ENABLED is off on this server")
        if not service.program_enabled(program_id):
            raise CommandError(f"messaging is off for program {program_id} (messaging.settings › enabled)")

        address = self._address(username, opportunity_id, program_id)
        message = service.record_inbound(address, transport_message_id=f"simulated-{uuid.uuid4()}", text=text)
        if message is None:
            raise CommandError("the message was not stored")
        thread = message.thread
        self.stdout.write(f"thread {thread.pk} (program {program_id}): the test worker wrote; waiting up to {wait}s")
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            thread.refresh_from_db()
            answered = thread.messages.filter(direction=Direction.OUTBOUND, author_kind=AuthorKind.AGENT).exists()
            if answered or not thread.is_open or thread.handler_kind == "human":
                break
            time.sleep(POLL_SECONDS)
        ok = self._report(thread)
        if not ok:
            raise CommandError("the conversation did not complete; see the checks above")

    def _test_program(self, opportunity_id: int) -> int:
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        synthetic = SyntheticOpportunity.objects.filter(opportunity_id=opportunity_id, labs_only=True).first()
        if synthetic is None:
            raise CommandError(f"refusing: opportunity {opportunity_id} is not a labs-only synthetic opportunity")
        return synthetic.program_id or synthetic.opportunity_id

    def _address(self, username: str, opportunity_id: int, program_id: int) -> Address:
        from connect_labs.workers.models import ConnectWorker, Stage, WorkerMembership, WorkerState

        worker, _ = ConnectWorker.objects.get_or_create(username=username, defaults={"name": "Test worker"})
        membership, _ = WorkerMembership.objects.get_or_create(
            worker=worker, opportunity_id=opportunity_id, defaults={"program_id": program_id}
        )
        WorkerState.objects.get_or_create(membership=membership, defaults={"stage": Stage.DELIVERING})
        address, _ = Address.objects.get_or_create(
            worker=worker,
            transport=TransportKind.SIMULATED,
            channel_source=service.channel_source(program_id),
            defaults={"program_id": program_id, "channel_name": "Simulated", "key": crypto.new_key()},
        )
        if not address.channel_id:
            get_transport(TransportKind.SIMULATED).ensure_channel(address)
        return address

    def _report(self, thread: Thread) -> bool:
        from connect_labs.mcp.models import MCPAuditLog
        from connect_labs.messaging.canopy_bridge.models import CanopySession, PendingTurn, WorkerBrief

        thread.refresh_from_db()
        out = self.stdout.write
        out(
            f"== thread {thread.pk}: state={thread.state} handler={thread.handler_kind}:{thread.handler_ref} "
            f"playbook={thread.playbook_key or '-'} transport={thread.address.transport if thread.address else '-'}"
        )
        messages = list(Message.objects.filter(thread=thread).order_by("created_at", "id"))
        for m in messages:
            out(
                f"-- {m.created_at:%H:%M:%S} {m.direction} {m.author_kind}:{m.author_ref} "
                f"[{m.delivery_status}] brief={m.brief_hash[:12] or '-'} turn={m.canopy_turn_id or '-'}"
            )
            out("   " + m.text.replace("\n", "\n   "))
        for s in CanopySession.objects.filter(thread=thread):
            out(f"canopy session {s.session_id or '(not opened)'} agent={s.agent}:{s.skill} sub={s.sub}")
        for t in PendingTurn.objects.filter(thread=thread):
            out(f"turn {t.turn_id}: {t.status} brief={t.brief_hash[:12]} {t.detail}")
        tools = list(MCPAuditLog.objects.filter(execution__thread_id=thread.pk).order_by("id"))
        for a in tools:
            out(
                f"tool {a.tool_name}: success={a.success} {a.error_code} mode={(a.execution or {}).get('mode')} "
                f"agent={a.actor}"
            )
        if thread.attention_reason:
            out(f"attention: {thread.attention_reason}")

        hashes = {m.brief_hash for m in messages if m.brief_hash}
        replies = [m for m in messages if m.direction == Direction.OUTBOUND and m.author_kind == AuthorKind.AGENT]
        checks = {
            "agent reply in the thread": bool(replies),
            "reply recorded, not sent to PersonalID": all(m.transport == TransportKind.SIMULATED for m in replies)
            and bool(replies),
            "brief stored (brief_hash)": bool(hashes) and WorkerBrief.objects.filter(hash__in=hashes).exists(),
            "canopy contact": bool(thread.worker.canopy_contact_id),
            "worker tool called with subject auth": any(
                a.success and (a.execution or {}).get("mode") == "subject" for a in tools
            ),
        }
        contact = thread.worker.canopy_contact_id or "-"
        out(f"canopy contact {contact} (record_uri labs-worker://{thread.worker.username})")
        for name, passed in checks.items():
            out(f"{'PASS' if passed else 'FAIL'} {name}")
        return all(checks.values())
