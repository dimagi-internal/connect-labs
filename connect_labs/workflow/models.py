"""The workflow app's one table: a record of each workflow action that was run.

Everything else a workflow owns lives in Connect's LabsRecord API. An action's
EFFECTS are written where they always are (tasks as LabsRecords, conversations in
OCS); this row is the labs-side record of the run itself — who asked, through which
door (a button on the page, the labs MCP, canopy acting as the visitor), for which
workers, and what happened to each. It is read while the action runs (a page or an
agent polling its progress) and afterwards as its audit trail. It names workers by
username and carries the prompt text, and no patient data.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models


class WorkflowActionExecution(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        COMPLETED_WITH_ERRORS = "completed_with_errors", "Completed with errors"
        FAILED = "failed", "Failed"

    #: The person the action ran as: its tasks are theirs and its conversations
    #: are started with their OCS account.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    #: "page" (a button), "mcp" (a PAT or MCP sign-in), or "canopy" (canopy acting
    #: as the visitor). `actor` is canopy's agent slug when there is one.
    via = models.CharField(max_length=16)
    actor = models.CharField(max_length=100, blank=True, default="")

    definition_id = models.IntegerField()
    run_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)

    #: The workflow's name for the action (`config.actions[].key`) and its type
    #: (`actions.ACTION_TYPES`), both as they were when it ran.
    action_key = models.CharField(max_length=64)
    action_type = models.CharField(max_length=64)
    #: The resolved arguments that were previewed and confirmed.
    arguments = models.JSONField()

    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    #: worker key -> {"status": "ok"|"failed", "task_id", "session_id", "error", "at"}
    results = models.JSONField(default=dict, blank=True)
    error = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [models.Index(fields=["run_id", "action_key"])]
        ordering = ["-created_at"]

    def worker_keys(self) -> list[str]:
        return [item["key"] for item in (self.arguments or {}).get("workers") or []]

    def as_dict(self) -> dict:
        results = self.results or {}
        keys = self.worker_keys()
        ok = sum(1 for k in keys if (results.get(k) or {}).get("status") == "ok")
        failed = sum(1 for k in keys if (results.get(k) or {}).get("status") == "failed")
        return {
            "id": self.pk,
            "action": self.action_key,
            "type": self.action_type,
            "status": self.status,
            "via": self.via,
            "run_id": self.run_id,
            "progress": {"total": len(keys), "done": ok + failed, "ok": ok, "failed": failed},
            "results": results,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


# =============================================================================
# Template workflows: a template's render, config defaults and snapshot spec as DATA
# =============================================================================
#
# A workflow that follows `render_source: {"workflow": <id>, <scope>}` renders the
# PUBLISHED version of that template workflow and inherits its config defaults and
# snapshot_inputs (its own keys win). The content lives here, in the labs DB, and not
# on the template's LabsRecord, for three reasons: a follower in another scope (a
# synthetic twin, another programme) must read it without a cross-scope API call; a
# publish needs an immutable history to roll back to; and a page load must not wait on
# production for it. See workflow/template_workflows.py.


class TemplateWorkflow(models.Model):
    """One template workflow: its editable draft, and which version is live."""

    #: The template's own workflow definition record, and its home scope. A labs-only
    #: (synthetic) record and a production one can share an id, so the scope is part
    #: of the identity: `scope_key` is "opp:<id>" or "program:<id>".
    workflow_id = models.IntegerField()
    scope_key = models.CharField(max_length=40)
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)

    name = models.CharField(max_length=255)
    #: Who may FOLLOW it: "global", "org:<id>" or "program:<id>" (the existing
    #: `template_scope` vocabulary on workflow definitions).
    template_scope = models.CharField(max_length=40)
    #: The code template its render is written against (`config.templateType`). A
    #: follower must carry the same one: the render reads that template's pipelines,
    #: config and snapshot contract.
    template_type = models.CharField(max_length=100, blank=True, default="")
    #: Where it started: "code:<template key>" or "workflow:<id>". Informational; the
    #: seed has no role after creation.
    seeded_from = models.CharField(max_length=120, blank=True, default="")

    #: Only owners edit the draft, publish, roll back and preview the draft.
    owners = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="owned_template_workflows")

    draft_render_code = models.TextField(blank=True, default="")
    draft_config = models.JSONField(default=dict, blank=True)
    draft_snapshot_inputs = models.JSONField(null=True, blank=True)
    #: Bumped on every draft edit: the optimistic-concurrency token for edits.
    draft_revision = models.IntegerField(default=1)
    draft_updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    draft_updated_at = models.DateTimeField(auto_now_add=True)

    #: The version every follower renders. Null until the first publish.
    published = models.ForeignKey(
        "TemplateWorkflowVersion", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["workflow_id", "scope_key"], name="uniq_template_workflow")]


class TemplateWorkflowVersion(models.Model):
    """An immutable published version. A rollback publishes a COPY of an older one,
    so the history only ever grows and always says what was live when."""

    template = models.ForeignKey(TemplateWorkflow, on_delete=models.CASCADE, related_name="versions")
    number = models.IntegerField()
    render_code = models.TextField()
    config = models.JSONField(default=dict, blank=True)
    snapshot_inputs = models.JSONField(null=True, blank=True)
    note = models.TextField(blank=True, default="")
    #: Set when this version is a rollback: the version number it copies.
    restores_version = models.IntegerField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    published_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["template", "number"], name="uniq_template_version")]
        ordering = ["-number"]


class TemplateWorkflowFollower(models.Model):
    """A workflow set to follow a template workflow -- the list a preview and a
    publish report against. The follow itself is the definition's `render_source`."""

    template = models.ForeignKey(TemplateWorkflow, on_delete=models.CASCADE, related_name="followers")
    workflow_id = models.IntegerField()
    scope_key = models.CharField(max_length=40)
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)
    followed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    followed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["template", "workflow_id", "scope_key"], name="uniq_template_follower")
        ]
