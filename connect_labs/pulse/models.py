"""Local storage for Connect Pulse — funder-facing service-delivery telemetry.

These are real labs-DB tables (unlike most labs apps, which proxy LabsRecords),
because the whole point is to hold a local, queryable mirror of what has flowed
into Connect so a dashboard never waits on the prod export API.

Design constraints that are load-bearing, not stylistic:

* **PulseEvent is PII-free by construction.** The Connect export carries real
  beneficiary names and phone numbers on ``entity_name`` and real FLW identities
  on ``user_data.name``/``phone``. None of that has a column here. Stripping at
  ingest rather than at render means a careless template can't leak it, and
  ``test_models.py`` asserts the field list so a future change can't quietly add
  one back.
* **Cursors are per (opportunity, endpoint).** Connect's export API paginates on
  ``last_id``, which makes it a change feed: store the high-water mark and the
  next poll returns only new rows.
* **Health is a first-class row, not a log line.** The failure mode that matters
  is ingest silently stopping while the screen keeps showing yesterday's numbers
  under a green LIVE badge. Something has to be queryable to prevent that.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import models
from django.utils import timezone

# Sentinel "due forever ago" for cursors that have never been polled.
_EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

# Poll cadence by how recently an opportunity produced work. Polling all ~494
# visible opps uniformly would be absurd; in practice ~14 are live at any time.
TIER_HOT = "hot"
TIER_WARM = "warm"
TIER_COLD = "cold"
TIER_DORMANT = "dormant"

TIER_CHOICES = [
    (TIER_HOT, "Hot — visit in the last 6h"),
    (TIER_WARM, "Warm — visit in the last 7d"),
    (TIER_COLD, "Cold — visit in the last 90d"),
    (TIER_DORMANT, "Dormant — nothing recent"),
]

TIER_INTERVALS_SECONDS = {
    TIER_HOT: 60,
    TIER_WARM: 15 * 60,
    TIER_COLD: 24 * 60 * 60,
    TIER_DORMANT: 7 * 24 * 60 * 60,
}


class PulseOpportunity(models.Model):
    """Cheap-tier mirror of an opportunity's display + rate metadata.

    Fed by ``/export/opp_org_program_list/``, which returns every visible opp
    *with* its lifetime ``visit_count`` in a single request — so the headline
    scale numbers cost essentially nothing to keep current.
    """

    opportunity_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=300, blank=True)
    org_slug = models.CharField(max_length=120, blank=True)
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    country = models.CharField(max_length=2, blank=True)
    service_slug = models.CharField(max_length=48, blank=True)

    is_active = models.BooleanField(default=False)
    end_date = models.DateField(null=True, blank=True)
    lifetime_visit_count = models.IntegerField(default=0)

    currency = models.CharField(max_length=8, blank=True)
    budget_per_visit = models.BigIntegerField(null=True, blank=True)
    total_budget = models.BigIntegerField(null=True, blank=True)

    # Measured USD actually accrued to the worker per approved unit of work.
    # Preferred over converting budget_per_visit, because it is what was really
    # paid; the two agree to within cents, which the ingest asserts.
    usd_per_service = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "pulse opportunities"

    def __str__(self) -> str:
        return f"{self.opportunity_id} · {self.name[:40]}"


class PulseEvent(models.Model):
    """One delivered service.

    NOTE: adding a column here is a privacy decision. See the module docstring —
    there is a test asserting exactly this field list.
    """

    connect_visit_id = models.BigIntegerField(unique=True, db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    org_slug = models.CharField(max_length=120, blank=True)

    # field_ts = when the service happened; sync_ts = when Connect received it.
    # These differ by a median of 9 minutes and a p90 of ~3 hours because the
    # work happens where there is no signal. Both matter: replay is paced on
    # field_ts, freshness is judged on sync_ts.
    field_ts = models.DateTimeField(db_index=True)
    sync_ts = models.DateTimeField(db_index=True)

    lat = models.FloatField(null=True, blank=True)
    lon = models.FloatField(null=True, blank=True)
    country = models.CharField(max_length=2, blank=True, db_index=True)

    status = models.CharField(max_length=32, db_index=True)
    flagged = models.BooleanField(default=False)
    flag_type = models.CharField(max_length=48, blank=True)
    review_status = models.CharField(max_length=24, blank=True)

    service_slug = models.CharField(max_length=48, blank=True)
    worker_hash = models.CharField(max_length=64, blank=True)
    usd_to_worker = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["field_ts", "opportunity_id"]),
            models.Index(fields=["sync_ts"]),
        ]

    def __str__(self) -> str:
        return f"visit {self.connect_visit_id} · {self.status}"


class PulseCursor(models.Model):
    """High-water mark for one (opportunity, endpoint) export stream."""

    opportunity_id = models.IntegerField(db_index=True)
    endpoint = models.CharField(max_length=48)

    last_id = models.BigIntegerField(null=True, blank=True)
    newest_sync_ts = models.DateTimeField(null=True, blank=True)
    last_polled_at = models.DateTimeField(null=True, blank=True)

    tier = models.CharField(max_length=12, choices=TIER_CHOICES, default=TIER_COLD, db_index=True)

    # Backfill walks *backwards* from the oldest row we hold; tailing walks
    # forwards from last_id. Keeping them separate means a slow backfill can
    # never stall the live tail.
    backfill_complete = models.BooleanField(default=False)
    backfill_oldest_id = models.BigIntegerField(null=True, blank=True)

    consecutive_failures = models.IntegerField(default=0)
    last_error = models.TextField(blank=True)

    class Meta:
        unique_together = [("opportunity_id", "endpoint")]

    def __str__(self) -> str:
        return f"{self.opportunity_id}/{self.endpoint} @ {self.last_id}"

    @property
    def due_at(self):
        """When this cursor next wants polling.

        A never-polled cursor is due at the epoch, not at ``now()``. Returning
        ``now()`` here looks equivalent but is not: callers capture their own
        ``now`` first and compare against this, so a freshly-evaluated ``now()``
        is always a few microseconds *later* and the cursor is never due. That
        bug polls nothing, forever, while looking perfectly healthy.
        """
        if self.last_polled_at is None:
            return _EPOCH
        return self.last_polled_at + timedelta(seconds=TIER_INTERVALS_SECONDS[self.tier])

    def is_due(self, now=None) -> bool:
        return (now or timezone.now()) >= self.due_at


class PulseRollup(models.Model):
    """Hourly aggregate, so no card ever scans raw events."""

    bucket_hour = models.DateTimeField(db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    status = models.CharField(max_length=32)

    n = models.IntegerField(default=0)
    flagged_n = models.IntegerField(default=0)
    usd = models.DecimalField(max_digits=14, decimal_places=4, default=0)

    class Meta:
        unique_together = [("bucket_hour", "opportunity_id", "status")]
        indexes = [models.Index(fields=["bucket_hour", "status"])]


class PulseScalar(models.Model):
    """All-time figures refreshed on the cheap tier (lifetime visits, opp counts…)."""

    key = models.CharField(max_length=64, unique=True)
    value = models.JSONField(default=dict)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return self.key


class PulseIngestHealth(models.Model):
    """Per-tier ingest health.

    Exists so the read API can refuse to claim LIVE when ingest is actually
    dead. The poller runs on a user's refreshable Connect token, and refresh
    tokens have an absolute lifetime — so "stopped working days ago" is a real
    steady state that must be visible rather than inferred.
    """

    tier = models.CharField(max_length=24, unique=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    consecutive_failures = models.IntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.tier}: {'ok' if self.is_healthy else 'UNHEALTHY'}"

    @property
    def is_healthy(self) -> bool:
        """Healthy = a success recently enough to trust what's on screen.

        Deliberately generous (6x the hot interval) so a single transient
        failure doesn't red-flag a wall display, but bounded so a dead token
        can't masquerade as live for a whole afternoon.
        """
        if self.last_success_at is None:
            return False
        age = (timezone.now() - self.last_success_at).total_seconds()
        return age < 6 * TIER_INTERVALS_SECONDS[TIER_HOT] and self.consecutive_failures < 5


class PulsePublicToken(models.Model):
    """A revocable, individually-scoped public link.

    Individually scoped on purpose: a link handed to one funder can be killed
    without breaking anyone else's. A single shared public URL would be a
    one-way door.
    """

    token = models.CharField(max_length=64, unique=True, db_index=True)
    label = models.CharField(max_length=200, blank=True)
    layout_slug = models.CharField(max_length=64, default="nightmap")

    # If False the page renders partner orgs as descriptors ("a partner in
    # northern Nigeria") instead of names. Partner volumes and per-service
    # rates are commercial information; this is the escape hatch if one objects.
    show_partner_names = models.BooleanField(default=True)

    revoked = models.BooleanField(default=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="pulse_tokens"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_viewed_at = models.DateTimeField(null=True, blank=True)
    view_count = models.IntegerField(default=0)

    def __str__(self) -> str:
        return f"{self.label or self.token[:8]}{' (revoked)' if self.revoked else ''}"

    @property
    def is_usable(self) -> bool:
        return not self.revoked
