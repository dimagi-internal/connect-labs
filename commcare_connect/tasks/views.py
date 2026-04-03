"""
Task views using ExperimentRecord-based TaskRecord model.

These views replace the old Django ORM-based views with ExperimentRecord-backed
implementation using TaskDataAccess for OAuth-based API access.
"""

import json
import logging
from datetime import datetime, timezone

from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView

from commcare_connect.labs.context import get_org_data
from commcare_connect.labs.integrations.ocs.api_client import OCSAPIError, OCSDataAccess
from commcare_connect.tasks.data_access import TaskDataAccess
from commcare_connect.tasks.models import TaskRecord

logger = logging.getLogger(__name__)


class TaskListView(LoginRequiredMixin, ListView):
    """List view for tasks with filtering and statistics."""

    model = TaskRecord
    template_name = "tasks/tasks_list.html"
    context_object_name = "tasks"
    paginate_by = 50

    def get_queryset(self):
        """Get tasks the user can access with filtering."""
        # Check if required context is present (program or opportunity)
        labs_context = getattr(self.request, "labs_context", {})
        if not labs_context.get("opportunity_id") and not labs_context.get("program_id"):
            # No program or opportunity selected, return empty list
            return []

        data_access = TaskDataAccess(user=self.request.user, request=self.request)

        # Get all tasks (OAuth enforces access) - returns a list, not QuerySet
        tasks = data_access.get_tasks()

        # Apply filters from GET parameters
        status_filter = self.request.GET.get("status")
        if status_filter and status_filter != "all":
            tasks = [t for t in tasks if t.status == status_filter]

        search_query = self.request.GET.get("search")
        if search_query:
            search_lower = search_query.lower()
            tasks = [t for t in tasks if search_lower in t.title.lower()]

        task_ids_param = self.request.GET.get("task_ids")
        if task_ids_param:
            allowed_ids = {int(tid) for tid in task_ids_param.split(",") if tid.strip().isdigit()}
            tasks = [t for t in tasks if t.id in allowed_ids]

        # Sort by id descending (higher IDs are more recent)
        return sorted(tasks, key=lambda x: x.id, reverse=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check if required context is present (program or opportunity)
        labs_context = getattr(self.request, "labs_context", {})
        has_context = bool(labs_context.get("opportunity_id") or labs_context.get("program_id"))

        if has_context:
            data_access = TaskDataAccess(user=self.request.user, request=self.request)
            all_tasks = data_access.get_tasks()
        else:
            all_tasks = []

        # Calculate statistics - all_tasks is a list, not QuerySet
        stats = {
            "total": len(all_tasks),
            "unassigned": len([t for t in all_tasks if t.status == "unassigned"]),
            "network_manager": len([t for t in all_tasks if t.status == "network_manager"]),
            "program_manager": len([t for t in all_tasks if t.status == "program_manager"]),
            "action_underway": len([t for t in all_tasks if t.status == "action_underway"]),
            "resolved": len([t for t in all_tasks if t.status == "resolved"]),
        }

        # Status and type choices for filters
        statuses = [
            "unassigned",
            "network_manager",
            "program_manager",
            "action_underway",
            "resolved",
            "closed",
        ]
        action_types = ["warning", "deactivation"]

        # Check for Connect OAuth token
        has_token = self.request.user.is_authenticated
        token_expires_at = None

        context.update(
            {
                "stats": stats,
                "statuses": statuses,
                "action_types": action_types,
                "selected_status": self.request.GET.get("status", "all"),
                "selected_action_type": self.request.GET.get("action_type", "all"),
                "has_connect_token": has_token,
                "token_expires_at": token_expires_at,
                "has_context": has_context,
                "task_ids_filter": self.request.GET.get("task_ids", ""),
            }
        )

        return context


# TaskDetailView removed - TaskCreateEditView is the main interface for viewing/editing tasks


class TaskCreationWizardView(LoginRequiredMixin, TemplateView):
    """Wizard interface for creating tasks using Connect OAuth."""

    template_name = "tasks/task_creation_wizard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check for Connect OAuth token
        has_token = self.request.user.is_authenticated
        token_expires_at = None

        # Pass labs_context to template for pre-selection
        labs_context = getattr(self.request, "labs_context", {})
        context["default_opportunity_id"] = labs_context.get("opportunity_id") or ""
        context["default_program_id"] = labs_context.get("program_id") or ""

        # Pass opportunities from user's org_data (already fetched from opp_org_program API)
        org_data = get_org_data(self.request)
        opportunities = org_data.get("opportunities", [])

        # Filter by program if one is selected in labs_context
        program_id = labs_context.get("program_id")
        if program_id:
            opportunities = [o for o in opportunities if o.get("program") == program_id]

        # Format for template
        context["opportunities_json"] = json.dumps(
            [
                {
                    "id": opp.get("id"),
                    "name": opp.get("name"),
                    "organization_name": opp.get("organization", ""),
                    "program_name": "",
                    "visit_count": opp.get("visit_count", 0),
                    "end_date": opp.get("end_date"),
                    "active": opp.get("is_active", True),
                }
                for opp in opportunities
            ]
        )

        context.update(
            {
                "has_connect_token": has_token,
                "token_expires_at": token_expires_at,
            }
        )

        return context


class TaskCreateEditView(LoginRequiredMixin, TemplateView):
    """Combined create/edit view for single-FLW tasks.

    This is the main workhorse page for task management:
    - Create mode: Select FLW, fill task details, create task
    - Edit mode: Load existing task, edit details, manage timeline/comments/actions
    """

    template_name = "tasks/task_create_edit.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get task_id from URL if editing
        task_id = self.kwargs.get("task_id")
        is_edit_mode = task_id is not None

        # Get labs_context for opportunity
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        # Get opportunity name from user's org_data
        org_data = get_org_data(self.request)
        opportunities = org_data.get("opportunities", [])
        opportunity_name = ""
        for opp in opportunities:
            if opp.get("id") == opportunity_id:
                opportunity_name = opp.get("name", "")
                break

        # Load FLW list if we have an opportunity
        flw_list = []
        if opportunity_id:
            try:
                data_access = TaskDataAccess(user=self.request.user, request=self.request)
                flw_list = data_access.get_users_from_opportunity(opportunity_id)
                data_access.close()
            except Exception as e:
                logger.warning(f"Failed to load FLW list for opportunity {opportunity_id}: {e}")

        # Check for Connect OAuth token
        has_token = self.request.user.is_authenticated
        token_expires_at = None

        # If editing, load the existing task
        task_data = None
        timeline = []
        if is_edit_mode:
            try:
                data_access = TaskDataAccess(user=self.request.user, request=self.request)
                task = data_access.get_task(task_id)
                if task:
                    timeline = task.get_timeline()
                    task_data = {
                        "id": task.id,
                        "username": task.task_username,
                        "flw_name": task.flw_name,
                        "user_id": task.user_id,
                        "opportunity_id": task.opportunity_id,
                        "status": task.status,
                        "priority": task.priority,
                        "title": task.title,
                        "description": task.description,
                        "audit_session_id": task.audit_session_id,
                        "assigned_to_type": task.assigned_to_type,
                        "assigned_to_name": task.assigned_to_name,
                        "resolution_details": task.resolution_details,
                    }
                data_access.close()
            except Exception as e:
                logger.error(f"Failed to load task {task_id}: {e}")

        # Get current user info for assignment dropdown
        current_user_name = self.request.user.get_display_name()

        # Get manager names from opportunity's org/program references
        labs_context = getattr(self.request, "labs_context", {})
        opportunity = labs_context.get("opportunity", {})
        org_data = get_org_data(self.request)

        # Look up organization name (Network Manager) by slug
        org_slug = opportunity.get("organization")
        network_manager_name = "Network Manager"
        for org in org_data.get("organizations", []):
            if org.get("slug") == org_slug:
                network_manager_name = org.get("name", "Network Manager")
                break

        # Look up program name (Program Manager) by ID
        program_id = opportunity.get("program")
        program_manager_name = "Program Manager"
        for prog in org_data.get("programs", []):
            if prog.get("id") == program_id:
                program_manager_name = prog.get("name", "Program Manager")
                break

        # Quick creation URL parameters for pre-filling the form
        # These allow other pages (e.g., audit session list) to link directly with params
        quick_params = {
            "audit_session_id": self.request.GET.get("audit_session_id", ""),
            "username": self.request.GET.get("username", ""),
            "title": self.request.GET.get("title", ""),
            "description": self.request.GET.get("description", ""),
        }
        # Filter out empty values
        quick_params = {k: v for k, v in quick_params.items() if v}

        context.update(
            {
                "is_edit_mode": is_edit_mode,
                "task_id": task_id,
                "task_data": json.dumps(task_data) if task_data else "null",
                "task": task_data,  # Also pass as dict for template access
                "timeline_json": json.dumps(timeline),
                "opportunity_id": opportunity_id,
                "opportunity_name": opportunity_name,
                "flw_list_json": json.dumps(flw_list),
                "has_connect_token": has_token,
                "token_expires_at": token_expires_at,
                "has_context": bool(opportunity_id),
                "current_user_name": current_user_name,
                "network_manager_name": network_manager_name,
                "program_manager_name": program_manager_name,
                "quick_params": json.dumps(quick_params),
            }
        )

        return context


# Opportunity API Views (used by creation wizard)


class OpportunitySearchAPIView(LoginRequiredMixin, View):
    """Search opportunities via Connect OAuth API."""

    def get(self, request):
        query = request.GET.get("query", "")
        limit = int(request.GET.get("limit", 50))

        try:
            data_access = TaskDataAccess(user=request.user, request=request)
            opportunities = data_access.search_opportunities(query, limit)
            data_access.close()

            return JsonResponse({"success": True, "opportunities": opportunities})

        except Exception:
            logger.exception("Failed to search opportunities")
            return JsonResponse({"error": "An internal error occurred"}, status=500)


class OpportunityWorkersAPIView(LoginRequiredMixin, View):
    """Get workers for an opportunity via Connect OAuth API."""

    def get(self, request, opportunity_id):
        try:
            data_access = TaskDataAccess(user=request.user, request=request)
            workers = data_access.get_users_from_opportunity(opportunity_id)
            data_access.close()

            return JsonResponse({"success": True, "workers": workers})

        except Exception:
            logger.exception("Failed to fetch workers for opportunity %s", opportunity_id)
            return JsonResponse({"error": "An internal error occurred"}, status=500)


class OpportunityLearningModulesAPIView(LoginRequiredMixin, View):
    """Get learning modules for an opportunity via Connect OAuth API."""

    def get(self, request, opportunity_id):
        try:
            data_access = TaskDataAccess(user=request.user, request=request)
            modules = data_access.get_learning_modules(opportunity_id)
            data_access.close()

            return JsonResponse({"success": True, "modules": modules})

        except Exception:
            logger.exception("Failed to fetch learning modules for opportunity %s", opportunity_id)
            return JsonResponse({"error": "An internal error occurred"}, status=500)


class CompletedModulesAPIView(LoginRequiredMixin, View):
    """Get completed learning modules for a user in an opportunity via Connect OAuth API."""

    def get(self, request, opportunity_id):
        username = request.GET.get("username")

        if not username:
            return JsonResponse({"error": "username parameter is required"}, status=400)

        try:
            data_access = TaskDataAccess(user=request.user, request=request)
            completed_modules = data_access.get_completed_modules(opportunity_id, username)
            data_access.close()

            return JsonResponse({"success": True, "completed_modules": completed_modules})

        except Exception:
            logger.exception("Failed to fetch completed modules for opportunity %s", opportunity_id)
            return JsonResponse({"error": "An internal error occurred"}, status=500)


# Task Bulk Creation API


@login_required
@csrf_exempt
@require_POST
def task_bulk_create(request):
    """Create multiple tasks at once (bulk creation)."""
    try:
        body = json.loads(request.body)
        opportunity_id = body.get("opportunity_id")
        flw_ids = body.get("flw_ids", [])
        flw_names = body.get("flw_names", {})  # Optional mapping: {username: display_name}
        priority = body.get("priority", "medium")
        title = body.get("title", "")
        description = body.get("description", "")

        if not opportunity_id:
            return JsonResponse({"success": False, "error": "opportunity_id is required"}, status=400)

        if not flw_ids:
            return JsonResponse({"success": False, "error": "At least one FLW must be selected"}, status=400)

        # Get a display name for the creator
        creator_name = request.user.get_display_name()

        data_access = TaskDataAccess(user=request.user, request=request)
        created_count = 0
        created_tasks = []
        errors = []

        for flw_id in flw_ids:
            try:
                username = str(flw_id)
                flw_name = flw_names.get(username, username)  # Use display name if provided
                # Create task for each FLW
                task = data_access.create_task(
                    username=username,
                    flw_name=flw_name,
                    opportunity_id=opportunity_id,
                    priority=priority,
                    title=title,
                    description=description,
                    creator_name=creator_name,
                )
                created_count += 1
                created_tasks.append({"username": username, "id": task.id})
            except Exception as e:
                errors.append(f"Failed to create task for FLW {flw_id}: {e!s}")
                logger.error(f"Error creating task for FLW {flw_id}: {e}", exc_info=True)

        data_access.close()

        return JsonResponse(
            {"success": True, "created_count": created_count, "tasks": created_tasks, "errors": errors}
        )

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error in bulk task creation")
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)


