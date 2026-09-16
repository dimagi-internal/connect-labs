"""
Labs Models

LocalLabsRecord and SQL cache models for the labs environment.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone


class LocalLabsRecord:
    """Transient object for Labs API responses. Never saved to database.

    This class mimics production LabsRecord but is not a Django model.
    It's instantiated from production API responses and provides typed access
    to record data.
    """

    def __init__(self, api_data: dict[str, Any]) -> None:
        """Initialize from production API response.

        Args:
            api_data: Response data from /export/labs_record/ API
        """
        self.id: int = api_data["id"]
        self.experiment: str = api_data["experiment"]
        self.type: str = api_data["type"]
        self.data: dict = api_data["data"]
        self.username: str | None = api_data.get("username")  # Primary user identifier (not user_id)
        self.opportunity_id: int = api_data["opportunity_id"]
        self.organization_id: str | None = api_data.get("organization_id")
        self.program_id: int | None = api_data.get("program_id")
        self.labs_record_id: int | None = api_data.get("labs_record_id")  # Parent reference
        self.public: bool = api_data.get("public", False)  # Public records can be queried without scope

    @property
    def pk(self) -> int:
        """Alias for id to mimic Django model interface.

        This allows LocalLabsRecord instances to be used in contexts that expect
        Django models, such as django-tables2 and URL reverse lookups.
        """
        return self.id

    def __str__(self) -> str:
        return f"{self.experiment}:{self.type}:{self.id}"

    def __repr__(self) -> str:
        return f"<LocalLabsRecord: {self}>"

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize for API POST/PUT requests.

        Returns:
            Dict suitable for posting to production API
        """
        return {
            "id": self.id,
            "experiment": self.experiment,
            "type": self.type,
            "data": self.data,
            "username": self.username,
            "program_id": self.program_id,
            "labs_record_id": self.labs_record_id,
            "opportunity_id": self.opportunity_id,
            "organization_id": self.organization_id,
            "public": self.public,
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Prevent saving to database."""
        raise NotImplementedError("LocalLabsRecord cannot be saved. Use LabsRecordAPIClient instead.")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        """Prevent deletion from database."""
        raise NotImplementedError("LocalLabsRecord cannot be deleted. Use LabsRecordAPIClient instead.")


# Import SQL cache models so Django can discover them for migrations
from connect_labs.labs.analysis.backends.sql.models import (  # noqa: E402, F401
    ComputedFLWCache,
    ComputedVisitCache,
    RawVisitCache,
)


class UserConnectToken(models.Model):
    """Persistent store of a user's production-Connect OAuth token.

    Populated when the user logs into labs via the Connect OAuth flow. Looked up
    by the MCP server (and eventually by celery tasks) to act on a user's behalf
    without a browser session.

    The refresh_token is used to extend the access_token when it expires.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_token",
    )
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "labs_user_connect_token"

    def __str__(self) -> str:
        return f"ConnectToken({self.user.username})"

    @property
    def is_expired(self) -> bool:
        # Treat tokens within 60 seconds of expiry as expired to avoid races.
        return timezone.now() >= (self.expires_at - timedelta(seconds=60))


