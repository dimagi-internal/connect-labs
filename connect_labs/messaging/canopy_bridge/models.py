"""What the canopy bridge keeps: labs' side of each Connect Worker's anchor in canopy.

Labs is the system of record. Canopy holds an anchor (the contact, keyed ``personalid:<username>``),
a pointer (``labs-worker://<username>``) and a small profile cache labs keeps current; these rows
are labs' record of what it last told canopy.
"""

from __future__ import annotations

from django.db import models

from connect_labs.workers.models import ConnectWorker


class PushStatus(models.TextChoices):
    PENDING = "pending"
    CREATED = "created"
    UPDATED = "updated"
    STALE = "stale"
    REFUSED = "refused"
    ERROR = "error"


class ProfilePush(models.Model):
    """The last profile labs pushed to canopy for one Connect Worker (S2), and whether it is due again.

    ``dirty`` is set when something the profile is made of may have changed (a stage, a channel, the
    worker's language); the sweep recomputes the profile and pushes only if its hash changed.
    """

    worker = models.OneToOneField(ConnectWorker, on_delete=models.CASCADE, related_name="canopy_profile_push")
    profile_hash = models.CharField(max_length=64, blank=True, default="")
    pushed_version = models.BigIntegerField(default=0)
    pushed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=PushStatus, default=PushStatus.PENDING)
    reason = models.CharField(max_length=300, blank=True, default="")
    dirty = models.BooleanField(default=True, db_index=True)
    attempts = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"profile push for {self.worker.username}: {self.status}"
