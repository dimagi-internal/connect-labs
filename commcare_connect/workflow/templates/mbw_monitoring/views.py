"""
Views for MBW Monitoring Dashboard.

Three-tab dashboard (Overview, GPS Analysis, Follow-Up Rate) with SSE data loading,
client-side filtering, and interactive features.
"""

import copy
import gc
import json
import logging
import platform

try:
    import resource
except ImportError:
    resource = None
from collections import Counter
from collections.abc import Generator
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone

import sentry_sdk
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme, urlencode
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.labs.analysis.data_access import fetch_flw_names
from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
from commcare_connect.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, BaseSSEStreamView, send_sse_event
from commcare_connect.labs.integrations.commcare.api_client import CommCareDataAccess
from commcare_connect.workflow.data_access import WorkflowDataAccess
from commcare_connect.workflow.templates.mbw_monitoring.data_fetchers import (
    _get_cache_config,
    fetch_gs_forms,
    fetch_opportunity_metadata,
    fetch_registration_forms,
)
from commcare_connect.workflow.templates.mbw_monitoring.data_transforms import (
    build_gps_visit_dicts,
    compute_ebf_by_flw,
    extract_per_mother_fields,
)
from commcare_connect.workflow.templates.mbw_monitoring.followup_analysis import (
    aggregate_flw_followup,
    aggregate_mother_metrics,
    aggregate_visit_status_distribution,
    build_followup_from_pipeline,
    compute_flw_performance_by_status,
    compute_overview_quality_metrics,
    count_mothers_from_pipeline,
    extract_mother_metadata_from_forms,
)
from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import (
    analyze_gps_metrics,
    build_result_from_analyzed_visits,
    compute_median_meters_per_visit,
    compute_median_minutes_per_visit,
)
from commcare_connect.workflow.templates.mbw_monitoring.pipeline_config import MBW_GPS_PIPELINE_CONFIG
from commcare_connect.workflow.templates.mbw_monitoring.serializers import (
    filter_visits_by_date,
    serialize_flw_summary,
    serialize_visit,
)
from commcare_connect.workflow.templates.mbw_monitoring.session_adapter import (
    VALID_FLW_RESULTS,
    complete_monitoring_run,
    load_monitoring_run,
    save_dashboard_snapshot,
)
from commcare_connect.workflow.templates.mbw_monitoring.session_adapter import (
    save_flw_result as save_flw_result_helper,
)

logger = logging.getLogger(__name__)

VALID_STATUS_FILTER_VALUES = frozenset({"approved", "pending", "rejected", "over_limit"})


def _parse_status_filter(raw: str | None) -> tuple[list[str], str | None]:
    """Parse and validate a comma-separated status filter string.

    Returns (valid_statuses, error_message).
    error_message is None on success, non-None when raw was provided but
    contained any invalid tokens.  Rejects the entire input if any single
    token is unrecognised (after strip + lowercase).
    """
    if not raw:
        return [], None
    tokens = [s.strip().lower() for s in raw.split(",") if s.strip()]
    invalid = [t for t in tokens if t not in VALID_STATUS_FILTER_VALUES]
    if invalid:
        return [], f"Invalid status_filter values: {', '.join(invalid)}"
    if not tokens:
        return [], "Invalid status_filter values"
    return tokens, None


def _log_rss(label: str) -> None:
    """Log current RSS (max resident set size) for memory diagnostics."""
    if resource is None:
        return
    rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS returns bytes, Linux returns kilobytes
    rss_mb = rss_kb / (1024 * 1024) if platform.system() == "Darwin" else rss_kb / 1024
    logger.info("[MBW Dashboard] RSS at %s: %.1f MB", label, rss_mb)


def _check_app_version(version, op: str, val: int) -> bool:
    """Check if a visit's app_build_version satisfies the operator comparison."""
    if version is None:
        return False
    try:
        version = int(version)
    except (ValueError, TypeError):
        return False
    if op == "gt":
        return version > val
    if op == "gte":
        return version >= val
    if op == "eq":
        return version == val
    if op == "lte":
        return version <= val
    if op == "lt":
        return version < val
    return False