# Single Task Creation API (for combined create/edit page)


@login_required
@csrf_exempt
@require_POST
def task_single_create(request):
    """Create a single task for one FLW. Used by combined create/edit page."""
    try:
        body = json.loads(request.body)
        username = body.get("username")
        flw_name = body.get("flw_name", username)
        priority = body.get("priority", "medium")
        title = body.get("title", "")
        description = body.get("description", "")

        if not username:
            return JsonResponse({"success": False, "error": "username is required"}, status=400)

        if not title:
            return JsonResponse({"success": False, "error": "title is required"}, status=400)

        # Get opportunity from labs_context
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            return JsonResponse({"success": False, "error": "No opportunity selected in labs context"}, status=400)

        data_access = TaskDataAccess(user=request.user, request=request)

        try:
            # Get a display name for the creator
            creator_name = request.user.get_display_name()

            task = data_access.create_task(
                username=username,
                flw_name=flw_name,
                opportunity_id=opportunity_id,
                priority=priority,
                title=title,
                description=description,
                creator_name=creator_name,
            )

            return JsonResponse(
                {
                    "success": True,
                    "task_id": task.id,
                    "message": f"Task created successfully for {flw_name}",
                }
            )
        finally:
            data_access.close()

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception as e:
        logger.error(f"Error in single task creation: {e}", exc_info=True)
        return JsonResponse(
            {"success": False, "error": "Failed to create task. Please try again or contact support."}, status=500
        )


