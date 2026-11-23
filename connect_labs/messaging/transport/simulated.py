"""The recorder transport: a channel that carries nothing out of labs.

`manage.py messaging_simulate` runs a staff test conversation over it, so a test worker's
thread goes through the real router, handler, canopy relay and reply path while no call
reaches PersonalID. A simulated channel's outbound messages are stored (the `Message` row is
the record) and marked sent; nothing is delivered to a phone.

It carries only for the usernames in `MESSAGING_SIMULATION_WORKERS`, whichever process
delivers, so a misconfigured address can never turn into a send.
"""

from __future__ import annotations

import logging
import uuid

from django.conf import settings

from connect_labs.messaging.transport.base import ChannelGone, Transport, register_transport

logger = logging.getLogger(__name__)

NAME = "simulated"


def allowed(username: str) -> bool:
    return bool(username) and username in set(getattr(settings, "MESSAGING_SIMULATION_WORKERS", []) or [])


class SimulatedTransport(Transport):
    name = NAME

    def ensure_channel(self, address) -> None:
        if not allowed(address.worker.username):
            raise ChannelGone("not_simulated", "this Connect Worker is not a simulation test worker")
        if not address.channel_id:
            address.channel_id = f"simulated-{uuid.uuid4()}"
        if not address.consent_history:
            address.record_consent(True, source="simulated")
        address.save()

    def send(self, message) -> None:
        address = message.thread.address
        if address is None or not address.channel_id:
            raise ChannelGone("no_channel", "this thread has no channel")
        if not allowed(address.worker.username):
            raise ChannelGone("not_simulated", "this Connect Worker is not a simulation test worker")
        logger.info(
            "messaging: simulated send of message %s on thread %s (recorded, not sent)", message.pk, message.thread_id
        )


register_transport(SimulatedTransport())
