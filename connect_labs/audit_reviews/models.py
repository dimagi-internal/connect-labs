"""Audit reviews: follow-up tickets for entities a source report (KC Pulse) shows as yellow or red.

Real labs-DB tables (like pulse, benchmarks and supply_chain): the reviews are labs' own data.

What is stored, and what is derived
-----------------------------------
STORED, the inputs and the source of truth:

- ``AuditSource``  one per audit workflow: which report it follows and how it is set up.
- ``SourceRun``    a frozen copy of the figures from one saved run of that report.
- ``Decision``     what staff decided on a run's proposal: open a review, close one, or
                   set a proposal aside this week.
- ``TeamEvent``    an action or comment staff recorded on a review.

DERIVED, rebuilt by ``service.recompute`` after every new run or decision, by replaying every
stored run through the rules (``engine.py``) with the stored decisions:

- ``Review``       one row per review, for lists and analytics.
- ``AuditWorld``   the page's whole answer, served as it is.

So a change to the rules applies to past weeks as well (decided 2026-11-23, while the rules are
still being iterated); only staff decisions and team events are kept as they were made.
"""

from django.conf import settings
from django.db import models


class AuditSource(models.Model):
    """One audit workflow and the report it follows."""

    MODE_TEST = "test"
    MODE_LIVE = "live"

    workflow_id = models.IntegerField(unique=True, help_text="The audit workflow's definition id.")
    opportunity_id = models.IntegerField(null=True, blank=True, help_text="The scope the workflows are read in.")
    program_id = models.IntegerField(null=True, blank=True)
    source_workflow_id = models.IntegerField(db_index=True, help_text="The report followed (KC Pulse).")
    go_live_run_id = models.IntegerField(
        null=True, blank=True, help_text="The first source run that can open reviews; earlier runs are history."
    )
    mode = models.CharField(max_length=10, default=MODE_TEST)
    program_name = models.CharField(max_length=200, blank=True)
    setup = models.JSONField(
        default=dict, blank=True, help_text="Registry set-up: pool, parents, supporting, known, registry."
    )
    settings = models.JSONField(default=dict, blank=True, help_text="Saved rule settings (engine.default_config).")
    imported_from_run_id = models.IntegerField(
        null=True, blank=True, help_text="The workflow run whose saved decisions were imported, once."
    )
    last_synced_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = "audit_reviews"
        db_table = "labs_audit_review_source"

    def __str__(self):
        return f"Audit reviews for workflow {self.workflow_id} (follows {self.source_workflow_id})"


class SourceRun(models.Model):
    """The figures the rules read from one completed run of the source report, frozen on first sight."""

    source = models.ForeignKey(AuditSource, on_delete=models.CASCADE, related_name="source_runs")
    run_id = models.IntegerField()
    period_start = models.CharField(max_length=10, blank=True)
    period_end = models.CharField(max_length=10, blank=True)
    completed_at = models.CharField(max_length=40, blank=True)
    data = models.JSONField(help_text="engine.from_kc_run's shape: programme, llo, opportunity, worker, measures.")
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "audit_reviews"
        db_table = "labs_audit_review_source_run"
        constraints = [models.UniqueConstraint(fields=["source", "run_id"], name="audit_review_source_run_once")]
        ordering = ["period_end", "completed_at"]


class Decision(models.Model):
    """A person's decision on one entity's proposal in one source run."""

    OPEN = "open"
    CLOSE = "close"
    DISMISS = "dismiss"
    KINDS = [(OPEN, "Open a review"), (CLOSE, "Close a review"), (DISMISS, "Not this week")]

    source = models.ForeignKey(AuditSource, on_delete=models.CASCADE, related_name="decisions")
    kind = models.CharField(max_length=10, choices=KINDS)
    run_id = models.IntegerField(help_text="The source run the decision was made on.")
    entity = models.CharField(max_length=300, help_text="program | llo:<name> | opp:<id> | flw:<username>")
    review_number = models.IntegerField(null=True, blank=True, help_text="For an open: the review's number.")
    status = models.CharField(max_length=30, blank=True, help_text="For a close: closed_improved and so on.")
    note = models.TextField(blank=True)
    decided_by = models.CharField(max_length=200, blank=True, help_text="The name shown on the timeline.")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    decided_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "audit_reviews"
        db_table = "labs_audit_review_decision"
        constraints = [
            models.UniqueConstraint(fields=["source", "kind", "run_id", "entity"], name="audit_review_decision_once"),
            models.UniqueConstraint(
                fields=["source", "review_number"],
                condition=models.Q(review_number__isnull=False),
                name="audit_review_number_once",
            ),
        ]
        ordering = ["decided_at", "id"]


class TeamEvent(models.Model):
    """An action or comment staff recorded on a review."""

    source = models.ForeignKey(AuditSource, on_delete=models.CASCADE, related_name="team_events")
    event_id = models.CharField(max_length=60, help_text="Made by the page, so a retried save is not doubled.")
    review_key = models.CharField(max_length=320, help_text="<entity>@<run the review was opened in>")
    type = models.CharField(max_length=30)
    payload = models.JSONField(default=dict, blank=True)
    created_by = models.CharField(max_length=200, blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField()

    class Meta:
        app_label = "audit_reviews"
        db_table = "labs_audit_review_team_event"
        constraints = [models.UniqueConstraint(fields=["source", "event_id"], name="audit_review_event_once")]
        ordering = ["created_at", "id"]


class Review(models.Model):
    """Derived: one review as the last recompute left it."""

    source = models.ForeignKey(AuditSource, on_delete=models.CASCADE, related_name="reviews")
    number = models.IntegerField()
    entity = models.CharField(max_length=300)
    entity_type = models.CharField(max_length=20)
    name = models.CharField(max_length=300, blank=True)
    llo = models.CharField(max_length=200, blank=True)
    status = models.CharField(max_length=30)
    opened_run_id = models.IntegerField()
    opened_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    metrics = models.JSONField(default=list)
    previous_number = models.IntegerField(null=True, blank=True)
    team_event_count = models.IntegerField(default=0)
    last_action_at = models.DateTimeField(null=True, blank=True)
    last_action_type = models.CharField(max_length=40, blank=True)

    class Meta:
        app_label = "audit_reviews"
        db_table = "labs_audit_review"
        constraints = [models.UniqueConstraint(fields=["source", "number"], name="audit_review_one_per_number")]
        ordering = ["number"]


class AuditWorld(models.Model):
    """Derived: what the page shows, as the last recompute produced it."""

    source = models.OneToOneField(AuditSource, on_delete=models.CASCADE, related_name="world")
    engine_version = models.IntegerField()
    fingerprint = models.CharField(max_length=64, help_text="Of the inputs it was computed from.")
    payload = models.JSONField()
    computed_at = models.DateTimeField()

    class Meta:
        app_label = "audit_reviews"
        db_table = "labs_audit_review_world"
