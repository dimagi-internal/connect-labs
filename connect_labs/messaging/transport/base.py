"""What a transport does for Messaging: open a channel to a worker, and carry one message.

Messaging talks to a worker through a `Transport`; PersonalID is the only one today
(`personalid.py`). A transport never decides WHETHER to send -- consent, caps, quiet hours
and approval are decided before it is called -- it only carries what it is given, and says
plainly when it could not.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class TransportError(Exception):
    """A send or channel call that failed. `code` is stable; `retryable` says whether to try again."""

    retryable = False

    def __init__(self, code: str, message: str = ""):
        self.code = code
        super().__init__(message or code)


class TransportUnavailable(TransportError):
    """The other end could not be reached, or failed on its side. Try again later."""

    retryable = True


class ConsentRefused(TransportError):
    """The worker has turned this channel off."""


class ChannelGone(TransportError):
    """The channel does not exist on the other end (or is not ours)."""


class Transport(ABC):
    name: str

    @abstractmethod
    def ensure_channel(self, address) -> None:
        """Make sure `address` has a channel on the other end; fill in `channel_id` and consent. Saves."""

    @abstractmethod
    def send(self, message) -> None:
        """Carry one outbound `Message` (its `transport_message_id` is the idempotency key)."""


_TRANSPORTS: dict[str, Transport] = {}


def register_transport(transport: Transport) -> Transport:
    _TRANSPORTS[transport.name] = transport
    return transport


def get_transport(name: str) -> Transport:
    if name not in _TRANSPORTS:
        from connect_labs.messaging.transport import personalid, simulated  # noqa: F401 -- register themselves
    return _TRANSPORTS[name]
