"""
Workflow views for dynamic AI-generated workflows.

These views handle listing, viewing, and executing workflows that are stored
as LabsRecord objects with React component code for rendering.
"""

import json
import logging

import httpx
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from commcare_connect.labs import s3_export
from commcare_connect.labs.context import get_org_data
from commcare_connect.workflow.data_access import PipelineDataAccess, WorkflowDataAccess
from commcare_connect.workflow.templates import TEMPLATES
from commcare_connect.workflow.templates import create_workflow_from_template as create_from_template
from commcare_connect.workflow.templates import list_templates

logger = logging.getLogger(__name__)


def _is_dimagi_user(user) -> bool:
    """Return True if the user is a @dimagi.com staff member or in the local dev allowlist."""
    # TODO: Re-enable email detection once Connect server PR is merged and deployed.
    # The email field is currently empty because /o/introspect/ and /o/userinfo/ don't
    # return it; the fix adds it to /export/opp_org_program_list/ instead.
    return True
    email = getattr(user, "email", "") or ""  # noqa: F401
    username = getattr(user, "username", "") or ""  # noqa: F401
    allowlist = getattr(settings, "LABS_ADMIN_USERNAMES", [])  # noqa: F401
    return (
        email.endswith("@dimagi.com")
        or username.endswith("@dimagi.com")
        or bool(username and username in allowlist)
    )


