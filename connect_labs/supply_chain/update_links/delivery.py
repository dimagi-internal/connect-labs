"""Delivering text to a person's PersonalID chat, through Open Chat Studio.

How the channel works (docs/personalid-authorization.md §2.3): labs asks OCS to
send `message_text` verbatim to a participant whose identifier is the person's
PersonalID username, on the chatbot's `commcare_connect` channel. OCS creates the
PersonalID channel on first use and refuses unless the person has consented in
the app; the reason it gives is passed back here unchanged.

Labs calls OCS **as a person** -- that person's stored OAuth token, the same as a
workflow's coaching send -- so there is always a named sender: the person who
issued the link. There is no service identity.
"""

import logging

from django.conf import settings

from connect_labs.labs.integrations.ocs.api_client import OCSAPIError, OCSDataAccess

logger = logging.getLogger(__name__)

PLATFORM = "commcare_connect"


class DeliveryError(Exception):
    """The message did not go. ``str()`` is written to be shown to the person who asked."""


def default_bot() -> str:
    """The chatbot links are sent through unless the issuer picks another (settings.SUPPLY_LINK_OCS_BOT)."""
    return getattr(settings, "SUPPLY_LINK_OCS_BOT", "") or ""


def available_bots(user) -> list[dict] | None:
    """The chatbots ``user`` can send through, or None when their OCS connection is missing or dead."""
    client = OCSDataAccess(user=user)
    try:
        if not client.check_token_valid():
            return None
        return [
            {"id": e.get("public_id") or str(e.get("id")), "name": e.get("name") or ""}
            for e in client.list_experiments()
        ]
    except Exception:  # noqa: BLE001 -- an unreachable OCS reads as "not connected"
        logger.warning("Could not list OCS bots for user %s", getattr(user, "pk", None), exc_info=True)
        return None
    finally:
        client.close()


def send_text(sender, *, bot: str, username: str, text: str, session_data: dict | None = None) -> dict:
    """Send ``text`` to PersonalID user ``username`` through chatbot ``bot``, as ``sender``.

    Returns OCS's response. Raises ``DeliveryError`` with a plain reason when it cannot.
    """
    if sender is None or not getattr(sender, "pk", None):
        raise DeliveryError("Sending needs a signed-in person whose Open Chat Studio connection it can use.")
    if not bot:
        raise DeliveryError("No chatbot was chosen to send it through.")
    client = OCSDataAccess(user=sender)
    try:
        if not client.check_token_valid():
            raise DeliveryError("Open Chat Studio is not connected for you — connect it, then try again.")
        return client.trigger_bot(
            identifier=username,
            platform=PLATFORM,
            experiment_id=bot,
            message_text=text,
            start_new_session=False,
            session_data=session_data or None,
        )
    except OCSAPIError as e:
        detail = (getattr(e, "detail", "") or "").strip()
        raise DeliveryError(
            f"Open Chat Studio refused it: {detail[:200]}" if detail else "Open Chat Studio refused the request."
        ) from e
    finally:
        client.close()