@login_required
def task_detail_api(request, task_id):
    """Return task data as JSON for inline task management."""
    data_access = TaskDataAccess(user=request.user, request=request)
    try:
        task = data_access.get_task(task_id)
        if not task:
            return JsonResponse({"success": False, "error": "Task not found"}, status=404)

        return JsonResponse(
            {
                "success": True,
                "task": {
                    "id": task.id,
                    "title": task.title,
                    "status": task.status,
                    "username": task.task_username,
                    "flw_name": task.flw_name,
                    "priority": task.priority,
                    "description": task.description,
                    "resolution_details": task.resolution_details,
                    "events": task.events,
                },
            }
        )
    except Exception as e:
        logger.error(f"Error fetching task detail: {e}", exc_info=True)
        return JsonResponse(
            {"success": False, "error": "Failed to fetch task details. Please try again or contact support."},
            status=500,
        )
    finally:
        data_access.close()


@login_required
@csrf_exempt
@require_POST
def task_update(request, task_id):
    """Update an existing task."""
    try:
        body = json.loads(request.body)

        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)

        if not task:
            return JsonResponse({"success": False, "error": "Task not found"}, status=404)

        # Get actor name for event logging
        actor_name = request.user.get_display_name()

        # Track what changed for event logging
        changes = []

        # Update allowed fields
        if "title" in body and body["title"] != task.title:
            task.data["title"] = body["title"]
            changes.append("title")

        if "description" in body and body["description"] != task.description:
            task.data["description"] = body["description"]
            changes.append("description")

        if "priority" in body and body["priority"] != task.priority:
            task.data["priority"] = body["priority"]
            changes.append("priority")

        if "status" in body and body["status"] != task.status:
            old_status = task.status
            task.data["status"] = body["status"]
            changes.append(f"status from {old_status} to {body['status']}")

        if "assigned_to_type" in body and body["assigned_to_type"] != task.assigned_to_type:
            task.data["assigned_to_type"] = body["assigned_to_type"]
            # Set assigned_to_name based on type - look up actual names from labs_context
            labs_context = getattr(request, "labs_context", {})
            opportunity = labs_context.get("opportunity", {})
            org_data = get_org_data(request)

            if body["assigned_to_type"] == "self":
                task.data["assigned_to_name"] = actor_name
            elif body["assigned_to_type"] == "network_manager":
                # Look up organization name by slug
                org_slug = opportunity.get("organization")
                assigned_name = "Network Manager"
                for org in org_data.get("organizations", []):
                    if org.get("slug") == org_slug:
                        assigned_name = org.get("name", "Network Manager")
                        break
                task.data["assigned_to_name"] = assigned_name
            elif body["assigned_to_type"] == "program_manager":
                # Look up program name by ID
                program_id = opportunity.get("program")
                assigned_name = "Program Manager"
                for prog in org_data.get("programs", []):
                    if prog.get("id") == program_id:
                        assigned_name = prog.get("name", "Program Manager")
                        break
                task.data["assigned_to_name"] = assigned_name
            changes.append(f"assignment to {task.data.get('assigned_to_name')}")

        if "resolution_details" in body:
            task.data["resolution_details"] = body["resolution_details"]
            if body.get("status") == "closed":
                changes.append("closed task")

        # Add event for the changes
        if changes:
            task.add_event(
                event_type="updated",
                actor=actor_name,
                description=f"Updated {', '.join(changes)}",
            )

        # Save via API
        data_access.save_task(task)
        data_access.close()

        return JsonResponse({"success": True, "message": "Task updated successfully"})

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error updating task %s", task_id)
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)


