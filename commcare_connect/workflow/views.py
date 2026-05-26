"""
Workflow views for dynamic AI-generated workflows.

These views handle listing, viewing, and executing workflows that are stored
as LabsRecord objects with React component code for rendering.
"""

import json
import logging
from collections.abc import Generator

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from commcare_connect.decisions.data_access import DecisionsDataAccess
from commcare_connect.labs import s3_export
from commcare_connect.labs.analysis.sse_streaming import BaseSSEStreamView
from commcare_connect.labs.context import get_org_data
from commcare_connect.utils.feature_access import can_create_from_template, get_allowed_templates
from commcare_connect.workflow.data_access import PipelineDataAccess, WorkflowDataAccess
from commcare_connect.workflow.templates import TEMPLATES
from commcare_connect.workflow.templates import create_workflow_from_template as create_from_template

logger = logging.getLogger(__name__)


def _resolve_pipeline_sources_for_run(pipeline_access, pipeline_sources: list[dict]):
    """Pre-build configs for every pipeline source and topologically sort
    them so JOIN dependencies execute before their dependents.

    Returns (ordered_sources, configs_by_alias). The caller passes
    configs_by_alias to `resolve_join_hashes` and consumes ordered_sources
    in order so each pipeline runs after the pipelines its JOINs read from.

    Why topological sort matters: visits.joins[0]={"from_alias":"registrations"}
    means visits' SQL reads `labs_computed_visit_cache WHERE config_hash =
    <registrations_hash>`. If registrations hasn't run yet that cache slot is
    empty and visits' JOIN returns NULL for every joined field — silent
    correctness gap, not an error. Running registrations FIRST populates the
    slot before visits queries it.

    Edge cases:
    - A pipeline whose schema can't be loaded is excluded from the topo sort
      and appended at the end so the rest of the workflow still progresses.
      The streaming loop will surface the per-pipeline error from its own
      definition lookup.
    - Cycles (rare, would mean two pipelines JOIN each other) fall through to
      definition order rather than infinite-looping. Worth detecting later.
    """
    # Build {alias: (source, config)} keeping insertion order for tie-breaking
    pipeline_meta: dict[str, dict] = {}
    configs_by_alias: dict = {}
    for source in pipeline_sources:
        pid = source.get("pipeline_id")
        alias = source.get("alias", f"pipeline_{pid}")
        if not pid:
            continue
        pipeline_def = pipeline_access.get_definition(pid)
        if not pipeline_def or not pipeline_def.schema:
            # Defer surfacing — the streaming loop emits a per-pipeline
            # "Pipeline not found" event for this case.
            pipeline_meta[alias] = {"source": source, "config": None}
            continue
        try:
            cfg = pipeline_access._schema_to_config(pipeline_def.schema, pid)
            pipeline_meta[alias] = {"source": source, "config": cfg}
            configs_by_alias[alias] = cfg
        except Exception:
            logger.exception("[PipelineSort] Failed to build config for pipeline %s (%s)", pid, alias)
            pipeline_meta[alias] = {"source": source, "config": None}

    # Topological order: a pipeline depends on every JOIN's from_alias. Use
    # a simple DFS-based topo sort with cycle protection (cycles fall back
    # to insertion order).
    visited: set[str] = set()
    visiting: set[str] = set()
    ordered_aliases: list[str] = []

    def _visit(alias: str):
        if alias in visited or alias in visiting:
            return
        visiting.add(alias)
        cfg = configs_by_alias.get(alias)
        if cfg is not None:
            for j in getattr(cfg, "joins", None) or []:
                if j.from_alias in pipeline_meta:
                    _visit(j.from_alias)
        visiting.discard(alias)
        visited.add(alias)
        ordered_aliases.append(alias)

    for alias in pipeline_meta:
        _visit(alias)

    ordered_sources = [pipeline_meta[a]["source"] for a in ordered_aliases]
    return ordered_sources, configs_by_alias


class WorkflowTemplateListAPIView(LoginRequiredMixin, View):
    """API endpoint to list available workflow templates."""

    def get(self, request):
        """Return list of workflow templates with metadata for UI rendering."""
        return JsonResponse({"templates": get_allowed_templates(request.user)})


class WorkflowListView(LoginRequiredMixin, TemplateView):
    """List all workflow definitions the user can access."""

    template_name = "workflow/list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check for labs context
        labs_context = getattr(self.request, "labs_context", {})
        context["has_context"] = bool(labs_context.get("opportunity_id") or labs_context.get("program_id"))
        context["opportunity_id"] = labs_context.get("opportunity_id")
        context["opportunity_name"] = labs_context.get("opportunity_name")

        # Restrict Create Workflow button based on allowed templates
        allowed_templates = get_allowed_templates(self.request.user)
        context["can_create_workflow"] = bool(allowed_templates)

        # Get workflow definitions and their runs
        if context["has_context"]:
            data_access = None
            pipeline_access = None
            try:
                from commcare_connect.workflow.data_access import PipelineDataAccess

                data_access = WorkflowDataAccess(request=self.request)
                pipeline_access = PipelineDataAccess(request=self.request)
                definitions = data_access.list_definitions()

                # Build a cache of pipeline names
                pipeline_cache = {}

                # Fetch all runs once, then group by definition_id
                all_runs = data_access.list_runs()
                runs_by_def = {}
                for run in all_runs:
                    def_id = run.data.get("definition_id")
                    runs_by_def.setdefault(def_id, []).append(run)

                # For each definition, get its runs and pipeline info
                workflows_with_runs = []
                for definition in definitions:
                    runs = runs_by_def.get(definition.id, [])
                    # Sort runs by ID descending (latest first)
                    runs.sort(key=lambda r: r.id, reverse=True)

                    # Get pipeline details for this workflow
                    pipelines = []
                    for source in definition.pipeline_sources:
                        pipeline_id = source.get("pipeline_id")
                        alias = source.get("alias")
                        if pipeline_id:
                            # Use cache to avoid repeated lookups
                            if pipeline_id not in pipeline_cache:
                                pipeline_def = pipeline_access.get_definition(pipeline_id)
                                pipeline_cache[pipeline_id] = pipeline_def
                            pipeline_def = pipeline_cache.get(pipeline_id)
                            pipelines.append(
                                {
                                    "id": pipeline_id,
                                    "alias": alias,
                                    "name": pipeline_def.name if pipeline_def else f"Pipeline {pipeline_id}",
                                }
                            )

                    workflows_with_runs.append(
                        {
                            "definition": definition,
                            "runs": runs,
                            "run_count": len(runs),
                            "pipelines": pipelines,
                            "template_type": definition.template_type,
                            "latest_run_id": runs[0].id if runs else 0,
                        }
                    )

                context["workflows"] = workflows_with_runs
                context["definitions"] = definitions  # Keep for backwards compatibility
                context["available_templates"] = allowed_templates
            except Exception as e:
                logger.error(f"Failed to load workflow definitions: {e}", exc_info=True)
                context["workflows"] = []
                context["definitions"] = []
                context["available_templates"] = allowed_templates
                context["error"] = str(e)
            finally:
                if pipeline_access is not None:
                    pipeline_access.close()
                if data_access is not None:
                    data_access.close()
        else:
            context["workflows"] = []
            context["definitions"] = []
            context["available_templates"] = allowed_templates

        return context


class PipelineListView(LoginRequiredMixin, TemplateView):
    """List all pipeline definitions the user can access."""

    template_name = "workflow/pipeline_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        from commcare_connect.workflow.data_access import PipelineDataAccess

        # Check for labs context
        labs_context = getattr(self.request, "labs_context", {})
        context["has_context"] = bool(labs_context.get("opportunity_id") or labs_context.get("program_id"))
        context["opportunity_id"] = labs_context.get("opportunity_id")
        context["opportunity_name"] = labs_context.get("opportunity_name")

        # Get pipeline definitions
        if context["has_context"]:
            try:
                data_access = PipelineDataAccess(request=self.request)
                definitions = data_access.list_definitions()
                data_access.close()

                pipelines = []
                for definition in definitions:
                    pipelines.append(
                        {
                            "definition": definition,
                        }
                    )

                context["pipelines"] = pipelines
            except Exception as e:
                logger.error(f"Failed to load pipeline definitions: {e}")
                context["pipelines"] = []
                context["error"] = str(e)
        else:
            context["pipelines"] = []

        return context


class WorkflowDefinitionView(LoginRequiredMixin, TemplateView):
    """View workflow definition details."""

    template_name = "workflow/detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        definition_id = self.kwargs.get("definition_id")

        try:
            data_access = WorkflowDataAccess(request=self.request)
            definition = data_access.get_definition(definition_id)
            context["definition"] = definition
            context["definition_json"] = json.dumps(definition.data if definition else {}, indent=2)
        except Exception as e:
            logger.error(f"Failed to load workflow definition {definition_id}: {e}")
            context["error"] = str(e)

        return context


