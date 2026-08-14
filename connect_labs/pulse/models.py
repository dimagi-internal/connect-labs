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

# How often each INGEST tier's beat actually fires. Distinct from
# TIER_INTERVALS_SECONDS below, which is the per-opportunity CURSOR cadence --
# they were conflated, and health was judged against the wrong one.
# Keep in step with CELERY_BEAT_SCHEDULE.
TIER_CADENCE_SECONDS = {
    "tail": 60,
    "works": 2 * 60,
    "cheap": 15 * 60,
}

TIER_INTERVALS_SECONDS = {
    # 15s, not 60. This is the floor on how fresh the live view can be: a hot
    # opportunity is only re-polled once its cursor is due, so at 60 a visit
    # could sit for a minute before the map knew about it. The live view's whole
    # claim is that it is happening now.
    TIER_HOT: 15,
    TIER_WARM: 15 * 60,
    TIER_COLD: 24 * 60 * 60,
    TIER_DORMANT: 7 * 24 * 60 * 60,
}


class PulseProgram(models.Model):
    """Connect's own programmes — the grouping a funder actually asks about.

    Arrives in the same ``opp_org_program_list`` response as the opportunities,
    and was previously read only to derive an org slug. It carries two things
    worth keeping:

    ``name`` is presentable as-is ("ECD Nigeria 2025"), so a programme filter
    needs no labels invented in labs.

    ``delivery_type`` is Connect's service taxonomy — ``ecd``, ``chc``, ``mbw``,
    ``readers``, ``kmc``. Pulse used to infer that by regex over opportunity
    *names*, which had no pattern for ``ecd`` and so filed 163,473 Early
    Childhood Development visits — the largest delivery type on the platform —
    under the generic "Service delivery" bucket. Taking the field Connect
    already publishes removes the guess.
    """

    program_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=300, blank=True)
    delivery_type = models.CharField(max_length=48, blank=True, db_index=True)
    org_slug = models.CharField(max_length=120, blank=True)
    currency = models.CharField(max_length=8, blank=True)

    # Programmes named as tests are excluded from the filter menu. Computed at
    # ingest rather than at query time so the rule is applied in one place and
    # is inspectable in the DB.
    is_test = models.BooleanField(default=False, db_index=True)

    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.name or self.program_id} ({self.delivery_type or '—'})"