class WorkflowTemplateListAPIView(LoginRequiredMixin, View):
    """API endpoint to list available workflow templates."""

    def get(self, request):
        """Return list of workflow templates with metadata for UI rendering."""
        return JsonResponse({"templates": list_templates()})


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

        # Restrict Create Workflow button to @dimagi.com users / allowlist
        context["is_dimagi"] = _is_dimagi_user(self.request.user)

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
                context["available_templates"] = list_templates()
            except Exception as e:
                logger.error(f"Failed to load workflow definitions: {e}", exc_info=True)
                context["workflows"] = []
                context["definitions"] = []
                context["available_templates"] = list_templates()
                context["error"] = str(e)
            finally:
                if pipeline_access is not None:
                    pipeline_access.close()
                if data_access is not None:
                    data_access.close()
        else:
            context["workflows"] = []
            context["definitions"] = []
            context["available_templates"] = list_templates()

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
        """Create-and-redirect when no run_id is provided.

        When the URL has no ``?run_id=`` and is not in edit mode, create a new
        run and redirect to the same URL with ``?run_id=<id>``.  This ensures
        the URL always reflects the active run, preventing duplicate run
        creation when the user is redirected back (e.g. after OAuth
        re-authorization).
        """
        definition_id = kwargs.get("definition_id")
        run_id = request.GET.get("run_id")
        is_edit_mode = request.GET.get("edit") == "true"

        if not run_id and not is_edit_mode:
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id")
            if opportunity_id:
                from datetime import datetime, timedelta, timezone

                from django.shortcuts import redirect

                data_access = WorkflowDataAccess(request=request)
                try:
                    today = datetime.now(timezone.utc).date()
                    week_start = today - timedelta(days=today.weekday())
                    week_end = week_start + timedelta(days=6)
                    run = data_access.create_run(
                        definition_id=definition_id,
                        opportunity_id=opportunity_id,
                        period_start=week_start.isoformat(),
                        period_end=week_end.isoformat(),
                        initial_state={"worker_states": {}},
                    )
                    org_data = get_org_data(request)
                    opp_map = {o["id"]: o.get("name", "") for o in org_data.get("opportunities", [])}
                    # definition_name and template_type omitted — require loading the
                    # definition record (extra API call); definition_id in the row
                    # allows downstream joins.
                    s3_export.upsert_workflow_run(
                        run,
                        opportunity_name=opp_map.get(run.opportunity_id, ""),
                        username=getattr(request.user, "username", "") or "",
                    )
                except Exception as e:
                    logger.exception("Failed to create run for opp %s", opportunity_id)
                    return super().get(request, *args, **kwargs)
                finally:
                    data_access.close()
                params = request.GET.copy()
                params["run_id"] = str(run.id)
                return redirect(f"{request.path}?{params.urlencode()}")

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

            # Get render code — in DEBUG mode, try template file first so local
            # edits are reflected immediately without a manual sync step.
            # Falls back to DB if no template name match (e.g. custom-named workflow).
            if settings.DEBUG:
                name_lower = definition.name.lower().replace(" ", "_")
                live_code = None
                for key, tmpl in TEMPLATES.items():
                    if key == name_lower or tmpl["name"].lower() == definition.name.lower():
                        live_code = tmpl.get("render_code")
                        break
                if live_code is not None:
                    context["render_code"] = live_code
                else:
                    render_code = data_access.get_render_code(definition_id)
                    context["render_code"] = render_code.data.get("component_code") if render_code else None
            else:
                render_code = data_access.get_render_code(definition_id)
                context["render_code"] = render_code.data.get("component_code") if render_code else None

            # Get workers for the opportunity
            workers = data_access.get_workers(opportunity_id)
            context["workers"] = workers

            # Get or create run based on mode
            if is_edit_mode:
                # Edit mode: create temporary run (not persisted)
                from datetime import datetime, timedelta, timezone

                today = datetime.now(timezone.utc).date()
                week_start = today - timedelta(days=today.weekday())
                week_end = week_start + timedelta(days=6)

                run_data = {
                    "id": 0,  # Temporary ID
                    "definition_id": definition_id,
                    "opportunity_id": opportunity_id,
                    "opportunity_name": labs_context.get("opportunity", {}).get("name"),
                    "status": "preview",
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
                    "opportunity_name": labs_context.get("opportunity", {}).get("name"),
                    "status": run.data.get("status", "in_progress"),
                    "state": run.data.get("state", {}),
                    "period_start": run.data.get("period_start"),
                    "period_end": run.data.get("period_end"),
                }
                context["is_edit_mode"] = False
            else:
                # No run_id and not edit mode — get() should have redirected.
                # This branch only executes if opportunity_id was missing at
                # get() time (no labs context), so show a friendly error.
                context["error"] = "Could not create a new run. Please select an opportunity."
                return context

            # Pipeline data will be loaded async via SSE - don't block page load
            # Pass empty data initially; frontend will connect to SSE stream
            pipeline_data = {}

            # Prepare data for React (pass as dict, json_script will handle encoding)
            context["workflow_data"] = {
                "definition": definition.data,
                "definition_id": definition.id,
                "opportunity_id": opportunity_id,
                "render_code": context.get("render_code"),
                "instance": run_data,
                "is_edit_mode": is_edit_mode,
                "workers": workers,
                "pipeline_data": pipeline_data,
                "links": {
                    "auditUrlBase": "/labs/audit/create/",
                    "taskUrlBase": "/labs/tasks/new/",
                },
                "apiEndpoints": {
                    # In edit mode, state updates are local only
                    "updateState": None if is_edit_mode else f"/labs/workflow/api/run/{run_data['id']}/state/",
                    "getWorkers": "/labs/workflow/api/workers/",
                    "getPipelineData": f"/labs/workflow/api/{definition_id}/pipeline-data/",
                    # SSE stream for async pipeline data loading
                    "streamPipelineData": f"/labs/workflow/api/{definition_id}/pipeline-data/stream/",
                    # MBW monitoring actions
                    "saveWorkerResult": f"/labs/workflow/api/run/{run_data['id']}/worker-result/",
                    "completeRun": f"/labs/workflow/api/run/{run_data['id']}/complete/",
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
                # Also get the definition
                definition_id = run.data.get("definition_id")
                if definition_id:
                    context["definition"] = data_access.get_definition(definition_id)
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
    except Exception as e:
        logger.error(f"Failed to fetch workers: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
def update_state_api(request, run_id):
    """API endpoint to update workflow run state."""
    try:
        data = json.loads(request.body)
        new_state = data.get("state")

        if new_state is None:
            return JsonResponse({"error": "state required in request body"}, status=400)

        data_access = WorkflowDataAccess(request=request)
        updated_run = data_access.update_run_state(run_id, new_state)

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
            return JsonResponse({"error": "Run not found"}, status=404)

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Failed to update run state: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
            "result": "eligible_for_renewal" | "probation" | "suspended" | null,
            "notes": "Optional notes"
        }
    """
    VALID_RESULTS = ("eligible_for_renewal", "probation", "suspended")

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
    except Exception as e:
        logger.error(f"Failed to save worker result for run {run_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)
    finally:
        if data_access:
            data_access.close()


@login_required
@require_POST
def complete_run_api(request, run_id):
    """Mark a workflow run as completed.

    Updates run.data.status to 'completed' and stores overall_result/notes
    in the state via WorkflowDataAccess.complete_run().

    Request body:
        {
            "overall_result": "completed",  // optional, defaults to "completed"
            "notes": ""  // optional
        }
    """
    data_access = None
    try:
        data = json.loads(request.body)
        overall_result = data.get("overall_result", "completed")
        notes = data.get("notes", "")

        data_access = WorkflowDataAccess(request=request)
        run = data_access.get_run(run_id)
        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)

        result = data_access.complete_run(
            run_id=run_id,
            overall_result=overall_result,
            notes=notes,
            run=run,
        )

        if not result:
            return JsonResponse({"error": "Failed to update run"}, status=500)

        return JsonResponse(
            {
                "success": True,
                "status": "completed",
                "overall_result": overall_result,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Failed to complete run {run_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)
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
                        "status": run.data.get("status", "in_progress"),
                        "state": run.data.get("state", {}),
                    }
                }
            )
        else:
            return JsonResponse({"error": "Run not found"}, status=404)

    except Exception as e:
        logger.error(f"Failed to get run: {e}")
        return JsonResponse({"error": str(e)}, status=500)


@login_required
@require_POST
@login_required
@require_POST
def create_workflow_from_template_view(request):
    """Create a workflow from a template."""
    from django.contrib import messages
    from django.core.exceptions import PermissionDenied
    from django.shortcuts import redirect

    if not _is_dimagi_user(request.user):
        raise PermissionDenied

    template_key = request.POST.get("template", "performance_review")

    if template_key not in TEMPLATES:
        messages.error(request, f"Unknown template: {template_key}")
        return redirect("labs:workflow:list")

    try:
        data_access = WorkflowDataAccess(request=request)
        definition, render_code, pipeline = create_from_template(data_access, template_key, request=request)

        if pipeline:
            messages.success(
                request, f"Created workflow: {definition.name} (ID: {definition.id}) with pipeline: {pipeline.name}"
            )
        else:
            messages.success(request, f"Created workflow: {definition.name} (ID: {definition.id})")
        return redirect("labs:workflow:list")

    except Exception as e:
        logger.error(f"Failed to create workflow from template {template_key}: {e}", exc_info=True)
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

    except Exception as e:
        logger.error(f"Failed to get chat history for definition {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to clear chat history for definition {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to add chat message for definition {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to save render code for definition {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to sync template render code for definition {definition_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)
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

    except OCSAPIError as e:
        logger.error(f"OCS API error listing bots: {e}")
        return JsonResponse({"success": False, "error": str(e)}, status=500)
    except Exception as e:
        logger.error(f"Error listing OCS bots: {e}", exc_info=True)
        return JsonResponse({"success": False, "error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to fetch pipeline data for workflow {definition_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to list available pipelines: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to add pipeline source: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to remove pipeline source: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to share workflow {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to unshare workflow {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to delete workflow {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to rename workflow {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to delete pipeline {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to list shared workflows: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to copy workflow {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to share pipeline {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to unshare pipeline {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to list shared pipelines: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to copy pipeline {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to get pipeline definition {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to update pipeline schema {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to execute pipeline preview {definition_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to generate SQL preview for pipeline {definition_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to get pipeline chat history {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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

    except Exception as e:
        logger.error(f"Failed to clear pipeline chat history {definition_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to start job for run {run_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


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
    except Exception as e:
        logger.error(f"Failed to cancel job {task_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


class PipelineDataStreamView(LoginRequiredMixin, View):
    """
    SSE endpoint for streaming pipeline data loading progress.

    Follows same pattern as custom_analysis KMCChildListStreamView.
    Page loads instantly, SSE streams progress, data renders when complete.
    """

    def get(self, request, definition_id):
        from collections.abc import Generator

        from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
        from commcare_connect.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, send_sse_event

        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

        def stream_data() -> Generator[str, None, None]:
            """Stream pipeline data loading progress via SSE."""
            mixin = AnalysisPipelineSSEMixin()

            try:
                if not opportunity_id:
                    yield send_sse_event("Error", error="No opportunity selected")
                    return

                # Check for OAuth token
                labs_oauth = request.session.get("labs_oauth", {})
                if not labs_oauth.get("access_token"):
                    yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                    return

                # Get workflow definition to find pipeline sources
                data_access = WorkflowDataAccess(request=request)
                definition = data_access.get_definition(definition_id)

                if not definition:
                    yield send_sse_event("Error", error=f"Workflow {definition_id} not found")
                    return

                if not definition.pipeline_sources:
                    yield send_sse_event("No pipelines", data={"pipelines": {}})
                    return

                yield send_sse_event("Loading pipeline configurations...")

                # Execute each pipeline source with streaming
                pipeline_data = {}

                for source in definition.pipeline_sources:
                    pipeline_id = source.get("pipeline_id")
                    alias = source.get("alias", f"pipeline_{pipeline_id}")

                    if not pipeline_id:
                        continue

                    # Get pipeline definition
                    pipeline_access = PipelineDataAccess(
                        request=request,
                        access_token=labs_oauth.get("access_token"),
                        opportunity_id=opportunity_id,
                    )

                    pipeline_def = pipeline_access.get_definition(pipeline_id)
                    if not pipeline_def:
                        yield send_sse_event(f"Pipeline {pipeline_id} not found")
                        continue

                    yield send_sse_event(f"Executing pipeline: {pipeline_def.name}...")

                    # Convert schema to config
                    config = pipeline_access._schema_to_config(pipeline_def.schema, pipeline_id)

                    # Execute with streaming using AnalysisPipeline
                    pipeline = AnalysisPipeline(request)
                    pipeline_stream = pipeline.stream_analysis(config, opportunity_id=opportunity_id)

                    logger.info(f"[PipelineStream] Starting stream for pipeline {pipeline_id}, opp {opportunity_id}")

                    # Stream all pipeline events as SSE (using mixin pattern)
                    yield from mixin.stream_pipeline_events(pipeline_stream)

                    # Result is now available
                    result = mixin._pipeline_result
                    from_cache = mixin._pipeline_from_cache

                    if result:
                        logger.info(
                            f"[PipelineStream] Got {len(result.rows) if hasattr(result, 'rows') else 0} rows"
                            f" (cache: {from_cache})"
                        )

                        yield send_sse_event(f"Processing {alias} data...")

                        # Convert result to serializable format
                        rows = []
                        for row in result.rows:
                            # Handle dates - may be datetime or string depending on backend
                            def format_date(d):
                                if d and hasattr(d, "isoformat"):
                                    return d.isoformat()
                                return d

                            row_dict = {
                                "id": getattr(row, "id", None),
                                "entity_id": row.entity_id,
                                "entity_name": row.entity_name,
                                "username": row.username,
                                "visit_date": format_date(row.visit_date),
                                # Built-in FLW aggregation fields
                                "total_visits": getattr(row, "total_visits", 0),
                                "approved_visits": getattr(row, "approved_visits", 0),
                                "pending_visits": getattr(row, "pending_visits", 0),
                                "rejected_visits": getattr(row, "rejected_visits", 0),
                                "flagged_visits": getattr(row, "flagged_visits", 0),
                                "first_visit_date": format_date(getattr(row, "first_visit_date", None)),
                                "last_visit_date": format_date(getattr(row, "last_visit_date", None)),
                            }
                            # Add computed fields (custom fields from config)
                            # FLWRow uses custom_fields, VisitRow uses computed
                            custom = getattr(row, "custom_fields", None) or getattr(row, "computed", None)
                            if custom:
                                row_dict.update(custom)
                            rows.append(row_dict)

                        pipeline_data[alias] = {
                            "rows": rows,
                            "metadata": {
                                "pipeline_id": pipeline_id,
                                "pipeline_name": pipeline_def.name,
                                "row_count": len(rows),
                                "from_cache": from_cache,
                            },
                        }

                    pipeline_access.close()

                # Send final complete event with all data
                yield send_sse_event(
                    f"Loaded {sum(len(p.get('rows', [])) for p in pipeline_data.values())} records",
                    data={"pipelines": pipeline_data},
                )

            except Exception as e:
                logger.error(f"[PipelineStream] Error: {e}", exc_info=True)
                yield send_sse_event("Error", error=str(e))

        response = StreamingHttpResponse(
            stream_data(),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response


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

    except Exception as e:
        logger.error(f"[DeleteRun] Failed to delete run {run_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)
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
    except Exception as e:
        logger.error(f"Visit images fetch failed: opp_id={opp_id}: {e}")
        return JsonResponse({"error": str(e)}, status=500)