class WorkflowRunView(LoginRequiredMixin, TemplateView):
    """Main UI for executing a workflow. Also handles edit mode via ?edit=true."""

    template_name = "workflow/run.html"

    def get(self, request, *args, **kwargs):
        """Render the workflow runner.

        Pre-2026-04-30 this view auto-created a run on every visit without a
        `run_id`, which silently piled up untouched run records on every
        reload. The lifecycle (see docs/plans/2026-05-04-run-state-final.md)
        removes the auto-create:

        - `?run_id=<id>` → render that specific run (in_progress or completed)
        - `?edit=true`   → preview-only, no run record involved
        - no run_id      → render the run picker (list of past runs +
                          "Start Run" button). Creating a run is now an
                          explicit user action.

        The picker is just the same template with `select_run_mode=True` —
        run.html branches on it.
        """
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        definition_id = self.kwargs.get("definition_id")

        # Check for run_id in query params (to load existing run)
        run_id = self.request.GET.get("run_id")
        # Check for edit mode (temporary run, not persisted)
        is_edit_mode = self.request.GET.get("edit") == "true"

        # Get labs context
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")
        context["opportunity_id"] = opportunity_id
        context["opportunity_name"] = labs_context.get("opportunity_name")
        context["has_context"] = bool(opportunity_id)
        context["user_opportunities"] = (get_org_data(self.request) or {}).get("opportunities", [])

        if not opportunity_id:
            context["error"] = "Please select an opportunity to run this workflow."
            return context

        try:
            data_access = WorkflowDataAccess(request=self.request)

            # Get workflow definition
            definition = data_access.get_definition(definition_id)
            if not definition:
                context["error"] = f"Workflow definition {definition_id} not found."
                return context
            context["definition"] = definition

            # Sync render code from template if requested via ?sync=true
            # Supports ?sync=true&template=mbw_monitoring to specify template explicitly
            if self.request.GET.get("sync") == "true":
                explicit_template = self.request.GET.get("template")
                matched_template = None

                if explicit_template:
                    # Normalize dashes to underscores (e.g. mbw-monitoring → mbw_monitoring)
                    explicit_template = explicit_template.replace("-", "_")
                if explicit_template and explicit_template in TEMPLATES:
                    matched_template = explicit_template
                else:
                    name_lower = definition.name.lower().replace(" ", "_")
                    for key, tmpl in TEMPLATES.items():
                        if key == name_lower or tmpl["name"].lower() == definition.name.lower():
                            matched_template = key
                            break

                if matched_template:
                    data_access.save_render_code(
                        definition_id=definition_id,
                        component_code=TEMPLATES[matched_template]["render_code"],
                        version=1,
                    )
                    logger.info(
                        f"Synced render code for definition {definition_id} from template '{matched_template}'"
                    )

            # Render code always comes from the workflow's LabsRecord — same
            # in local and prod. Local doesn't shortcut to the template file:
            # a workflow's render_code is data, not source, and serving it
            # from disk in DEBUG diverges local behavior from prod in a
            # confusing way. Iteration loop for render-code edits is now:
            # edit .js → `inv push-render` → reload page (works against any
            # environment, including labs.connect.dimagi.com).
            render_code = data_access.get_render_code(definition_id)
            context["render_code"] = render_code.data.get("component_code") if render_code else None

            # Determine effective opportunity list (fallback to primary)
            effective_opp_ids = definition.opportunity_ids or [opportunity_id]

            # Fetch workers for each opp and tag with opportunity_id
            workers: list[dict] = []
            for oid in effective_opp_ids:
                try:
                    for w in data_access.get_workers(oid):
                        workers.append({**w, "opportunity_id": oid})
                except Exception:
                    logger.exception("Failed to load workers for opp %s", oid)
            context["workers"] = workers

            # Hoisted: decisions are loaded only on the run-id branch (live
            # query — never frozen on the watched workflow's snapshot, per
            # spec §3.3). For edit mode and the picker branch, this stays [].
            decisions_for_run: list[dict] = []

            # Get or create run based on mode
            if is_edit_mode:
                # Edit mode: create temporary run (not persisted)
                from datetime import datetime, timedelta, timezone

                today = datetime.now(timezone.utc).date()
                week_start = today - timedelta(days=today.weekday())
                week_end = week_start + timedelta(days=6)

                run_data = {
                    "id": 0,  # Temporary ID — edit mode is not persisted.
                    "definition_id": definition_id,
                    "opportunity_id": opportunity_id,
                    "opportunity_ids": effective_opp_ids,
                    "opportunity_name": labs_context.get("opportunity", {}).get("name"),
                    # Edit mode is in_progress for render-code purposes; the FE
                    # sees `is_edit_mode: true` separately and disables persistence.
                    "status": "in_progress",
                    "state": {"worker_states": {}},
                    "period_start": week_start.isoformat(),
                    "period_end": week_end.isoformat(),
                }
                context["is_edit_mode"] = True
            elif run_id:
                # Load existing run by ID
                run = data_access.get_run(int(run_id))
                if not run:
                    context["error"] = f"Workflow run {run_id} not found."
                    return context
                run_data = {
                    "id": run.id,
                    "definition_id": definition_id,
                    "opportunity_id": opportunity_id,
                    "opportunity_ids": effective_opp_ids,
                    "opportunity_name": labs_context.get("opportunity", {}).get("name"),
                    # Canonical lifecycle: in_progress | completed. The proxy
                    # also maps any legacy `active`/`frozen` rows back to this
                    # vocabulary.
                    "status": run.status,
                    "state": run.state,
                    "period_start": run.period_start,
                    "period_end": run.period_end,
                    "completed_at": run.completed_at,
                    # Snapshot is null while in_progress; populated on completion.
                    # The useRunView FE helper reads it when status='completed' so
                    # render code never recomputes against live data on a finalized run.
                    "snapshot": run.snapshot,
                }
                context["is_edit_mode"] = False

                # Load Decisions for this run (queried live; not stored on the
                # run snapshot). Render code uses these via
                # `view.decisionsFor(username)` to show what the LLO recorded
                # during the run, in both in_progress and completed modes —
                # Decision lifecycle is live by design (spec §3.3).
                try:
                    dda = DecisionsDataAccess(request=self.request, opportunity_id=opportunity_id)
                    for d in dda.get_decisions_for_run(int(run_id)):
                        decisions_for_run.append(
                            {
                                "id": d.id,
                                "flw_id": d.flw_id,
                                "decision_type": d.decision_type,
                                "reason_key": d.reason_key,
                                "reason_label": d.reason_label,
                                "audit_session_ids": d.audit_session_ids,
                                "task_ids": d.task_ids,
                                "kpi_snapshot": d.kpi_snapshot,
                                "notes": d.notes,
                                "decided_at": d.decided_at,
                                "decided_by": d.decided_by,
                            }
                        )
                except Exception:
                    logger.warning("Failed to load decisions for run %s", run_id, exc_info=True)
            else:
                # No run_id and not edit mode — render the run picker. Past runs
                # are listed; user clicks "Open" to load one or "Start Run" to
                # create a fresh active run via POST /api/<def_id>/run/start/.
                # No auto-create, ever. (Pre-2026-04-30 this branch silently spawned
                # a new run on every URL hit.)
                context["select_run_mode"] = True
                past_runs = []
                for r in data_access.list_runs(definition_id):
                    if r.opportunity_id != opportunity_id:
                        continue
                    past_runs.append(
                        {
                            "id": r.id,
                            "status": r.status,
                            "completed_at": r.completed_at,
                            "period_start": r.period_start,
                            "period_end": r.period_end,
                            "created_at": r.created_at,
                        }
                    )
                # Most recent first.
                past_runs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
                context["past_runs"] = past_runs
                context["start_run_url"] = f"/labs/workflow/api/{definition_id}/run/start/"
                return context

            # Pipeline data will be loaded async via SSE - don't block page load
            # Pass empty data initially; frontend will connect to SSE stream
            pipeline_data = {}

            # Prepare data for React (pass as dict, json_script will handle encoding)
            context["workflow_data"] = {
                "definition": definition.data,
                "definition_id": definition.id,
                "opportunity_id": opportunity_id,
                "opportunity_ids": effective_opp_ids,
                "multi_opp": definition.multi_opp,
                "render_code": context.get("render_code"),
                "instance": run_data,
                "is_edit_mode": is_edit_mode,
                "workers": workers,
                "pipeline_data": pipeline_data,
                "decisions": decisions_for_run,
                "links": {
                    "auditUrlBase": "/audit/create/",
                    "taskUrlBase": "/tasks/new/",
                },
                "apiEndpoints": {
                    # In edit mode, state updates are local only
                    "updateState": None if is_edit_mode else f"/labs/workflow/api/run/{run_data['id']}/state/",
                    "getWorkers": "/labs/workflow/api/workers/",
                    "getPipelineData": f"/labs/workflow/api/{definition_id}/pipeline-data/",
                    # SSE stream for async pipeline data loading
                    "streamPipelineData": f"/labs/workflow/api/{definition_id}/pipeline-data/stream/",
                    # Framework: auth-status for declared auth_requires
                    "authStatus": "/labs/workflow/api/auth-status/",
                    # MBW monitoring actions
                    "saveWorkerResult": f"/labs/workflow/api/run/{run_data['id']}/worker-result/",
                    # Single completion verb — handles snapshot build + status flip atomically.
                    "completeRun": (None if is_edit_mode else f"/labs/workflow/api/run/{run_data['id']}/complete/"),
                    # Back-compat alias for mbw_monitoring_v3 render code (it calls
                    # `links.buildSnapshot` to drive completion). Points at the same
                    # /complete/ endpoint as `completeRun`. Don't add new callers —
                    # render code should use `view.complete()` from the view helper.
                    "buildSnapshot": (None if is_edit_mode else f"/labs/workflow/api/run/{run_data['id']}/complete/"),
                    "updateOpportunityIds": f"/labs/workflow/api/{definition_id}/opportunity-ids/",
                    # Read-only snapshot inspection (debug); render code reads
                    # instance.snapshot via the useRunView helper, not this URL.
                    "getSnapshot": (None if is_edit_mode else f"/labs/workflow/api/run/{run_data['id']}/snapshot/"),
                },
            }

        except Exception as e:
            logger.error(f"Failed to load workflow {definition_id}: {e}", exc_info=True)
            context["error"] = str(e)

        return context


class WorkflowRunDetailView(LoginRequiredMixin, TemplateView):
    """View a specific workflow run."""

    template_name = "workflow/run_detail.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        run_id = self.kwargs.get("run_id")

        try:
            data_access = WorkflowDataAccess(request=self.request)
            run = data_access.get_run(run_id)
            if run:
                context["run"] = run
                # Template historically renders via `instance` — keep that working.
                context["instance"] = run
                # Also get the definition
                definition_id = run.data.get("definition_id")
                if definition_id:
                    context["definition"] = data_access.get_definition(definition_id)

                # Tasks created by this run (live query — current state, not snapshot).
                from commcare_connect.tasks.data_access import TaskDataAccess

                try:
                    task_da = TaskDataAccess(user=self.request.user, request=self.request)
                    context["tasks_for_run"] = task_da.get_tasks_for_run(run_id)
                    task_da.close()
                except Exception as e:
                    logger.warning(f"Failed to load tasks for run {run_id}: {e}")
                    context["tasks_for_run"] = []
        except Exception as e:
            logger.error(f"Failed to load workflow run {run_id}: {e}")
            context["error"] = str(e)

        return context