class PulseOrganization(models.Model):
    """The delivery partners — who actually runs the work.

    Arrives in the same ``opp_org_program_list`` response as the opportunities
    and programmes, which published ``["id", "slug", "name", "funder"]`` per org
    all along. Pulse read ``len(organizations)`` for a headline count and threw
    the rows away, so the only org identity anywhere downstream was the
    ``org_slug`` denormalised onto events and works.

    That is the same omission ``PulseProgram`` documents above, and it has the
    same consequence: a slug is not a name. Drilling into "connect-nigeria"
    puts an internal identifier on a funder's screen, and title-casing it in
    labs would be inventing a label -- the failure the ``SERVICE_LABELS``
    comment exists to prevent. Connect knows these names; we just have to keep
    them.

    ``funder`` is kept because on a funder-facing display "who else backs this
    partner" is a question the screen can now answer for free.
    """

    # Keyed on slug, not on Connect's numeric id: the slug is what ingest
    # denormalises onto every event and work row, so it is the only join key
    # pulse actually uses. Carrying the numeric id as well would be an unused
    # unique column that ingest could crash on -- two orgs with no id upstream
    # would collide on the same default.
    slug = models.CharField(max_length=120, unique=True, db_index=True)
    name = models.CharField(max_length=300, blank=True)
    funder_slug = models.CharField(max_length=120, blank=True)

    updated_at = models.DateTimeField(auto_now=True)

    # A row existing here means Connect named this partner to us. Most partners
    # have no row -- the export scopes its organizations list to the poller's
    # memberships -- and are represented by ``api._UnnamedOrg``, which sets this
    # False. Declared here so both stand-ins answer the same question and the
    # display can mark the difference. Not a field: it is true of the class.
    named = True

    def __str__(self) -> str:
        return f"{self.name or self.slug}"

    @property
    def display_name(self) -> str:
        """Never blank: falls back to the slug rather than rendering nothing.

        A partner with no name upstream should read as its identifier, which is
        at least true, instead of as an empty cell that looks like a bug.
        """
        return self.name or self.slug


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
    # Indexed: the delivery-type filter selects on it across every spine,
    # and the type menu groups by it.
    service_slug = models.CharField(max_length=48, blank=True, db_index=True)

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
    # Indexed for the same reason program_id is: the partner filter selects on
    # it directly and the partner menu groups by it, both over the whole table.
    org_slug = models.CharField(max_length=120, blank=True, db_index=True)

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

    # Indexed: the delivery-type filter selects on it across every spine,
    # and the type menu groups by it.
    service_slug = models.CharField(max_length=48, blank=True, db_index=True)
    # Indexed: the worker roster on a partner window groups by this across the
    # whole table, and a worker window selects on it directly.
    worker_hash = models.CharField(max_length=64, blank=True, db_index=True)
    usd_to_worker = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    # When this row's coordinates were folded into PulseGridCell. Separating
    # "folded" from "deleted" is what lets the rows be KEPT as a local fact
    # store: the fold used to guarantee it could not double-count by deleting
    # in the same transaction, and this timestamp provides that guarantee
    # instead. Retention then becomes a second, independent step that can be
    # turned off (PULSE_EVENT_RETENTION_DAYS = None) without breaking the map.
    #
    # Why keep them at all: re-deriving a new aggregate from local rows is a
    # query, whereas re-pulling them is ~1.6M rows over an API that ships each
    # visit's full form JSON -- about an hour and ~2 GB. The rows themselves are
    # cheap (no form_json is ever stored, so ~300 MB for all history).
    folded_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["field_ts", "opportunity_id"]),
            models.Index(fields=["sync_ts"]),
        ]

    def __str__(self) -> str:
        return f"visit {self.connect_visit_id} · {self.status}"


class PulseWork(models.Model):
    """One unit of payable work — the money and payment-status spine.

    Sourced from ``completed_works``, which is **25x cheaper than
    ``user_visits``** (53 vs 1,346 bytes/row gzipped) because it carries no
    ``form_json``. All 1.65M visits' worth of history is ~87 MB here versus
    ~7.5 GB via visits, so this stream — not the visit stream — is what carries
    deep history.

    It is *not* one row per visit. Measured ratios of works-to-visits: ~0.92 for
    simple programmes (Malaria RDT, Sahaj), but ~0.23 for KMC, where one payment
    unit spans several follow-up visits. So this answers "how much work was done
    and paid for", never "how many visits happened" — that number comes free
    from ``PulseOpportunity.lifetime_visit_count``.

    No natural key: the export omits ``id`` (it is used for the cursor but not
    serialised). ``work_key`` is a salted hash of the identifying tuple, so rows
    dedupe across overlapping polls without storing the beneficiary identifier
    the tuple contains.
    """

    work_key = models.CharField(max_length=64, unique=True, db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    # Indexed: money per partner groups by this across every row of the spine.
    org_slug = models.CharField(max_length=120, blank=True, db_index=True)

    # Indexed for the same reason as on PulseEvent: money per worker groups by
    # this over every row of the spine.
    worker_hash = models.CharField(max_length=64, blank=True, db_index=True)
    payment_unit_id = models.IntegerField(null=True, blank=True)
    # Indexed: the delivery-type filter selects on it across every spine,
    # and the type menu groups by it.
    service_slug = models.CharField(max_length=48, blank=True, db_index=True)
    # Denormalised from the opportunity (works carry no GPS of their own) so
    # money can be grouped by country without a join on every card.
    country = models.CharField(max_length=2, blank=True, db_index=True)

    status = models.CharField(max_length=32, db_index=True)
    created_ts = models.DateTimeField(db_index=True)
    status_ts = models.DateTimeField(null=True, blank=True)
    payment_date = models.DateTimeField(null=True, blank=True, db_index=True)

    approved_count = models.IntegerField(default=0)
    completed_count = models.IntegerField(default=0)
    usd_to_worker = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    usd_to_org = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["created_ts", "opportunity_id"]),
            models.Index(fields=["status", "created_ts"]),
        ]

    def __str__(self) -> str:
        return f"work {self.work_key[:8]} · {self.status}"