class UserCCHQToken(models.Model):
    """Persistent store of a user's CommCare HQ OAuth token.

    Populated when the user connects CommCare HQ via the labs OAuth flow
    (/labs/commcare/initiate/). Looked up by headless callers (celery tasks,
    management commands) to act on a user's behalf without a browser session,
    mirroring UserConnectToken for CommCare HQ instead of Connect.

    The refresh_token is used to extend the access_token when it expires.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="cchq_token",
    )
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "labs_user_cchq_token"

    def __str__(self) -> str:
        return f"CCHQToken({self.user.username})"

    @property
    def is_expired(self) -> bool:
        # Treat tokens within 60 seconds of expiry as expired to avoid races.
        return timezone.now() >= (self.expires_at - timedelta(seconds=60))


class DeletedWorkflowBackup(models.Model):
    """Safety copy of a workflow definition, written just before it is deleted.

    Deleting a workflow hard-deletes its definition and render code from the
    Connect LabsRecord store with no way to recover them (a surviving run only
    keeps the ``definition_id``). This table captures the restorable pair — the
    definition JSON plus its render-code JSX — so a deleted workflow can be
    reconstructed by hand from the admin. Runs, audit sessions, and chat
    history are intentionally not backed up.

    Written by ``WorkflowDataAccess.delete_definition`` before the delete
    executes; that write is fail-closed (a failure aborts the delete).
    """

    definition_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    template_type = models.CharField(max_length=100, blank=True, default="")
    definition_data = models.JSONField(default=dict)
    render_code = models.TextField(blank=True, default="")
    deleted_by = models.CharField(max_length=150, blank=True, default="")
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "labs_deleted_workflow_backup"
        ordering = ["-id"]
        verbose_name = "Deleted workflow backup"
        verbose_name_plural = "Deleted workflow backups"

    def __str__(self) -> str:
        return f"backup:def={self.definition_id}:opp={self.opportunity_id}:{self.name}"


class DocComment(models.Model):
    """A comment left on a Documentations page (``/labs/docs/<doc_key>/``).

    Stored in the labs database, deliberately NOT in the Connect LabsRecord
    store: these are notes about labs' own documentation, not program data, so
    they have no opportunity/program/organization to be scoped by and nothing
    about them belongs on production Connect.

    Comments are global — every logged-in labs user sees the same thread on a
    page, regardless of their selected program or opportunity context.

    A comment is anchored to a section of the page (``section_id`` matches an
    element id in the document, e.g. ``setup-sequence``) so the sidebar can group
    threads the way Google Docs does. ``section_id`` is blank for a comment on
    the page as a whole.
    """

    doc_key = models.CharField(max_length=64, db_index=True)
    # Element id within the document; blank means "the page as a whole".
    section_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    # Heading text captured at write time, so a thread still labels itself if
    # the document is re-exported and that section is renamed or removed.
    section_label = models.CharField(max_length=255, blank=True, default="")
    body = models.TextField()
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="doc_comments",
    )
    # Denormalised so a thread still reads sensibly if a display name changes.
    author_name = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "labs_doc_comment"
        ordering = ["created_at", "id"]
        verbose_name = "Documentation comment"
        verbose_name_plural = "Documentation comments"

    def __str__(self) -> str:
        return f"comment:{self.doc_key}:{self.author_name or self.author_id}"

    def as_dict(self) -> dict[str, Any]:
        """Serialize for the comments JSON API."""
        return {
            "id": self.id,
            "body": self.body,
            "section_id": self.section_id,
            "section_label": self.section_label,
            "author_name": self.author_name or self.author.username,
            "author_username": self.author.username,
            "created_at": self.created_at.isoformat(),
        }


class WorkflowSchedule(models.Model):
    """A recurring schedule that runs a workflow's default-run headlessly.

    One row per (workflow definition, scope, owner). ``owner`` is the user whose
    persisted Connect token (see UserConnectToken) is used to mint a fresh access
    token at fire time — no token is stored here. Only workflows whose template
    supports a default run can be scheduled.
    """

    CADENCE_DAILY = "daily"
    CADENCE_INTERVAL = "interval"
    CADENCE_WEEKDAYS = "weekdays"
    CADENCE_WEEKLY = "weekly"
    CADENCE_BIWEEKLY = "biweekly"
    CADENCE_MONTHLY = "monthly"
    CADENCE_CHOICES = [
        (CADENCE_DAILY, "Daily"),
        (CADENCE_INTERVAL, "Every N hours"),
        (CADENCE_WEEKDAYS, "Weekdays (Mon–Fri)"),
        (CADENCE_WEEKLY, "Weekly"),
        (CADENCE_BIWEEKLY, "Every 2 weeks"),
        (CADENCE_MONTHLY, "Monthly"),
    ]

    STATUS_OK = "ok"
    STATUS_FAILED = "failed"
    STATUS_AUTH_EXPIRED = "auth_expired"
    STATUS_RUNNING = "running"
    STATUS_CHOICES = [
        (STATUS_OK, "OK"),
        (STATUS_FAILED, "Failed"),
        (STATUS_AUTH_EXPIRED, "Auth expired"),
        (STATUS_RUNNING, "Running"),
    ]

    definition_id = models.IntegerField()
    definition_name = models.CharField(max_length=255, blank=True)
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workflow_schedules",
    )

    cadence = models.CharField(max_length=16, choices=CADENCE_CHOICES)
    hour = models.PositiveSmallIntegerField(default=6)  # 0-23 UTC
    day_of_week = models.PositiveSmallIntegerField(null=True, blank=True)  # 0=Mon..6=Sun, weekly only
    day_of_month = models.PositiveSmallIntegerField(null=True, blank=True)  # 1-28, monthly only
    # INTERVAL cadence only: hours between fires. Restricted to divisors of 24
    # (see schedules.INTERVAL_HOURS_CHOICES) so every fire time stays derivable
    # from the clock alone -- compute_next_run keeps no schedule history, so a
    # non-divisor would leave a short gap at each day boundary with nowhere to
    # carry the offset. `hour` is the anchor within the grid: hour=0 with
    # interval_hours=6 fires at 00/06/12/18 UTC.
    interval_hours = models.PositiveSmallIntegerField(null=True, blank=True)

    enabled = models.BooleanField(default=True)
    next_run_at = models.DateTimeField(null=True, blank=True)
    last_run_at = models.DateTimeField(null=True, blank=True)
    last_status = models.CharField(max_length=16, choices=STATUS_CHOICES, null=True, blank=True)
    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "labs_workflow_schedule"
        constraints = [
            models.UniqueConstraint(
                fields=["definition_id", "opportunity_id", "program_id", "owner"],
                name="uniq_schedule_per_definition_scope_owner",
                # opportunity_id/program_id are nullable scope columns; without this,
                # Postgres treats NULL as distinct from NULL and the constraint never
                # fires for two schedules that are both opportunity- or program-scoped
                # (the common case — one of the two is always null).
                nulls_distinct=False,
            )
        ]

    def __str__(self) -> str:
        return f"WorkflowSchedule(def={self.definition_id}, {self.cadence}, owner={self.owner_id})"

    def recompute_next_run(self, from_dt) -> None:
        """Set ``next_run_at`` to the next fire time after ``from_dt`` and save it."""
        from connect_labs.workflow.schedules import compute_next_run

        self.next_run_at = compute_next_run(
            self.cadence,
            self.hour,
            self.day_of_week,
            self.day_of_month,
            from_dt,
            interval_hours=self.interval_hours,
        )
        if self.pk:
            self.save(update_fields=["next_run_at"])


class LabsOrg(models.Model):
    """An organisation, as labs knows it — until Connect knows it too.

    Labs builds features ahead of production, so it routinely needs to name
    organisations Connect has no row for: a manufacturer we buy cartons from,
    a partner working with us before anyone creates their Connect account, a
    delivery partner known only by a slug on an export. Every app that needed
    one invented its own registry — `supply_chain.Party`,
    `supply_chain.Supplier`, `pulse.PulsePartner` — three tables meaning "an
    organisation in real life", drifting apart.

    **This does not wait on production; that is the point.** Labs names an
    organisation whenever it needs one. A row carries both join keys from the
    start, so linking to Connect later is an update rather than a migration,
    and production catching up is a linking event rather than a redesign.
    What has to shrink to zero is not this table but the DUPLICATION: every
    organisation Connect does represent should be linked to it and should
    stop being separately edited here.

    **Identity only.** Name, country, and the keys that join it to Connect.
    Domain state belongs to the domain: a supplier's sourcing lifecycle and
    its contacts stay on the supplier profile, a delivery partner's telemetry
    stays in pulse. A field one feature invented makes the whole row
    unmigratable, because the blocker becomes Connect not having that field.

    **Two join keys, because Connect hands out two.** A real organisation has
    an integer id; a labs-only synthetic one is identified by slug
    (`labs-synthetic-…`, see `labs/synthetic/org_tree`). Holding only the
    integer is what made synthetic organisations unrepresentable and pushed
    callers into inventing unlinked local rows for organisations that plainly
    exist.

    Matching, stated so a reconciliation cannot be improvised later:

      * `connect_organization_id` is the identity. It does not change.
      * the slug is a finding aid, and matches only a row with no id yet.
        Once an id is set, a slug that disagrees is refreshed rather than
        treated as a second candidate — a rename is not a new organisation.
      * two local rows resolving to one Connect id is a conflict, reported
        and never merged: merging folds two histories — two supplier
        profiles, two sets of contacts — on the strength of a string.
    """

    # A stable local handle, because an organisation needs referring to
    # before Connect has one, and because pulse matches on slugs. Unique
    # across labs: this is ONE registry. An organisation is the same
    # organisation in every programme it appears in, which is the whole
    # correction this model exists to make.
    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=300)
    short_name = models.CharField(max_length=120, blank=True, default="")
    country = models.CharField(max_length=2, blank=True, default="")

    # The join. Either, neither, or — once reconciled — both.
    connect_organization_id = models.IntegerField(null=True, blank=True, unique=True, db_index=True)
    connect_organization_slug = models.CharField(max_length=200, blank=True, default="", db_index=True)

    # Slugs no matcher can reach, pointed here by a person. Pulse learned
    # this the hard way: a wrong parent name is worse than a visible slug.
    aliases = models.JSONField(default=list, blank=True)
    notes = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "labs organisation"

    def __str__(self) -> str:
        return self.name

    @property
    def is_linked(self) -> bool:
        """Whether Connect represents this organisation. The rest is the backlog."""
        return self.connect_organization_id is not None

    def matches(self, *, organization_id=None, slug=None) -> bool:
        """Whether this row is the organisation those keys describe."""
        if self.connect_organization_id is not None:
            # Linked: the id is the identity, and it is the ONLY thing that
            # answers. Falling through to the slug here let a stale name
            # resolve to an organisation that has already been reconciled --
            # the opposite of the rule this docstring states, and the way a
            # rename turns into a mis-attribution.
            return organization_id is not None and int(organization_id) == self.connect_organization_id
        if slug:
            candidates = {self.connect_organization_slug, *(self.aliases or [])}
            return slug in {c for c in candidates if c}
        return False