class OpportunitySummaryView(LoginRequiredMixin, TemplateView):
    """
    Summary view showing all objects (tasks, audits, workflows, pipelines)
    associated with a particular opportunity.
    """

    template_name = "workflow/summary.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get labs context
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")
        context["opportunity_id"] = opportunity_id
        context["opportunity_name"] = labs_context.get("opportunity_name")
        context["has_context"] = bool(opportunity_id)

        if not opportunity_id:
            context["error"] = "Please select an opportunity to view its summary."
            return context

        # Initialize summary data
        context["tasks_summary"] = self._get_tasks_summary()
        context["audits_summary"] = self._get_audits_summary()
        context["workflows_summary"] = self._get_workflows_summary()
        context["pipelines_summary"] = self._get_pipelines_summary()

        return context

    def _get_tasks_summary(self):
        """Get task summary data."""
        from commcare_connect.tasks.data_access import TaskDataAccess

        summary = {
            "total": 0,
            "by_status": {},
            "recent": [],
            "error": None,
        }

        try:
            data_access = TaskDataAccess(user=self.request.user, request=self.request)
            tasks = data_access.get_tasks()
            data_access.close()

            summary["total"] = len(tasks)

            # Count by status
            status_counts = {}
            for task in tasks:
                status = task.status or "unknown"
                status_counts[status] = status_counts.get(status, 0) + 1
            summary["by_status"] = status_counts

            # Get recent tasks (last 5, sorted by ID desc)
            sorted_tasks = sorted(tasks, key=lambda x: x.id, reverse=True)
            summary["recent"] = [
                {
                    "id": t.id,
                    "title": t.title,
                    "status": t.status,
                    "username": t.data.get("username", ""),
                }
                for t in sorted_tasks[:5]
            ]

        except Exception as e:
            logger.error(f"Failed to fetch tasks summary: {e}")
            summary["error"] = str(e)

        return summary

    def _get_audits_summary(self):
        """Get audit summary data."""
        from commcare_connect.audit.data_access import AuditDataAccess

        summary = {
            "total": 0,
            "by_status": {},
            "recent": [],
            "error": None,
        }

        try:
            data_access = AuditDataAccess(request=self.request)
            audits = data_access.get_audit_sessions()
            data_access.close()

            summary["total"] = len(audits)

            # Count by status
            status_counts = {}
            for audit in audits:
                status = audit.data.get("status", "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            summary["by_status"] = status_counts

            # Get recent audits (last 5)
            sorted_audits = sorted(audits, key=lambda x: x.id, reverse=True)
            summary["recent"] = [
                {
                    "id": a.id,
                    "title": a.data.get("title", f"Audit {a.id}"),
                    "status": a.data.get("status", "unknown"),
                    "visit_count": a.data.get("visit_count", 0),
                }
                for a in sorted_audits[:5]
            ]

        except Exception as e:
            logger.error(f"Failed to fetch audits summary: {e}")
            summary["error"] = str(e)

        return summary

    def _get_workflows_summary(self):
        """Get workflow summary data."""
        summary = {
            "total": 0,
            "items": [],
            "error": None,
        }

        try:
            data_access = WorkflowDataAccess(request=self.request)
            definitions = data_access.list_definitions()
            data_access.close()

            summary["total"] = len(definitions)
            summary["items"] = [
                {
                    "id": d.id,
                    "name": d.name,
                    "description": d.description,
                    "is_shared": d.is_shared,
                }
                for d in definitions
            ]

        except Exception as e:
            logger.error(f"Failed to fetch workflows summary: {e}")
            summary["error"] = str(e)

        return summary

    def _get_pipelines_summary(self):
        """Get pipeline summary data."""
        from commcare_connect.workflow.data_access import PipelineDataAccess

        summary = {
            "total": 0,
            "items": [],
            "error": None,
        }

        try:
            data_access = PipelineDataAccess(request=self.request)
            definitions = data_access.list_definitions()
            data_access.close()

            summary["total"] = len(definitions)
            summary["items"] = [
                {
                    "id": d.id,
                    "name": d.name,
                    "description": d.description,
                    "is_shared": d.is_shared,
                }
                for d in definitions
            ]

        except Exception as e:
            logger.error(f"Failed to fetch pipelines summary: {e}")
            summary["error"] = str(e)

        return summary


@login_required
@require_GET
def workflow_auth_status_api(request):
    """
    Workflow framework auth-status endpoint.

    Returns the live state of every OAuth provider the workflow runner can
    require: Connect, CommCare HQ, OCS. Each entry has `active` (true if the
    session has a non-expired access token), `authorize_url` (where to send
    the user to refresh that service), and `label` (display name).

    The runner reads `definition.config.auth_requires` (a list of provider
    keys) and gates entry to the workflow's render_code on every required
    provider being `active`. Templates that don't list this field default to
    `["connect"]` (already enforced by labs login_required middleware).

    Behavior beyond a timestamp check:
      * For each provider, if the access token has expired, attempt a silent
        refresh using the stored refresh_token. The framework gate then only
        forces re-authorization when refresh actually fails.
      * For `commcare_hq`, when ?opportunity_id= is supplied, we additionally
        ping the CCHQ Application API for the opportunity's domain. This
        catches the case where refresh "succeeded" but came back with a
        downgraded scope, or the user lost domain membership — situations
        where the timestamp would say active but pipelines still 403.

    Query params:
        next (optional): URL to redirect back to after re-authorization.
            Defaults to the request's referer or the workflow runner page.
        opportunity_id (optional): If supplied, enable the real CCHQ ping for
            that opportunity's domain. Without this we can only do timestamp +
            refresh checks for CCHQ.
    """
    from django.urls import reverse
    from django.utils import timezone
    from django.utils.http import url_has_allowed_host_and_scheme, urlencode

    from commcare_connect.labs.integrations.commcare.api_client import CommCareDataAccess
    from commcare_connect.labs.integrations.connect.oauth import refresh_connect_token
    from commcare_connect.labs.integrations.ocs.api_client import OCSDataAccess

    next_url = request.GET.get("next") or request.headers.get("Referer", "/labs/overview/")
    next_url = (next_url or "/labs/overview/").replace("\\", "/")
    if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
        next_url = "/labs/overview/"

    opportunity_id_param = request.GET.get("opportunity_id")

    def _is_active(session_key: str) -> bool:
        """Timestamp check against the *current* session state."""
        oauth = request.session.get(session_key, {}) or {}
        if not oauth.get("access_token"):
            return False
        return timezone.now().timestamp() < oauth.get("expires_at", 0)

    # ---- Connect -----------------------------------------------------------
    if not _is_active("labs_oauth"):
        # Try a silent refresh before declaring inactive.
        refresh_connect_token(request)
    connect_active = _is_active("labs_oauth")

    # ---- OCS ---------------------------------------------------------------
    if not _is_active("ocs_oauth"):
        try:
            with OCSDataAccess(request) as ocs_client:
                ocs_client._refresh_token()
        except Exception:
            logger.exception("OCS silent refresh attempt raised")
    ocs_active = _is_active("ocs_oauth")

    # ---- CommCare HQ -------------------------------------------------------
    # Two distinct questions:
    #   1) Is the OAuth token alive at all? (verify_token_alive — domain-less)
    #   2) Does this token have access to the *specific* domain pipelines need?
    #      (verify_hq_access — pings form/v1 on the opp's domain)
    #
    # The two answers map to different user-facing actions:
    #   token dead     → "Authorize CommCare HQ" (re-auth fixes it)
    #   wrong domain   → "Your account doesn't have access to <domain>"
    #                    (re-auth WON'T fix it — needs HQ admin)
    cchq_active = _is_active("commcare_oauth")
    cchq_reason: str | None = None
    cchq_domain_for_probe: str | None = None

    if opportunity_id_param:
        try:
            from commcare_connect.workflow.templates.mbw_monitoring.data_fetchers import fetch_opportunity_metadata

            access_token = (request.session.get("labs_oauth") or {}).get("access_token", "")
            if access_token:
                metadata = fetch_opportunity_metadata(access_token, int(opportunity_id_param))
                cchq_domain_for_probe = metadata.get("cc_domain") or None
        except Exception:
            logger.exception("Failed to look up cc_domain for auth-status probe")

    if cchq_active:
        # NOTE: this supersedes PR #104's "skip the probe when timestamp
        # is active" approach. PR #104 was solving the right problem
        # (false-negative loop for users without domain membership) but
        # via a workaround. Here we fix the root cause: switch the probe
        # from /api/application/v1 (needs app-builder scope LLO accounts
        # often lack) to /api/form/v1 (the SAME endpoint pipelines use),
        # AND split token-alive from domain-access so the UI can say
        # "account lacks access to <domain>" instead of looping on
        # Authorize. See verify_token_alive vs verify_hq_access.
        try:
            client = CommCareDataAccess(request, cchq_domain_for_probe or "")
            if not client.verify_token_alive():
                cchq_active = False
                cchq_reason = "token_expired"
            elif cchq_domain_for_probe and not client.verify_hq_access():
                # Token works, but the user can't read forms in this opp's
                # domain. Re-auth would not fix this — surface the actual
                # situation so the user can talk to a CCHQ admin.
                cchq_active = False
                cchq_reason = "no_domain_access"
        except Exception:
            logger.exception("CCHQ probe raised")
            cchq_active = False
            cchq_reason = "probe_error"
    elif not cchq_active:
        cchq_reason = "token_expired"

    cchq_payload: dict = {
        "active": cchq_active,
        "authorize_url": reverse("labs:commcare_initiate") + "?" + urlencode({"next": next_url}),
        "label": "CommCare HQ",
    }
    if cchq_reason:
        cchq_payload["reason"] = cchq_reason
    if cchq_reason == "no_domain_access" and cchq_domain_for_probe:
        cchq_payload["domain"] = cchq_domain_for_probe
        cchq_payload["message"] = (
            f"Your CommCare HQ account does not have form-read access to "
            f"{cchq_domain_for_probe!r}. Re-authorizing won't fix this — "
            f"contact a CommCare HQ admin to add your account to that project."
        )

    return JsonResponse(
        {
            "connect": {
                "active": connect_active,
                "authorize_url": "/labs/login/?" + urlencode({"next": next_url}),
                "label": "Connect",
            },
            "commcare_hq": cchq_payload,
            "ocs": {
                "active": ocs_active,
                "authorize_url": reverse("labs:ocs_initiate") + "?" + urlencode({"next": next_url}),
                "label": "OCS",
            },
        }
    )


@login_required
@require_GET
def get_workers_api(request):
    """API endpoint to fetch workers for an opportunity."""
    labs_context = getattr(request, "labs_context", {})
    opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

    if not opportunity_id:
        return JsonResponse({"error": "opportunity_id required"}, status=400)

    try:
        data_access = WorkflowDataAccess(request=request)
        workers = data_access.get_workers(opportunity_id)
        return JsonResponse({"workers": workers})
    except Exception:
        logger.exception("Failed to fetch workers")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def update_state_api(request, run_id):
    """API endpoint to update workflow run state.

    Refuses with 409 if the run is already completed — completed runs are
    immutable artifacts.
    """
    try:
        data = json.loads(request.body)
        new_state = data.get("state")

        if new_state is None:
            return JsonResponse({"error": "state required in request body"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)
        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)
        if run.is_completed:
            return JsonResponse(
                {"error": "Run is completed; state is immutable. Start a new run."},
                status=409,
            )

        updated_run = data_access.update_run_state(run_id, new_state, run=run)

        if updated_run:
            s3_export.upsert_workflow_run(
                updated_run,
                username=getattr(request.user, "username", "") or "",
            )
            return JsonResponse(
                {
                    "success": True,
                    "run": {
                        "id": updated_run.id,
                        "state": updated_run.data.get("state", {}),
                    },
                }
            )
        else:
            return JsonResponse({"error": "Failed to update run state"}, status=500)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to update run state")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def save_worker_result_api(request, run_id):
    """Save an assessment result for a worker in a workflow run.

    Handles the shallow-merge caveat: reads the full worker_results dict,
    adds/updates the entry for the specified worker, then writes the entire
    dict back via update_run_state().

    Request body:
        {
            "username": "worker@example.com",
            "result": "eligible_for_renewal" | "probation" | "requires_improvement" | "suspended" | null,
            "notes": "Optional notes"
        }
    """
    VALID_RESULTS = ("eligible_for_renewal", "probation", "requires_improvement", "suspended")

    data_access = None
    try:
        data = json.loads(request.body)
        username = data.get("username")
        result = data.get("result")
        notes = data.get("notes", "")

        if not username:
            return JsonResponse({"error": "username is required"}, status=400)

        if result and result not in VALID_RESULTS:
            return JsonResponse(
                {"error": f"result must be one of {VALID_RESULTS} or null"},
                status=400,
            )

        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)
        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)
        if run.is_completed:
            return JsonResponse(
                {"error": "Run is completed; worker results are immutable. Start a new run."},
                status=409,
            )

        # Read-modify-write: get full worker_results, update one entry, write back
        current_state = run.data.get("state", {})
        current_results = current_state.get("worker_results") or current_state.get("flw_results", {})

        from datetime import datetime
        from datetime import timezone as tz

        updated_results = {
            **current_results,
            username: {
                "result": result,
                "notes": notes,
                "assessed_by": request.user.id if request.user.is_authenticated else 0,
                "assessed_at": datetime.now(tz.utc).isoformat(),
            },
        }

        # Write back the full dict (shallow merge safe)
        updated_run = data_access.update_run_state(
            run_id,
            {
                "worker_results": updated_results,
            },
            run=run,
        )

        if not updated_run:
            return JsonResponse({"error": "Failed to update run"}, status=500)

        # Compute progress
        selected = current_state.get("selected_workers") or current_state.get("selected_flws", [])
        total = len(selected)
        assessed = sum(1 for u in selected if updated_results.get(u, {}).get("result"))
        pct = round((assessed / total) * 100) if total > 0 else 0

        return JsonResponse(
            {
                "success": True,
                "worker_results": updated_results,
                "progress": {"percentage": pct, "assessed": assessed, "total": total},
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to save worker result for run %s", run_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    finally:
        if data_access:
            data_access.close()


@login_required
@require_POST
def complete_run_api(request, run_id):
    """Mark a workflow run as completed — atomic terminal transition.

    Builds the snapshot via the template's `build_snapshot` hook (or the
    declarative-input fallback), then writes status=completed, completed_at,
    and the snapshot in a single LabsRecord write. If snapshot assembly
    raises, the run stays in_progress.

    Returns:
      - 200 with `{success, status, completed_at, snapshot}` on success.
      - 404 if the run/definition is missing.
      - 409 if the run is already completed.
      - 400 if the workflow's template doesn't declare `supports_saved_runs`.
    """
    from commcare_connect.workflow.templates import TEMPLATES, build_snapshot_for_template

    data_access = None
    try:
        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)
        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)
        if run.is_completed:
            return JsonResponse(
                {"error": "Run is already completed; start a new run to redo this work."},
                status=409,
            )

        definition_id = run.data.get("definition_id")
        if not definition_id:
            return JsonResponse({"error": "Run has no definition_id"}, status=400)

        definition = data_access.get_definition(definition_id)
        if not definition:
            return JsonResponse({"error": "Workflow definition not found"}, status=404)

        template_key = definition.template_type
        if not template_key:
            return JsonResponse(
                {"error": "Workflow has no template_type; cannot resolve completion handler"},
                status=400,
            )

        template = TEMPLATES.get(template_key)
        if not template:
            return JsonResponse({"error": f"Unknown template: {template_key}"}, status=400)
        if not template.get("supports_saved_runs"):
            return JsonResponse(
                {
                    "error": (
                        f"Template {template_key!r} does not declare supports_saved_runs=True; "
                        "this template's runs cannot be marked complete."
                    )
                },
                status=400,
            )

        opportunity_id = run.opportunity_id or definition.opportunity_id
        if not opportunity_id:
            return JsonResponse({"error": "Run has no opportunity_id"}, status=400)

        # Single source of truth: same pipeline+worker fetch the runner uses.
        pipelines = data_access.get_pipeline_data(definition_id, opportunity_id)

        effective_opp_ids = definition.opportunity_ids or [opportunity_id]
        workers: list[dict] = []
        for oid in effective_opp_ids:
            try:
                for w in data_access.get_workers(oid):
                    workers.append({**w, "opportunity_id": oid})
            except Exception:
                logger.exception("Failed to load workers for opp %s", oid)

        snapshot_payload = build_snapshot_for_template(
            template_key=template_key,
            pipelines=pipelines,
            state=run.data.get("state", {}),
            opportunity_id=opportunity_id,
            workers=workers,
            opportunity_ids=effective_opp_ids,
            # Optional context fields that some templates' build_snapshot hooks
            # accept (definition_id, request). The framework relays via
            # **context — hooks that don't use these fields just absorb them
            # into **_.
            definition_id=definition_id,
            request=request,
        )
        if not isinstance(snapshot_payload, dict):
            return JsonResponse(
                {"error": f"build_snapshot for {template_key!r} returned non-dict"},
                status=500,
            )

        completed_run = data_access.complete_run(run_id, snapshot_payload, run=run)
        if completed_run is None:
            return JsonResponse(
                {"error": "Failed to persist completion — run stays in_progress"},
                status=500,
            )

        return JsonResponse(
            {
                "success": True,
                "status": completed_run.status,
                "completed_at": completed_run.completed_at,
                # Legacy alias for pre-rename callers (mbw_monitoring_v3 render).
                "frozen_at": completed_run.completed_at,
                "snapshot": completed_run.snapshot,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to complete run %s", run_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    finally:
        if data_access:
            data_access.close()


@login_required
@require_GET
def get_run_api(request, run_id):
    """API endpoint to get workflow run details."""
    try:
        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)

        if run:
            return JsonResponse(
                {
                    "run": {
                        "id": run.id,
                        "definition_id": run.data.get("definition_id"),
                        "opportunity_id": run.opportunity_id,
                        "status": run.status,
                        "state": run.data.get("state", {}),
                        "snapshot": run.data.get("snapshot"),
                        "completed_at": run.completed_at,
                    }
                }
            )
        else:
            return JsonResponse({"error": "Run not found"}, status=404)

    except Exception:
        logger.exception("Failed to get run")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def get_snapshot_api(request, run_id):
    """Read-only inspection: return the saved snapshot for a completed run.

    Used by the framework's `useRunView` helper on the FE; render code does
    not call this directly (it reads `instance.snapshot` from props instead).
    """
    try:
        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)
        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)
        return JsonResponse(
            {
                "has_snapshot": bool(run.snapshot),
                "snapshot": run.snapshot,
                "completed_at": run.completed_at,
                # Legacy alias — pre-rename callers (e.g. mbw_monitoring_v3 render)
                # read `frozen_at` off this response. New callers should use
                # `completed_at`. Drop after the v3 render migrates.
                "frozen_at": run.completed_at,
                "status": run.status,
            }
        )
    except Exception:
        logger.exception("Failed to get snapshot for run %s", run_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def start_run_api(request, definition_id):
    """Create a new active run for a workflow definition.

    Replaces the implicit auto-create that used to happen on every URL visit.
    Now an explicit user action: client POSTs here, gets back the new run_id,
    redirects to ?run_id=<id>.

    Failure mode: returns 4xx if the workflow doesn't exist or the user has no
    opportunity_id in their session context.
    """
    from datetime import datetime, timedelta
    from datetime import timezone as _tz

    labs_context = getattr(request, "labs_context", {})
    opportunity_id = labs_context.get("opportunity_id")
    if not opportunity_id:
        return JsonResponse({"error": "Select an opportunity before starting a run"}, status=400)

    try:
        data_access = WorkflowDataAccess(request=request)
        definition = data_access.get_definition(definition_id)
        if not definition:
            return JsonResponse({"error": "Workflow not found"}, status=404)

        # Default period: current ISO week (Mon–Sun, UTC). Templates that need a
        # different period scheme should override via update_run_state immediately
        # after creation.
        today = datetime.now(_tz.utc).date()
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        run = data_access.create_run(
            definition_id=definition_id,
            opportunity_id=opportunity_id,
            period_start=week_start.isoformat(),
            period_end=week_end.isoformat(),
            initial_state={"worker_states": {}},
        )

        # Mirror to S3 for the runs-list export (same convention as legacy auto-create).
        try:
            org_data = get_org_data(request)
            opp_map = {o["id"]: o.get("name", "") for o in org_data.get("opportunities", [])}
            s3_export.upsert_workflow_run(
                run,
                opportunity_name=opp_map.get(run.opportunity_id, ""),
                username=getattr(request.user, "username", "") or "",
            )
        except Exception:
            logger.exception("Failed to S3-mirror new run %s", run.id)

        redirect_url = f"/labs/workflow/{definition_id}/run/?run_id={run.id}"

        # If the request came from an HTML form (e.g. the run-picker page's
        # "Start Run" button), redirect into the new run. Programmatic clients
        # that ask for JSON (Accept includes application/json) get the run_id
        # back and decide what to do client-side.
        accepts_json = "application/json" in request.headers.get("Accept", "")
        if not accepts_json and "text/html" in request.headers.get("Accept", ""):
            from django.shortcuts import redirect as _redirect

            return _redirect(redirect_url)

        return JsonResponse(
            {
                "success": True,
                "run_id": run.id,
                "status": run.status,
                "redirect": redirect_url,
            }
        )
    except Exception:
        logger.exception("Failed to start run for definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def create_workflow_from_template_view(request):
    """Create a workflow from a template.

    For multi_opp templates, accepts an `opportunity_ids` POST field (getlist)
    and validates each ID against the user's accessible opportunities.
    """
    from django.contrib import messages
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import redirect

    from commcare_connect.workflow.templates import get_template

    template_key = request.POST.get("template", "performance_review")

    if not can_create_from_template(request.user, template_key):
        raise PermissionDenied

    if template_key not in TEMPLATES:
        messages.error(request, f"Unknown template: {template_key}")
        return redirect("labs:workflow:list")

    # Parse opportunity_ids, if provided
    raw_opp_ids = request.POST.getlist("opportunity_ids")
    opportunity_ids: list[int] = []
    if raw_opp_ids:
        try:
            opportunity_ids = [int(x) for x in raw_opp_ids if str(x).strip()]
        except (TypeError, ValueError):
            messages.error(request, "Invalid opportunity_ids.")
            return redirect("labs:workflow:list")

        # Validate against user's accessible opportunities
        user_opp_ids = {
            int(o["id"]) for o in (get_org_data(request) or {}).get("opportunities", []) if o.get("id") is not None
        }
        invalid = [oid for oid in opportunity_ids if oid not in user_opp_ids]
        if invalid:
            messages.error(
                request,
                f"You do not have access to opportunities: {invalid}",
            )
            return redirect("labs:workflow:list")

    # Only multi_opp templates should receive opportunity_ids
    template = get_template(template_key)
    if not template.get("multi_opp"):
        opportunity_ids = []  # silently ignored for single-opp templates

    try:
        data_access = WorkflowDataAccess(request=request)
        definition, render_code, pipeline = create_from_template(
            data_access,
            template_key,
            request=request,
            opportunity_ids=opportunity_ids,
        )

        if pipeline:
            messages.success(
                request,
                f"Created workflow: {definition.name} (ID: {definition.id}) with pipeline: {pipeline.name}",
            )
        else:
            messages.success(request, f"Created workflow: {definition.name} (ID: {definition.id})")
        return redirect("labs:workflow:list")

    except Exception as e:
        logger.error(
            f"Failed to create workflow from template {template_key}: {e}",
            exc_info=True,
        )
        messages.error(request, f"Failed to create workflow: {e}")
        return redirect("labs:workflow:list")


# Keep old function name for backwards compatibility
@login_required
@require_POST
def create_example_workflow(request):
    """Create the example 'Weekly Performance Review' workflow. Deprecated: use create_workflow_from_template_view."""
    # Inject the template parameter and forward to the new function
    request.POST = request.POST.copy()
    request.POST["template"] = "performance_review"
    return create_workflow_from_template_view(request)


@login_required
@require_GET
def get_chat_history_api(request, definition_id):
    """API endpoint to get chat history for a workflow definition."""
    try:
        data_access = WorkflowDataAccess(request=request)
        messages = data_access.get_chat_messages(definition_id)

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "messages": messages,
            }
        )

    except Exception:
        logger.exception("Failed to get chat history for definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def clear_chat_history_api(request, definition_id):
    """API endpoint to clear chat history for a workflow definition."""
    try:
        data_access = WorkflowDataAccess(request=request)
        cleared = data_access.clear_chat_history(definition_id)

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "cleared": cleared,
            }
        )

    except Exception:
        logger.exception("Failed to clear chat history for definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def add_chat_message_api(request, definition_id):
    """API endpoint to add a message to chat history."""
    try:
        data = json.loads(request.body)
        role = data.get("role")
        content = data.get("content")

        if not role or not content:
            return JsonResponse({"error": "role and content are required"}, status=400)

        if role not in ("user", "assistant"):
            return JsonResponse({"error": "role must be 'user' or 'assistant'"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        data_access.add_chat_message(definition_id, role, content)

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to add chat message for definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def save_render_code_api(request, definition_id):
    """API endpoint to save render code for a workflow definition."""
    try:
        data = json.loads(request.body)
        component_code = data.get("component_code")
        definition_data = data.get("definition")

        if not component_code:
            return JsonResponse({"error": "component_code is required"}, status=400)

        data_access = WorkflowDataAccess(request=request)

        # Save render code
        render_code_record = data_access.save_render_code(
            definition_id=definition_id,
            component_code=component_code,
            version=1,  # TODO: implement versioning
        )

        # Optionally update definition if provided
        if definition_data:
            data_access.update_definition(definition_id, definition_data)

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "render_code_id": render_code_record.id,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to save render code for definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def sync_template_render_code_api(request, definition_id):
    """Sync render code from the source template for a workflow definition.

    Accepts JSON body with optional 'template_key'. If not provided, tries to
    detect the template from the definition name.
    """
    data_access = None
    try:
        data = json.loads(request.body) if request.body else {}
        template_key = data.get("template_key")

        data_access = WorkflowDataAccess(request=request)
        definition = data_access.get_definition(definition_id)
        if not definition:
            return JsonResponse({"error": "Workflow not found"}, status=404)

        # Auto-detect template from definition name if not provided
        if not template_key:
            name_lower = definition.name.lower().replace(" ", "_")
            for key in TEMPLATES:
                if key == name_lower or TEMPLATES[key]["name"].lower() == definition.name.lower():
                    template_key = key
                    break

        if not template_key:
            return JsonResponse(
                {
                    "error": "Could not detect template. Pass 'template_key' in request body.",
                    "available": list(TEMPLATES.keys()),
                },
                status=400,
            )

        from commcare_connect.workflow.templates import get_template

        template = get_template(template_key)
        if not template:
            return JsonResponse({"error": f"Template '{template_key}' not found"}, status=404)

        render_code_record = data_access.save_render_code(
            definition_id=definition_id,
            component_code=template["render_code"],
            version=1,
        )

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "render_code_id": render_code_record.id,
                "template_key": template_key,
            }
        )
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to sync template render code for definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    finally:
        if data_access:
            data_access.close()


# =============================================================================
# OCS Integration APIs
# =============================================================================


@login_required
def ocs_status_api(request):
    """Check if OCS OAuth is configured and valid for the current user."""
    from commcare_connect.labs.integrations.ocs.api_client import OCSDataAccess

    try:
        ocs = OCSDataAccess(request=request)
        connected = ocs.check_token_valid()
        ocs.close()

        return JsonResponse(
            {
                "connected": connected,
                "login_url": "/labs/ocs/initiate/",
            }
        )
    except Exception as e:
        logger.error(f"Error checking OCS status: {e}")
        return JsonResponse(
            {
                "connected": False,
                "login_url": "/labs/ocs/initiate/",
                "error": str(e),
            }
        )


@login_required
def ocs_bots_api(request):
    """List available OCS bots for the current user."""
    from commcare_connect.labs.integrations.ocs.api_client import OCSAPIError, OCSDataAccess

    try:
        ocs = OCSDataAccess(request=request)

        if not ocs.check_token_valid():
            ocs.close()
            return JsonResponse({"success": False, "needs_oauth": True}, status=401)

        experiments = ocs.list_experiments()
        ocs.close()

        # Format bots for frontend
        bots = [
            {
                "id": exp.get("public_id") or exp.get("id"),
                "name": exp.get("name", "Unnamed Bot"),
                "version": exp.get("version_number", 1),
            }
            for exp in experiments
        ]

        return JsonResponse({"success": True, "bots": bots})

    except OCSAPIError:
        logger.exception("OCS API error listing bots")
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)
    except Exception:
        logger.exception("Error listing OCS bots")
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)