def _parse_int_param(value: str | None) -> int | None:
    """Safely parse a query parameter to int, returning None if invalid."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def get_default_date_range() -> tuple[date, date]:
    """Get default date range (last 30 days)."""
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    return start_date, end_date


def parse_date_param(date_str: str | None, default: date) -> date:
    """Parse date from query param or return default."""
    if not date_str:
        return default
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return default


def _get_latest_flw_statuses(
    request,
    active_usernames: set[str],
) -> dict[str, str]:
    """Get the latest known assessment status for each FLW.

    Checks two sources (same logic as flw_api._build_flw_history):
    1. Traditional audit sessions (AuditDataAccess)
    2. All workflow monitoring runs (flw_results in run state)

    Returns dict mapping username (lowercase) → status key.
    FLWs in active_usernames with no assessment get "none".
    """
    # Track latest per FLW: {username: (date_str, result)}
    latest: dict[str, tuple[str, str]] = {}

    def _update(uname: str, date_str: str, result: str):
        uname = uname.lower()
        prev = latest.get(uname)
        if prev is None or (date_str and date_str > prev[0]):
            latest[uname] = (date_str or "", result)

    # 1. Traditional audit sessions
    try:
        from commcare_connect.audit.data_access import AuditDataAccess

        audit_access = AuditDataAccess(request=request)
        try:
            for session in audit_access.get_audit_sessions():
                username = session.flw_username
                result = session.overall_result
                if not username or not result:
                    continue
                session_date = session.data.get("created_at") or session.data.get("start_date") or ""
                _update(username, session_date, result.lower())
        finally:
            audit_access.close()
    except Exception as e:
        logger.warning("[MBW Dashboard] Failed to fetch audit sessions: %s", e)

    # 2. All workflow monitoring runs (including in-progress)
    try:
        wf_access = WorkflowDataAccess(request=request)
        try:
            for run in wf_access.list_runs():
                state = run.data.get("state", {})
                flw_results = state.get("worker_results", state.get("flw_results", {}))
                for username, result_data in flw_results.items():
                    if not isinstance(result_data, dict):
                        continue
                    result = result_data.get("result")
                    if not result:
                        continue
                    _update(username, result_data.get("assessed_at", ""), result)
        finally:
            wf_access.close()
    except Exception as e:
        logger.warning("[MBW Dashboard] Failed to fetch workflow runs: %s", e)

    # Build final mapping: all active usernames get a status
    return {username: latest[username][1] if username in latest else "none" for username in active_usernames}


class MBWMonitoringDashboardView(LoginRequiredMixin, TemplateView):
    """
    Main dashboard view rendering the three-tab interface.

    Supports direct URL access to specific tabs via URL path or query param.
    When ?session_id=X is provided, scopes the dashboard to a monitoring session.
    """

    template_name = "custom_analysis/mbw_monitoring/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        # Check for monitoring session (accept both run_id and session_id for backward compat)
        run_id = _parse_int_param(self.request.GET.get("run_id") or self.request.GET.get("session_id"))
        monitoring_session = None
        if run_id:
            monitoring_session = load_monitoring_run(self.request, run_id)
            if monitoring_session:
                session_opp_id = monitoring_session.opportunity_id
                if session_opp_id:
                    opportunity_id = session_opp_id

        context["opportunity_id"] = opportunity_id
        context["opportunity_name"] = labs_context.get("opportunity_name", "")
        context["has_context"] = bool(opportunity_id)
        context["session_id"] = run_id or ""  # template uses session_id for display
        context["monitoring_session_json"] = json.dumps(
            monitoring_session.to_summary_dict() if monitoring_session else None
        )

        if not opportunity_id:
            context["error"] = "No opportunity selected. Please select an opportunity from the labs context."
            return context

        # Date range defaults
        default_start, default_end = get_default_date_range()
        start_date = parse_date_param(self.request.GET.get("start_date"), default_start)
        end_date = parse_date_param(self.request.GET.get("end_date"), default_end)

        context["start_date"] = start_date.isoformat()
        context["end_date"] = end_date.isoformat()

        # Active tab (from URL kwargs or query param)
        default_tab = kwargs.get("default_tab") or self.request.GET.get("tab", "overview")
        context["active_tab"] = default_tab

        # API URLs
        context["stream_api_url"] = reverse("mbw:stream")
        context["gps_detail_api_url"] = reverse("mbw:gps_detail", kwargs={"username": "__USERNAME__"})
        context["suspend_api_url"] = reverse("mbw:suspend_user")
        context["task_create_api_url"] = reverse("tasks:single_create")

        # OAuth status — check token presence AND expiry
        labs_oauth = self.request.session.get("labs_oauth", {})
        context["has_oauth"] = bool(labs_oauth.get("access_token"))

        commcare_oauth = self.request.session.get("commcare_oauth", {})
        commcare_expires_at = commcare_oauth.get("expires_at", 0)
        commcare_oauth_active = bool(
            commcare_oauth.get("access_token") and timezone.now().timestamp() < commcare_expires_at
        )
        context["commcare_oauth_active"] = commcare_oauth_active

        # Build CommCare authorize URL with ?next= pointing back here
        current_path = self.request.get_full_path()
        commcare_initiate_url = reverse("labs:commcare_initiate") + "?" + urlencode({"next": current_path})
        context["commcare_authorize_url"] = commcare_initiate_url

        # OCS OAuth status
        ocs_oauth = self.request.session.get("ocs_oauth", {})
        ocs_expires_at = ocs_oauth.get("expires_at", 0)
        context["ocs_oauth_active"] = bool(
            ocs_oauth.get("access_token") and timezone.now().timestamp() < ocs_expires_at
        )
        context["ocs_authorize_url"] = reverse("labs:ocs_initiate") + "?" + urlencode({"next": current_path})

        # API URLs for AI task flow
        context["ocs_bots_api_url"] = reverse("tasks:ocs_bots")
        context["ai_initiate_url_template"] = "/tasks/__TASK_ID__/ai/initiate/"

        # Session API URLs (for save/complete)
        context["save_flw_result_url"] = reverse("mbw:save_flw_result")
        context["complete_session_url"] = reverse("mbw:complete_session")

        # Dev fixture mode: show bust cache button
        context["dev_fixture"] = getattr(settings, "MBW_DEV_FIXTURE", False)

        # Cache tolerance defaults (passed to template for SSE URL construction)
        cache_config = _get_cache_config()
        context["default_cache_tolerance_pct"] = cache_config["cache_tolerance_pct"]
        context["default_cache_tolerance"] = cache_config["cache_tolerance_minutes"]

        return context


def _ensure_cchq_oauth(request, timeout=300):
    """
    Ensure CCHQ OAuth is valid. Try auto-refresh first, then poll for re-auth.

    This is a generator — caller must use ``yield from _ensure_cchq_oauth(request)``.
    Yields SSE events only if user intervention is needed. After returning, the
    caller should re-check ``request.session.get("commcare_oauth")`` to see
    whether re-auth succeeded or timed out.

    Only waits once per request — if a previous call already timed out, subsequent
    calls return immediately to avoid compounding stalls.
    """
    # Short-circuit if a previous call in this request already timed out
    if getattr(request, "_cchq_oauth_unavailable", False):
        return

    import time
    from importlib import import_module

    # Phase 1: Try auto-refresh (silent — user never notices)
    commcare_access = CommCareDataAccess(request=request, domain="")
    if commcare_access.check_token_valid():
        return

    logger.info("[MBW Dashboard] CCHQ OAuth expired and refresh failed — requesting re-auth")

    # Phase 2: Build authorize URL
    referer = request.headers.get("Referer", "")
    if referer and "://" in referer:
        after_scheme = referer.split("://", 1)[1]
        slash_pos = after_scheme.find("/")
        next_page = after_scheme[slash_pos:] if slash_pos >= 0 else "/labs/overview/"
    else:
        next_page = referer if referer.startswith("/") else "/labs/overview/"
    if not next_page or not next_page.startswith("/") or next_page.startswith("//"):
        next_page = "/labs/overview/"
    authorize_url = reverse("labs:commcare_initiate") + "?" + urlencode({"next": next_page})

    # Send auth_required event (frontend shows modal, keeps EventSource open)
    event = {
        "message": ("CommCare authorization expired. " "Please re-authorize in a new tab."),
        "complete": False,
        "auth_required": True,
        "authorize_url": authorize_url,
    }
    yield f"data: {json.dumps(event)}\n\n"

    # Poll session DB for re-auth (heartbeat wrapper keeps SSE alive)
    engine = import_module(settings.SESSION_ENGINE)
    deadline = time.time() + timeout

    while time.time() < deadline:
        time.sleep(10)
        # Re-read session from DB (bypass in-memory cache)
        fresh_session = engine.SessionStore(session_key=request.session.session_key)
        fresh_oauth = fresh_session.get("commcare_oauth", {})
        if fresh_oauth.get("access_token") and timezone.now().timestamp() < fresh_oauth.get("expires_at", 0):
            # User re-authenticated — update in-memory session and resume
            request.session["commcare_oauth"] = fresh_oauth
            logger.info("[MBW Dashboard] CCHQ OAuth re-authenticated, resuming stream")
            yield send_sse_event("CommCare re-authorized! Resuming data load...")
            return

    # Timeout — mark so subsequent calls in this request skip the wait
    request._cchq_oauth_unavailable = True
    logger.warning("[MBW Dashboard] CCHQ re-auth timeout after %ds", timeout)
    yield send_sse_event(
        "Authorization timeout \u2014 continuing without CommCare HQ data. "
        "Re-authorize and click 'Refresh Data' to load complete results."
    )


class MBWMonitoringStreamView(AnalysisPipelineSSEMixin, BaseSSEStreamView):
    """
    SSE streaming endpoint that loads ALL dashboard data in one connection.

    Fetches GPS data, visit cases, mother cases, and computes all metrics.
    Frontend receives one combined payload for all three tabs.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream all dashboard data via SSE."""
        try:
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id")

            if not opportunity_id:
                yield send_sse_event("Error", error="No opportunity selected")
                return

            labs_oauth = request.session.get("labs_oauth", {})
            access_token = labs_oauth.get("access_token")
            if not access_token:
                yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                return

            # Ensure CCHQ OAuth before starting (auto-refresh or pause for re-auth).
            # Unlike the old blocking check, this lets the pipeline proceed with
            # Connect data even if CCHQ auth needs user intervention later.
            yield from _ensure_cchq_oauth(request)

            # Parse date range (for GPS filtering only)
            default_start, default_end = get_default_date_range()
            start_date = parse_date_param(request.GET.get("start_date"), default_start)
            end_date = parse_date_param(request.GET.get("end_date"), default_end)

            # App version filter (for GPS data only)
            app_version_op = request.GET.get("app_version_op", "")
            if app_version_op and app_version_op not in ("gt", "gte", "eq", "lte", "lt"):
                app_version_op = ""
            app_version_val = _parse_int_param(request.GET.get("app_version_val"))

            # Visit approval status filter (pipeline-level, affects all tabs)
            status_filter, status_err = _parse_status_filter(request.GET.get("status_filter"))
            if status_err:
                yield send_sse_event("Error", error=status_err)
                return

            # Bust cache: when MBW_DEV_FIXTURE is on and ?bust_cache=1 is passed
            bust_cache = request.GET.get("bust_cache") == "1"
            if bust_cache:
                yield send_sse_event("Cache busted — re-fetching all data...")

            # Load monitoring session early so we know which FLWs to filter to
            session_id = _parse_int_param(request.GET.get("run_id") or request.GET.get("session_id"))
            monitoring_session = None
            session_flw_filter = None
            if session_id:
                monitoring_session = load_monitoring_run(request, session_id)
                if monitoring_session:
                    session_flw_filter = {u.lower() for u in monitoring_session.selected_flw_usernames}
                    logger.info(
                        f"[MBW Dashboard] Monitoring session {session_id}: "
                        f"filtering to {len(session_flw_filter)} FLWs"
                    )

            # Determine which opportunities to load data from.
            # Workflow-based monitoring is single-opportunity.
            if monitoring_session:
                opportunity_ids = [monitoring_session.opportunity_id or opportunity_id]
            else:
                opportunity_ids = [opportunity_id]

            # Build pipeline config, injecting status filter if provided.
            # deepcopy to avoid mutating the module-level singleton.
            if status_filter:
                pipeline_config = copy.deepcopy(MBW_GPS_PIPELINE_CONFIG)
                pipeline_config.filters["status"] = status_filter
                logger.info(f"[MBW Dashboard] Pipeline status filter: {status_filter}")
            else:
                pipeline_config = MBW_GPS_PIPELINE_CONFIG

            # Step 1: Fetch GPS visit forms via pipeline (per opportunity, merge rows)
            all_pipeline_rows = []
            from_cache = False
            for i, opp_id in enumerate(opportunity_ids):
                if len(opportunity_ids) > 1:
                    yield send_sse_event(f"Loading visits from opportunity {i + 1}/{len(opportunity_ids)}...")
                else:
                    yield send_sse_event("Loading visit forms from Connect...")

                pipeline = AnalysisPipeline(request)
                pipeline_stream = pipeline.stream_analysis(pipeline_config, opportunity_id=opp_id)
                yield from self.stream_pipeline_events(pipeline_stream)

                if self._pipeline_result:
                    all_pipeline_rows.extend(self._pipeline_result.rows)
                    from_cache = from_cache or self._pipeline_from_cache

            # Use last pipeline result as a container, replace rows with merged set
            pipeline_result = self._pipeline_result
            if pipeline_result:
                pipeline_result.rows = all_pipeline_rows

            if not pipeline_result or not all_pipeline_rows:
                yield send_sse_event("Error", error="No data returned from Connect API")
                return

            total_rows = len(pipeline_result.rows)
            logger.info(
                f"[MBW Dashboard] Pipeline returned {total_rows} visits across {len(opportunity_ids)} opportunities"
            )
            _log_rss(f"after pipeline download ({total_rows} visits)")

            # Step 2: Get active Connect users and FLW names (per opportunity, merge)
            yield send_sse_event("Loading FLW data...")
            active_usernames = set()
            flw_names = {}
            flw_last_active = {}
            for opp_id in opportunity_ids:
                try:
                    opp_flw_names = fetch_flw_names(access_token, opp_id, last_active_out=flw_last_active)
                    flw_names.update(opp_flw_names)
                    active_usernames.update(opp_flw_names.keys())
                except Exception as e:
                    logger.warning(f"[MBW Dashboard] Failed to fetch FLW names for opp {opp_id}: {e}")
            logger.info(
                f"[MBW Dashboard] Fetched {len(active_usernames)} FLW usernames "
                f"across {len(opportunity_ids)} opportunities"
            )

            # Normalize usernames to lowercase for case-insensitive comparison.
            # CCHQ lowercases usernames while Connect may preserve original casing.
            active_usernames = {u.lower() for u in active_usernames}
            flw_names = {k.lower(): v for k, v in flw_names.items()}
            flw_last_active = {k.lower(): v for k, v in flw_last_active.items()}

            # Scope to monitoring session FLWs if applicable
            if session_flw_filter:
                intersection = active_usernames & session_flw_filter
                logger.info(
                    f"[MBW Dashboard] Monitoring session {session_id}: "
                    f"active_usernames={len(active_usernames)}, "
                    f"session_flw_filter={len(session_flw_filter)}, "
                    f"intersection={len(intersection)}"
                )
                if not intersection and session_flw_filter:
                    # Fallback: if the intersection is empty (e.g., FLW names fetch failed
                    # or returned a different set), use the session's FLWs directly.
                    logger.warning(
                        f"[MBW Dashboard] Empty intersection — falling back to session FLWs. "
                        f"session_flw_filter sample: {list(session_flw_filter)[:3]}, "
                        f"active_usernames sample: {list(active_usernames)[:3]}"
                    )
                    active_usernames = session_flw_filter
                else:
                    active_usernames = intersection

            # Step 2b: Fetch GS forms from CCHQ (with mid-stream re-auth support)
            gs_forms = []
            gs_app_id = monitoring_session.gs_app_id if monitoring_session else None

            # Ensure CCHQ OAuth is valid (auto-refresh → poll for re-auth)
            yield from _ensure_cchq_oauth(request)
            cchq_oauth = request.session.get("commcare_oauth", {})
            cchq_oauth_valid = bool(
                cchq_oauth.get("access_token") and timezone.now().timestamp() < cchq_oauth.get("expires_at", 0)
            )

            if cchq_oauth_valid:
                for opp_id in opportunity_ids:
                    try:
                        yield send_sse_event("Fetching GS forms... (metadata)")
                        metadata = fetch_opportunity_metadata(access_token, opp_id)
                        cc_domain = metadata.get("cc_domain")
                        cc_app_id = metadata.get("cc_app_id")
                        if cc_domain:
                            yield send_sse_event("Fetching GS forms... (forms)")
                            forms = fetch_gs_forms(
                                request,
                                cc_domain,
                                cc_app_id=cc_app_id,
                                gs_app_id=gs_app_id,
                                bust_cache=bust_cache,
                                opportunity_id=opp_id,
                            )
                            gs_forms.extend(forms)
                            yield send_sse_event(f"Fetching GS forms... ({len(gs_forms)} forms)")
                    except Exception as e:
                        logger.warning(f"[MBW Dashboard] GS form fetch failed for opp {opp_id}: {e}")
            else:
                logger.info("[MBW Dashboard] Skipping GS fetch — CCHQ OAuth not available after re-auth attempt")
            logger.info(f"[MBW Dashboard] Fetched {len(gs_forms)} GS forms from CCHQ")

            # Step 3: GPS analysis (on ALL visits, then filter by date)
            _log_rss("before GPS analysis")
            yield send_sse_event("Analyzing GPS data...")

            visits_for_gps = build_gps_visit_dicts(pipeline_result.rows, active_usernames)

            # Apply app version filter to GPS visits if configured
            if app_version_op and app_version_val is not None:
                pre_filter_count = len(visits_for_gps)
                visits_for_gps = [
                    v
                    for v in visits_for_gps
                    if _check_app_version(v["computed"].get("app_build_version"), app_version_op, app_version_val)
                ]
                logger.info(
                    "[MBW Dashboard] App version filter (%s %d): %d -> %d GPS visits",
                    app_version_op,
                    app_version_val,
                    pre_filter_count,
                    len(visits_for_gps),
                )

            gps_result = analyze_gps_metrics(visits_for_gps, flw_names)
            del visits_for_gps  # Free GPS visit dicts (~1-2 MB)

            # Filter GPS by date range
            filtered_gps_visits = filter_visits_by_date(gps_result.visits, start_date, end_date)
            gps_result = build_result_from_analyzed_visits(filtered_gps_visits, flw_names)

            # GPS visits are NOT embedded in the SSE response to save ~30 MB of memory.
            # The frontend fetches per-FLW visits on demand via MBWGPSDetailView API.
            # Snapshots include visits (re-computed server-side during save).
            del filtered_gps_visits

            gps_data = {
                "total_visits": gps_result.total_visits,
                "total_flagged": gps_result.total_flagged,
                "date_range_start": start_date.isoformat(),
                "date_range_end": end_date.isoformat(),
                "flw_summaries": [serialize_flw_summary(flw) for flw in gps_result.flw_summaries],
            }

            # Step 4: Fetch registration forms from CCHQ
            yield send_sse_event("Fetching registration data...")
            followup_data = None
            overview_data = None
            visit_status_distribution = None
            registration_forms = []

            # Re-check CCHQ OAuth (may have expired during GPS analysis)
            yield from _ensure_cchq_oauth(request)
            cchq_oauth_valid = bool(
                request.session.get("commcare_oauth", {}).get("access_token")
                and timezone.now().timestamp() < request.session.get("commcare_oauth", {}).get("expires_at", 0)
            )

            if not cchq_oauth_valid:
                logger.warning("[MBW Dashboard] CommCare OAuth not available after re-auth attempt")
                yield send_sse_event("Fetching registration data... skipped " "(CommCare authorization not available)")
            else:
                for opp_id in opportunity_ids:
                    try:
                        yield send_sse_event("Fetching registration data... (metadata)")
                        metadata = fetch_opportunity_metadata(access_token, opp_id)
                        cc_domain = metadata.get("cc_domain")
                        cc_app_id = metadata.get("cc_app_id")
                        if cc_domain:
                            yield send_sse_event("Fetching registration data... (forms)")
                            forms = fetch_registration_forms(
                                request,
                                cc_domain,
                                cc_app_id=cc_app_id,
                                bust_cache=bust_cache,
                                opportunity_id=opp_id,
                            )
                            registration_forms.extend(forms)
                            yield send_sse_event(f"Fetching registration data... ({len(registration_forms)} forms)")
                    except Exception as e:
                        logger.warning(f"[MBW Dashboard] Registration form fetch failed for opp {opp_id}: {e}")
                logger.info(f"[MBW Dashboard] Fetched {len(registration_forms)} registration forms")

                # GS forms already fetched in step 2b (before GPS analysis)

            # Step 5: Build follow-up data from registration forms + pipeline completions
            yield send_sse_event("Calculating follow-up metrics...")

            visit_cases_by_flw = build_followup_from_pipeline(
                all_pipeline_rows, active_usernames, registration_forms=registration_forms
            )

            current_date = date.today()

            # Extract mother metadata FIRST (needed by both flw_followup and drilldown)
            mother_metadata = extract_mother_metadata_from_forms(registration_forms, current_date=current_date)

            flw_followup = aggregate_flw_followup(
                visit_cases_by_flw, current_date, flw_names, mother_cases_map=mother_metadata
            )
            visit_status_distribution = aggregate_visit_status_distribution(visit_cases_by_flw, current_date)

            # Extract per-mother fields from pipeline rows (needed by drilldown + quality metrics)
            per_mother = extract_per_mother_fields(all_pipeline_rows)
            parity_by_mother = per_mother["parity_by_mother"]
            anc_date_by_mother = per_mother["anc_date_by_mother"]
            pnc_date_by_mother = per_mother["pnc_date_by_mother"]
            baby_dob_by_mother = per_mother["baby_dob_by_mother"]

            # Compute % EBF (exclusive breastfeeding) per FLW from pipeline rows
            ebf_pct_by_flw = compute_ebf_by_flw(all_pipeline_rows)

            logger.info(
                "[MBW Dashboard] Pipeline extraction: parity=%d, anc_date=%d, pnc_date=%d, baby_dob=%d mothers, ebf=%d FLWs",
                len(parity_by_mother),
                len(anc_date_by_mother),
                len(pnc_date_by_mother),
                len(baby_dob_by_mother),
                len(ebf_pct_by_flw),
            )

            # Log form name distribution for debugging

            form_name_counts = Counter(row.computed.get("form_name", "").strip() for row in all_pipeline_rows)
            logger.info("[MBW Dashboard] Form name distribution: %s", dict(form_name_counts))

            flw_drilldown = {}
            for flw_username, flw_cases in visit_cases_by_flw.items():
                flw_drilldown[flw_username] = aggregate_mother_metrics(
                    flw_cases,
                    current_date,
                    mother_cases_map=mother_metadata,
                    anc_date_by_mother=anc_date_by_mother,
                    pnc_date_by_mother=pnc_date_by_mother,
                    baby_dob_by_mother=baby_dob_by_mother,
                )

            followup_data = {
                "flw_summaries": flw_followup,
                "total_cases": sum(len(v) for v in visit_cases_by_flw.values()),
                "flw_drilldown": flw_drilldown,
            }

            # Extract first (oldest) GS score per FLW from CCHQ forms
            gs_scores_by_flw: dict[str, list[tuple[str, str]]] = {}
            for form_dict in gs_forms:
                form = form_dict.get("form", {})
                connect_id = (form.get("load_flw_connect_id", "") or "").lower()
                score = form.get("checklist_percentage", "")
                time_end = form.get("meta", {}).get("timeEnd", "")
                if connect_id and score:
                    gs_scores_by_flw.setdefault(connect_id, []).append((time_end, score))

            first_gs_by_flw = {}
            for connect_id, scores in gs_scores_by_flw.items():
                scores.sort(key=lambda x: x[0])  # oldest first
                first_gs_by_flw[connect_id] = scores[0][1]

            logger.info(
                "[MBW Dashboard] GS scores from CCHQ: %d forms found, %d unique FLWs. "
                "Sample connect_ids: %s, Sample usernames: %s",
                sum(len(v) for v in gs_scores_by_flw.values()),
                len(gs_scores_by_flw),
                list(gs_scores_by_flw.keys())[:3],
                list(active_usernames)[:3],
            )
            del gs_forms, gs_scores_by_flw  # Free raw GS form dicts

            # Compute quality/fraud overview metrics
            quality_metrics = compute_overview_quality_metrics(
                visit_cases_by_flw,
                mother_metadata,
                parity_by_mother,
                anc_date_by_mother=anc_date_by_mother,
                pnc_date_by_mother=pnc_date_by_mother,
            )

            # Step 6: Build overview metrics
            yield send_sse_event("Building overview...")

            mother_counts = count_mothers_from_pipeline(
                all_pipeline_rows, active_usernames, registration_forms=registration_forms
            )

            # Free pipeline rows and registration forms — no longer needed.
            del all_pipeline_rows
            del registration_forms  # Free ~24k raw CCHQ form dicts (biggest win)
            if pipeline_result:
                pipeline_result.rows = []

            # Build GPS median distances per FLW (revisit distance)
            gps_median_by_flw = {}
            gps_revisit_cases_by_flw = {}
            for flw in gps_result.flw_summaries:
                if flw.avg_case_distance_km is not None:
                    gps_median_by_flw[flw.username] = round(flw.avg_case_distance_km, 2)
                gps_revisit_cases_by_flw[flw.username] = flw.cases_with_revisits

            # Compute median meters/visit and minutes/visit from GPS visits
            meters_per_visit_by_flw = compute_median_meters_per_visit(gps_result.visits)
            minutes_per_visit_by_flw = compute_median_minutes_per_visit(gps_result.visits)

            # Merge meter/visit and cases_with_revisits into GPS FLW summaries for GPS tab
            for flw_summary in gps_data["flw_summaries"]:
                flw_summary["median_meters_per_visit"] = meters_per_visit_by_flw.get(flw_summary["username"])

            # Extract lightweight coordinates for aggregate GPS map
            all_coordinates = []
            for v in gps_result.visits:
                if v.gps:
                    all_coordinates.append(
                        {
                            "lat": round(v.gps.latitude, 5),
                            "lng": round(v.gps.longitude, 5),
                            "u": v.username,
                            "f": v.is_flagged,
                            "d": v.visit_date.isoformat() if v.visit_date else None,
                            "e": v.entity_name,
                            "m": v.mother_case_id or v.case_id,
                        }
                    )
            gps_data["all_coordinates"] = all_coordinates

            del gps_result  # Free GPSAnalysisResult with ~60k VisitWithGPS objects

            # Build completed visits and followup rate from follow-up data
            completed_by_flw = {}
            followup_rate_by_flw = {}
            for flw_summary in flw_followup:
                completed_by_flw[flw_summary["username"]] = flw_summary["completed_total"]
                followup_rate_by_flw[flw_summary["username"]] = flw_summary["completion_rate"]

            # Build eligible mothers count per FLW
            eligible_mothers_by_flw = {}
            for flw_username, flw_cases in visit_cases_by_flw.items():
                mother_ids = {
                    c.get("properties", {}).get("mother_case_id", "")
                    for c in flw_cases
                    if c.get("properties", {}).get("mother_case_id")
                }
                eligible_count = sum(
                    1
                    for mid in mother_ids
                    if mother_metadata.get(mid, {}).get("properties", {}).get("eligible_full_intervention_bonus")
                    == "1"
                )
                eligible_mothers_by_flw[flw_username] = eligible_count
            del mother_metadata  # Free ~15k mother metadata dicts

            # Compute "cases still eligible" per FLW from drill-down data
            # Among eligible mothers, count those still on track (5+ completed OR <= 1 missed)
            cases_eligible_by_flw = {}
            for flw_username, mothers in flw_drilldown.items():
                eligible_mothers = [m for m in mothers if m.get("eligible")]
                still_on_track = 0
                for m in eligible_mothers:
                    completed_count = sum(1 for v in m["visits"] if v["status"].startswith("Completed"))
                    missed_count = sum(1 for v in m["visits"] if v["status"] == "Missed")
                    if completed_count >= 5 or missed_count <= 1:
                        still_on_track += 1
                total_eligible = len(eligible_mothers)
                cases_eligible_by_flw[flw_username] = {
                    "eligible": still_on_track,
                    "total": total_eligible,
                    "pct": round(still_on_track / total_eligible * 100) if total_eligible > 0 else 0,
                }
            del visit_cases_by_flw  # Free grouped visit cases

            overview_flws = []
            now_utc = datetime.now(dt_timezone.utc)
            for username in sorted(active_usernames):
                display_name = flw_names.get(username, username)
                la_str = flw_last_active.get(username)
                last_active_days = None
                last_active_date = None
                if la_str:
                    try:
                        la_dt = datetime.fromisoformat(la_str.replace("Z", "+00:00"))
                        last_active_days = max(0, (now_utc - la_dt).days)
                        last_active_date = la_dt.strftime("%Y-%m-%d %H:%M")
                    except (ValueError, TypeError):
                        pass
                overview_flws.append(
                    {
                        "username": username,
                        "display_name": display_name,
                        "last_active_days": last_active_days,
                        "last_active_date": last_active_date,
                        "cases_registered": mother_counts.get(username, 0),
                        "eligible_mothers": eligible_mothers_by_flw.get(username, 0),
                        "first_gs_score": first_gs_by_flw.get(username),
                        "post_test_attempts": None,  # TBD
                        "followup_rate": followup_rate_by_flw.get(username, 0),
                        "ebf_pct": ebf_pct_by_flw.get(username),
                        "revisit_distance_km": gps_median_by_flw.get(username),
                        "cases_with_revisits": gps_revisit_cases_by_flw.get(username, 0),
                        "median_meters_per_visit": meters_per_visit_by_flw.get(username),
                        "median_minutes_per_visit": minutes_per_visit_by_flw.get(username),
                        **quality_metrics.get(username, {}),
                        "cases_still_eligible": cases_eligible_by_flw.get(
                            username, {"eligible": 0, "total": 0, "pct": 0}
                        ),
                    }
                )

            overview_data = {
                "flw_summaries": overview_flws,
                "visit_status_distribution": visit_status_distribution,
            }

            # Force garbage collection to reclaim memory from freed structures
            # (registration_forms, gps_result, mother_metadata, visit_cases_by_flw, etc.)
            gc.collect()
            _log_rss("after gc.collect (before FLW performance)")

            # Step 7: FLW Performance by assessment status
            yield send_sse_event("Computing FLW performance metrics...")
            flw_statuses = _get_latest_flw_statuses(request, active_usernames)
            performance_data = compute_flw_performance_by_status(flw_statuses, flw_drilldown, current_date)

            # Fetch open tasks so the frontend can grey out the Task button and
            # provide inline task management (task_id needed for detail/update APIs)
            yield send_sse_event("Fetching tasks...")
            open_tasks = {}
            task_data_access = None
            try:
                from commcare_connect.tasks.data_access import TaskDataAccess

                task_data_access = TaskDataAccess(user=request.user, request=request)
                all_tasks = task_data_access.get_tasks()
                closed_statuses = {"closed", "resolved"}
                for t in all_tasks:
                    if t.username and t.status not in closed_statuses:
                        open_tasks[t.username.lower()] = {
                            "task_id": t.id,
                            "status": t.status,
                            "title": t.title,
                        }
            except Exception as e:
                logger.warning(f"[MBW Dashboard] Failed to fetch tasks: {e}")
            finally:
                if task_data_access:
                    task_data_access.close()
            open_task_usernames = sorted(open_tasks.keys())

            # ---- Sectioned SSE streaming ----
            # Send data in sections to avoid serializing the full payload at once.
            # Each section is JSON-serialized independently; the frontend accumulates
            # them in sseSectionsRef and merges on the final "Complete!" event.
            #
            # Snapshot is NOT saved inline — it would spike memory ~75 MB (slim_followup
            # copy + json.dumps). Instead, the frontend saves via a separate POST to
            # /api/save-snapshot/ (manual button or auto-save on Complete).

            # Section 1: GPS data (small — send first, free early)
            yield send_sse_event("Sending data...")
            yield send_sse_event("data_section", {"section": "gps", "gps_data": gps_data})
            del gps_data

            # Section 2: Follow-up data (largest — flw_drilldown has per-visit details)
            yield send_sse_event("data_section", {"section": "followup", "followup_data": followup_data})
            del followup_data

            # Section 3: Overview + performance + lists
            yield send_sse_event(
                "data_section",
                {
                    "section": "overview",
                    "overview_data": overview_data,
                    "performance_data": performance_data,
                    "active_usernames": sorted(active_usernames),
                    "flw_names": flw_names,
                    "open_tasks": open_tasks,
                    "open_task_usernames": open_task_usernames,
                },
            )
            del overview_data, performance_data

            # Final: small metadata only — frontend merges with accumulated sections
            complete_meta = {
                "success": True,
                "opportunity_id": opportunity_id,
                "opportunity_name": labs_context.get("opportunity_name", ""),
                "from_cache": from_cache,
                "dev_fixture": getattr(settings, "MBW_DEV_FIXTURE", False),
                "status_filter": status_filter,
            }
            if monitoring_session:
                complete_meta["monitoring_session"] = {
                    "id": monitoring_session.id,
                    "title": monitoring_session.title,
                    "status": monitoring_session.status,
                    "flw_results": monitoring_session.flw_results,
                    "progress": monitoring_session.get_monitoring_progress_stats(),
                    "selected_flw_usernames": monitoring_session.selected_flw_usernames,
                }

            _log_rss("before Complete (all sections sent)")
            yield send_sse_event("Complete!", complete_meta)

        except Exception as e:
            logger.error(f"[MBW Dashboard] Stream failed: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            yield send_sse_event("Error", error="Failed to load dashboard data. Please try again or contact support.")


class MBWGPSDetailView(LoginRequiredMixin, View):
    """JSON API endpoint for GPS drill-down to get visits for a specific FLW.

    Optimized to query the computed visit cache directly by username,
    avoiding loading ALL visits through the pipeline (O(FLW_visits) not O(total_visits)).
    Falls back to full pipeline if the cache is cold.
    """

    def get(self, request, username: str):
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired. Please refresh the page."}, status=401)

        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            return JsonResponse({"error": "No opportunity selected"}, status=400)

        default_start, default_end = get_default_date_range()
        start_date = parse_date_param(request.GET.get("start_date"), default_start)
        end_date = parse_date_param(request.GET.get("end_date"), default_end)

        app_version_op = request.GET.get("app_version_op", "")
        if app_version_op and app_version_op not in ("gt", "gte", "eq", "lte", "lt"):
            app_version_op = ""
        app_version_val = _parse_int_param(request.GET.get("app_version_val"))

        # Visit approval status filter (must match SSE stream filter)
        status_filter, status_err = _parse_status_filter(request.GET.get("status_filter"))
        if status_err:
            return JsonResponse({"error": status_err}, status=400)

        try:
            visits_for_analysis = self._load_visits_from_cache(opportunity_id, username, status_filter=status_filter)

            if visits_for_analysis is None:
                # Cache cold — fall back to full pipeline (slow for large opportunities)
                logger.info("[MBW Dashboard] GPS detail: cache miss for %s, running full pipeline", username)
                visits_for_analysis = self._load_visits_from_pipeline(
                    request, opportunity_id, username, status_filter=status_filter
                )

            # Apply app version filter if configured
            if app_version_op and app_version_val is not None:
                visits_for_analysis = [
                    v
                    for v in visits_for_analysis
                    if _check_app_version(v["computed"].get("app_build_version"), app_version_op, app_version_val)
                ]

            gps_result = analyze_gps_metrics(visits_for_analysis, {})
            filtered_visits = filter_visits_by_date(gps_result.visits, start_date, end_date)

            return JsonResponse(
                {
                    "success": True,
                    "username": username,
                    "visits": [serialize_visit(v) for v in filtered_visits],
                    "total_visits": len(filtered_visits),
                    "flagged_visits": sum(1 for v in filtered_visits if v.is_flagged),
                }
            )

        except Exception as e:
            logger.error(f"[MBW Dashboard] GPS detail failed: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            return JsonResponse({"error": "An error occurred. Please try again."}, status=500)

    @staticmethod
    def _load_visits_from_cache(opportunity_id, username, status_filter=None):
        """Try to load visits from the computed cache directly (fast path).

        Queries ComputedVisitCache by username, returning only this FLW's visits
        instead of loading all 50k+ visits through the pipeline.
        Returns None if cache is cold.
        """
        from commcare_connect.labs.analysis.backends.sql.cache import SQLCacheManager

        cache_mgr = SQLCacheManager(opportunity_id, MBW_GPS_PIPELINE_CONFIG)
        username_lower = username.lower()

        # Check if computed cache exists for this config
        base_qs = cache_mgr.get_computed_visits_queryset()
        if not base_qs.exists():
            return None

        # Query just this FLW's visits (fast: indexed by username)
        qs = base_qs.filter(username=username_lower)
        if status_filter:
            qs = qs.filter(status__in=status_filter)
        rows = qs.values(
            "visit_id",
            "username",
            "visit_date",
            "entity_name",
            "computed_fields",
            "location",
        )

        visits_for_analysis = []
        for row in rows:
            computed = row["computed_fields"] or {}
            gps_location = computed.get("gps_location") or row.get("location")
            visits_for_analysis.append(
                {
                    "id": row["visit_id"],
                    "username": username_lower,
                    "visit_date": row["visit_date"].isoformat() if row["visit_date"] else None,
                    "entity_name": row["entity_name"],
                    "computed": computed,
                    "metadata": {"location": gps_location},
                }
            )

        logger.info(
            "[MBW Dashboard] GPS detail: loaded %d visits for %s from cache (fast path)",
            len(visits_for_analysis),
            username,
        )
        return visits_for_analysis

    @staticmethod
    def _load_visits_from_pipeline(request, opportunity_id, username, status_filter=None):
        """Fall back to full pipeline load (slow path)."""
        if status_filter:
            config = copy.deepcopy(MBW_GPS_PIPELINE_CONFIG)
            config.filters["status"] = status_filter
        else:
            config = MBW_GPS_PIPELINE_CONFIG
        pipeline = AnalysisPipeline(request)
        result = pipeline.stream_analysis_ignore_events(config, opportunity_id)

        visits_for_analysis = []
        username_lower = username.lower()
        for row in result.rows:
            if (row.username or "").lower() != username_lower:
                continue

            gps_location = None
            if row.latitude is not None and row.longitude is not None:
                gps_location = f"{row.latitude} {row.longitude}"

            visits_for_analysis.append(
                {
                    "id": row.id,
                    "username": username_lower,
                    "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                    "entity_name": row.entity_name,
                    "computed": row.computed,
                    "metadata": {"location": gps_location},
                }
            )
        return visits_for_analysis


class MBWSaveFlwResultView(LoginRequiredMixin, View):
    """Save an assessment result for a FLW in a monitoring session."""

    def post(self, request):
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired"}, status=401)

        try:
            body = json.loads(request.body)
            session_id = _parse_int_param(body.get("session_id"))
            username = body.get("username")
            result = body.get("result")  # One of VALID_FLW_RESULTS or None
            notes = body.get("notes", "")

            if not session_id or not username:
                return JsonResponse({"error": "session_id and username are required"}, status=400)

            if result and result not in VALID_FLW_RESULTS:
                return JsonResponse(
                    {"error": f"result must be one of {VALID_FLW_RESULTS} or null"},
                    status=400,
                )

            assessed_by = request.user.id if request.user.is_authenticated else 0
            updated_session = save_flw_result_helper(request, session_id, username, result, notes, assessed_by)
            if not updated_session:
                return JsonResponse({"error": "Monitoring session not found"}, status=404)

            return JsonResponse(
                {
                    "success": True,
                    "flw_results": updated_session.flw_results,
                    "progress": updated_session.get_monitoring_progress_stats(),
                }
            )

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"[MBW Dashboard] Save FLW result failed: {e}", exc_info=True)
            return JsonResponse({"error": "An error occurred. Please try again."}, status=500)


class MBWCompleteSessionView(LoginRequiredMixin, View):
    """Mark a monitoring session as completed."""

    def post(self, request):
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired"}, status=401)

        try:
            body = json.loads(request.body)
            session_id = _parse_int_param(body.get("session_id"))
            overall_result = body.get("overall_result", "completed")
            notes = body.get("notes", "")

            if not session_id:
                return JsonResponse({"error": "session_id is required"}, status=400)

            updated_session = complete_monitoring_run(request, session_id, overall_result, notes)
            if not updated_session:
                return JsonResponse({"error": "Monitoring session not found"}, status=404)

            return JsonResponse(
                {
                    "success": True,
                    "status": updated_session.status,
                    "overall_result": updated_session.overall_result,
                }
            )

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"[MBW Dashboard] Complete session failed: {e}", exc_info=True)
            return JsonResponse({"error": "An error occurred. Please try again."}, status=500)


class MBWSnapshotView(LoginRequiredMixin, View):
    """Return stored dashboard snapshot for a monitoring run."""

    def get(self, request):
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired"}, status=401)

        run_id = _parse_int_param(request.GET.get("run_id") or request.GET.get("session_id"))
        if not run_id:
            return JsonResponse({"error": "run_id is required"}, status=400)

        monitoring_session = load_monitoring_run(request, run_id)
        if not monitoring_session:
            return JsonResponse({"error": "Run not found"}, status=404)

        snapshot = monitoring_session.dashboard_snapshot
        if not snapshot:
            return JsonResponse({"has_snapshot": False})

        return JsonResponse(
            {
                "has_snapshot": True,
                "snapshot_timestamp": snapshot.get("timestamp"),
                "success": True,
                "from_snapshot": True,
                "gps_data": snapshot.get("gps_data"),
                "followup_data": snapshot.get("followup_data"),
                "overview_data": snapshot.get("overview_data"),
                "active_usernames": snapshot.get("active_usernames", []),
                "flw_names": snapshot.get("flw_names", {}),
                "open_tasks": snapshot.get("open_tasks", {}),
                "open_task_usernames": snapshot.get("open_task_usernames", []),
                "performance_data": snapshot.get("performance_data", []),
                "monitoring_session": {
                    "id": monitoring_session.id,
                    "title": monitoring_session.title,
                    "status": monitoring_session.status,
                    "flw_results": monitoring_session.flw_results,
                    "progress": monitoring_session.get_monitoring_progress_stats(),
                    "selected_flw_usernames": monitoring_session.selected_flw_usernames,
                },
            }
        )


def _rebuild_gps_with_visits(request, gps_data, opportunity_id=None):
    """Re-read GPS visits from computed cache and embed in gps_data for snapshot fidelity.

    Uses the SQL computed cache directly (fast local DB query) instead of running
    the full pipeline (which would re-download 50k+ visits from production if the
    cache count doesn't match the expected count).

    If the computed cache is cold/expired, skips visit embedding — the frontend
    falls back to lazy-loading via MBWGPSDetailView API when viewing the snapshot.
    """
    if not opportunity_id:
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")
    if not opportunity_id:
        return gps_data

    try:
        from commcare_connect.labs.analysis.backends.sql.cache import SQLCacheManager

        cache_mgr = SQLCacheManager(opportunity_id, MBW_GPS_PIPELINE_CONFIG)
        base_qs = cache_mgr.get_computed_visits_queryset()

        if not base_qs.exists():
            logger.info("[MBW Dashboard] GPS rebuild: computed cache cold, skipping visit embedding")
            return gps_data

        # Get all FLW usernames from the snapshot's flw_summaries
        flw_usernames = [(s.get("username") or "").lower() for s in gps_data.get("flw_summaries", [])]

        # Query visits from computed cache (fast: local DB, indexed by username)
        rows = base_qs.filter(username__in=flw_usernames).values(
            "visit_id",
            "username",
            "visit_date",
            "entity_name",
            "computed_fields",
            "location",
        )

        # Build visit dicts in the format expected by analyze_gps_metrics
        visits_for_analysis = []
        for row in rows:
            computed = row["computed_fields"] or {}
            gps_location = computed.get("gps_location") or row.get("location")
            visits_for_analysis.append(
                {
                    "id": row["visit_id"],
                    "username": (row["username"] or "").lower(),
                    "visit_date": row["visit_date"].isoformat() if row["visit_date"] else None,
                    "entity_name": row["entity_name"],
                    "computed": computed,
                    "metadata": {"location": gps_location},
                }
            )

        gps_result = analyze_gps_metrics(visits_for_analysis, {})
        del visits_for_analysis

        # Apply date range filter from the snapshot's GPS data
        start_str = gps_data.get("date_range_start")
        end_str = gps_data.get("date_range_end")
        start_date = date.fromisoformat(start_str) if start_str else None
        end_date = date.fromisoformat(end_str) if end_str else None

        if start_date and end_date:
            filtered = filter_visits_by_date(gps_result.visits, start_date, end_date)
        else:
            filtered = gps_result.visits
        del gps_result

        # Group serialized visits by FLW username
        visits_by_flw = {}
        for v in filtered:
            visits_by_flw.setdefault(v.username, []).append(serialize_visit(v))
        del filtered

        # Embed visits into the existing flw_summaries
        for summary in gps_data.get("flw_summaries", []):
            summary["visits"] = visits_by_flw.get(summary["username"], [])

        logger.info(
            "[MBW Dashboard] GPS rebuild: embedded visits for %d FLWs from computed cache",
            len(visits_by_flw),
        )
        return gps_data
    except Exception as e:
        logger.warning("[MBW Dashboard] Failed to rebuild GPS visits for snapshot: %s", e)
        return gps_data


class MBWSaveSnapshotView(LoginRequiredMixin, View):
    """Save dashboard snapshot from frontend (manual button or auto-save on Complete).

    Receives the full dashData from the frontend as JSON POST body.
    Re-computes GPS visits from pipeline cache for full snapshot fidelity
    (SSE stream omits visits to save memory).
    """

    def post(self, request):
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired"}, status=401)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"error": "Invalid JSON"}, status=400)

        if not isinstance(body, dict):
            return JsonResponse({"error": "Request body must be a JSON object"}, status=400)

        run_id = _parse_int_param(body.get("run_id"))
        if not run_id:
            return JsonResponse({"error": "run_id is required"}, status=400)

        snapshot_data = body.get("snapshot_data", {})
        if not isinstance(snapshot_data, dict):
            return JsonResponse({"error": "snapshot_data must be a JSON object"}, status=400)

        # Load the run to validate it exists and derive opportunity_id
        data_access = WorkflowDataAccess(request=request)
        try:
            run = data_access.get_run(run_id)
        finally:
            data_access.close()

        if not run:
            return JsonResponse({"error": "Run not found"}, status=404)

        # Re-read GPS visits from pipeline cache if not already embedded
        gps_data = snapshot_data.get("gps_data")
        if gps_data is not None and not isinstance(gps_data, dict):
            return JsonResponse({"error": "gps_data must be a JSON object"}, status=400)
        if gps_data:
            has_visits = any(s.get("visits") for s in gps_data.get("flw_summaries", []))
            if not has_visits:
                snapshot_data["gps_data"] = _rebuild_gps_with_visits(
                    request, gps_data, opportunity_id=run.opportunity_id
                )

        try:
            save_dashboard_snapshot(request, run_id, snapshot_data)
            logger.info("[MBW Dashboard] Saved snapshot for run %s (via API)", run_id)
            saved_at = datetime.now(dt_timezone.utc).isoformat()
            return JsonResponse({"success": True, "timestamp": saved_at})
        except Exception as e:
            logger.error("[MBW Dashboard] Snapshot save failed: %s", e, exc_info=True)
            return JsonResponse({"error": "Failed to save snapshot"}, status=500)


class MBWOAuthStatusView(LoginRequiredMixin, View):
    """Return current OAuth token status for Connect, CommCare HQ, and OCS."""

    def get(self, request):
        now_ts = timezone.now().timestamp()
        next_url = request.GET.get("next", request.get_full_path())
        # Sanitize: only allow safe internal paths
        next_url = (next_url or "").replace("\\", "/")
        if not url_has_allowed_host_and_scheme(next_url, allowed_hosts=None):
            next_url = request.get_full_path()

        labs = request.session.get("labs_oauth", {})
        cchq = request.session.get("commcare_oauth", {})
        ocs = request.session.get("ocs_oauth", {})

        return JsonResponse(
            {
                "connect": {
                    "active": bool(labs.get("access_token") and now_ts < labs.get("expires_at", 0)),
                },
                "commcare": {
                    "active": bool(cchq.get("access_token") and now_ts < cchq.get("expires_at", 0)),
                    "authorize_url": reverse("labs:commcare_initiate") + "?" + urlencode({"next": next_url}),
                },
                "ocs": {
                    "active": bool(ocs.get("access_token") and now_ts < ocs.get("expires_at", 0)),
                    "authorize_url": reverse("labs:ocs_initiate") + "?" + urlencode({"next": next_url}),
                },
            }
        )


class MBWSuspendUserView(LoginRequiredMixin, View):
    """
    API endpoint to suspend a user.

    Note: This endpoint is retained but not called from the UI.
    "Suspended" is now an assessment label only (stored in flw_results),
    not a Connect API action. The actual Connect API endpoint for
    suspension from Labs environment needs to be confirmed.
    """

    def post(self, request):
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired"}, status=401)

        try:
            body = json.loads(request.body)
            username = body.get("username")
            reason = body.get("reason", "Suspended from MBW Monitoring Dashboard")

            if not username:
                return JsonResponse({"error": "username is required"}, status=400)

            # TODO: Implement actual suspension via Connect API
            # The existing suspension mechanism (OpportunityAccess.suspended = True)
            # is DB-level and not accessible from Labs environment.
            # Need to confirm the Connect API endpoint for this.
            logger.warning(
                f"[MBW Dashboard] Suspend user requested for {username} " f"(reason: {reason}) - NOT IMPLEMENTED YET"
            )

            return JsonResponse(
                {
                    "success": False,
                    "error": "User suspension is not yet available from the dashboard. "
                    "Please use the main Connect interface to suspend users.",
                }
            )

        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        except Exception as e:
            logger.error(f"[MBW Dashboard] Suspend failed: {e}", exc_info=True)
            return JsonResponse({"error": "An error occurred. Please try again."}, status=500)