@login_required
@csrf_exempt
@require_POST
def task_add_comment(request, task_id):
    """Add a comment to a task."""
    try:
        body = json.loads(request.body)
        content = body.get("content", "").strip()

        if not content:
            return JsonResponse({"success": False, "error": "Comment content is required"}, status=400)

        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)

        if not task:
            return JsonResponse({"success": False, "error": "Task not found"}, status=404)

        # Add comment using the actor's display name
        actor_name = request.user.get_display_name()
        task.add_comment(actor=actor_name, content=content)

        # Save via API
        data_access.save_task(task)
        data_access.close()

        # Return the new comment event for the UI to display
        comment_events = task.get_comment_events()
        new_comment = comment_events[-1] if comment_events else None

        return JsonResponse(
            {
                "success": True,
                "message": "Comment added successfully",
                "comment": new_comment,
            }
        )

    except json.JSONDecodeError:
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error adding comment to task %s", task_id)
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)


# OCS Integration API Views


class OCSBotsListAPIView(LoginRequiredMixin, View):
    """List available OCS bots (experiments) via OAuth API."""

    def get(self, request):
        try:
            ocs_client = OCSDataAccess(request=request)

            if not ocs_client.check_token_valid():
                return JsonResponse(
                    {
                        "success": False,
                        "error": "OCS not connected. Please connect to Open Chat Studio first.",
                        "needs_oauth": True,
                    },
                    status=401,
                )

            experiments = ocs_client.list_experiments()
            ocs_client.close()

            # Format for frontend dropdown
            bots = [
                {
                    "id": exp.get("id"),
                    "name": exp.get("name"),
                    "version_number": exp.get("version_number"),
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


# AI Assistant Integration Views


@login_required
@csrf_exempt
@require_POST
def task_initiate_ai(request, task_id):
    """Initiate an AI assistant conversation for a task via OCS."""
    try:
        # Get the task using TaskDataAccess
        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)
        if not task:
            return JsonResponse({"error": "Task not found"}, status=404)
    except Exception:
        return JsonResponse({"error": "Task not found"}, status=404)

    try:
        # Parse request body
        body = json.loads(request.body)

        # Extract parameters from request
        identifier = body.get("identifier", "").strip()
        experiment = body.get("experiment", "").strip()
        platform = body.get("platform", "commcare_connect")
        prompt_text = body.get("prompt_text", "").strip()
        start_new_session = body.get("start_new_session", False)

        # Validate required fields
        if not identifier:
            return JsonResponse({"error": "Participant ID is required"}, status=400)
        if not experiment:
            return JsonResponse({"error": "Bot ID (experiment) is required"}, status=400)
        if not prompt_text:
            return JsonResponse({"error": "Prompt instructions are required"}, status=400)

        # Prepare session data to link back to Connect
        session_data = {
            "task_id": str(task.id),
            "opportunity_id": str(task.opportunity_id),
            "username": task.task_username,
            "created_by": request.user.username if hasattr(request.user, "username") else "unknown",
        }

        # Trigger bot with OCS using OAuth
        ocs_client = OCSDataAccess(request=request)
        result = ocs_client.trigger_bot(
            identifier=identifier,
            platform=platform,
            experiment_id=experiment,
            prompt_text=prompt_text,
            start_new_session=start_new_session,
            session_data=session_data,
        )
        ocs_client.close()

        # Log minimal diagnostics (avoid leaking participant data)
        logger.info(
            "trigger_bot response for task %s: status=%s keys=%s",
            task_id,
            result.get("status") if isinstance(result, dict) else type(result).__name__,
            list(result.keys()) if isinstance(result, dict) else None,
        )

        # Extract session_id from trigger_bot response
        session_id = None
        status = "pending"
        if isinstance(result, dict):
            session = result.get("session")
            session_id = (
                (session.get("id") if isinstance(session, dict) else None)
                or result.get("session_id")
                or result.get("id")
            )
            if session_id:
                session_id = str(session_id)
                status = "completed"
                logger.info(f"Session linked immediately from trigger_bot: {session_id}")
            else:
                logger.warning(f"trigger_bot response has no session_id. Keys: {list(result.keys())}")

        # Add AI session event with session_id if available from trigger_bot response
        actor_name = request.user.get_display_name()
        session_params = {
            "identifier": identifier,
            "experiment": experiment,
            "platform": platform,
            "prompt_text": prompt_text,
        }
        task.add_ai_session(
            actor=actor_name,
            session_params=session_params,
            session_id=session_id,
            status=status,
        )

        # Save task via data access
        data_access.save_task(task)
        data_access.close()

        return JsonResponse(
            {
                "success": True,
                "message": "AI conversation initiated.",
                "session_id": session_id,
            }
        )

    except OCSAPIError:
        logger.exception("OCS error when initiating AI for task %s", task_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON in request body"}, status=400)
    except Exception:
        logger.exception("Unexpected error when initiating AI for task %s", task_id)
        return JsonResponse({"error": "An internal error occurred"}, status=500)


@login_required
def task_ai_sessions(request, task_id):
    """Get AI session events and try to link session_id from OCS for pending sessions."""
    try:
        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)
        if not task:
            return JsonResponse({"error": "Task not found"}, status=404)
    except Exception:
        return JsonResponse({"error": "Task not found"}, status=404)

    # Get AI session events from the events array
    ai_sessions = task.get_ai_session_events()

    # Find pending session to try linking (most recent without session_id)
    pending_session = None
    for session in reversed(ai_sessions):
        if not session.get("session_id"):
            pending_session = session
            break

    logger.info(f"Task {task_id} has {len(ai_sessions)} AI sessions, pending_session={pending_session is not None}")

    # Try to fetch and populate session_id from OCS
    if pending_session:
        params = pending_session.get("session_params", {})
        experiment = params.get("experiment")
        identifier = params.get("identifier")
        logger.info(f"Attempting to link session for task {task_id}: experiment={experiment}, identifier={identifier}")

        if experiment and identifier:
            try:
                # Query OCS for recent sessions using OAuth
                ocs_client = OCSDataAccess(request=request)
                sessions = ocs_client.list_sessions(experiment_id=experiment, limit=5)
                ocs_client.close()
                logger.info(f"OCS returned {len(sessions) if sessions else 0} sessions: {sessions}")

                # Filter by identifier (case-insensitive)
                if sessions and identifier:
                    identifier_lower = identifier.lower()
                    filtered_sessions = [
                        s
                        for s in sessions
                        if s.get("participant", {}).get("identifier", "").lower() == identifier_lower
                    ]
                    logger.info(f"After filtering by identifier '{identifier}': {len(filtered_sessions)} sessions")
                    sessions = filtered_sessions

                if sessions:
                    # Get the most recent session - OCS returns 'id' as the session identifier
                    most_recent = sessions[0]
                    session_id = most_recent.get("id")
                    logger.info(f"Most recent session id={session_id}")

                    if session_id:
                        # Update the session event in the events array
                        pending_session["session_id"] = session_id
                        pending_session["status"] = "completed"
                        data_access.save_task(task)
                        logger.info(f"Auto-linked OCS session {session_id} to task {task_id}")
                else:
                    logger.info(f"No matching sessions found for identifier '{identifier}'")

            except OCSAPIError as e:
                logger.error(f"Error fetching OCS sessions: {e}")

    data_access.close()

    # Return the AI session events
    return JsonResponse({"success": True, "sessions": ai_sessions})


@login_required
@csrf_exempt
@require_POST
def task_add_ai_session(request, task_id):
    """Manually add OCS session ID to a task."""
    try:
        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)
        if not task:
            return JsonResponse({"error": "Task not found"}, status=404)
    except Exception:
        return JsonResponse({"error": "Task not found"}, status=404)

    session_id = request.POST.get("session_id", "").strip()

    if not session_id:
        return JsonResponse({"success": False, "error": "Session ID is required"}, status=400)

    # Check if we already have an AI session event without a session_id
    ai_sessions = task.get_ai_session_events()
    created = False

    if ai_sessions:
        # Update the most recent session event
        ai_sessions[-1]["session_id"] = session_id
        ai_sessions[-1]["status"] = "completed"
    else:
        # Create new AI session event
        actor_name = request.user.get_display_name()
        task.add_ai_session(
            actor=actor_name,
            session_params={},
            session_id=session_id,
            status="completed",
        )
        created = True

    # Save task via data access
    data_access.save_task(task)
    data_access.close()

    return JsonResponse({"success": True, "session_id": session_id, "created": created})