class PulseGridCell(models.Model):
    """Aggregated geography, so the map can show history without holding it.

    Visit-level rows expire (see ``PULSE_EVENT_RETENTION_DAYS``); before they
    do, their coordinates are folded into ~1 km cells and the rows are deleted.
    A cell recording "412 services here" is genuinely not client-level data —
    it cannot be resolved back to a household — yet it renders a *denser* map
    than retaining the rows would, because it accumulates indefinitely.

    This is the mechanism that lets Pulse show years of coverage while storing
    no deep archive of beneficiary-level records.
    """

    lat_q = models.IntegerField()  # round(lat * 100) — ~1.1 km
    lon_q = models.IntegerField()
    country = models.CharField(max_length=2, blank=True, db_index=True)
    # Indexed: the delivery-type filter selects on it across every spine,
    # and the type menu groups by it.
    service_slug = models.CharField(max_length=48, blank=True, db_index=True)

    # Part of the cell key, so filtering the map by programme narrows the
    # accumulated geography as well as the live points. The events being folded
    # always carried this; the fold simply never selected it, which left the
    # density layer showing every programme's history under one programme's
    # header -- a map glowing across four countries beside "COUNTRIES 1".
    # Nullable for cells folded before this existed.
    program_id = models.IntegerField(null=True, blank=True, db_index=True)

    n = models.IntegerField(default=0)
    # Kept per cell so the historical map can show *quality*, not just volume —
    # a cell with a high flag ratio is a story a bare count can't tell.
    approved_n = models.IntegerField(default=0)
    flagged_n = models.IntegerField(default=0)

    first_ts = models.DateTimeField(null=True, blank=True)
    last_ts = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        unique_together = [("lat_q", "lon_q", "service_slug", "program_id")]
        indexes = [models.Index(fields=["country", "n"])]

    def __str__(self) -> str:
        return f"cell {self.lat_q/100:.2f},{self.lon_q/100:.2f} n={self.n}"

    @property
    def lat(self) -> float:
        return self.lat_q / 100.0

    @property
    def lon(self) -> float:
        return self.lon_q / 100.0


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
    def stale_after_seconds(self) -> int:
        """How old a success may get on THIS tier before it means anything.

        Each ingest tier runs on its own beat, so one shared threshold is only
        ever right for the fastest of them. It used to be 6x the hot interval --
        six minutes -- which held while every tier ran at least every five. When
        the cheap tier moved to every fifteen (it is one ~4.5s upstream call
        against a list that changes daily), it spent nine minutes out of every
        fifteen looking dead, and the whole display said "Data is not live" over
        data that was thirty seconds old.

        Six times the tier's OWN cadence keeps the original intent: generous
        enough that one transient failure does not red-flag a wall display,
        bounded enough that a dead token cannot masquerade as live all
        afternoon.
        """
        return 6 * TIER_CADENCE_SECONDS.get(self.tier, TIER_INTERVALS_SECONDS[TIER_HOT])

    @property
    def is_healthy(self) -> bool:
        """Healthy = a success recently enough to trust what's on screen."""
        if self.last_success_at is None:
            return False
        age = (timezone.now() - self.last_success_at).total_seconds()
        return age < self.stale_after_seconds and self.consecutive_failures < 5


