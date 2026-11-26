"""Connect Web's own records: the pool of test workers, who has which one checked out, and the
Formplayer form sessions labs opened.

Secrets (backup code, PIN, PersonalID password, every token) live only in `EncryptedTextField`s on
`PoolWorker` and never appear in a repr, a str, an admin form or a log line. They are the
*worker's* (the phone's), not a checkout's, so they survive a release and the next checkout signs
in with the saved password instead of a full recovery. Read the token caches only through
`service`, which is the one place that decides when a cached token is still good.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from connect_labs.utils.encrypted_fields import EncryptedTextField


class PoolWorker(models.Model):
    """A Connect test worker labs users can act as: a +7426 demo number with a known backup code."""

    phone_number = models.CharField(max_length=32, unique=True, help_text="E.164, a +7426 demo number.")
    display_name = models.CharField(max_length=255)
    backup_code = EncryptedTextField(help_text="PersonalID backup code (encrypted).")
    pin = EncryptedTextField(blank=True, default="", help_text="Device PIN (encrypted; stored, not used yet).")
    personalid_username = models.CharField(
        max_length=150,
        blank=True,
        default="",
        db_index=True,
        help_text="Learned at first sign-in. The same key as workers.ConnectWorker.username.",
    )
    device_id = models.UUIDField(
        default=uuid.uuid4, unique=True, editable=False, help_text="Stable cc_device_id labs signs in with."
    )
    active = models.BooleanField(default=True)
    #: Set when PersonalID rejected the backup code: sign-in stops until an admin saves a new one,
    #: so a wrong code is never tried again into a lockout.
    sign_in_blocked_reason = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # The worker's sign-in, kept across checkouts (see the module docstring). Only `service`
    # reads and writes these.
    personalid_password = EncryptedTextField(blank=True, default="")
    access_token = EncryptedTextField(blank=True, default="")
    access_token_expires_at = models.DateTimeField(null=True, blank=True)
    #: JSON ``{"<hq host>/<domain>": {"token": ..., "expires_at": "<iso>"}}``, encrypted as a whole.
    #: Written only under ``select_for_update`` with a re-read (`service.hq_token`), so two domains'
    #: tokens fetched at once never overwrite each other.
    hq_tokens = EncryptedTextField(blank=True, default="")

    class Meta:
        ordering = ["display_name", "pk"]

    def __str__(self):
        return self.display_name

    def __repr__(self):
        return f"<PoolWorker {self.pk}: {self.display_name}>"

    @property
    def connect_worker(self):
        """Labs' record of this worker (`workers.ConnectWorker`), once both know the PersonalID username."""
        if not self.personalid_username:
            return None
        from connect_labs.workers.models import ConnectWorker

        return ConnectWorker.objects.filter(username__iexact=self.personalid_username).first()

    def active_session(self) -> WorkerSession | None:
        return self.sessions.filter(ended_at__isnull=True).select_related("user").first()

    # The HQ token cache, as a dict. Only `service` should call these.
    def get_hq_tokens(self) -> dict[str, dict]:
        if not self.hq_tokens:
            return {}
        try:
            value = json.loads(self.hq_tokens)
        except ValueError:
            return {}
        return value if isinstance(value, dict) else {}

    def set_hq_token(self, key: str, token: str, expires_at: datetime) -> None:
        tokens = self.get_hq_tokens()
        tokens[key] = {"token": token, "expires_at": expires_at.isoformat()}
        self.hq_tokens = json.dumps(tokens)

    def clear_tokens(self) -> None:
        self.access_token = ""
        self.access_token_expires_at = None
        self.hq_tokens = ""


class WorkerSession(models.Model):
    """One labs user acting as one pool worker. At most one open session per worker and per user."""

    pool_worker = models.ForeignKey(PoolWorker, on_delete=models.CASCADE, related_name="sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="connect_web_sessions")
    started_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["pool_worker"],
                condition=Q(ended_at__isnull=True),
                name="connect_web_one_open_session_per_worker",
            ),
            models.UniqueConstraint(
                fields=["user"], condition=Q(ended_at__isnull=True), name="connect_web_one_open_session_per_user"
            ),
        ]

    def __str__(self):
        return f"{self.user} as {self.pool_worker}"

    def __repr__(self):
        state = "open" if self.ended_at is None else "ended"
        return f"<WorkerSession {self.pk}: user={self.user_id} worker={self.pool_worker_id} {state}>"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class FormSession(models.Model):
    """A Formplayer form session labs opened for one checkout, opportunity and app kind.

    An answer, repeat, language change or submit names a form session id; it is accepted only if
    a navigation in the same checkout opened it for the same opportunity and kind
    (`api._EngineView.form_session`). Kept in the database — not the Django session, which every
    request re-saves (a second tab could write back a stale copy), and not the cache, whose
    outage must never stop a form.
    """

    worker_session = models.ForeignKey(WorkerSession, on_delete=models.CASCADE, related_name="form_sessions")
    session_id = models.CharField(max_length=64)
    opportunity_id = models.CharField(max_length=64)
    app_kind = models.CharField(max_length=16)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["worker_session", "session_id"], name="connect_web_one_form_session_row")
        ]

    def __repr__(self):
        return f"<FormSession {self.pk}: worker_session={self.worker_session_id} {self.app_kind}>"
