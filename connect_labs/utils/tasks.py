import logging

from django.conf import settings
from django.core.mail import send_mail

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


@celery_app.task(bind=True, max_retries=MAX_RETRIES)
def send_mail_async(self, subject, message, recipient_list, from_email=None, html_message=None):
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
        raise self.retry(exc=exc, countdown=RETRY_BACKOFF_SECONDS * (2**self.request.retries))

    if not sent:
        # NotConfiguredEmailBackend returns 0 by design. Don't retry — the
        # environment will not become configured between attempts — but don't
        # let it pass for a delivery either.
        logger.warning(
            "Email reported 0 messages sent: subject=%r to=%r backend=%s",
            subject,
            recipient_list,
            settings.EMAIL_BACKEND,
        )
    return sent
