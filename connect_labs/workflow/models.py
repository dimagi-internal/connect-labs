"""The workflow app's tables: a record of each workflow action that was run, and the
coaching charts its previews froze, and the few definitions recorded as kept out of
the sandbox frame (``SandboxExemption``).

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
    #: The run it acted on -- or none, for a PAGE's action, which acts within the
    #: page's scope instead (``page_scope``, e.g. "program:25"; workflow/actions.py).
    run_id = models.IntegerField(db_index=True, null=True, blank=True)
    page_scope = models.CharField(max_length=200, blank=True, default="")
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
            "page_scope": self.page_scope or None,
            "progress": {"total": len(keys), "done": ok + failed, "ok": ok, "failed": failed},
            "results": results,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }


class CoachChart(models.Model):
    """A coaching chart as a person previewed it, frozen: the Vega-Lite spec, the data
    Labs resolved for it, and Labs' caption (``coach_charts/chart.py``).

    Made when a coaching preview pictures a worker, and never changed: the id is a
    hash of the chart's content and of who previewed it for which run and worker, so
    the same preview finds the same row. The conversation that is sent links to it
    (``coach_image``), so the picture Open Chat Studio fetches is the one that was
    approved -- not re-graded when it is fetched. Holds a worker's own figures and
    anonymous peers' (``Peer A`` ...) figures, no names but the worker's first name.
    """

    id = models.CharField(max_length=32, primary_key=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    run_id = models.IntegerField()
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)
    #: The worker it pictures ('<opportunity_id>::<username>').
    worker_key = models.CharField(max_length=200)
    #: What was asked for (``chart.normalise_request``) and the chart that resolved.
    request = models.JSONField()
    chart = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=["run_id", "worker_key"])]


class SandboxExemption(models.Model):
    """One workflow definition kept out of the sandbox frame under ``LABS_WORKFLOW_SANDBOX=all``,
    recorded by hand (``manage.py workflow_sandbox_exempt``), with why.

    For a live copy whose render code needs what the frame will never give it (a retired
    endpoint, a definition write) and is kept only to stay readable. It holds for THIS
    record, on THIS owner, drawing THIS code: ``render_code_sha256`` is the digest
    (``sandbox.render_code_digest``) of the effective render code when the row was
    written, and any edit to that code voids it -- the definition is sandboxed again,
    fail closed. A page is never exempt (``sandbox.should_sandbox`` decides pages first),
    and ``?sandbox=1`` still draws an exempt workflow in the frame. Written only by the
    command, never by the app or the MCP: an author cannot exempt their own workflow.
    """

    #: The definition's LabsRecord id (on production Connect, or labs-local for a
    #: synthetic opportunity: the owner tells them apart).
    definition_id = models.IntegerField(unique=True)
    #: The definition's owner, as the scan names it: exactly one of these is set.
    opportunity_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True)
    organization_id = models.CharField(max_length=100, blank=True, default="")
    render_code_sha256 = models.CharField(max_length=64)
    reason = models.TextField()
    #: The labs username the command read the definition as, and wrote the row for.
    added_by = models.CharField(max_length=150)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["definition_id"]

    def owner(self) -> str:
        if self.opportunity_id is not None:
            return f"opportunity:{self.opportunity_id}"
        if self.program_id is not None:
            return f"program:{self.program_id}"
        if self.organization_id:
            return f"organization:{self.organization_id}"
        return "unscoped"