class PulseReport(models.Model):
    """A donor stewardship report — a saved scope, window and set of manual copy.

    The report holds *only* what Pulse cannot compute. Every figure that can be
    derived is derived at render time from the works spine, so a report reopened
    six months later reflects corrected data rather than a stale copy taken the
    day it was written. That is the opposite of the usual "snapshot the numbers"
    instinct, and it is deliberate: a donor report whose figures silently
    disagree with the platform is worse than one that has to be re-read.

    **Everything counted here is verified, reimbursed delivery.** The spine is
    ``PulseWork`` filtered to ``status="approved"``, not ``PulseEvent``, for two
    independent reasons that happen to point the same way:

    * It is the reimbursement record. Counting recorded-but-unapproved visits
      would put work we did not pay for into a funder's total.
    * Events are retained ~30 days (``PULSE_EVENT_RETENTION_DAYS``) because they
      carry ``form_json`` at 1,346 B/row; works are 53 B/row and are kept in
      full. Keying the report on events would make any window older than a month
      silently report zero.

    Note that a work is *not* a visit: measured ratios run ~0.92 for simple
    programmes and ~0.23 for KMC, where one payment unit spans several follow-up
    visits. So this reports units of service delivered and paid for, which is
    what a funder is being told about, and never claims to be a visit count.
    """

    # Basis for a derived deliverable line. Deliberately short: every option is
    # a verified, reimbursed quantity. There is no "recorded visits" basis --
    # see the class docstring.
    BASIS_SERVICES = "services"
    BASIS_WORKS = "works"
    BASIS_WORKERS = "workers"
    BASIS_MANUAL = "manual"

    BASIS_CHOICES = [
        (BASIS_SERVICES, "Verified service deliveries"),
        (BASIS_WORKS, "Completed care episodes (payment units)"),
        (BASIS_WORKERS, "Frontline workers"),
        (BASIS_MANUAL, "Entered by hand"),
    ]

    # URL identity. Unguessable so the same row can back both the authenticated
    # editor and a shared read-only link without a second identifier.
    slug = models.CharField(max_length=64, unique=True, db_index=True)

    eyebrow = models.CharField(max_length=120, blank=True, default="Donor stewardship report")
    title = models.CharField(max_length=300, blank=True)
    prepared_for = models.CharField(max_length=300, blank=True)

    # The gift is the funder's own figure; Connect never sees it. Stored as
    # text, not a decimal, because reports say "$20,000" and also "£15k over two
    # years" and coercing the second into a number would lose the report.
    gift_line = models.CharField(max_length=300, blank=True)

    window_start = models.DateField(null=True, blank=True)
    window_end = models.DateField(null=True, blank=True)

    # Scope. Composes exactly as the display filters do -- these are handed
    # straight to the same `_program_scope` the live API uses, so a report can
    # never drift from what the dashboard would show for the same selection.
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    org_slug = models.CharField(max_length=120, blank=True)
    opportunity_id = models.IntegerField(null=True, blank=True)
    service_slug = models.CharField(max_length=48, blank=True)

    intro = models.TextField(blank=True)
    where_we_worked = models.TextField(blank=True)
    partner_funding = models.TextField(blank=True)
    footnote = models.TextField(blank=True)

    photo = models.ImageField(upload_to="pulse_reports/", null=True, blank=True)
    photo_caption = models.TextField(blank=True)

    # [{"label", "description", "basis", "multiplier", "override", "emphasis"}]
    # A line with basis=manual, or with a non-null override, is human-entered
    # and is badged as such in the editor -- the reader of a donor report is
    # entitled to know which figures the platform stands behind.
    deliverables = models.JSONField(default=list, blank=True)
    # Free-text site names (camps, wards, facilities). Connect does not model
    # these, so they are typed.
    site_chips = models.JSONField(default=list, blank=True)

    show_partner_names = models.BooleanField(default=True)
    revoked = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="pulse_reports"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.title or 'Untitled report'}{' (revoked)' if self.revoked else ''}"

    @property
    def is_usable(self) -> bool:
        return not self.revoked

    def scope_params(self) -> dict:
        """The report's scope as the GET params ``_program_scope`` expects.

        Expressed as query params rather than as a bespoke filter path so the
        report and the dashboard cannot disagree: both run the same resolver.
        """
        params = {}
        if self.program_id:
            params["program"] = str(self.program_id)
        if self.org_slug:
            params["org"] = self.org_slug
        if self.opportunity_id:
            params["opportunity"] = str(self.opportunity_id)
        if self.service_slug:
            params["service"] = self.service_slug
        if self.window_start:
            params["from"] = self.window_start.isoformat()
        if self.window_end:
            params["to"] = self.window_end.isoformat()
        return params


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
