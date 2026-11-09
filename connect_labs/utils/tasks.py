import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail

from config import celery_app

# Celery autodiscovery only imports <app>.tasks — importing here registers the
# analytics sender task with the worker (it lives in server_analytics.py).
from connect_labs.utils.server_analytics import send_event_task  # noqa: E402,F401  isort:skip

logger = logging.getLogger(__name__)

# SES throttles (labs is capped at 1 message/sec even with production access)
# and occasionally returns transient 5xx. Those deserve a retry. A rejected
# sender or suppressed recipient does not, but telling them apart reliably means
# reaching for botocore exception types, so retry broadly with a short ceiling
# and log every attempt.
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 10
# How long a recorded message waits for a worker with mail configured (a deploy in progress).
NOT_CONFIGURED_RETRY_SECONDS = 120


@celery_app.task(bind=True, max_retries=MAX_RETRIES)
def send_mail_async(
    self,
    subject,
    message,
    recipient_list,
    from_email=None,
    html_message=None,
    reply_to=None,
    cc=None,
    sent_message_id=None,
):
    """
    Deliver one email.

    Prefer ``connect_labs.utils.email.send_labs_email``, which queues this task —
    calling ``send_mail`` inline puts SES latency on the request path.

    ``from_email`` resolves at call time rather than import time. It previously
    defaulted to ``settings.DEFAULT_FROM_EMAIL`` in the signature, which froze
    the value when the module was first imported, making the sender impossible
    to override per environment (or in a test) once the worker was warm.
    """
    sender = from_email or settings.DEFAULT_FROM_EMAIL

    try:
        if reply_to or cc:
            # Replies go to whoever wrote the message, not to labs' own sender.
            email = EmailMultiAlternatives(
                subject=subject,
                body=message,
                from_email=sender,
                to=recipient_list,
                cc=list(cc or []),
                reply_to=list(reply_to or []),
            )
            if html_message:
                email.attach_alternative(html_message, "text/html")
            sent = email.send()
        else:
            sent = send_mail(
                subject=subject,
                message=message,
                recipient_list=recipient_list,
                from_email=sender,
                html_message=html_message,
            )
    except Exception as exc:
        logger.exception(
            "Email send failed (attempt %s/%s): subject=%r to=%r",
            self.request.retries + 1,
            MAX_RETRIES + 1,
            subject,
            recipient_list,
        )
        if sent_message_id and self.request.retries >= MAX_RETRIES:
            _report(sent_message_id, delivered=False, failed=True)
        raise self.retry(exc=exc, countdown=RETRY_BACKOFF_SECONDS * (2**self.request.retries))

    if not sent:
        # NotConfiguredEmailBackend returns 0 by design, so nothing was delivered.
        logger.warning(
            "Email reported 0 messages sent: subject=%r to=%r backend=%s",
            subject,
            recipient_list,
            settings.EMAIL_BACKEND,
        )
        if sent_message_id and self.request.retries < MAX_RETRIES:
            # A recorded message was queued by a web task that had mail on: a
            # worker without it is most likely one a deploy has not replaced yet.
            # Hold it for a later worker rather than calling it not sent.
            raise self.retry(countdown=NOT_CONFIGURED_RETRY_SECONDS)
    if sent_message_id:
        _report(sent_message_id, delivered=bool(sent), failed=False)
    return sent


def _report(sent_message_id, *, delivered: bool, failed: bool) -> None:
    """Tell communications what became of a message it recorded, as its own retried task.

    Separate from this one, so a database that is unavailable now delays the
    record (and its follow-up) instead of losing it -- and retrying it never
    resends the email. If even queueing that fails, the reconcile job finds the
    message still waiting and marks its outcome unknown for a person to settle.
    """
    try:
        from connect_labs.communications.tasks import record_delivery

        record_delivery.delay(sent_message_id, delivered, failed)
    except Exception:  # noqa: BLE001 -- the email's fate is decided; reconcile settles a lost record
        logger.exception("Could not queue the delivery record of communications message %s", sent_message_id)
