"""
Outbound email for labs.

Labs sends mail through Amazon SES (via django-anymail) when — and only when —
the environment is actually configured for it. Until then it uses
:class:`NotConfiguredEmailBackend`, which **fails loudly**.

That last part is the whole point of this module. The previous state of the
world was ``EMAIL_BACKEND = console.EmailBackend``: every ``send_mail()`` wrote
the message to stdout and returned ``1``, i.e. "I sent one email." Any feature
built on top of that would have looked fine in code review, fine in tests, and
fine in the logs, while silently delivering nothing. A backend that reports
success for mail it discarded is worse than no backend at all, because it
removes the signal that would have told you to finish the setup (#1039).

So the contract here is:

* Not configured  -> log a WARNING naming the subject + recipients, return 0.
                     Callers that check the return value learn the truth, and
                     the log line is greppable in CloudWatch.
* Configured      -> real SES send, from a verified labs-controlled domain,
                     tagged with a configuration set so bounces and complaints
                     land on an SNS topic.

Sending is always dispatched to Celery via :func:`send_labs_email`. Nothing
should call ``django.core.mail.send_mail`` directly from a request path — the
web tier is a single 1 vCPU task and ``EMAIL_TIMEOUT`` is 5s, so a slow SES
call would eat a worker thread (see the 2026-07-29 incident, #1037).
"""

import logging

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.core.mail.message import sanitize_address

logger = logging.getLogger(__name__)


def email_enabled() -> bool:
    """True when this environment is wired to actually deliver mail."""
    return bool(getattr(settings, "LABS_EMAIL_ENABLED", False))


def sending_domain() -> str:
    """The SES-verified domain labs is allowed to send from ('' if unset)."""
    return (getattr(settings, "LABS_EMAIL_DOMAIN", "") or "").strip().lower()


def _address_domain(address: str) -> str:
    """Extract the domain from a possibly display-name-wrapped address."""
    # sanitize_address normalises 'Name <a@b.com>' -> 'a@b.com'.
    try:
        addr = sanitize_address(address, "utf-8")
    except Exception:
        addr = address
    _, _, domain = addr.rpartition("@")
    return domain.strip().strip(">").lower()


def check_email_config() -> list[str]:
    """
    Return a list of human-readable configuration problems (empty == healthy).

    Used by the ``send_test_email`` management command as a preflight and by the
    Django system check below, so a misconfigured deploy is caught before a
    feature depends on it rather than after.
    """
    problems: list[str] = []

    if not email_enabled():
        problems.append(
            "LABS_EMAIL_ENABLED is false — mail is routed to NotConfiguredEmailBackend and will NOT be delivered."
        )
        return problems

    domain = sending_domain()
    if not domain:
        problems.append("LABS_EMAIL_ENABLED is true but LABS_EMAIL_DOMAIN is empty — nothing verifies the sender.")

    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "")
    from_domain = _address_domain(from_email)
    if domain and from_domain != domain and not from_domain.endswith(f".{domain}"):
        # This is the trap called out in #1039: the inherited default is
        # 'Connect <noreply@commcare-connect.org>', a domain labs does not
        # control and SES has not verified. Sending from it fails at best and
        # burns someone else's reputation at worst.
        problems.append(
            f"DEFAULT_FROM_EMAIL ({from_email!r}) is on domain {from_domain!r}, "
            f"which is not the SES-verified sending domain {domain!r}."
        )

    backend = getattr(settings, "EMAIL_BACKEND", "")
    if "anymail" not in backend and "console" not in backend:
        problems.append(f"EMAIL_BACKEND is {backend!r}, which is not the SES backend.")

    return problems


class NotConfiguredEmailBackend(BaseEmailBackend):
    """
    Backend for environments that are not wired for real delivery.

    Logs each message at WARNING (subject + recipient count + recipients) and
    reports **0** messages sent, so neither the logs nor the return value can be
    mistaken for a successful delivery. Contrast with Django's console backend,
    which returns ``len(messages)``.
    """

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        for message in email_messages:
            logger.warning(
                "Email NOT sent (labs outbound email is not configured): "
                "subject=%r from=%r to=%r. Set LABS_EMAIL_ENABLED=True with a "
                "verified SES domain to enable delivery.",
                message.subject,
                message.from_email,
                list(message.recipients()),
            )
        # Deliberately 0, not len(email_messages): nothing was delivered.
        return 0


def send_labs_email(subject, message, recipient_list, html_message=None, from_email=None):
    """
    Queue an email for delivery. The one front door for labs features.

    Always returns immediately — the actual send happens in the Celery worker
    (``connect_labs.utils.tasks.send_mail_async``), keeping SES latency off the
    request path. Returns the Celery ``AsyncResult``, or ``None`` when there are
    no recipients to send to.
    """
    recipients = [r for r in (recipient_list or []) if r]
    if not recipients:
        logger.debug("send_labs_email called with no recipients (subject=%r); nothing queued.", subject)
        return None

    # Imported here rather than at module scope: utils.tasks imports Celery app
    # state, and this module is imported from settings-adjacent code paths.
    from connect_labs.utils.tasks import send_mail_async

    return send_mail_async.delay(
        subject=subject,
        message=message,
        recipient_list=recipients,
        from_email=from_email,
        html_message=html_message,
    )


def check_labs_email(app_configs, **kwargs):
    """
    Django system check, registered from ``connect_labs.web.apps``.

    Only fires once someone has turned delivery ON (``LABS_EMAIL_ENABLED``).
    A disabled environment is a valid state — local dev and CI both live there —
    so staying quiet keeps ``manage.py`` output clean; the loud signal in that
    case is :class:`NotConfiguredEmailBackend` at send time. But a deploy that
    claims to send mail and is misconfigured should fail its checks rather than
    discover it against a real recipient.
    """
    from django.core.checks import Error

    if not email_enabled():
        return []
    return [Error(problem, id="connect_labs.E001") for problem in check_email_config()]