@login_required
def task_ai_transcript(request, task_id):
    """Fetch AI conversation transcript - from saved history or OCS."""
    try:
        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)
        if not task:
            data_access.close()
            return JsonResponse({"error": "Task not found"}, status=404)
    except Exception:
        return JsonResponse({"error": "Task not found"}, status=404)

    session_id = request.GET.get("session_id")
    force_refresh = request.GET.get("refresh") == "true"

    # Find the session event
    ai_sessions = task.get_ai_session_events()
    target_session = None

    if session_id:
        for session in ai_sessions:
            if session.get("session_id") == session_id:
                target_session = session
                break
    elif ai_sessions:
        target_session = ai_sessions[-1]
        session_id = target_session.get("session_id") if target_session else None

    if not session_id:
        data_access.close()
        return JsonResponse({"success": False, "error": "No session ID available yet"}, status=404)

    # Check for saved history first (unless force refresh)
    if not force_refresh and target_session and target_session.get("saved_transcript"):
        saved = target_session["saved_transcript"]
        data_access.close()
        return JsonResponse(
            {
                "success": True,
                "messages": saved.get("messages", []),
                "session_id": session_id,
                "from_saved": True,
                "saved_at": saved.get("saved_at"),
            }
        )

    # Fetch transcript from OCS using OAuth
    try:
        ocs_client = OCSDataAccess(request=request)
        session_data = ocs_client.get_session(session_id)
        ocs_client.close()

        if session_data and session_data.get("messages"):
            formatted_messages = []
            for msg in session_data["messages"]:
                formatted_messages.append(
                    {
                        "role": msg.get("role", "user"),
                        "content": msg.get("content", ""),
                        "created_at": msg.get("created_at", ""),
                    }
                )

            data_access.close()
            return JsonResponse(
                {
                    "success": True,
                    "messages": formatted_messages,
                    "session_id": session_id,
                    "from_saved": False,
                    "ended_at": session_data.get("ended_at"),
                }
            )
        else:
            data_access.close()
            return JsonResponse({"success": False, "error": "Invalid transcript format from OCS"}, status=500)

    except OCSAPIError:
        logger.exception("Error fetching transcript from OCS")
        data_access.close()
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)


