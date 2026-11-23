"""`manage.py messaging_simulate` and the recorder transport: test workers only, nothing leaves labs.

Every name and message here is invented.
"""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from connect_labs.labs.access import scopes
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.messaging import handlers, playbooks, router, service
from connect_labs.messaging.handlers import Handler
from connect_labs.messaging.models import Address, AuthorKind, Direction, Message, TransportKind
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.messaging.transport import ChannelGone, get_transport
from connect_labs.workers.models import ConnectWorker

pytestmark = pytest.mark.django_db

TEST_WORKER = "sim.tester"
OPP = 10_901


@pytest.fixture
def sim(settings, messaging_on, run_now, monkeypatch):
    settings.MESSAGING_SIMULATION_WORKERS = [TEST_WORKER]
    SyntheticOpportunity.objects.create(
        opportunity_id=OPP, labs_only=True, program_id=PROGRAM, gdrive_folder_id="x", label="Sim"
    )
    # The debounced hand-off runs at once, as a worker would once the worker paused.
    monkeypatch.setattr(
        run_now.handle_inbound,
        "apply_async",
        lambda args, countdown=None, eta=None: router.handle_due(args[0], now=timezone.now() + timedelta(seconds=60)),
    )
    playbooks.set_playbook(
        scopes.SYSTEM,
        PROGRAM,
        "general-help",
        {
            "version": 1,
            "direction": "inbound",
            "handler": {"kind": "agent", "agent": "helper", "skill": "general-help"},
        },
    )
    return run_now


class Answers(Handler):
    def start(self, thread, ctx):
        pass

    def on_message(self, thread, msgs):
        service.send(thread, f"You wrote: {handlers.joined(msgs)}", author_kind=AuthorKind.AGENT, author_ref="helper")


@pytest.fixture
def agent():
    previous = handlers.get("agent")
    handlers.register("agent", Answers())
    yield
    handlers.register("agent", previous)


def _run(*args):
    out = io.StringIO()
    try:
        call_command("messaging_simulate", *args, stdout=out)
    except CommandError as exc:
        return out.getvalue(), exc
    return out.getvalue(), None


def test_refuses_a_worker_who_is_not_a_test_worker(sim):
    out, exc = _run("--username", "a.real.worker", "--opportunity-id", str(OPP))
    assert exc is not None and "MESSAGING_SIMULATION_WORKERS" in str(exc)
    assert not ConnectWorker.objects.filter(username="a.real.worker").exists()


def test_refuses_an_opportunity_that_is_not_labs_only(sim):
    out, exc = _run("--username", TEST_WORKER, "--opportunity-id", "123")
    assert exc is not None and "labs-only" in str(exc)
    assert not Message.objects.exists()


def test_refuses_when_the_program_is_off(sim, settings):
    from connect_labs.scope_config.models import ScopeConfig

    ScopeConfig.objects.filter(scope_key=str(PROGRAM)).update(data={"enabled": False})
    out, exc = _run("--username", TEST_WORKER, "--opportunity-id", str(OPP))
    assert exc is not None and "messaging is off" in str(exc)


def test_the_conversation_takes_the_real_path_and_is_recorded_not_sent(sim, agent):
    out, exc = _run("--username", TEST_WORKER, "--opportunity-id", str(OPP), "--text", "Hi there", "--wait", "0")
    assert "PASS agent reply in the thread" in out
    assert "PASS reply recorded, not sent to PersonalID" in out
    assert "You wrote: Hi there" in out
    # No canopy here, so the canopy checks fail and the command says so.
    assert exc is not None and "FAIL brief stored" in out
    reply = Message.objects.get(direction=Direction.OUTBOUND)
    assert reply.transport == TransportKind.SIMULATED and reply.delivery_status == "sent"
    assert reply.thread.playbook_key == "general-help"
    assert not Address.objects.filter(transport=TransportKind.PERSONALID).exists()


def test_a_test_worker_stays_on_the_simulated_channel(sim, agent):
    _run("--username", TEST_WORKER, "--opportunity-id", str(OPP), "--wait", "0")
    worker = ConnectWorker.objects.get(username=TEST_WORKER)
    assert service.address_for(worker, PROGRAM).transport == TransportKind.SIMULATED


def test_the_recorder_never_carries_for_anyone_else(sim, settings, agent):
    _run("--username", TEST_WORKER, "--opportunity-id", str(OPP), "--wait", "0")
    reply = Message.objects.get(direction=Direction.OUTBOUND)
    settings.MESSAGING_SIMULATION_WORKERS = []
    with pytest.raises(ChannelGone):
        get_transport(TransportKind.SIMULATED).send(reply)
