"""How a notice leaves: one interface, email shipped, WhatsApp declared but not yet connected.

A subscription names its `channel`. Email is delivered by `service.deliver` through labs' own sender
(`utils.email`, docs/OUTBOUND_EMAIL.md). WhatsApp is the channel Sophie asked for beside email; labs has
no WhatsApp provider to reuse today:

  * `utils/sms.py` is Twilio SMS for Connect ID one-time codes (a messaging service and sender ids
    chosen for that), not a WhatsApp business sender with approved templates;
  * Open Chat Studio is reached as the signed-in user's OAuth session
    (`labs/integrations/ocs/api_client.py`, `trigger_bot`), not as a standing system sender.

So a WhatsApp subscription can be set up today, and each notice for it is logged as
`no_channel` rather than silently dropped or pretended sent. Connecting a provider means
implementing `WhatsAppChannel.send` and nothing else here changes.
"""

from __future__ import annotations

CHANNEL_NAMES = ("email", "whatsapp")


class Channel:
    name = ""
    label = ""
    # Which recipient field carries the address on this channel.
    address_field = ""

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def send(self, address: str, subject: str, text: str) -> str:
        """Send one message. Returns the delivery status to log: `queued` when handed on."""
        raise NotImplementedError


class EmailChannel(Channel):
    name = "email"
    label = "Email"
    address_field = "recipient_email"

    def available(self):
        from connect_labs.utils.email import email_enabled

        return (True, "") if email_enabled() else (False, "email is off")

    # Email is sent by service.deliver, which keeps the HTML body and the programme-wide lock.


class WhatsAppChannel(Channel):
    name = "whatsapp"
    label = "WhatsApp"
    address_field = "recipient_phone"
    reason = "no WhatsApp provider is connected to labs yet"

    def available(self):
        return False, self.reason

    def send(self, address, subject, text):
        return "no_channel"


_CHANNELS = {c.name: c for c in (EmailChannel(), WhatsAppChannel())}


def channel_for(name: str) -> Channel:
    try:
        return _CHANNELS[name]
    except KeyError:
        raise ValueError(f"no alert channel is called {name!r}; the channels are {', '.join(CHANNEL_NAMES)}") from None
