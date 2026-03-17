"""
Data access layer for the Audit of Audits admin report.

Unlike other DataAccess classes, this does NOT subclass BaseDataAccess because
BaseDataAccess auto-populates opportunity_id from request.labs_context, which
would scope results to a single opportunity.

Two-phase query strategy
------------------------
The Connect API requires at least one scope parameter to return private records.
Workflow runs and definitions are stored with opportunity_id scope (always set),
but NOT reliably with organization_id scope (only added if labs_context had it at
creation time). Audit sessions, however, ARE stored with organization_id scope.

Phase 1 — Sessions by organization:
    Query each of the user's ~10 organizations to collect all audit sessions.
    Sessions are reliably indexed by organization_id.

Phase 2 — Runs & definitions by opportunity:
    The caller passes ALL opportunity IDs the user has access to (from their
    OAuth session). This ensures runs with zero sessions are still shown.
    get_records() has no opportunity_id param — scope is set at client init —
    so a short-lived LabsRecordAPIClient is created per opportunity.

Result: every workflow run the user can access is shown, regardless of whether
it has audit sessions yet.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from commcare_connect.audit.models import AuditSessionRecord
from commcare_connect.labs.integrations.connect.api_client import LabsAPIError, LabsRecordAPIClient
from commcare_connect.workflow.data_access import WorkflowDefinitionRecord, WorkflowRunRecord

logger = logging.getLogger(__name__)

WORKFLOW_EXPERIMENT = "workflow"
AUDIT_EXPERIMENT = "audit"

# Maximum number of concurrent HTTP requests to the Connect API.
# 276 sequential calls × 300 ms ≈ 83 s.  10 workers → ~9 s.
# Keep below 20 to avoid triggering server-side rate limits.
MAX_CONCURRENT_REQUESTS = 10

_DATE_PARSE_FORMATS = ("%b %d, %Y", "%B %d, %Y", "%m/%d/%Y", "%d/%m/%Y")


def _normalize_date(value: str | None) -> str | None:
    """Normalize a date string to YYYY-MM-DD.

    Handles ISO strings (2026-01-24 or 2026-01-24T...) as well as
    human-readable formats like "Jan 24, 2026" that some workflow
    template states produce.
    """
    if not value:
        return None
    # Already ISO — just take the date portion
    if len(value) >= 10 and value[4:5] == "-":
        return value[:10]
    # Try known human-readable formats
    for fmt in _DATE_PARSE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return value  # Return as-is if unrecognised


def _log_api_error(record_type: str, scope_type: str, scope_id: int, error: LabsAPIError) -> None:
    """Log an API fetch error at the appropriate level.

    404 responses are expected (opportunity not enrolled in any workflow yet)
    and are logged at DEBUG to avoid alarming noise in production logs.
    All other errors are logged at WARNING.
    """
    # LabsAPIError may wrap an httpx.HTTPStatusError — try common attribute locations.
    status_code = getattr(error, "status_code", None)
    if status_code is None:
        cause = getattr(error, "__cause__", None)
        status_code = getattr(getattr(cause, "response", None), "status_code", None)

    if status_code == 404:
        logger.debug(
            "[AuditOfAudits] No labs records for %s %d (404 — not enrolled in workflow)",
            scope_type,
            scope_id,
        )
    else:
        logger.warning(
            "[AuditOfAudits] Failed to fetch %s for %s %d: %s",
            record_type,
            scope_type,
            scope_id,
            error,
        )


class AuditOfAuditsDataAccess:
    """
    Cross-opportunity admin data access for the Audit of Audits report.

    See module docstring for the two-phase query strategy.

    Optional template_types filter
    --------------------------------
    When template_types is provided (e.g. ["bulk_image_audit"]), the fetch is
    optimised in two passes:

    Pass A — Definitions (all opps): fetch definitions for every opportunity,
             keep only those whose templateType matches the filter.  Record which
             opportunity IDs have at least one matching definition.

    Pass B — Runs (filtered opps only): fetch workflow runs only for the
             opportunities identified in Pass A.  Opportunities with no matching
             definitions are skipped entirely, saving 1 HTTP call each.

    Without a filter (template_types=None) the behaviour is unchanged and runs
    are fetched for every opportunity.
    """

    def __init__(
        self,
        access_token: str,
        organization_ids: list[int],
        opportunity_ids: list[int],
        template_types: list[str] | None = None,
    ):
        self.access_token = access_token
        self.organization_ids = organization_ids
        self.opportunity_ids = opportunity_ids
        # None → no filter (fetch all template types)
        self.template_types: frozenset[str] | None = frozenset(template_types) if template_types else None

    def close(self):
        pass  # All HTTP clients are short-lived and closed after each request

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ── Thread-safe per-item fetch helpers ───────────────────────────────────
    # Each helper creates its own short-lived HTTP client so they are safe to
    # call concurrently from a ThreadPoolExecutor.

    def _fetch_sessions_for_org(self, org_id: int) -> list[AuditSessionRecord]:
        """Fetch all AuditSession records scoped to one organization."""
        try:
            with LabsRecordAPIClient(access_token=self.access_token) as client:
                return client.get_records(
                    experiment=AUDIT_EXPERIMENT,
                    type="AuditSession",
                    organization_id=org_id,
                    model_class=AuditSessionRecord,
                )
        except LabsAPIError as e:
            _log_api_error("sessions", "org", org_id, e)
            return []

    def _fetch_definitions_for_opp(self, opp_id: int) -> list[WorkflowDefinitionRecord]:
        """Fetch all workflow definition records scoped to one opportunity."""
        try:
            with LabsRecordAPIClient(access_token=self.access_token, opportunity_id=opp_id) as client:
                return client.get_records(
                    experiment=WORKFLOW_EXPERIMENT,
                    type="workflow_definition",
                    model_class=WorkflowDefinitionRecord,
                )
        except LabsAPIError as e:
            _log_api_error("definitions", "opp", opp_id, e)
            return []

    def _fetch_runs_for_opp(self, opp_id: int) -> list[WorkflowRunRecord]:
        """Fetch all workflow run records scoped to one opportunity."""
        try:
            with LabsRecordAPIClient(access_token=self.access_token, opportunity_id=opp_id) as client:
                return client.get_records(
                    experiment=WORKFLOW_EXPERIMENT,
                    type="workflow_run",
                    model_class=WorkflowRunRecord,
                )
        except LabsAPIError as e:
            _log_api_error("runs", "opp", opp_id, e)
            return []

    def build_report_data(self) -> list[dict]:
        """
        Fetch audit sessions (org-scoped), then fetch workflow runs and
        definitions (opportunity-scoped) for the opportunities discovered from
        those sessions. Join everything in Python and return rows sorted by
        created_at descending (most recent first).

        API call count: (org_count × 1 session call) + (opp_count × 2 calls)
        Typically ~10 org calls + 2×N opportunity calls.

        Returns:
            List of dicts with keys:
                run_id, definition_id, definition_name, template_type,
                opportunity_id, created_at, period_start, period_end,
                status, selected_count, username, session_count,
                completed_session_count, avg_pct_passed
        """
        if not self.organization_ids and not self.opportunity_ids:
            logger.warning("[AuditOfAudits] No org or opportunity IDs provided — returning empty report")
            return []

        # ── Phase 1: Fetch sessions concurrently across all organizations ────────
        valid_org_ids = [o for o in self.organization_ids if isinstance(o, int)]
        all_sessions: list[AuditSessionRecord] = []

        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_REQUESTS, len(valid_org_ids) or 1)) as ex:
            future_to_org = {ex.submit(self._fetch_sessions_for_org, oid): oid for oid in valid_org_ids}
            for future in as_completed(future_to_org):
                all_sessions.extend(future.result())

        logger.info(
            "[AuditOfAudits] Phase 1: fetched %d sessions across %d orgs (concurrent)",
            len(all_sessions),
            len(valid_org_ids),
        )

        # ── Phase 2A: Fetch definitions concurrently for ALL opportunities ───────
        # 276 sequential calls × ~300 ms ≈ 83 s.  With 10 workers → ~9 s.
        # Results are aggregated in the main thread (no shared-state races).
        opportunity_ids: set[int] = set(self.opportunity_ids)
        logger.info(
            "[AuditOfAudits] Phase 2A: fetching definitions for %d opportunities "
            "concurrently (template filter: %s)",
            len(opportunity_ids),
            sorted(self.template_types) if self.template_types else "none",
        )

        def_map: dict[int, WorkflowDefinitionRecord] = {}
        opps_with_matching_defs: set[int] = set()

        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_REQUESTS, len(opportunity_ids) or 1)) as ex:
            future_to_opp = {ex.submit(self._fetch_definitions_for_opp, oid): oid for oid in opportunity_ids}
            for future in as_completed(future_to_opp):
                opp_id = future_to_opp[future]
                for d in future.result():
                    def_map[d.id] = d
                    if self.template_types is None or d.template_type in self.template_types:
                        opps_with_matching_defs.add(opp_id)

        # ── Phase 2B: Fetch runs — skip opps with no matching definitions ─────
        run_opps: set[int] = opps_with_matching_defs if self.template_types else opportunity_ids
        logger.info(
            "[AuditOfAudits] Phase 2B: fetching runs for %d/%d opportunities " "(%d skipped by template filter)",
            len(run_opps),
            len(opportunity_ids),
            len(opportunity_ids) - len(run_opps),
        )

        runs: list[WorkflowRunRecord] = []

        with ThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_REQUESTS, len(run_opps) or 1)) as ex:
            future_to_opp = {ex.submit(self._fetch_runs_for_opp, oid): oid for oid in run_opps}
            for future in as_completed(future_to_opp):
                opp_id = future_to_opp[future]
                opp_runs = future.result()
                logger.info("[AuditOfAudits] opp_id=%d → %d runs", opp_id, len(opp_runs))
                runs.extend(opp_runs)

        logger.info(
            "[AuditOfAudits] Totals — definitions: %d, runs: %d, sessions: %d",
            len(def_map),
            len(runs),
            len(all_sessions),
        )

        # ── Phase 3: Join and build rows ─────────────────────────────────────────
        # labs_record_id may be stored as a string in the JSON payload even
        # though run.id is always an int — coerce to int for reliable dict lookup.
        sessions_by_run: dict[int, list[AuditSessionRecord]] = {}
        for session in all_sessions:
            run_id = session.labs_record_id
            if run_id is not None:
                try:
                    sessions_by_run.setdefault(int(run_id), []).append(session)
                except (TypeError, ValueError):
                    logger.warning("[AuditOfAudits] Could not coerce labs_record_id %r to int", run_id)

        rows = []
        for run in runs:
            definition = def_map.get(run.definition_id)
            # Skip runs whose definition doesn't match the template type filter.
            # (opps_with_matching_defs ensures the opp has at least one matching
            # definition, but the opp may also have non-matching definitions.)
            if self.template_types and (not definition or definition.template_type not in self.template_types):
                continue
            linked_sessions = sessions_by_run.get(run.id, [])
            avg_pct_passed = _extract_avg_pct_passed(run, linked_sessions)

            state = run.state or {}

            # ── Passing % — percentage of completed sessions that passed ──────
            completed_sessions = [s for s in linked_sessions if s.overall_result in ("pass", "fail")]
            passing_sessions = [s for s in completed_sessions if s.overall_result == "pass"]
            pct_passing = (
                round(len(passing_sessions) / len(completed_sessions) * 100, 1) if completed_sessions else None
            )

            # ── Tasks created — CommCare tasks created by this run ───────────
            # Stored by the template as run.state["tasks_created"].
            # This is distinct from flw_count (= number of audit sessions / FLWs).
            raw_tasks_created = state.get("tasks_created")
            tasks_created: int | None = None
            if raw_tasks_created is not None:
                try:
                    tasks_created = int(raw_tasks_created)
                except (TypeError, ValueError):
                    pass

            # ── Images reviewed — total images reviewed in this run ───────────
            raw_images = state.get("images_reviewed")
            images_reviewed: int | None = None
            if raw_images is not None:
                try:
                    images_reviewed = int(raw_images)
                except (TypeError, ValueError):
                    pass

            # ── % Sampled — percentage of submissions sampled for image review ─
            pct_sampled = state.get("sample_percentage")

            # ── Run By — username of the NM who created the run ──────────────
            # Priority: top-level API field → state["run_by"] (written by
            # the template on creation for newer runs) → data["username"].
            run_by = run.username or state.get("run_by", "") or run.data.get("username", "") or ""

            rows.append(
                {
                    "run_id": run.id,
                    "definition_id": run.definition_id,
                    "definition_name": definition.name if definition else f"Workflow #{run.definition_id}",
                    "template_type": definition.template_type if definition else "",
                    "opportunity_id": run.opportunity_id,
                    "created_at": run.created_at or "",
                    "period_start": _normalize_date(run.period_start) or "",
                    "period_end": _normalize_date(run.period_end) or "",
                    "status": run.status or "unknown",
                    "selected_count": run.selected_count or 0,
                    "username": run_by,
                    "session_count": len(linked_sessions),
                    "completed_session_count": len(completed_sessions),
                    "avg_pct_passed": avg_pct_passed,
                    "pct_passing": pct_passing,
                    "tasks_created": tasks_created,
                    "images_reviewed": images_reviewed,
                    "pct_sampled": pct_sampled,
                }
            )

        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return rows


def _extract_avg_pct_passed(run: WorkflowRunRecord, sessions: list[AuditSessionRecord]) -> float | None:
    """
    Determine the average % passed for a workflow run.

    Priority:
    1. Pre-computed value stored in run.state (workflow template may store this)
    2. Calculated from linked audit session overall_result fields
    3. None if no completed sessions exist
    """
    state = run.state or {}

    # Check common pre-computed keys in run state.
    # "avg_passed" is written by the bulk_image_audit template on completion.
    # The others are aliases used by other template types.
    for key in ("avg_passed", "avg_pct_passed", "avg_pass_rate", "pass_rate"):
        val = state.get(key)
        if val is not None:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    # Check nested overall_stats dict
    overall_stats = state.get("overall_stats", {})
    if isinstance(overall_stats, dict):
        for key in ("avg_passed", "avg_pct_passed", "avg_pass_rate", "pass_rate"):
            val = overall_stats.get(key)
            if val is not None:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass

    # Fallback: calculate from session results
    completed = [s for s in sessions if s.overall_result in ("pass", "fail")]
    if not completed:
        return None

    pass_count = sum(1 for s in completed if s.overall_result == "pass")
    return round(pass_count / len(completed) * 100, 1)