# =============================================================================
# Pipeline Data APIs
# =============================================================================


@login_required
@require_GET
def get_pipeline_data_api(request, definition_id):
    """
    API endpoint to fetch pipeline data for a workflow.

    Returns data from all pipeline sources defined in the workflow.
    """
    labs_context = getattr(request, "labs_context", {})
    opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

    if not opportunity_id:
        return JsonResponse({"error": "opportunity_id required"}, status=400)

    try:
        data_access = WorkflowDataAccess(request=request)
        pipeline_data = data_access.get_pipeline_data(definition_id, int(opportunity_id))
        data_access.close()

        return JsonResponse(pipeline_data)

    except Exception:
        logger.exception("Failed to fetch pipeline data for workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def list_available_pipelines_api(request):
    """
    API endpoint to list pipelines available to add as sources.

    Returns user's own pipelines plus shared pipelines.
    """
    from commcare_connect.workflow.data_access import PipelineDataAccess

    try:
        data_access = PipelineDataAccess(request=request)
        pipelines = data_access.list_definitions(include_shared=True)
        data_access.close()

        result = [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "is_shared": p.is_shared,
                "shared_scope": p.shared_scope,
            }
            for p in pipelines
        ]

        return JsonResponse({"pipelines": result})

    except Exception:
        logger.exception("Failed to list available pipelines")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def add_pipeline_source_api(request, definition_id):
    """
    API endpoint to add a pipeline as a data source for a workflow.
    """
    try:
        data = json.loads(request.body)
        pipeline_id = data.get("pipeline_id")
        alias = data.get("alias")

        if not pipeline_id or not alias:
            return JsonResponse({"error": "pipeline_id and alias are required"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        updated = data_access.add_pipeline_source(definition_id, int(pipeline_id), alias)
        data_access.close()

        if updated:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "pipeline_sources": updated.pipeline_sources,
                }
            )
        else:
            return JsonResponse({"error": "Workflow not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to add pipeline source")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def remove_pipeline_source_api(request, definition_id):
    """
    API endpoint to remove a pipeline source from a workflow.
    """
    try:
        data = json.loads(request.body)
        alias = data.get("alias")

        if not alias:
            return JsonResponse({"error": "alias is required"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        updated = data_access.remove_pipeline_source(definition_id, alias)
        data_access.close()

        if updated:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "pipeline_sources": updated.pipeline_sources,
                }
            )
        else:
            return JsonResponse({"error": "Workflow not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to remove pipeline source")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


# =============================================================================
# Sharing APIs
# =============================================================================


@login_required
@require_POST
def share_workflow_api(request, definition_id):
    """API endpoint to share a workflow."""
    try:
        data = json.loads(request.body)
        scope = data.get("scope", "global")

        if scope not in ("program", "organization", "global"):
            return JsonResponse({"error": "scope must be 'program', 'organization', or 'global'"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        updated = data_access.share_workflow(definition_id, scope)
        data_access.close()

        if updated:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "is_shared": True,
                    "shared_scope": scope,
                }
            )
        else:
            return JsonResponse({"error": "Workflow not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to share workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def unshare_workflow_api(request, definition_id):
    """API endpoint to unshare a workflow."""
    try:
        data_access = WorkflowDataAccess(request=request)
        updated = data_access.unshare_workflow(definition_id)
        data_access.close()

        if updated:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "is_shared": False,
                }
            )
        else:
            return JsonResponse({"error": "Workflow not found"}, status=404)

    except Exception:
        logger.exception("Failed to unshare workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def delete_workflow_api(request, definition_id):
    """API endpoint to delete a workflow definition.

    Accepts JSON body with optional:
        delete_linked: bool - if True, also deletes render code, runs, and chat history
    """
    try:
        # Parse request body for options
        delete_linked = False
        if request.body:
            try:
                body = json.loads(request.body)
                delete_linked = body.get("delete_linked", False)
            except json.JSONDecodeError:
                pass  # Treat as delete_linked=False

        data_access = WorkflowDataAccess(request=request)
        deleted_counts = data_access.delete_definition(definition_id, delete_linked=delete_linked)
        data_access.close()

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "deleted_counts": deleted_counts,
            }
        )

    except Exception:
        logger.exception("Failed to delete workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def rename_workflow_api(request, definition_id):
    """API endpoint to rename a workflow definition."""
    try:
        data = json.loads(request.body)
        new_name = data.get("name", "").strip()

        if not new_name:
            return JsonResponse({"error": "name is required"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        definition = data_access.get_definition(definition_id)

        if not definition:
            return JsonResponse({"error": "Workflow not found"}, status=404)

        # Update the name in the definition data
        definition_data = definition.data or {}
        definition_data["name"] = new_name
        data_access.update_definition(definition_id, definition_data)
        data_access.close()

        return JsonResponse({"success": True, "definition_id": definition_id, "name": new_name})

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to rename workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def delete_pipeline_api(request, definition_id):
    """API endpoint to delete a pipeline definition."""
    from commcare_connect.workflow.data_access import PipelineDataAccess

    try:
        data_access = PipelineDataAccess(request=request)
        data_access.delete_definition(definition_id)
        data_access.close()

        return JsonResponse({"success": True, "definition_id": definition_id})

    except Exception:
        logger.exception("Failed to delete pipeline %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def list_shared_workflows_api(request):
    """API endpoint to list shared workflows."""
    scope = request.GET.get("scope", "global")

    try:
        data_access = WorkflowDataAccess(request=request)
        shared = data_access.list_shared_workflows(scope)
        data_access.close()

        result = [
            {
                "id": w.id,
                "name": w.name,
                "description": w.description,
                "shared_scope": w.shared_scope,
            }
            for w in shared
        ]

        return JsonResponse({"workflows": result})

    except Exception:
        logger.exception("Failed to list shared workflows")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def copy_workflow_api(request, definition_id):
    """API endpoint to copy a workflow definition."""
    try:
        data = json.loads(request.body) if request.body else {}
        new_name = data.get("name")
        source_is_public = data.get("source_is_public", False)

        data_access = WorkflowDataAccess(request=request)
        copied = data_access.copy_workflow(definition_id, new_name, source_is_public)
        data_access.close()

        if copied:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": copied.id,
                    "name": copied.name,
                }
            )
        else:
            return JsonResponse({"error": "Workflow not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to copy workflow %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


# =============================================================================
# Pipeline Sharing APIs
# =============================================================================


@login_required
@require_POST
def share_pipeline_api(request, definition_id):
    """API endpoint to share a pipeline."""
    try:
        data = json.loads(request.body) if request.body else {}
        scope = data.get("scope", "global")

        if scope not in ("program", "organization", "global"):
            return JsonResponse({"error": "scope must be 'program', 'organization', or 'global'"}, status=400)

        data_access = PipelineDataAccess(request=request)
        updated = data_access.share_pipeline(definition_id, scope)
        data_access.close()

        if updated:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "is_shared": True,
                    "shared_scope": scope,
                }
            )
        else:
            return JsonResponse({"error": "Pipeline not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to share pipeline %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def unshare_pipeline_api(request, definition_id):
    """API endpoint to unshare a pipeline."""
    try:
        data_access = PipelineDataAccess(request=request)
        updated = data_access.unshare_pipeline(definition_id)
        data_access.close()

        if updated:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "is_shared": False,
                }
            )
        else:
            return JsonResponse({"error": "Pipeline not found"}, status=404)

    except Exception:
        logger.exception("Failed to unshare pipeline %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def list_shared_pipelines_api(request):
    """API endpoint to list shared pipelines."""
    scope = request.GET.get("scope", "global")

    try:
        data_access = PipelineDataAccess(request=request)
        shared = data_access.list_shared_pipelines(scope)
        data_access.close()

        result = [
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "shared_scope": p.shared_scope,
            }
            for p in shared
        ]

        return JsonResponse({"pipelines": result})

    except Exception:
        logger.exception("Failed to list shared pipelines")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def copy_pipeline_api(request, definition_id):
    """API endpoint to copy a pipeline definition."""
    try:
        data = json.loads(request.body) if request.body else {}
        new_name = data.get("name")
        source_is_public = data.get("source_is_public", False)

        data_access = PipelineDataAccess(request=request)
        copied = data_access.copy_pipeline(definition_id, new_name, source_is_public)
        data_access.close()

        if copied:
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": copied.id,
                    "name": copied.name,
                }
            )
        else:
            return JsonResponse({"error": "Pipeline not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to copy pipeline %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


# =============================================================================
# Pipeline Editor Views and APIs
# =============================================================================


class PipelineEditView(LoginRequiredMixin, TemplateView):
    """
    Standalone pipeline editor view.

    Allows editing pipeline schema and previewing extracted data.
    Can also be embedded in workflow UI via tabs.
    """

    template_name = "workflow/pipeline_edit.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        definition_id = self.kwargs.get("definition_id")

        # Get labs context
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")
        context["opportunity_id"] = opportunity_id
        context["opportunity_name"] = labs_context.get("opportunity_name")
        context["has_context"] = bool(opportunity_id)

        if not opportunity_id:
            context["error"] = "Please select an opportunity to edit this pipeline."
            return context

        try:
            from commcare_connect.workflow.data_access import PipelineDataAccess

            data_access = PipelineDataAccess(request=self.request)

            # Get pipeline definition
            definition = data_access.get_definition(definition_id)
            if not definition:
                context["error"] = f"Pipeline {definition_id} not found."
                return context

            context["definition"] = definition
            context["definition_id"] = definition_id

            # Get initial data preview (limited rows for performance)
            try:
                preview_data = data_access.execute_pipeline(definition_id, opportunity_id)
                # Limit to 100 rows for preview
                if preview_data.get("rows"):
                    preview_data["rows"] = preview_data["rows"][:100]
                    preview_data["metadata"]["preview_limited"] = len(preview_data["rows"]) >= 100
                context["preview_data"] = preview_data
            except Exception as e:
                logger.warning(f"Failed to get pipeline preview: {e}")
                context["preview_data"] = {"rows": [], "metadata": {"error": str(e)}}

            # Prepare data for React component
            context["pipeline_data"] = {
                "definition_id": definition_id,
                "opportunity_id": opportunity_id,
                "definition": definition.data,
                "preview_data": context.get("preview_data", {}),
                "apiEndpoints": {
                    "getDefinition": f"/labs/workflow/api/pipeline/{definition_id}/",
                    "updateSchema": f"/labs/workflow/api/pipeline/{definition_id}/schema/",
                    "preview": f"/labs/workflow/api/pipeline/{definition_id}/preview/",
                    "sqlPreview": f"/labs/workflow/api/pipeline/{definition_id}/sql/",
                    "chatHistory": f"/labs/workflow/api/pipeline/{definition_id}/chat/history/",
                    "chatClear": f"/labs/workflow/api/pipeline/{definition_id}/chat/clear/",
                },
            }

            data_access.close()

        except Exception as e:
            logger.error(f"Failed to load pipeline {definition_id}: {e}", exc_info=True)
            context["error"] = str(e)

        return context


@login_required
@require_GET
def get_pipeline_definition_api(request, definition_id):
    """API endpoint to get a pipeline definition."""
    from commcare_connect.workflow.data_access import PipelineDataAccess

    try:
        data_access = PipelineDataAccess(request=request)
        definition = data_access.get_definition(definition_id)
        data_access.close()

        if not definition:
            return JsonResponse({"error": "Pipeline not found"}, status=404)

        return JsonResponse(
            {
                "success": True,
                "definition": {
                    "id": definition.id,
                    "name": definition.name,
                    "description": definition.description,
                    "version": definition.version,
                    "schema": definition.schema,
                    "is_shared": definition.is_shared,
                    "shared_scope": definition.shared_scope,
                },
            }
        )

    except Exception:
        logger.exception("Failed to get pipeline definition %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def update_pipeline_schema_api(request, definition_id):
    """API endpoint to update a pipeline schema."""
    from commcare_connect.workflow.data_access import PipelineDataAccess

    try:
        data = json.loads(request.body)
        schema = data.get("schema")
        name = data.get("name")
        description = data.get("description")

        if schema is None:
            return JsonResponse({"error": "schema is required"}, status=400)

        data_access = PipelineDataAccess(request=request)
        updated = data_access.update_definition(
            definition_id,
            name=name,
            description=description,
            schema=schema,
        )
        data_access.close()

        if not updated:
            return JsonResponse({"error": "Pipeline not found"}, status=404)

        return JsonResponse(
            {
                "success": True,
                "definition": {
                    "id": updated.id,
                    "name": updated.name,
                    "description": updated.description,
                    "version": updated.version,
                    "schema": updated.schema,
                },
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to update pipeline schema %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def execute_pipeline_preview_api(request, definition_id):
    """
    API endpoint to execute a pipeline and return preview data.

    Optionally accepts a schema in query params for previewing unsaved changes.
    """
    from commcare_connect.workflow.data_access import PipelineDataAccess

    labs_context = getattr(request, "labs_context", {})
    opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

    if not opportunity_id:
        return JsonResponse({"error": "opportunity_id required"}, status=400)

    try:
        data_access = PipelineDataAccess(request=request)
        result = data_access.execute_pipeline(definition_id, int(opportunity_id))
        data_access.close()

        # Limit to 100 rows for preview
        if result.get("rows"):
            total_rows = len(result["rows"])
            result["rows"] = result["rows"][:100]
            result["metadata"]["total_rows"] = total_rows
            result["metadata"]["preview_limited"] = total_rows > 100

        return JsonResponse(result)

    except Exception:
        logger.exception("Failed to execute pipeline preview %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def get_pipeline_sql_preview_api(request, definition_id):
    """
    API endpoint to get the SQL that would be generated from a pipeline schema.

    Returns the SQL queries without executing them, useful for debugging
    and understanding what the pipeline will do.
    """
    from commcare_connect.labs.analysis.backends.sql.query_builder import generate_sql_preview
    from commcare_connect.workflow.data_access import PipelineDataAccess

    labs_context = getattr(request, "labs_context", {})
    opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

    if not opportunity_id:
        return JsonResponse({"error": "opportunity_id required"}, status=400)

    try:
        data_access = PipelineDataAccess(request=request)
        definition = data_access.get_definition(definition_id)

        if not definition:
            data_access.close()
            return JsonResponse({"error": "Pipeline not found"}, status=404)

        # definition is a PipelineDefinitionRecord object, access .data for the dict
        schema = definition.data.get("schema", {})

        # Convert schema to config (before closing data_access)
        config = data_access._schema_to_config(schema, definition_id)
        data_access.close()

        # Generate SQL preview
        sql_preview = generate_sql_preview(config, int(opportunity_id))

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "opportunity_id": opportunity_id,
                "sql_preview": sql_preview,
            }
        )

    except Exception:
        logger.exception("Failed to generate SQL preview for pipeline %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def get_pipeline_chat_history_api(request, definition_id):
    """API endpoint to get chat history for a pipeline."""
    from commcare_connect.workflow.data_access import PipelineDataAccess

    try:
        data_access = PipelineDataAccess(request=request)
        messages = data_access.get_chat_history(definition_id)
        data_access.close()

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "messages": messages,
            }
        )

    except Exception:
        logger.exception("Failed to get pipeline chat history %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_POST
def clear_pipeline_chat_history_api(request, definition_id):
    """API endpoint to clear chat history for a pipeline."""
    from commcare_connect.workflow.data_access import PipelineDataAccess

    try:
        data_access = PipelineDataAccess(request=request)
        data_access.clear_chat_history(definition_id)
        data_access.close()

        return JsonResponse(
            {
                "success": True,
                "definition_id": definition_id,
                "cleared": True,
            }
        )

    except Exception:
        logger.exception("Failed to clear pipeline chat history %s", definition_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


# =============================================================================
# Workflow Job APIs
# =============================================================================


@login_required
@require_POST
def start_job_api(request, run_id):
    """
    Start an async workflow job.

    Kicks off a Celery task to execute a multi-stage job (pipeline + processing).
    Results are saved incrementally to workflow run state.
    """
    from commcare_connect.workflow.tasks import run_workflow_job

    try:
        data = json.loads(request.body)
        job_config = data.get("job_config")

        if not job_config:
            return JsonResponse({"error": "job_config required"}, status=400)

        access_token = request.session.get("labs_oauth", {}).get("access_token")
        if not access_token:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        # Get opportunity_id from labs_context
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")
        if not opportunity_id:
            return JsonResponse({"error": "opportunity_id required in context"}, status=400)

        # Start async task
        task = run_workflow_job.delay(
            job_config=job_config,
            access_token=access_token,
            run_id=run_id,
            opportunity_id=opportunity_id,
        )

        logger.info(f"[StartJob] Started job {task.id} for run {run_id}")

        return JsonResponse(
            {
                "success": True,
                "task_id": task.id,
                "run_id": run_id,
                "status": "pending",
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to start job for run %s", run_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


class JobStatusStreamView(LoginRequiredMixin, View):
    """
    SSE endpoint for real-time multi-stage job progress streaming.

    Follows same pattern as custom_analysis SSE views.
    Shows stage progress: "Stage 1/2: Loading data...", "Stage 2/2: Validating 5/10"

    Results are already being saved to workflow state by the task.
    This endpoint is for live viewing - user can close and return later.
    """

    def get(self, request, task_id):
        from celery.result import AsyncResult

        from commcare_connect.labs.analysis.sse_streaming import send_sse_event

        def stream_progress():
            task = AsyncResult(task_id)

            while True:
                task_meta = task._get_task_meta()
                status = task_meta.get("status")

                if status == "SUCCESS":
                    yield send_sse_event(
                        "Complete!",
                        data={
                            "status": "completed",
                            "results": task.get(),
                        },
                    )
                    break
                elif status == "FAILURE":
                    error_msg = str(task.result) if task.result else "Unknown error"
                    yield send_sse_event("Failed", error=error_msg)
                    break
                elif status == "REVOKED":
                    yield send_sse_event(
                        "Cancelled",
                        data={"status": "cancelled"},
                    )
                    break
                else:
                    meta = task_meta.get("result", {}) or {}

                    # Build event data with stage info
                    event_data = {
                        "status": "running",
                        "current_stage": meta.get("current_stage", 1),
                        "total_stages": meta.get("total_stages", 1),
                        "stage_name": meta.get("stage_name", "Processing"),
                        "processed": meta.get("processed", 0),
                        "total": meta.get("total", 0),
                    }

                    # Include item_result for real-time row updates
                    if meta.get("item_result"):
                        event_data["item_result"] = meta["item_result"]

                    yield send_sse_event(
                        meta.get("message", "Processing..."),
                        data=event_data,
                    )

                import time

                time.sleep(0.5)  # Poll every 500ms for responsive updates

        response = StreamingHttpResponse(
            stream_progress(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


@login_required
@require_POST
def cancel_job_api(request, task_id):
    """
    Cancel a running job.

    Revokes the Celery task. Partial results are preserved in workflow state.
    """
    from datetime import datetime

    from celery.result import AsyncResult

    from config import celery_app

    try:
        data = json.loads(request.body) if request.body else {}
        run_id = data.get("run_id")

        task = AsyncResult(task_id)

        # Check if task is still running
        if task.state in ("PENDING", "STARTED", "PROGRESS", "RETRY"):
            # Revoke the task (terminate if running)
            celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

            # Update job state in workflow run if run_id provided
            if run_id:
                access_token = request.session.get("labs_oauth", {}).get("access_token")
                labs_context = getattr(request, "labs_context", {})
                opportunity_id = labs_context.get("opportunity_id")

                if access_token and opportunity_id:
                    data_access = WorkflowDataAccess(request=request)
                    run = data_access.get_run(int(run_id))
                    if run:
                        current_state = run.data.get("state", {})
                        current_job = current_state.get("active_job", {})
                        current_job.update(
                            {
                                "status": "cancelled",
                                "cancelled_at": datetime.now().isoformat(),
                                "cancelled_by": request.user.username if request.user else None,
                            }
                        )
                        data_access.update_run_state(int(run_id), {"active_job": current_job})
                    data_access.close()

            logger.info(f"[CancelJob] Cancelled job {task_id}")

            return JsonResponse(
                {
                    "success": True,
                    "task_id": task_id,
                    "status": "cancelled",
                }
            )
        else:
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Task is not running (state: {task.state})",
                },
                status=400,
            )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Failed to cancel job %s", task_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def open_tasks_api(request):
    """
    Return open tasks for the current opportunity, keyed by lowercase username.

    Used by render code to fetch task state independently of the background job,
    so task display works reliably regardless of Celery worker deployment state.
    """
    from commcare_connect.tasks.data_access import TaskDataAccess

    try:
        task_access = TaskDataAccess(request=request)
        all_tasks = task_access.get_tasks()
        task_access.close()

        by_username: dict = {}
        for task in all_tasks:
            if task.data.get("status") == "closed":
                continue
            username = (task.data.get("username") or "").lower()
            if not username:
                continue
            created_at = ""
            for event in task.data.get("events", []):
                if event.get("event_type") == "created":
                    created_at = event.get("timestamp") or ""
                    break
            existing = by_username.get(username)
            if not existing or created_at > existing.get("triggered_at", ""):
                by_username[username] = {
                    "task_id": task.id,
                    "status": task.data.get("status", "investigating"),
                    "triggered_at": created_at,
                    "title": task.data.get("title", ""),
                }

        return JsonResponse({"open_tasks": by_username, "total_fetched": len(all_tasks)})
    except Exception:
        logger.exception("Failed to fetch open tasks for opportunity")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
@require_GET
def prev_categories_api(request):
    """
    Return worker_results from the most recent run (any workflow version) that
    has at least one category assigned for this opportunity.

    Scoped by opportunity via labs_context — intentionally ignores definition_id
    so categories from prior workflow versions are visible.
    """
    try:
        wf_access = WorkflowDataAccess(request=request)
        runs = wf_access.list_runs()
        wf_access.close()

        candidates = [r for r in runs if r.is_completed and (r.data.get("state") or {}).get("worker_results")]
        if not candidates:
            return JsonResponse({"prev_categories": {}, "source_run_id": None})

        candidates.sort(key=lambda r: r.data.get("created_at") or "", reverse=True)
        best = candidates[0]
        worker_results = (best.data.get("state") or {}).get("worker_results") or {}
        return JsonResponse({"prev_categories": worker_results, "source_run_id": best.id})
    except Exception:
        logger.exception("Failed to fetch prev categories for opportunity")
        return JsonResponse({"error": "An internal error occurred"}, status=500)


class PipelineDataStreamView(BaseSSEStreamView):
    """
    SSE endpoint for streaming pipeline data loading progress.

    Inherits BaseSSEStreamView so heartbeat comments fire every 20s during
    long silent periods (CCHQ pagination, visit cold-load, etc.). Without
    heartbeats AWS ALB drops idle SSE connections after 60s — the user
    sees the generic "Pipeline stream connection lost" with no diagnostic.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
        from commcare_connect.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, send_sse_event

        # Django's View.dispatch() sets self.kwargs from URL path kwargs.
        definition_id = self.kwargs.get("definition_id")
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

        try:
            if not opportunity_id:
                yield send_sse_event("Error", error="No opportunity selected")
                return

            # Check for OAuth token
            labs_oauth = request.session.get("labs_oauth", {})
            if not labs_oauth.get("access_token"):
                yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                return

            # Get workflow definition to find pipeline sources.
            data_access = WorkflowDataAccess(request=request)
            try:
                definition = data_access.get_definition(definition_id)
            finally:
                data_access.close()

            if not definition:
                yield send_sse_event("Error", error=f"Workflow {definition_id} not found")
                return

            if not definition.pipeline_sources:
                yield send_sse_event("No pipelines", data={"pipelines": {}})
                return

            # Early CCHQ access probe — fail fast (1-2s) instead of letting
            # the user wait through a 60s ALB timeout, before discovering
            # CCHQ is unreachable mid-pipeline. Only fires if any pipeline
            # source declares a cchq_forms data source.
            yield from self._maybe_probe_cchq_access(
                request, definition, int(opportunity_id), labs_oauth.get("access_token")
            )

            yield send_sse_event("Loading pipeline configurations...")

            # Determine which opps to pull data from
            opp_ids = definition.opportunity_ids or [int(opportunity_id)]

            def format_date(d):
                if d and hasattr(d, "isoformat"):
                    return d.isoformat()
                return d

            # Execute each pipeline source with streaming.
            pipeline_data = {}
            pipeline_access = PipelineDataAccess(
                request=request,
                access_token=labs_oauth.get("access_token"),
                opportunity_id=int(opportunity_id),
            )

            # Pre-resolve cross-pipeline JOIN config hashes and topologically
            # sort so dependencies run before dependents. Without this, the
            # visits pipeline (which JOINs registrations) would either:
            # (a) fail with `resolved_config_hash not set`, because the
            #     orchestration layer didn't compute the registrations hash, or
            # (b) read an empty registrations cache, because registrations
            #     hadn't run yet.
            # Both happened on the first v3 deploy — see PR #135 deploy logs
            # at 16:16 UTC: visits errored out with the resolved_config_hash
            # message, while registrations downloaded after.
            from commcare_connect.labs.analysis.utils import resolve_join_hashes

            ordered_sources, configs_by_alias = _resolve_pipeline_sources_for_run(
                pipeline_access, definition.pipeline_sources
            )
            if configs_by_alias:
                resolve_join_hashes(configs_by_alias)

            try:
                for source in ordered_sources:
                    pipeline_id = source.get("pipeline_id")
                    alias = source.get("alias", f"pipeline_{pipeline_id}")

                    if not pipeline_id:
                        continue

                    pipeline_def = pipeline_access.get_definition(pipeline_id)
                    if not pipeline_def:
                        yield send_sse_event(f"Pipeline {pipeline_id} not found")
                        pipeline_data[alias] = {
                            "rows": [],
                            "metadata": {
                                "pipeline_id": pipeline_id,
                                "pipeline_name": None,
                                "row_count": 0,
                                "opportunity_ids": list(opp_ids),
                                "per_opp": {str(oid): {"error": "Pipeline not found"} for oid in opp_ids},
                            },
                        }
                        continue

                    merged_rows: list[dict] = []
                    per_opp_meta: dict[str, dict] = {}

                    for i, opp_id in enumerate(opp_ids):
                        mixin = AnalysisPipelineSSEMixin()
                        suffix = f" (opp {i + 1}/{len(opp_ids)})" if len(opp_ids) > 1 else ""
                        yield send_sse_event(f"Executing pipeline: {pipeline_def.name}{suffix}...")

                        try:
                            # Use the JOIN-resolved config we built above so
                            # the visits pipeline sees its registrations
                            # config_hash. Falling back to a fresh parse would
                            # lose the resolved_config_hash patch.
                            config = configs_by_alias.get(alias) or pipeline_access._schema_to_config(
                                pipeline_def.schema, pipeline_id
                            )
                            pipeline = AnalysisPipeline(request)
                            pipeline_stream = pipeline.stream_analysis(config, opportunity_id=opp_id)
                            logger.info(
                                "[PipelineStream] Starting stream for pipeline %s, opp %s",
                                pipeline_id,
                                opp_id,
                            )
                            yield from mixin.stream_pipeline_events(pipeline_stream)

                            result = mixin._pipeline_result
                            from_cache = mixin._pipeline_from_cache

                            row_count = len(result.rows) if result else 0
                            per_opp_meta[str(opp_id)] = {
                                "row_count": row_count,
                                "from_cache": from_cache,
                            }

                            if result:
                                yield send_sse_event(f"Processing {alias} data (opp {opp_id})...")
                                for row in result.rows:
                                    row_dict = {
                                        "id": getattr(row, "id", None),
                                        "entity_id": row.entity_id,
                                        "entity_name": row.entity_name,
                                        "username": row.username,
                                        "visit_date": format_date(row.visit_date),
                                        "total_visits": getattr(row, "total_visits", 0),
                                        "approved_visits": getattr(row, "approved_visits", 0),
                                        "pending_visits": getattr(row, "pending_visits", 0),
                                        "rejected_visits": getattr(row, "rejected_visits", 0),
                                        "flagged_visits": getattr(row, "flagged_visits", 0),
                                        "first_visit_date": format_date(getattr(row, "first_visit_date", None)),
                                        "last_visit_date": format_date(getattr(row, "last_visit_date", None)),
                                        "opportunity_id": opp_id,
                                    }
                                    custom = getattr(row, "custom_fields", None) or getattr(row, "computed", None)
                                    if custom:
                                        row_dict.update(custom)
                                    merged_rows.append(row_dict)
                        except Exception as e:
                            from commcare_connect.labs.analysis.backends.sql.cache import CacheConcurrencyError
                            from commcare_connect.labs.integrations.commcare.api_client import CCHQAuthError

                            logger.exception(
                                "[PipelineStream] Pipeline %s failed for opp %s",
                                pipeline_id,
                                opp_id,
                            )
                            per_opp_entry = {"error": str(e)}
                            if isinstance(e, CCHQAuthError):
                                per_opp_entry["auth_error"] = "commcare_hq"
                                per_opp_entry["auth_error_domain"] = e.domain
                            if isinstance(e, CacheConcurrencyError):
                                # Loud terminal error: another pipeline run for the
                                # same (opportunity, config) collided with this one
                                # in the cache layer. Re-running once the other
                                # writer finishes will hit the cache cleanly.
                                per_opp_entry["concurrent_run"] = True
                                per_opp_entry["cache_table"] = e.table
                                yield send_sse_event(
                                    f"Pipeline '{pipeline_def.name}' aborted: another run "
                                    f"for opp {opp_id} is already in flight (collided on "
                                    f"{e.table}). Wait a moment and retry — a cache hit "
                                    f"is likely.",
                                    error=str(e),
                                    data={
                                        "pipeline_alias": alias,
                                        "pipeline_name": pipeline_def.name,
                                        "pipeline_error": str(e)[:500],
                                        "concurrent_run": True,
                                        "cache_table": e.table,
                                    },
                                )
                                # Stop the entire pipeline stream — don't proceed
                                # to the next pipeline source. Any subsequent
                                # writer would just collide too.
                                per_opp_meta[str(opp_id)] = per_opp_entry
                                pipeline_data[alias] = {
                                    "rows": [],
                                    "metadata": {
                                        "pipeline_id": pipeline_id,
                                        "pipeline_name": pipeline_def.name,
                                        "row_count": 0,
                                        "concurrent_run": True,
                                        "cache_table": e.table,
                                        "opportunity_ids": list(opp_ids),
                                        "per_opp": per_opp_meta,
                                    },
                                }
                                return
                            per_opp_meta[str(opp_id)] = per_opp_entry
                            # Surface per-pipeline failure to the FE with the
                            # pipeline name so users see which one broke
                            # rather than a generic "connection lost".
                            yield send_sse_event(
                                f"Pipeline '{pipeline_def.name}' failed for opp {opp_id}: {str(e)[:200]}",
                                data={
                                    "pipeline_alias": alias,
                                    "pipeline_name": pipeline_def.name,
                                    "pipeline_error": str(e)[:500],
                                },
                            )

                    # Aggregate per-opp errors up to the alias level so the FE
                    # render can detect them with a single check
                    # (pipelines[alias].metadata.auth_error). The V2 render's
                    # auth-error gate looks here; the SSE path used to leave
                    # the auth_error tag buried under per_opp[opp_id], where
                    # the render didn't see it → the dashboard happily showed
                    # "0 rows (none found)" instead of the auth panel.
                    alias_metadata = {
                        "pipeline_id": pipeline_id,
                        "pipeline_name": pipeline_def.name,
                        "row_count": len(merged_rows),
                        "opportunity_ids": list(opp_ids),
                        "per_opp": per_opp_meta,
                    }
                    auth_failed_opps = [oid for oid, m in per_opp_meta.items() if m.get("auth_error") == "commcare_hq"]
                    if auth_failed_opps:
                        alias_metadata["auth_error"] = "commcare_hq"
                        alias_metadata["auth_error_domain"] = next(
                            (per_opp_meta[oid].get("auth_error_domain") for oid in auth_failed_opps),
                            None,
                        )
                        alias_metadata["auth_authorize_url"] = "/labs/commcare/initiate/"
                    pipeline_data[alias] = {
                        "rows": merged_rows,
                        "metadata": alias_metadata,
                    }
            finally:
                pipeline_access.close()

            # Send final complete event with all data
            yield send_sse_event(
                f"Loaded {sum(len(p.get('rows', [])) for p in pipeline_data.values())} records",
                data={"pipelines": pipeline_data},
            )

        except Exception:
            logger.exception("[PipelineStream] Error")
            yield send_sse_event("Error", error="An internal error occurred")

    def _maybe_probe_cchq_access(self, request, definition, opportunity_id, access_token):
        """If any pipeline source uses cchq_forms, ping CCHQ before the long pull.

        Yields an SSE error event and returns early (caller halts) if CCHQ
        is unreachable. The probe takes 1-2 seconds in the success case;
        in the failure case we surface 'CommCare HQ unreachable' to the
        user immediately instead of letting them wait 60+ seconds for the
        ALB to drop the connection.
        """
        from commcare_connect.labs.analysis.sse_streaming import send_sse_event
        from commcare_connect.labs.integrations.commcare.api_client import CommCareDataAccess
        from commcare_connect.workflow.templates.mbw_monitoring.data_fetchers import fetch_opportunity_metadata

        # Any cchq_forms sources?
        needs_cchq = False
        for source in definition.pipeline_sources or []:
            sid = source.get("pipeline_id")
            if not sid:
                continue
            try:
                pa = PipelineDataAccess(request=request, access_token=access_token, opportunity_id=opportunity_id)
                try:
                    pdef = pa.get_definition(sid)
                finally:
                    pa.close()
                if pdef and pdef.schema and pdef.schema.get("data_source", {}).get("type") == "cchq_forms":
                    needs_cchq = True
                    break
            except Exception:
                # Don't block on probe-classification errors
                continue
        if not needs_cchq:
            return

        try:
            metadata = fetch_opportunity_metadata(access_token, opportunity_id)
            cc_domain = metadata.get("cc_domain")
            if not cc_domain:
                yield send_sse_event(
                    "Error",
                    error=("Opportunity has no CommCare domain configured. " "Contact your project admin."),
                )
                return
            client = CommCareDataAccess(request, cc_domain)
            if not client.verify_hq_access():
                # send_sse_event(message, data, error) — extra fields go in data,
                # NOT as kwargs. (My first attempt passed cchq_auth_required as
                # a kwarg and it crashed with "unexpected keyword argument", so
                # the probe failed silently and the user saw "0 rows" instead
                # of the auth panel — even though verify_hq_access correctly
                # detected the 403.)
                yield send_sse_event(
                    "Error",
                    error=(
                        "CommCare HQ access denied. The OAuth token may have "
                        "expired or you may have lost access to the project. "
                        "Re-authorize at /labs/commcare/initiate/?next=/labs/overview/."
                    ),
                    data={
                        "cchq_auth_required": True,
                        "authorize_url": "/labs/commcare/initiate/?next=/labs/overview/",
                        "domain": cc_domain,
                    },
                )
                return
        except Exception as e:
            # Probe itself failed (network, etc.) — surface but don't block.
            # The downstream pipeline will still attempt and may succeed.
            logger.warning("[PipelineStream] CCHQ probe failed: %s", e)
            yield send_sse_event(f"Warning: could not verify CommCare HQ access ({type(e).__name__}). Continuing...")


@login_required
@require_POST
def delete_run_api(request, run_id):
    """
    Delete a workflow run and all its results.

    Cancels any running celery job first, then deletes:
    - Linked audit sessions
    - The run record itself
    """
    from config import celery_app

    data_access = None
    try:
        access_token = request.session.get("labs_oauth", {}).get("access_token")
        if not access_token:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)

        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)

        job_cancelled = False
        cancelled_job_id = None

        # Cancel any running celery job first
        try:
            # Use the state property which safely handles None data
            state = run.state if hasattr(run, "state") else (run.data or {}).get("state", {})
            active_job = state.get("active_job", {}) if isinstance(state, dict) else {}

            if active_job.get("status") == "running" and active_job.get("job_id"):
                cancelled_job_id = active_job["job_id"]
                try:
                    celery_app.control.revoke(cancelled_job_id, terminate=True)
                    job_cancelled = True
                    logger.info(f"[DeleteRun] Cancelled celery job {cancelled_job_id} before deleting run {run_id}")
                except Exception as e:
                    logger.warning(f"[DeleteRun] Failed to revoke celery task {cancelled_job_id}: {e}")
        except Exception as e:
            logger.warning(f"[DeleteRun] Error accessing job state for run {run_id}: {e}")

        # Delete the run and all linked records (audit sessions, etc.)
        deleted_counts = data_access.delete_run(run_id, delete_linked=True)

        logger.info(
            f"[DeleteRun] Deleted run {run_id}: "
            f"{deleted_counts.get('audit_sessions', 0)} audit sessions, "
            f"job_cancelled={job_cancelled}"
        )

        return JsonResponse(
            {
                "success": True,
                "run_id": run_id,
                "deleted": True,
                "deleted_counts": deleted_counts,
                "job_cancelled": job_cancelled,
                "cancelled_job_id": cancelled_job_id,
            }
        )

    except Exception:
        logger.exception("[DeleteRun] Failed to delete run %s", run_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    finally:
        if data_access:
            try:
                data_access.close()
            except Exception:
                pass


# =============================================================================
# Image Proxy and Visit Images API
# =============================================================================


class WorkflowImageProxyView(LoginRequiredMixin, View):
    """Serve visit images from Connect production API for workflow templates."""

    def get(self, request, opp_id, blob_id):
        try:
            labs_oauth = request.session.get("labs_oauth", {})
            access_token = labs_oauth.get("access_token")
            if not access_token:
                return HttpResponse("Unauthorized", status=401)

            production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
            with httpx.Client(
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=30.0,
            ) as client:
                resp = client.get(
                    f"{production_url}/export/opportunity/{opp_id}/image/",
                    params={"blob_id": blob_id},
                )
                resp.raise_for_status()

            response = HttpResponse(resp.content, content_type="image/jpeg")
            response["Content-Disposition"] = f'inline; filename="{blob_id}.jpg"'  # noqa: E702
            response["Cache-Control"] = "public, max-age=86400"
            return response
        except Exception as e:
            logger.error(f"Workflow image fetch failed: blob_id={blob_id}, opp_id={opp_id}: {e}")
            return HttpResponse("Image not found", status=404)


@login_required
@require_GET
def visit_images_api(request, opp_id):
    """Return image metadata for visits, keyed by visit_id.

    Query params:
        visit_ids: comma-separated visit IDs
    """
    visit_ids_raw = request.GET.get("visit_ids", "")
    if not visit_ids_raw:
        return JsonResponse({"error": "visit_ids required"}, status=400)

    try:
        visit_ids = [int(v.strip()) for v in visit_ids_raw.split(",") if v.strip()]
    except ValueError:
        return JsonResponse({"error": "Invalid visit_ids"}, status=400)

    if len(visit_ids) > 100:
        return JsonResponse({"error": "Max 100 visit IDs"}, status=400)

    try:
        labs_oauth = request.session.get("labs_oauth", {})
        access_token = labs_oauth.get("access_token")
        if not access_token:
            return JsonResponse({"error": "Unauthorized"}, status=401)

        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

        pipeline = AnalysisPipeline(request=request)
        visit_dicts = pipeline.fetch_raw_visits(
            opportunity_id=opp_id,
            filter_visit_ids=set(visit_ids),
            include_images=True,
        )

        from commcare_connect.audit.analysis_config import extract_images_with_question_ids

        result = {}
        for visit_dict in visit_dicts:
            vid = str(visit_dict.get("id", ""))
            images = extract_images_with_question_ids(visit_dict)
            if images:
                result[vid] = images

        return JsonResponse({"visit_images": result})
    except Exception:
        logger.exception("Visit images fetch failed: opp_id=%s", opp_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


class UpdateOpportunityIdsView(LoginRequiredMixin, View):
    """API endpoint to replace the opportunity_ids list on a workflow definition.

    POST JSON body: {"opportunity_ids": [int, ...]}
    All IDs are validated against the user's accessible opportunities.
    """

    def post(self, request, definition_id):
        from commcare_connect.labs.context import get_org_data

        try:
            body = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        raw = body.get("opportunity_ids", [])
        if not isinstance(raw, list):
            return JsonResponse({"error": "opportunity_ids must be a list"}, status=400)

        try:
            opportunity_ids = [int(x) for x in raw]
        except (TypeError, ValueError):
            return JsonResponse({"error": "Invalid opportunity_ids"}, status=400)

        if not opportunity_ids:
            return JsonResponse(
                {"error": "opportunity_ids must contain at least one opportunity"},
                status=400,
            )

        # Validate against user's accessible opportunities
        user_opp_ids = {
            int(o["id"]) for o in (get_org_data(request) or {}).get("opportunities", []) if o.get("id") is not None
        }
        unauthorized = [oid for oid in opportunity_ids if oid not in user_opp_ids]
        if unauthorized:
            return JsonResponse(
                {"error": f"Not authorized for opportunities: {unauthorized}"},
                status=403,
            )

        data_access = WorkflowDataAccess(request=request)
        try:
            existing = data_access.get_definition(definition_id)
            if not existing:
                return JsonResponse({"error": "Workflow not found"}, status=404)
            if not existing.multi_opp:
                return JsonResponse(
                    {"error": "Workflow is not multi-opp"},
                    status=400,
                )

            result = data_access.update_opportunity_ids(definition_id, opportunity_ids)
            if not result:
                return JsonResponse({"error": "Workflow not found"}, status=404)
            return JsonResponse(
                {
                    "success": True,
                    "definition_id": definition_id,
                    "opportunity_ids": opportunity_ids,
                }
            )
        except Exception:
            logger.exception("Failed to update opportunity_ids for %s", definition_id)
            return JsonResponse({"error": "An internal error occurred"}, status=500)
        finally:
            data_access.close()
