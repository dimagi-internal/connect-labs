"""How a notice leaves: one interface, two channels -- email, and a Connect message.

A subscription names its `channel`. Email is delivered by `service.deliver` through labs' own sender
(`utils.email`, docs/OUTBOUND_EMAIL.md).

A **Connect message** goes to a person's Connect (PersonalID) username, through Open Chat Studio's
`commcare_connect` channel -- the same delivery update links use (`update_links/delivery.py`). Labs calls
Open Chat Studio *as a person*, so the sender is the person who set the alert up (`created_by`); there is no
service identity. Every way it can fail is a status the log shows, with a plain reason, never a silent drop
and never a claimed send:

  * `no_connection`  the sender's Open Chat Studio connection is missing or has expired;
  * `no_chatbot`     no chatbot is chosen to send through (`SUPPLY_LINK_OCS_BOT`, as update links);
  * `refused`        Open Chat Studio declined it -- its own reason is kept, e.g. the person has not
                     agreed to messages in the Connect app.
"""

from __future__ import annotations

CHANNEL_NAMES = ("email", "connect_message")


class Channel:
    name = ""
    label = ""
    # Which recipient field carries the address on this channel.
    address_field = ""

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def send(self, subscription, address: str, subject: str, text: str) -> tuple[str, str]:
        """Send one message. Returns (delivery status to log, plain note): `queued` when handed on."""
        raise NotImplementedError


class EmailChannel(Channel):
    name = "email"
    label = "Email"
    address_field = "recipient_email"

    def available(self):
        from connect_labs.utils.email import email_enabled

        return (True, "") if email_enabled() else (False, "email is off")

    # Email is sent by service.deliver, which keeps the HTML body and the programme-wide lock.


class ConnectMessageChannel(Channel):
    name = "connect_message"
    label = "Connect message"
    address_field = "recipient_connect_username"

    def available(self):
        return True, ""

    def send(self, subscription, address, subject, text):
        from connect_labs.supply_chain.update_links import delivery

        sender = subscription.created_by
        if sender is None or not getattr(sender, "pk", None):
            return (
                "no_connection",
                "The person who set this alert up is no longer on labs, so there is nobody to send as.",
            )
        bot = delivery.default_bot()
        if not bot:
            return "no_chatbot", "No chatbot has been chosen to send Connect messages through."
        try:
            delivery.send_text(sender, bot=bot, username=address, text=f"{subject}\n\n{text}")
        except delivery.NotConnected as error:
            return "no_connection", str(error)
        except delivery.DeliveryError as error:
            return "refused", str(error)
        return "queued", ""


_CHANNELS = {c.name: c for c in (EmailChannel(), ConnectMessageChannel())}


def channel_for(name: str) -> Channel:
    try:
        return _CHANNELS[name]
    except KeyError:
        raise ValueError(f"no alert channel is called {name!r}; the channels are {', '.join(CHANNEL_NAMES)}") from None