@login_required
@csrf_exempt
@require_POST
def task_ai_save_transcript(request, task_id):
    """Save AI transcript to the task for offline access."""
    try:
        data_access = TaskDataAccess(user=request.user, request=request)
        task = data_access.get_task(task_id)
        if not task:
            return JsonResponse({"error": "Task not found"}, status=404)
    except Exception:
        return JsonResponse({"error": "Task not found"}, status=404)

    try:
        body = json.loads(request.body)
        session_id = body.get("session_id")
        messages = body.get("messages", [])

        if not session_id:
            data_access.close()
            return JsonResponse({"success": False, "error": "session_id is required"}, status=400)

        # Find and update the session event
        ai_sessions = task.get_ai_session_events()
        updated = False
        for session in ai_sessions:
            if session.get("session_id") == session_id:
                session["saved_transcript"] = {
                    "messages": messages,
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "saved_by": request.user.get_display_name(),
                }
                updated = True
                break

        if not updated:
            data_access.close()
            return JsonResponse({"success": False, "error": "Session not found"}, status=404)

        data_access.save_task(task)
        data_access.close()

        return JsonResponse({"success": True, "message": "Transcript saved"})

    except json.JSONDecodeError:
        data_access.close()
        return JsonResponse({"success": False, "error": "Invalid JSON"}, status=400)
    except Exception:
        logger.exception("Error saving transcript")
        data_access.close()
        return JsonResponse({"success": False, "error": "An internal error occurred"}, status=500)
