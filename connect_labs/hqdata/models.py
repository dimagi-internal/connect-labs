"""CommCare projects, what is pulled from them, and the durable rows they keep."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from connect_labs.hqdata.scope import hq_scope_id
from connect_labs.utils.encrypted_fields import EncryptedTextField

# A sync that has not heartbeat for this long is presumed dead (a deploy hard-kills
# every task with no drain), and the next run may take its lease.
LEASE_SECONDS = 30 * 60


class HQProject(models.Model):
    """One CommCare HQ domain on one HQ host: the unit of access, storage and sync."""

    STORAGE_LIVE = "live"
    STORAGE_PERSISTENT = "persistent"
    STORAGE_CHOICES = [
        (STORAGE_LIVE, "Live: fetched when asked, kept briefly"),
        (STORAGE_PERSISTENT, "Persistent: kept and refreshed on a schedule"),
    ]

    BACKEND_HQ_API = "hq_api"
    BACKEND_CHOICES = [(BACKEND_HQ_API, "CommCare HQ API")]

    STORE_LABS = "labs"
    STORE_EXTERNAL = "external"
    STORE_CHOICES = [
        (STORE_LABS, "Labs"),
        (STORE_EXTERNAL, "A database the customer hosts"),
    ]

    STATUS_ACTIVE = "active"
    STATUS_NEEDS_REAUTH = "needs_reauth"
    STATUS_ERROR = "error"
    STATUS_PAUSED = "paused"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "Active"),
        (STATUS_NEEDS_REAUTH, "Needs the owner to sign in to CommCare again"),
        (STATUS_ERROR, "Last sync failed"),
        (STATUS_PAUSED, "Paused"),
    ]

    hq_host = models.CharField(max_length=255, default="www.commcarehq.org")
    domain = models.CharField(max_length=255)
    name = models.CharField(max_length=255, blank=True)

    storage_mode = models.CharField(max_length=16, choices=STORAGE_CHOICES, default=STORAGE_LIVE)
    # Where the rows COME FROM and where they LIVE are separate choices (see stores.py).
    backend = models.CharField(max_length=16, choices=BACKEND_CHOICES, default=BACKEND_HQ_API)
    store = models.CharField(max_length=16, choices=STORE_CHOICES, default=STORE_LABS)
    sync_interval_minutes = models.PositiveIntegerField(default=60)

    # Whose CommCare token a scheduled (headless) sync uses. Never another person's.
    sync_owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    last_error = models.TextField(blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "hqdata"
        constraints = [
            models.UniqueConstraint(fields=["hq_host", "domain"], name="uniq_hqproject_host_domain"),
            models.CheckConstraint(
                condition=Q(sync_interval_minutes__gte=5), name="hqproject_sync_interval_at_least_5_minutes"
            ),
        ]

    def __str__(self) -> str:
        return self.name or self.domain

    @property
    def scope_id(self) -> int:
        """The integer the analysis caches key this project's rows by."""
        return hq_scope_id(self.pk)

    def purge_rows(self) -> dict[str, int] | None:
        """Delete every stored form and case of this project, from wherever its store is.

        Rows carry no foreign key to the project (see ``HQForm``), so deleting the project does
        not do this by itself. Returns None when the store could not be reached: the project can
        still be deleted, and what a customer's own database holds stays theirs to remove.
        """
        from connect_labs.hqdata.stores import StoreUnavailable, store_for

        try:
            with store_for(self).session() as store:
                return store.purge(self.pk)
        except StoreUnavailable:
            return None

    def delete(self, *args, **kwargs):
        self.purge_rows()
        return super().delete(*args, **kwargs)

    @property
    def is_persistent(self) -> bool:
        return self.storage_mode == self.STORAGE_PERSISTENT

    def sync_due(self, now=None) -> bool:
        """Whether a scheduled sync should run now."""
        if not self.is_persistent or self.status in (self.STATUS_PAUSED, self.STATUS_NEEDS_REAUTH):
            return False
        now = now or timezone.now()
        return self.last_synced_at is None or now - self.last_synced_at >= timedelta(
            minutes=self.sync_interval_minutes
        )


class HQProjectMember(models.Model):
    """A person who may work in a project, as last proven by CommCare HQ.

    Membership is not granted in labs: it is read off the person's own CommCare account
    (the domains their HQ identity lists) and expires if not proven again, so a person
    removed from the domain on HQ loses access here without anyone telling labs.
    """

    ROLE_ADMIN = "admin"
    ROLE_MEMBER = "member"
    ROLE_CHOICES = [(ROLE_ADMIN, "Admin"), (ROLE_MEMBER, "Member")]

    project = models.ForeignKey(HQProject, on_delete=models.CASCADE, related_name="members")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    verified_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "hqdata"
        constraints = [models.UniqueConstraint(fields=["project", "user"], name="uniq_hqmember_project_user")]


