"""The embed registry and the sessions it issues. Labs DB; nothing here reaches prod."""

from __future__ import annotations

import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

#: An embed session never outlives a working day, whatever a client asks for.
MAX_SESSION_SECONDS = 8 * 3600

_ORIGIN = re.compile(r"^https://[a-z0-9.-]+(:\d{1,5})?$")
_LOCAL_ORIGIN = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d{1,5})?$")


def valid_origin(origin: str) -> bool:
    """An exact origin, no path or wildcard: it becomes a ``frame-ancestors`` source.
    Plain http only for a local address, and only while DEBUG."""
    if not isinstance(origin, str):
        return False
    if _ORIGIN.match(origin):
        return True
    return bool(settings.DEBUG and _LOCAL_ORIGIN.match(origin))


class EmbedClient(models.Model):
    """A system allowed to frame labs, and what it may show.

    Its keys are public (a JWKS, inline or at ``jwks_uri``): there is no shared secret,
    because with one labs (or anyone who reads labs' copy) could sign as the client.
    """

    client_id = models.SlugField(max_length=64, unique=True, help_text="The assertion's iss, e.g. 'connect'.")
    name = models.CharField(max_length=200)
    active = models.BooleanField(default=True, help_text="Off stops every session it issued, on their next request.")
    identity_authority = models.BooleanField(
        default=False,
        help_text=(
            "May assert ANY labs user. Only for the system labs already trusts to say who people "
            "are (Connect, labs' identity provider). Staff only."
        ),
    )
    jwks_uri = models.URLField(blank=True, help_text="https URL of the client's JWKS.")
    jwks = models.JSONField(null=True, blank=True, help_text='Or the JWKS itself: {"keys": [...]}, public keys only.')
    allowed_origins = models.JSONField(
        default=list, blank=True, help_text='Origins that may frame labs: ["https://connect.dimagi.com"].'
    )
    resources = models.JSONField(
        default=list,
        blank=True,
        help_text='What it may show: [{"kind": "workflow", "id": 123, "opportunity_id": 456}].',
    )
    session_seconds = models.PositiveIntegerField(default=3600)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.name} ({self.client_id})"

    def clean(self):
        from connect_labs.embed.kinds import get_kind

        if bool(self.jwks_uri) == bool(self.jwks):
            raise ValidationError("Give either a JWKS URL or an inline JWKS, not both or neither.")
        if self.jwks_uri and not self.jwks_uri.startswith("https://"):
            raise ValidationError({"jwks_uri": "The JWKS URL must be https."})
        if self.jwks is not None and not isinstance((self.jwks or {}).get("keys"), list):
            raise ValidationError({"jwks": 'An inline JWKS is {"keys": [...]}.'})
        if not isinstance(self.allowed_origins, list) or not all(valid_origin(o) for o in self.allowed_origins):
            raise ValidationError({"allowed_origins": "A list of exact origins, e.g. https://connect.dimagi.com."})
        if not isinstance(self.resources, list):
            raise ValidationError({"resources": "A list of resources."})
        for entry in self.resources:
            if not isinstance(entry, dict):
                raise ValidationError({"resources": "Each resource is an object."})
            kind = get_kind(entry.get("kind"))
            if kind is None:
                raise ValidationError({"resources": f"No embeddable kind {entry.get('kind')!r}."})
            kind.validate_entry(entry)
        if not 60 <= self.session_seconds <= MAX_SESSION_SECONDS:
            raise ValidationError({"session_seconds": f"Between 60 and {MAX_SESSION_SECONDS} seconds."})

    def resource_entry(self, kind: str, resource_id: str) -> dict | None:
        """The registered entry for ``kind``/``resource_id``, or None if it may not show it."""
        for entry in self.resources or []:
            if isinstance(entry, dict) and entry.get("kind") == kind and str(entry.get("id")) == str(resource_id):
                return entry
        return None


class EmbedSession(models.Model):
    """One embed of one resource for one person, from bootstrap token to expiry.

    Only checksums are stored. The bootstrap token works once, within its window; the
    session token rides in a partitioned cookie and is checked on every request; the
    frame document is drawn once per session (``drawn_at``).
    ``state`` is the kind's own (a workflow's: the run the frame showed).
    """

    client = models.ForeignKey(EmbedClient, on_delete=models.CASCADE, related_name="sessions")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    kind = models.CharField(max_length=40)
    resource_id = models.CharField(max_length=64)
    entry = models.JSONField(default=dict)
    state = models.JSONField(default=dict, blank=True)
    bootstrap_checksum = models.CharField(max_length=64, unique=True)
    bootstrap_expires_at = models.DateTimeField()
    started_at = models.DateTimeField(null=True, blank=True)
    #: When the frame document was drawn for this session. Once only: a session serves the
    #: reads of the ONE document it drew, and every frame load needs a fresh token from the
    #: embedder, so a frame never shows an earlier person to whoever is signed in now.
    drawn_at = models.DateTimeField(null=True, blank=True)
    session_checksum = models.CharField(max_length=64, unique=True, null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self) -> str:
        return f"{self.client.client_id}: {self.kind}/{self.resource_id} for {self.user}"

    @property
    def live(self) -> bool:
        return (
            self.started_at is not None
            and self.revoked_at is None
            and self.expires_at is not None
            and self.expires_at > timezone.now()
            and self.client.active
        )
