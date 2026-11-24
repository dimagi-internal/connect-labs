"""Labs Admin › PersonalID messaging: the client id and secret connect-id issued labs.

Labs is a PersonalID message server (`connect_labs/messaging/README.md`). connect-id issues it a
client id and secret; labs sends with them (HTTP Basic) and checks connect-id's signature on what
it posts with the secret. They are entered here, by Dimagi staff, instead of through Secrets
Manager, a task-definition change and a deploy. The env settings stay as the fallback
(`messaging.credentials.personalid_credentials`).

The secret is write-only: never rendered back, never logged, never put in a message. The page
says when it was set and by whom, which source is in force, the four URLs connect-id needs, and
when labs last accepted a signed post from connect-id, so an admin can see the wiring work.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.contrib import messages
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.labs.view_mixins import AdminRequiredMixin
from connect_labs.messaging import credentials
from connect_labs.messaging.models import PersonalIDCredentials, TransportEvent, TransportKind
from connect_labs.utils.dimagi_user import is_dimagi_staff
from connect_labs.utils.encrypted_fields import EncryptionKeyError

logger = logging.getLogger(__name__)

#: The endpoints connect-id is given, by its field names (`messaging/personalid_urls.py`).
ENDPOINTS = (
    ("key_url", "messaging_personalid:key"),
    ("delivery_url", "messaging_personalid:delivery"),
    ("consent_url", "messaging_personalid:consent"),
    ("callback_url", "messaging_personalid:callback"),
)

SOURCE_LABELS = {
    credentials.DB: "Labs Admin (this page)",
    credentials.ENV: "Environment settings (Secrets Manager)",
    credentials.NONE: "None: labs cannot send, and refuses every post from connect-id",
}


def can_change(user) -> bool:
    return bool(user and user.is_authenticated and is_dimagi_staff(user))


def endpoint_urls(request) -> list[dict]:
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/") or request.build_absolute_uri("/").rstrip("/")
    return [{"field": field, "url": base + reverse(name)} for field, name in ENDPOINTS]


def _stored():
    """The row, and whether its secret could be read. The secret itself is never returned."""
    try:
        row = PersonalIDCredentials.load()
    except EncryptionKeyError:
        return (
            PersonalIDCredentials.objects.filter(pk=PersonalIDCredentials.SINGLETON_PK)
            .only("client_id", "updated_at", "updated_by")
            .first(),
            False,
        )
    return row, True


class PersonalIDMessagingView(AdminRequiredMixin, TemplateView):
    template_name = "labs/admin/personalid_messaging.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        row, readable = _stored()
        active = credentials.personalid_credentials()
        last = TransportEvent.objects.filter(transport=TransportKind.PERSONALID).order_by("-received_at").first()
        context.update(
            stored_client_id=row.client_id if row else "",
            secret_is_set=bool(row and readable and row.secret),
            secret_unreadable=bool(row and not readable),
            updated_at=row.updated_at if row else None,
            updated_by=row.updated_by if row else None,
            source=active.source,
            source_label=SOURCE_LABELS[active.source],
            active_client_id=active.client_id,
            env_is_set=bool(
                getattr(settings, "PERSONALID_MESSAGING_CLIENT_ID", "")
                and getattr(settings, "PERSONALID_MESSAGING_CLIENT_SECRET", "")
            ),
            endpoints=endpoint_urls(self.request),
            last_event=last,
            server_url=getattr(settings, "PERSONALID_SERVER_URL", ""),
            messaging_enabled=bool(getattr(settings, "MESSAGING_ENABLED", False)),
            can_change=can_change(self.request.user),
        )
        return context

    def post(self, request, *args, **kwargs):
        here = reverse("labs_admin:personalid_messaging")
        if not can_change(request.user):
            messages.error(request, "Only Dimagi staff can change the PersonalID messaging credentials.")
            return redirect(here)
        if request.POST.get("clear"):
            PersonalIDCredentials.objects.filter(pk=PersonalIDCredentials.SINGLETON_PK).delete()
            logger.info("messaging: PersonalID credentials cleared at Labs Admin by user %s", request.user.pk)
            messages.success(request, "Cleared. Labs now uses the environment settings, if any are set.")
            return redirect(here)

        client_id = (request.POST.get("client_id") or "").strip()
        new_secret = (request.POST.get("secret") or "").strip()
        row, readable = _stored()
        has_secret = bool(row and readable and row.secret)
        if not client_id:
            messages.error(request, "Enter the client id connect-id issued.")
            return redirect(here)
        if not new_secret and not has_secret:
            messages.error(request, "Enter the secret connect-id issued.")
            return redirect(here)
        if row is None or not readable:
            row = PersonalIDCredentials(pk=PersonalIDCredentials.SINGLETON_PK)
        row.client_id = client_id
        if new_secret:
            row.secret = new_secret
        row.updated_by = request.user
        row.save()
        logger.info(
            "messaging: PersonalID credentials saved at Labs Admin by user %s (secret %s)",
            request.user.pk,
            "replaced" if new_secret else "kept",
        )
        messages.success(request, "Saved. Labs uses these now, for sending and for checking connect-id's signature.")
        return redirect(here)