class HQIdentity(models.Model):
    """What CommCare HQ last said about a person who has used it with labs.

    One row per labs user. A person who signs in with CommCare and has no Connect login
    (their ``User`` is created here, with no email) is a CommCare-ONLY identity; a Connect
    person who connected their CommCare account gets the same row, so memberships are proven
    the same way for both.
    """

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="hq_identity")
    hq_host = models.CharField(max_length=255)
    hq_username = models.CharField(max_length=255)
    # Kept for display only. Never matched against a labs user and never read as a
    # Dimagi address: HQ's word about an email is not labs' proof of it.
    email = models.CharField(max_length=255, blank=True)
    name = models.CharField(max_length=255, blank=True)
    domains = models.JSONField(default=list)
    refreshed_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "hqdata"
        constraints = [
            models.UniqueConstraint(fields=["hq_host", "hq_username"], name="uniq_hqidentity_host_username")
        ]


class HQSource(models.Model):
    """What is pulled from a project (forms or cases, optionally narrowed) and how far it has got."""

    KIND_FORMS = "forms"
    KIND_CASES = "cases"
    KIND_CHOICES = [(KIND_FORMS, "Forms"), (KIND_CASES, "Cases")]

    project = models.ForeignKey(HQProject, on_delete=models.CASCADE, related_name="sources")
    kind = models.CharField(max_length=8, choices=KIND_CHOICES)
    # Narrowing. Forms: app_id and/or xmlns. Cases: case_type. Blank means "all".
    app_id = models.CharField(max_length=64, blank=True)
    xmlns = models.CharField(max_length=512, blank=True)
    case_type = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)

    # HQ's own ``indexed_on`` of the newest row stored. Every walk is ordered by it,
    # so everything at or before it is already here: it advances after each committed
    # batch, and a walk that dies half way resumes from it (the checkpoint model of
    # commcare-export, which commcare-sync schedules).
    watermark = models.DateTimeField(null=True, blank=True)
    # False until one walk from the beginning has finished. Until then the next run
    # resumes from the watermark instead of starting over.
    backfill_complete = models.BooleanField(default=False)
    last_full_sync_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    # The lease: set while a run owns the source, refreshed on every batch.
    running_since = models.DateTimeField(null=True, blank=True)

    class Meta:
        app_label = "hqdata"
        constraints = [
            models.UniqueConstraint(
                fields=["project", "kind", "app_id", "xmlns", "case_type"], name="uniq_hqsource_selector"
            ),
            models.CheckConstraint(
                condition=(Q(kind="forms", case_type="") | Q(kind="cases", app_id="", xmlns="")),
                name="hqsource_selector_matches_kind",
            ),
        ]

    def __str__(self) -> str:
        narrow = self.case_type or self.xmlns or self.app_id or "all"
        return f"{self.project} {self.kind}: {narrow}"

    # -- Lease -------------------------------------------------------------

    def claim(self, now=None) -> bool:
        """Take the lease if nobody holds a live one. Atomic: exactly one caller wins."""
        now = now or timezone.now()
        stale = now - timedelta(seconds=LEASE_SECONDS)
        won = (
            HQSource.objects.filter(pk=self.pk)
            .filter(Q(running_since__isnull=True) | Q(running_since__lt=stale))
            .update(running_since=now)
        )
        return won == 1

    def heartbeat(self, now=None) -> None:
        HQSource.objects.filter(pk=self.pk).update(running_since=now or timezone.now())

    def release(self) -> None:
        HQSource.objects.filter(pk=self.pk).update(running_since=None)

    # -- The rows this source's selector covers --------------------------------

    def rows(self, store=None):
        """The stored rows this source's selector covers, in the project's store."""
        from connect_labs.hqdata.stores import store_for

        store = store or store_for(self.project)
        if self.kind == self.KIND_FORMS:
            qs = store.forms(self.project_id)
            if self.app_id:
                qs = qs.filter(app_id=self.app_id)
            if self.xmlns:
                qs = qs.filter(xmlns=self.xmlns)
            return qs
        qs = store.cases(self.project_id)
        if self.case_type:
            qs = qs.filter(case_type=self.case_type)
        return qs


class HQForm(models.Model):
    """A form submission. ``project_id`` is a plain integer, not a foreign key: these rows
    live in the project's STORE, which can be a database the labs database has no
    constraints into (a customer-hosted one), so deleting a project purges them in code
    (``HQProject.purge_rows``)."""

    project_id = models.IntegerField(db_index=True)
    form_id = models.CharField(max_length=64)
    xmlns = models.CharField(max_length=512, blank=True, db_index=True)
    app_id = models.CharField(max_length=64, blank=True, db_index=True)
    build_id = models.CharField(max_length=64, blank=True)
    received_on = models.DateTimeField(null=True, blank=True, db_index=True)
    server_modified_on = models.DateTimeField(null=True, blank=True)
    indexed_on = models.DateTimeField(null=True, blank=True)
    username = models.CharField(max_length=255, blank=True, db_index=True)
    user_id = models.CharField(max_length=64, blank=True)
    archived = models.BooleanField(default=False)
    form_json = models.JSONField(default=dict)
    # Set by a reconcile that did not see the row (a purge HQ never announces).
    deleted_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "hqdata"
        # Named to read sensibly inside a customer's database, where these tables are created.
        db_table = "labs_commcare_form"
        constraints = [models.UniqueConstraint(fields=["project_id", "form_id"], name="uniq_hqform_project_form")]
        indexes = [models.Index(fields=["project_id", "received_on"])]


