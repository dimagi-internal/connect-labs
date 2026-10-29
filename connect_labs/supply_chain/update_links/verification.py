"""Proving that whoever opened an update link can read its recipient's PersonalID chat.

**What this proves, exactly.** A six-digit code is sent to the chat of the
PersonalID user the link was issued to (`UpdateLink.connect_username`), through
Open Chat Studio (delivery.py). Typing it back shows that this browser's user
could read that chat -- so held the registered device and the app -- at that
moment. The browser is then given a random secret (a cookie); the link's later
requests are "verified" while they carry it. It is the SMS-OTP shape over the
PersonalID channel, not a PersonalID sign-in: nothing here talks to connect-id.
docs/personalid-authorization.md §4 Stage 1.

**Sender.** Codes go out as the person who issued the link, with their stored
OCS connection, because that is the only kind of sender labs has. If their
connection lapses, codes stop (the person is told to ask the programme team) and
the issuer has to reconnect Open Chat Studio.

**Limits.** A code lives ten minutes and allows five tries; a link may be sent
five codes an hour and not two within thirty seconds. So guessing is bounded to
25 tries an hour per link against a million codes. Codes and device secrets are
stored only as keyed hashes.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from connect_labs.supply_chain.update_links import delivery
from connect_labs.supply_chain.update_links.models import LinkDevice, LinkVerification

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
CODE_LIFETIME = timedelta(minutes=10)
MAX_ATTEMPTS = 5
RESEND_AFTER = timedelta(seconds=30)
MAX_SENDS_PER_HOUR = 5
COOKIE_PREFIX = "ul_dev_"


class VerificationError(Exception):
    """Written to be shown to the person on the page, so it says nothing about why in detail."""


def cookie_name(link) -> str:
    return f"{COOKIE_PREFIX}{link.pk}"


def _key(purpose: str, link_id: int) -> bytes:
    return f"supply-update-link-{purpose}:{link_id}:{settings.SECRET_KEY}".encode()


def _hash(purpose: str, link_id: int, value: str) -> str:
    return hmac.new(_key(purpose, link_id), value.encode(), hashlib.sha256).hexdigest()


def bot_for(link) -> str:
    """The chatbot a code goes through: the one chosen at issue, else the one that sent the link, else the default."""
    return link.verify_bot or link.sent_via_bot or delivery.default_bot()


def can_send(link) -> bool:
    return bool(link.connect_username and link.issued_by_id and bot_for(link))


def start(link) -> None:
    """Send a fresh code to the recipient's chat. Raises VerificationError (with a plain reason)."""
    if not can_send(link):
        raise VerificationError("A code cannot be sent for this link. Ask the programme team that sent it to you.")
    now = timezone.now()
    recent = list(link.verifications.filter(created_at__gte=now - timedelta(hours=1)).order_by("-created_at"))
    if recent and now - recent[0].created_at < RESEND_AFTER:
        raise VerificationError("A code was just sent. Wait half a minute, then ask for another.")
    if len(recent) >= MAX_SENDS_PER_HOUR:
        raise VerificationError("Too many codes were asked for. Try again in an hour.")

    code = f"{secrets.randbelow(10**CODE_DIGITS):0{CODE_DIGITS}d}"
    verification = LinkVerification.objects.create(
        link=link, code_hash=_hash("code", link.pk, code), expires_at=now + CODE_LIFETIME
    )
    text = (
        f"Your code for {link.org.name} stock updates: {code}\n\n"
        "It works for 10 minutes. If you did not ask for it, ignore this message."
    )
    try:
        delivery.send_text(
            link.issued_by,
            bot=bot_for(link),
            username=link.connect_username,
            text=text,
            session_data={"supply_update_link_id": str(link.pk), "purpose": "verification_code"},
        )
    except delivery.DeliveryError as e:
        # The reason names OCS and consent; that is for the programme team, not
        # for the person on the page.
        logger.warning("verification code for link %s was not sent: %s", link.pk, e)
        verification.delete()
        raise VerificationError(
            "We could not send a code to your PersonalID chat. Check that you can receive messages in the "
            "Connect app, then try again; if it keeps failing, ask the programme team that sent the link."
        ) from e


def check(link, code: str) -> str:
    """Redeem a typed code. Returns the new device's raw secret; raises VerificationError.

    The attempt is counted and committed BEFORE any refusal is raised: raising inside the
    transaction would roll the count back, and wrong guesses would cost nothing.
    """
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    now = timezone.now()
    secret = None
    problem = None
    with transaction.atomic():
        verification = (
            LinkVerification.objects.select_for_update()
            .filter(link=link, consumed_at__isnull=True, expires_at__gt=now)
            .order_by("-created_at")
            .first()
        )
        if verification is None or verification.attempts >= MAX_ATTEMPTS:
            problem = "That code has expired or been used up. Ask for a new one."
        else:
            verification.attempts += 1
            good = len(code) == CODE_DIGITS and hmac.compare_digest(
                verification.code_hash, _hash("code", link.pk, code)
            )
            if good:
                verification.consumed_at = now
                verification.save(update_fields=["attempts", "consumed_at"])
                secret = secrets.token_urlsafe(32)
                LinkDevice.objects.create(
                    link=link, secret_hash=_hash("device", link.pk, secret), connect_username=link.connect_username
                )
            else:
                verification.save(update_fields=["attempts"])
                problem = "That is not the code. Check the message in your Connect app and try again."
    if problem:
        raise VerificationError(problem)
    return secret


def device_for(link, secret):
    """The verified device this cookie value belongs to, or None. Touches its last-used time."""
    if not secret or not isinstance(secret, str) or len(secret) > 128:
        return None
    digest = _hash("device", link.pk, secret)
    device = LinkDevice.objects.filter(link=link, secret_hash=digest).first()
    if device is None or not hmac.compare_digest(device.secret_hash, digest):
        return None
    # The link may have been re-pointed at somebody else since: a device
    # verified for another username does not carry over.
    if device.connect_username != link.connect_username:
        return None
    LinkDevice.objects.filter(pk=device.pk).update(last_used_at=timezone.now())
    return device
