"""
API endpoint for listing FLWs with audit history enrichment.

Used by the MBW monitoring workflow template's render_code to show
per-FLW audit history during FLW selection.

Source: Extracted from audit/views.py OpportunityFLWListAPIView
"""

import json
import logging
from collections import defaultdict

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from commcare_connect.labs.analysis.data_access import fetch_flw_names

logger = logging.getLogger(__name__)


class OpportunityFLWListAPIView(LoginRequiredMixin, View):
    """API endpoint to list FLWs for an opportunity, enriched with audit history.

    Used by the monitoring workflow template's render_code to show
    per-FLW past assessment results and open task indicators.
    """

    def post(self, request):
        try:
            data = json.loads(request.body)
            opportunity_ids = data.get("opportunities", [])

            if not opportunity_ids:
                return JsonResponse({"error": "No opportunities provided"}, status=400)

            access_token = request.session.get("labs_oauth", {}).get("access_token")
            if not access_token:
                return JsonResponse({"error": "Not authenticated"}, status=401)

            # Fetch users for each opportunity and merge
            all_flws = []
            seen_usernames = set()

            for opp_id in opportunity_ids:
                try:
                    flw_names = fetch_flw_names(access_token, opp_id)
                    for username, display_name in flw_names.items():
                        if username not in seen_usernames:
                            seen_usernames.add(username)
                            all_flws.append(
                                {
                                    "username": username,
                                    "name": display_name,
                                    "connect_id": username,
                                    "opportunity_id": opp_id,
                                }
                            )
                except Exception as e:
                    logger.warning(f"Failed to fetch FLWs for opportunity {opp_id}: {e}")

            # Enrich with audit history and task indicators
            flw_history = self._build_flw_history(request)
            for flw in all_flws:
                flw["history"] = flw_history.get(flw["username"].lower(), flw_history.get(flw["username"], {}))

            return JsonResponse(
                {
                    "success": True,
                    "flws": all_flws,
                    "total": len(all_flws),
                }
            )

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"Failed to list opportunity FLWs: {e}", exc_info=True)
            return JsonResponse({"error": str(e)}, status=500)

    def _build_flw_history(self, request):
        """Build per-FLW audit history from both audit sessions and workflow runs.

        Returns dict: {username: {last_audit_date, last_audit_result, audit_count,
                                   open_task_count, latest_task_date, latest_task_title}}

        Reads from two sources:
        - Traditional audit sessions (via AuditDataAccess) — single FLW per session
        - Monitoring workflow runs (via WorkflowDataAccess) — per-FLW results in state.flw_results
        """
        history = defaultdict(
            lambda: {
                "last_audit_date": None,
                "last_audit_result": None,
                "audit_count": 0,
                "open_task_count": 0,
                "latest_task_id": None,
                "latest_task_date": None,
                "latest_task_title": None,
            }
        )

        # 1. Read traditional audit sessions
        try:
            from commcare_connect.audit.data_access import AuditDataAccess

            data_access = AuditDataAccess(request=request)
            all_sessions = data_access.get_audit_sessions()
            data_access.close()

            for session in all_sessions:
                # Traditional audit: single FLW per session
                username = session.flw_username
                if not username:
                    continue
                result = session.overall_result
                if not result:
                    continue
                h = history[username]
                h["audit_count"] += 1
                session_date = session.data.get("created_at") or session.data.get("start_date")
                if session_date:
                    if not h["last_audit_date"] or session_date > h["last_audit_date"]:
                        h["last_audit_date"] = session_date
                        h["last_audit_result"] = result.lower()
        except Exception as e:
            logger.warning(f"Failed to fetch audit history: {e}")

        # 2. Read monitoring results from workflow runs
        try:
            from commcare_connect.workflow.data_access import WorkflowDataAccess

            wf_access = WorkflowDataAccess(request=request)
            all_runs = wf_access.list_runs()
            wf_access.close()

            for run in all_runs:
                state = run.data.get("state", {})
                flw_results = state.get("worker_results", state.get("flw_results", {}))
                if not flw_results:
                    continue
                for username, result_data in flw_results.items():
                    assessed_at = result_data.get("assessed_at")
                    result = result_data.get("result")
                    if not result:
                        continue
                    h = history[username]
                    h["audit_count"] += 1
                    if not h["last_audit_date"] or (assessed_at and assessed_at > h["last_audit_date"]):
                        h["last_audit_date"] = assessed_at
                        h["last_audit_result"] = result
        except Exception as e:
            logger.warning(f"Failed to fetch workflow monitoring history: {e}")

        # 3. Fetch open tasks
        try:
            from commcare_connect.tasks.data_access import TaskDataAccess

            task_access = TaskDataAccess(request=request)
            all_tasks = task_access.get_tasks()
            task_access.close()

            for task in all_tasks:
                username = task.task_username
                if not username:
                    continue
                if task.status != "closed":
                    h = history[username]
                    h["open_task_count"] += 1
                    task_date = None
                    if task.date_created:
                        task_date = task.date_created.isoformat()
                    if task_date and (not h["latest_task_date"] or task_date > h["latest_task_date"]):
                        h["latest_task_id"] = task.id
                        h["latest_task_date"] = task_date
                        h["latest_task_title"] = task.title
        except Exception as e:
            logger.warning(f"Failed to fetch task history: {e}")

        return dict(history)