class HQCase(models.Model):
    """A case. ``project_id`` is a plain integer for the reason on ``HQForm``."""

    project_id = models.IntegerField(db_index=True)
    case_id = models.CharField(max_length=64)
    case_type = models.CharField(max_length=255, blank=True, db_index=True)
    case_name = models.CharField(max_length=1024, blank=True)
    owner_id = models.CharField(max_length=64, blank=True, db_index=True)
    external_id = models.CharField(max_length=255, blank=True)
    closed = models.BooleanField(default=False)
    date_opened = models.DateTimeField(null=True, blank=True)
    last_modified = models.DateTimeField(null=True, blank=True)
    server_last_modified = models.DateTimeField(null=True, blank=True)
    indexed_on = models.DateTimeField(null=True, blank=True)
    properties = models.JSONField(default=dict)
    indices = models.JSONField(default=dict)
    deleted_at = models.DateTimeField(null=True, blank=True)
    synced_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "hqdata"
        db_table = "labs_commcare_case"
        constraints = [models.UniqueConstraint(fields=["project_id", "case_id"], name="uniq_hqcase_project_case")]
        indexes = [models.Index(fields=["project_id", "case_type"])]


class HQSyncRun(models.Model):
    """One attempt to bring a source up to date. The history the project page shows."""

    MODE_DELTA = "delta"
    MODE_FULL = "full"
    MODE_RECONCILE = "reconcile"
    MODE_CHOICES = [(MODE_DELTA, "Delta"), (MODE_FULL, "Full"), (MODE_RECONCILE, "Reconcile")]

    STATUS_RUNNING = "running"
    STATUS_OK = "ok"
    STATUS_ERROR = "error"

    source = models.ForeignKey(HQSource, on_delete=models.CASCADE, related_name="runs")
    mode = models.CharField(max_length=10, choices=MODE_CHOICES)
    status = models.CharField(max_length=10, default=STATUS_RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    since = models.DateTimeField(null=True, blank=True, help_text="The watermark this run started from")
    fetched = models.PositiveIntegerField(default=0)
    deleted = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        app_label = "hqdata"
        ordering = ["-started_at"]
        indexes = [models.Index(fields=["source", "-started_at"])]


class HQPipeline(models.Model):
    """A saved pipeline over a project's forms or cases.

    The ``schema`` is the same shape workflows use (fields with a ``path``, an ``aggregation``,
    a ``grouping_key``...). Its ``data_source`` is NOT stored here: where the data comes from is
    this row's own project and narrowing, stamped on at run time (``pipelines.config_for``), so
    a schema can never be pointed at a project the person does not belong to.
    """

    project = models.ForeignKey(HQProject, on_delete=models.CASCADE, related_name="pipelines")
    name = models.CharField(max_length=120)
    store_kind = models.CharField(max_length=8, choices=HQSource.KIND_CHOICES)
    app_id = models.CharField(max_length=64, blank=True)
    xmlns = models.CharField(max_length=512, blank=True)
    case_type = models.CharField(max_length=255, blank=True)
    schema = models.JSONField(default=dict)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "hqdata"
        constraints = [models.UniqueConstraint(fields=["project", "name"], name="uniq_hqpipeline_project_name")]
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.project}: {self.name}"


class HQExternalDatabase(models.Model):
    """The customer-hosted PostgreSQL a project keeps its data in (``HQProject.store == "external"``).

    The connection is stored as its parts, not a URL, so each can be checked on its own (see
    ``external.spec_for``). The password is encrypted at rest and never shown again, logged, put in
    an error or returned by an API. A project's connection cannot be deleted while it is in use.
    """

    SSLMODE_CHOICES = [
        ("verify-full", "Verify the certificate and the host name"),
        ("verify-ca", "Verify the certificate"),
    ]

    project = models.OneToOneField(HQProject, on_delete=models.CASCADE, related_name="external_database")
    host = models.CharField(max_length=253)
    port = models.PositiveIntegerField(default=5432)
    dbname = models.CharField(max_length=63)
    schema = models.CharField(max_length=63, default="labs_commcare")
    username = models.CharField(max_length=63)
    password = EncryptedTextField(blank=True)
    sslmode = models.CharField(max_length=12, choices=SSLMODE_CHOICES, default="verify-full")
    # Set by Dimagi staff, never by the customer: the database sits on a private network labs
    # reaches over a tunnel or a peering (``netguard``).
    allow_private_network = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "hqdata"

    def __str__(self) -> str:
        return f"{self.username}@{self.host}/{self.dbname}"

    def __repr__(self) -> str:
        return f"<HQExternalDatabase project={self.project_id} {self.host}:{self.port}/{self.dbname}>"

    def delete(self, *args, **kwargs):
        if HQProject.objects.filter(pk=self.project_id, store=HQProject.STORE_EXTERNAL).exists():
            raise models.ProtectedError("The project is keeping its data in this database.", {self})
        return super().delete(*args, **kwargs)
