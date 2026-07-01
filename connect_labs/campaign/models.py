from django.conf import settings
from django.db import models
from django.utils import timezone

from connect_labs.campaign.services import rbac


class CampaignUser(models.Model):
    """An in-app whitelist entry: who may sign in and with what role."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        DEACTIVATED = "deactivated", "Deactivated"

    ROLE_CHOICES = [(r, r.replace("_", " ").title()) for r in rbac.ROLES]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_membership",
    )
    commcare_username = models.CharField(max_length=255, unique=True)
    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default="reporting_user")
    scope = models.CharField(max_length=64, default="All regions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "campaign_user"

    def __str__(self):
        return f"{self.commcare_username} ({self.role})"

    @property
    def is_active_member(self) -> bool:
        return self.status == self.Status.ACTIVE


class Workspace(models.Model):
    country = models.CharField(max_length=128, default="Nigeria")
    name = models.CharField(max_length=128)
    slug = models.SlugField(unique=True)

    class Meta:
        db_table = "campaign_workspace"

    def __str__(self):
        return self.name


class Campaign(models.Model):
    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="campaigns")
    name = models.CharField(max_length=255)
    code = models.CharField(max_length=64)
    round = models.CharField(max_length=64, blank=True)
    country = models.CharField(max_length=128, default="Nigeria")
    period = models.CharField(max_length=128, blank=True)
    status = models.CharField(max_length=32, default="Active")
    days_elapsed = models.IntegerField(default=0)
    days_total = models.IntegerField(default=0)
    target_pop = models.BigIntegerField(default=0)
    # The CommCare project space (domain) this campaign's workers/KYC are read from
    # via the Case API. A synthetic domain (registered in SyntheticCommCareDomain) is
    # served in-app from WorkerCase; a real domain hits CommCare HQ. Blank = the legacy
    # demo path (workers read from the local Worker ORM).
    commcare_domain = models.CharField(max_length=128, blank=True, default="", db_index=True)

    class Meta:
        db_table = "campaign_campaign"

    def __str__(self):
        return f"{self.name} ({self.code})"


class Donor(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="donors")
    donor_id = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    short = models.CharField(max_length=64)
    committed = models.BigIntegerField(default=0)
    color = models.CharField(max_length=16, default="#5D70D2")
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "campaign_donor"
        ordering = ["order"]

    def __str__(self):
        return self.short


class Region(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="regions")
    region_id = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    lgas = models.JSONField(default=list)
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "campaign_region"
        ordering = ["order"]

    def __str__(self):
        return self.name


class WorkerRole(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="worker_roles")
    role_id = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    rate = models.IntegerField(default=0)
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "campaign_worker_role"
        ordering = ["order"]

    def __str__(self):
        return self.name


class RegionPlan(models.Model):
    region = models.OneToOneField(Region, on_delete=models.CASCADE, related_name="plan")
    planned_wf = models.IntegerField(default=0)
    actual_wf = models.IntegerField(default=0)
    budget = models.BigIntegerField(default=0)
    spent = models.BigIntegerField(default=0)
    target = models.BigIntegerField(default=0)
    reached = models.BigIntegerField(default=0)
    vaccine_alloc = models.BigIntegerField(default=0)
    vaccine_used = models.BigIntegerField(default=0)

    class Meta:
        db_table = "campaign_region_plan"

    def __str__(self):
        return f"plan:{self.region.name}"


class HouseholdStat(models.Model):
    campaign = models.OneToOneField(Campaign, on_delete=models.CASCADE, related_name="household_stat")
    registered = models.BigIntegerField(default=0)
    visited = models.BigIntegerField(default=0)
    members = models.BigIntegerField(default=0)
    members_reached = models.BigIntegerField(default=0)
    coverage = models.JSONField(default=list)

    class Meta:
        db_table = "campaign_household_stat"

    def __str__(self):
        return f"households:{self.campaign.code}"


class Worker(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="workers")
    worker_id = models.CharField(max_length=16)
    first = models.CharField(max_length=64)
    last = models.CharField(max_length=64)
    name = models.CharField(max_length=128)
    gender = models.CharField(max_length=1)  # 'M' | 'F'
    phone = models.CharField(max_length=32, blank=True)
    region_id = models.CharField(max_length=64)
    lga = models.CharField(max_length=128)
    role_id = models.CharField(max_length=64)
    rate = models.IntegerField(default=0)
    days_worked = models.IntegerField(default=0)
    days_approved = models.IntegerField(default=0)
    amount = models.IntegerField(default=0)
    kyc = models.CharField(max_length=16)  # approved|pending|rejected|review
    pay = models.CharField(max_length=16)  # paid|approved|pending|rejected|hold
    bank = models.CharField(max_length=64, blank=True)
    acct = models.CharField(max_length=32, blank=True)
    nin = models.CharField(max_length=32, blank=True)
    passport = models.CharField(max_length=32, null=True, blank=True)
    enrolled = models.CharField(max_length=32, blank=True)
    attendance = models.IntegerField(default=0)
    prior_campaigns = models.IntegerField(default=0)
    duplicate = models.BooleanField(default=False)
    dup_with = models.CharField(max_length=16, null=True, blank=True)
    fraud_rules = models.JSONField(default=list)
    linked = models.JSONField(default=list)
    investigation = models.JSONField(null=True, blank=True)
    documents = models.JSONField(default=list)

    class Meta:
        db_table = "campaign_worker"

    def __str__(self):
        return f"{self.worker_id} {self.name}"

    @property
    def is_flagged(self) -> bool:
        return len(self.fraud_rules or []) > 0


class Activity(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="activities")
    activity_id = models.CharField(max_length=16)
    name = models.CharField(max_length=255)
    donor = models.CharField(max_length=64)  # donor short name
    status = models.CharField(max_length=16, default="Planned")  # Active|At risk|Planned|Completed
    start = models.CharField(max_length=32, blank=True)
    end = models.CharField(max_length=32, blank=True)
    requests = models.IntegerField(default=0)
    workers = models.IntegerField(default=0)
    region = models.CharField(max_length=128)  # region display name
    target = models.BigIntegerField(default=0)
    reached = models.BigIntegerField(default=0)
    synced = models.BooleanField(default=False)

    class Meta:
        db_table = "campaign_activity"

    def __str__(self):
        return f"{self.activity_id} {self.name}"


class Microplan(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="microplans")
    microplan_id = models.CharField(max_length=16)
    region_id = models.CharField(max_length=64)
    region = models.CharField(max_length=128)
    lga = models.CharField(max_length=128)
    settlements = models.IntegerField(default=0)
    wards = models.IntegerField(default=0)
    planned_wf = models.IntegerField(default=0)
    actual_wf = models.IntegerField(default=0)
    roles = models.JSONField(default=list)  # [{roleId, role, rate, planned, actual}]
    budget = models.BigIntegerField(default=0)
    spent = models.BigIntegerField(default=0)
    planned_to_date = models.BigIntegerField(default=0)
    target = models.BigIntegerField(default=0)
    objective = models.BigIntegerField(default=0)
    goal_pct = models.IntegerField(default=95)
    reached = models.BigIntegerField(default=0)
    doses = models.BigIntegerField(default=0)
    doses_used = models.BigIntegerField(default=0)
    cold_boxes = models.IntegerField(default=0)
    vehicles = models.IntegerField(default=0)
    status = models.CharField(max_length=16, default="Planned")
    owner = models.CharField(max_length=128, blank=True)
    updated = models.CharField(max_length=32, blank=True)

    class Meta:
        db_table = "campaign_microplan"

    def __str__(self):
        return f"{self.microplan_id} {self.lga}"


class ReportDay(models.Model):
    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="report_days")
    day = models.CharField(max_length=8)
    enrolled = models.BigIntegerField(default=0)
    attended = models.BigIntegerField(default=0)
    paid = models.BigIntegerField(default=0)
    order = models.IntegerField(default=0)

    class Meta:
        db_table = "campaign_report_day"
        ordering = ["order"]

    def __str__(self):
        return f"{self.campaign_id}:{self.day}"


class AuditLog(models.Model):
    """A tool-owned record of an admin action. Written on every privileged write
    (payments, KYC, user management, activities, microplans) so the Activity log in
    System Administration reflects what actually happened rather than seeded text."""

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="audit_logs", null=True, blank=True)
    at = models.DateTimeField(default=timezone.now)
    user = models.CharField(max_length=255)  # actor display name (or username)
    action = models.TextField()
    module = models.CharField(max_length=64)
    ip = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "campaign_audit_log"
        ordering = ["-at", "-id"]

    def __str__(self):
        return f"{self.at:%Y-%m-%d %H:%M} {self.user}: {self.action}"


class WorkerCase(models.Model):
    """A synthetic CommCare *case* for a campaign worker.

    Mirrors how worker data lives in production: CommCare HQ owns the worker as a
    case (case_type ``campaign_worker``) whose case properties carry every field
    the Data Model marks CommCare-owned — the Worker dataset plus the embedded KYC
    Verification dataset. The campaign tool READS these; it does not author them.
    Here they are synthetic and local, but the shape matches a CommCare Case/Form
    API read, so the future CommCareProvider is a drop-in swap. Geography
    (``region_id``/``lga``/``ward``) is sourced from labs ``AdminBoundary``.

    The denormalized columns exist for scale-querying/filtering; ``properties`` is
    the faithful case-property bag the serializer consumes (snake_case keys match
    the worker attributes ``serializers._worker`` reads).
    """

    campaign = models.ForeignKey(Campaign, on_delete=models.CASCADE, related_name="worker_cases")
    case_id = models.CharField(max_length=64, db_index=True, help_text="CommCare case id")
    case_type = models.CharField(max_length=64, default="campaign_worker")
    worker_id = models.CharField(max_length=16, db_index=True, help_text="W##### external id (a case property)")
    region_id = models.CharField(max_length=100, db_index=True, help_text="AdminBoundary state boundary_id")
    lga = models.CharField(max_length=128, db_index=True, blank=True)
    ward = models.CharField(max_length=128, blank=True)
    properties = models.JSONField(default=dict)

    class Meta:
        db_table = "campaign_worker_case"
        indexes = [models.Index(fields=["campaign", "region_id"])]
        constraints = [
            models.UniqueConstraint(fields=["campaign", "case_id"], name="campaign_worker_case_uniq"),
        ]

    def __str__(self):
        return f"{self.case_type}:{self.worker_id}"


class SyntheticCommCareDomain(models.Model):
    """Registry of synthetic CommCare project spaces (domains).

    The Case-API analogue of labs' ``SyntheticOpportunity``: when the campaign tool
    reads cases for a domain registered + enabled here, the request is short-circuited
    in-app and served from ``WorkerCase`` (Case-API-v2 JSON shape) instead of hitting
    real CommCare HQ — exactly how ``LabsRecordAPIClient`` short-circuits a labs-only
    opportunity to ``local_records_backend``. Unregistered domains fall through to the
    real CommCare Case API.
    """

    domain = models.CharField(max_length=128, unique=True, db_index=True)
    label = models.CharField(max_length=200, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "campaign_synthetic_commcare_domain"

    def __str__(self):
        return self.domain
