from django.conf import settings
from django.contrib.sites.models import Site
from django.urls import reverse
from twilio.rest import Client


def _build_absolute_uri(path):
    """Replacement for allauth.utils.build_absolute_uri (removed during labs simplification)."""
    protocol = "https"
    try:
        site = Site.objects.get_current()
        domain = site.domain
    except Exception:
        domain = "localhost"
    return f"{protocol}://{domain}{path}"


class SMSException(Exception):
    pass


def send_sms(to, body):
    if not (settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN and settings.TWILIO_MESSAGING_SERVICE):
        raise SMSException("Twilio credentials not provided")
    client = Client(settings.TWILIO_ACCOUNT_SID, settings.TWILIO_AUTH_TOKEN)
    sender = get_sms_sender(to)
    return client.messages.create(
        body=body,
        to=to,
        from_=sender,
        messaging_service_sid=settings.TWILIO_MESSAGING_SERVICE,
        status_callback=_build_absolute_uri(reverse("users:sms_status_callback")),
    )


def get_sms_sender(number):
    SMS_SENDERS = {"+265": "ConnectID", "+258": "ConnectID", "+232": "ConnectID", "+44": "ConnectID"}
    for code, sender in SMS_SENDERS.items():
        if number.startswith(code):
            return sender
    return None
