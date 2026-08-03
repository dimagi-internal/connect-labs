"""
Optimized Data Access Layer for Audit.

Uses the analysis pipeline for field extraction and raw data access,
with optimized CSV caching that skips form_json parsing for selection operations.

Key optimizations:
1. Backend-agnostic raw data caching (SQL or Redis based on settings)
2. skip_form_json for selection - doesn't parse form_json for preview/filtering
3. filter_visit_ids for extraction - only parses form_json for selected visits
4. Uses FieldComputation with custom extractors - leverages analysis pipeline infrastructure
"""

import hashlib
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pandas as pd
from django.core.cache import cache
from django.http import HttpRequest

from connect_labs.audit.analysis_config import AUDIT_EXTRACTION_CONFIG
from connect_labs.audit.models import AuditSessionRecord
from connect_labs.labs.analysis.computations import compute_visit_fields
from connect_labs.labs.analysis.models import LocalUserVisit
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.models import LocalLabsRecord
from connect_labs.workflow.data_access import BaseDataAccess

logger = logging.getLogger(__name__)

# Cross-opportunity audit-session lookup (see AuditDataAccess.get_audit_session).
# The mapping is immutable in practice, so the TTL only bounds staleness after a
# record is deleted or re-scoped; a stale entry self-heals on the next miss.
_SESSION_OPP_CACHE_TTL = 60 * 60 * 6  # 6 hours
# Hard ceiling on how many opportunity scopes one lookup will probe. Each probe is
# a remote round-trip, so this bounds the worst case of a cache miss.
_SESSION_SEARCH_OPP_LIMIT = 1000
# How long a caller remembers that a sweep found nothing. Short, because unlike
# a hit this is not an immutable fact — it can change the moment the caller is
# granted access to another opportunity — but long enough that a polling page
# cannot re-run the fan-out on every request. See #1060.
_SESSION_MISS_CACHE_TTL = 60 * 5  # 5 minutes


def _session_opp_cache_key(session_id: int) -> str:
    """Cache key for an audit session's storage opportunity.

    Deliberately NOT namespaced per user: it records where a record lives, which
    is the same fact for everyone. Authorization is unaffected — every fetch
    still carries the caller's own token and is re-authorized server-side.
    """
    return f"audit:session-opp:{session_id}"


def _session_miss_cache_key(session_id: int, access_token: str) -> str:
    """Cache key for "this caller's sweep found nothing".

    Namespaced PER CALLER, unlike the location memo above, and that asymmetry is
    the point. Where a session lives is the same fact for everybody; whether a
    sweep can find it is not — the sweep only ever probes the caller's OWN
    opportunities. Caching a miss globally would hide a session from the people
    who can see it as soon as one person who can't went looking.

    The token is hashed rather than stored: cache keys end up in Redis and in
    logs, and a bearer token has no business in either. A token rotation just
    costs one extra sweep.
    """
    caller = hashlib.sha256(access_token.encode()).hexdigest()[:16]
    return f"audit:session-miss:{session_id}:{caller}"


def _coerce_int(value) -> int | None:
    """Best-effort int coercion for scope ids, which reach us as int or str."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _storage_record(session: AuditSessionRecord) -> LocalLabsRecord:
    """Plain LocalLabsRecord view of an audit session's STORAGE metadata.

    ``AuditSessionRecord.opportunity_id`` is the opportunity being *audited*;
    ``storage_opportunity_id`` is the scope the production API files, filters
    and writes by. See the block comment on that class for why both exist.

    Handing the raw proxy to ``update_record`` would be wrong twice over: it
    reads ``current.opportunity_id`` both to decide scope and to build the
    write payload, so a session whose audit subject differs from its storage
    opportunity would get silently *moved*. Build a storage-truth record
    instead.
    """
    storage_opp_id = session.storage_opportunity_id

    return LocalLabsRecord(
        {
            "id": session.id,
            "experiment": session.experiment,
            "type": session.type,
            "data": session.data,
            "username": session.username,
            "opportunity_id": storage_opp_id,
            "organization_id": session.organization_id,
            "program_id": session.program_id,
            "labs_record_id": session.labs_record_id,
        }
    )


# =============================================================================
# Mock Request for Celery Tasks
# =============================================================================


def create_mock_request(access_token: str, opportunity_id: int | None = None):
    """
    Create a mock request object for use in Celery tasks.

    Celery tasks don't have access to the original HTTP request, but
    AuditDataAccess needs request-like object to extract OAuth tokens
    and context. This creates a minimal object with the required attributes.

    Args:
        access_token: OAuth access token for API calls
        opportunity_id: Optional opportunity ID for context

    Returns:
        Mock object with session, labs_context, user, GET, POST attributes
    """
    import time

    class MockRequest:
        def __init__(self):
            self.session = {
                "labs_oauth": {
                    "access_token": access_token,
                    "expires_at": time.time() + 3600,
                }
            }
            self.labs_context = {"opportunity_id": opportunity_id} if opportunity_id else {}
            self.user = None
            self.GET = {}
            self.POST = {}

    return MockRequest()


class ImageDownloadError(ValueError):
    """An image could not be fetched from Connect.

    Carries the upstream HTTP status when there was one, so callers can tell the
    difference between "this blob genuinely does not exist" (a 404 worth passing
    through as a 404) and "the fetch failed" (a fault worth logging and
    surfacing as a 5xx). Subclasses ``ValueError`` because that is what this
    method has always raised — existing callers that catch ``ValueError`` keep
    working unchanged.
    """

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


# =============================================================================
# Filtering Logic
# =============================================================================


@dataclass
class AuditCriteria:
    """Structured audit selection criteria."""

    audit_type: str = "date_range"
    start_date: str | None = None
    end_date: str | None = None
    count_per_flw: int = 10
    count_per_opp: int = 10
    count_across_all: int = 100
    sample_percentage: int = 100
    selected_flw_user_ids: list[str] | None = None
    # Filter to specific deliver unit type(s) — derived from form.@name in form_json,
    # since Connect never exposes a deliver-unit name, only the numeric FK id.
    deliver_unit_types: list[str] | None = None
    visit_statuses: list[
        str
    ] | None = None  # Filter to specific visit status(es): pending/approved/rejected/over_limit
    related_fields: list[dict] | None = None  # List of {image_path, field_path, label}
    exclude_prior_audited: bool = False  # Drop images already audited in a completed session
    # Restrict date_range audits to visits falling on these ISO weekdays (1=Monday..7=Sunday).
    # None/empty = no restriction (all days). Only meaningful when audit_type == "date_range".
    days_of_week: list[int] | None = None
    # Visit-clustering (duplicate-grouping) filter -- see connect_labs/audit/visit_clustering.py
    # for how these are actually applied. Read back later by
    # AuditSessionRecord.to_summary_dict()'s visit_clustering_used field, so the FLW breakdown
    # UI can show a reviewer what thresholds produced THIS session's groupings -- keep both
    # readers in sync if these field names ever change.
    enable_time_gap: bool = False
    time_gap_minutes: int | None = None
    enable_distance: bool = False
    distance_meters: int | None = None

    @classmethod
    def from_dict(cls, data: dict) -> "AuditCriteria":
        """Create from dict, handling both snake_case and camelCase keys."""
        # Handle related_fields with camelCase normalization
        related_fields_raw = data.get("related_fields") or data.get("relatedFields", [])
        related_fields = None
        if related_fields_raw:
            related_fields = [
                {
                    "image_path": rf.get("image_path") or rf.get("imagePath", ""),
                    "field_path": rf.get("field_path") or rf.get("fieldPath", ""),
                    "label": rf.get("label", ""),
                    "filter_by_image": rf.get("filter_by_image") or rf.get("filterByImage", False),
                    "filter_by_field": rf.get("filter_by_field") or rf.get("filterByField", False),
                }
                for rf in related_fields_raw
                # Require image_path; field_path is optional (image-only filter rules are valid)
                if rf.get("image_path") or rf.get("imagePath")
            ]

        deliver_unit_types_raw = data.get("deliver_unit_types") or data.get("deliverUnitTypes") or []
        deliver_unit_types = [du for du in deliver_unit_types_raw if du] or None

        visit_statuses_raw = data.get("visit_statuses") or data.get("visitStatuses") or []
        visit_statuses = [s for s in visit_statuses_raw if s] or None

        days_of_week_raw = data.get("days_of_week") or data.get("daysOfWeek") or []
        days_of_week = [int(d) for d in days_of_week_raw if d] or None

        return cls(
            audit_type=data.get("audit_type") or data.get("type", "date_range"),
            start_date=data.get("start_date") or data.get("startDate"),
            end_date=data.get("end_date") or data.get("endDate"),
            count_per_flw=data.get("count_per_flw") or data.get("countPerFlw", 10),
            count_per_opp=data.get("count_per_opp") or data.get("countPerOpp", 10),
            count_across_all=data.get("count_across_all") or data.get("countAcrossAll", 100),
            sample_percentage=data.get("sample_percentage") or data.get("samplePercentage", 100),
            selected_flw_user_ids=data.get("selected_flw_user_ids") or data.get("selected_usernames", []),
            deliver_unit_types=deliver_unit_types,
            visit_statuses=visit_statuses,
            related_fields=related_fields or None,
            exclude_prior_audited=bool(data.get("exclude_prior_audited") or data.get("excludePriorAudited") or False),
            days_of_week=days_of_week,
            enable_time_gap=bool(data.get("enable_time_gap") or data.get("enableTimeGap") or False),
            time_gap_minutes=data.get("time_gap_minutes") or data.get("timeGapMinutes"),
            enable_distance=bool(data.get("enable_distance") or data.get("enableDistance") or False),
            distance_meters=data.get("distance_meters") or data.get("distanceMeters"),
        )


def filter_visits_for_audit(
    visits: list[dict], criteria: AuditCriteria, return_visits: bool = False
) -> list[int] | list[dict]:
    """
    Filter visits based on audit criteria.

    Uses pandas for efficient filtering and sampling.

    Args:
        visits: List of visit dicts
        criteria: AuditCriteria with filter settings
        return_visits: If True, return filtered visit dicts instead of just IDs

    Returns:
        List of visit IDs (default) or list of filtered visit dicts (if return_visits=True)
    """
    if not visits:
        return []

    df = pd.DataFrame(visits)

    if "id" not in df.columns:
        return []

    # Parse dates
    if "visit_date" in df.columns:
        df["visit_date"] = pd.to_datetime(df["visit_date"], format="mixed", utc=True, errors="coerce")

    # Apply filters based on audit type
    if criteria.audit_type == "date_range":
        if criteria.start_date and "visit_date" in df.columns:
            start = pd.to_datetime(criteria.start_date)
            df = df[df["visit_date"].dt.date >= start.date()]
        if criteria.end_date and "visit_date" in df.columns:
            end = pd.to_datetime(criteria.end_date)
            df = df[df["visit_date"].dt.date <= end.date()]
        if criteria.days_of_week and "visit_date" in df.columns:
            # pandas .dt.dayofweek is Monday=0..Sunday=6; +1 gives ISO weekday
            # (Monday=1..Sunday=7), matching AuditCriteria.days_of_week.
            df = df[(df["visit_date"].dt.dayofweek + 1).isin(criteria.days_of_week)]

    elif criteria.audit_type == "last_n_per_flw":
        if "visit_date" in df.columns and "username" in df.columns:
            df = df.sort_values("visit_date", ascending=False)
            df = df.groupby("username", dropna=False).head(criteria.count_per_flw)

    elif criteria.audit_type == "last_n_per_opp":
        if "visit_date" in df.columns and "opportunity_id" in df.columns:
            df = df.sort_values("visit_date", ascending=False)
            df = df.groupby("opportunity_id").head(criteria.count_per_opp)

    elif criteria.audit_type == "last_n_across_all":
        if "visit_date" in df.columns:
            df = df.sort_values("visit_date", ascending=False)
            df = df.head(criteria.count_across_all)

    # Filter by selected FLWs if provided
    if criteria.selected_flw_user_ids and "username" in df.columns:
        df = df[df["username"].isin(criteria.selected_flw_user_ids)]

    # Filter by deliver unit type(s) if provided — derived from form.@name, since
    # Connect never exposes a deliver-unit name, only the numeric FK id.
    if criteria.deliver_unit_types and "form_json" in df.columns:

        def _form_name(form_json):
            if isinstance(form_json, dict):
                form = form_json.get("form")
                if isinstance(form, dict):
                    return form.get("@name")
            return None

        df = df[df["form_json"].apply(_form_name).isin(criteria.deliver_unit_types)]

    # Filter by visit status(es) if provided
    if criteria.visit_statuses and "status" in df.columns:
        df = df[df["status"].isin(criteria.visit_statuses)]

    # Apply sample percentage — sample per FLW for equal representation, then shuffle
    if criteria.sample_percentage < 100 and len(df) > 0:
        if "username" in df.columns:
            groups = []
            for _, grp in df.groupby("username", dropna=False):
                n = max(1, int(len(grp) * criteria.sample_percentage / 100))
                groups.append(grp.sample(n=min(n, len(grp)), random_state=42))
            df = pd.concat(groups).sample(frac=1, random_state=42)
        else:
            sample_size = max(1, int(len(df) * criteria.sample_percentage / 100))
            df = df.sample(n=min(sample_size, len(df)), random_state=42)

    if return_visits:
        return df.to_dict("records")
    return df["id"].dropna().astype(int).unique().tolist()


_AUDIT_VERDICTS = {"pass", "fail", "duplicate_fake", "duplicate", "fake"}


def build_prior_audit_index(sessions, exclude_session_id=None) -> dict:
    """Map "<visit_id>:<blob_id>" -> prior verdict, from COMPLETED sessions only.

    Only images with a human verdict (pass/fail/duplicate_fake) count. When an
    image was audited in more than one completed session, the most-recently
    completed verdict wins. `exclude_session_id` skips a session so it never
    flags its own images (matters for reopened sessions).
    """
    index: dict[str, dict] = {}
    dt_by_key: dict[str, object] = {}
    for session in sessions:
        if session.status != "completed":
            continue
        if exclude_session_id is not None and session.id == exclude_session_id:
            continue
        completed_at = session.completed_at  # datetime | None
        for visit_key, visit_result in (session.data.get("visit_results") or {}).items():
            for blob_id, assessment in (visit_result.get("assessments") or {}).items():
                result = assessment.get("result")
                if result not in _AUDIT_VERDICTS:
                    continue
                key = f"{visit_key}:{blob_id}"
                prev_dt = dt_by_key.get(key)
                if prev_dt is not None and (completed_at is None or completed_at <= prev_dt):
                    continue
                dt_by_key[key] = completed_at
                index[key] = {
                    "result": result,
                    "session_id": session.id,
                    "session_title": session.data.get("title", ""),
                    "completed_at": completed_at.isoformat() if completed_at else None,
                }
    return index


_AUDIT_CANCEL_FLAG_TTL = 3600


def _audit_cancel_flag_key(task_id: str) -> str:
    return f"audit_creation_cancelled:{task_id}"


def mark_audit_creation_cancelled(task_id: str) -> None:
    """Set a cross-process flag (Redis cache) so a running creation worker can
    cooperatively abort before it creates a session. Complements the Celery
    revoke, which can race session creation. No-op for a falsy task_id.
    """
    if task_id:
        cache.set(_audit_cancel_flag_key(task_id), True, timeout=_AUDIT_CANCEL_FLAG_TTL)


def is_audit_creation_cancelled(task_id: str) -> bool:
    """True if this audit-creation task has been flagged cancelled."""
    return bool(task_id) and bool(cache.get(_audit_cancel_flag_key(task_id)))


def _created_session_ids(info) -> list:
    """Extract created audit-session ids from a task result / job-record dict.

    The creation task records created sessions under the "sessions" key as a
    list of ``{"id", ...}`` dicts (see tasks.py). Older / AI-review payloads
    used a flat "session_ids" list. Accept both, plus a nested "result" dict,
    and de-duplicate. Used by cancel cleanup to delete the sessions an
    abandoned/cancelled creation actually created.
    """
    if not isinstance(info, dict):
        return []
    out: list = []
    sessions = info.get("sessions")
    if isinstance(sessions, list):
        for s in sessions:
            sid = s.get("id") if isinstance(s, dict) else s
            if sid and sid not in out:
                out.append(sid)
    for sid in info.get("session_ids") or []:
        if sid and sid not in out:
            out.append(sid)
    nested = info.get("result")
    if isinstance(nested, dict):
        for sid in _created_session_ids(nested):
            if sid not in out:
                out.append(sid)
    return out


def all_sessions_completed(sessions) -> bool:
    """True iff there is at least one session and every one is completed.

    Used to decide whether a workflow run backed by audit sessions is done —
    an audit run can span multiple sessions, so the run completes only when
    all of them are completed.
    """
    sessions = list(sessions)
    return bool(sessions) and all(getattr(s, "status", None) == "completed" for s in sessions)


def filter_out_prior_audited(all_visit_images: dict, prior_index: dict) -> tuple[dict, int]:
    """Drop images whose "<visit_id>:<blob_id>" is in prior_index.

    Returns (filtered_visit_images, excluded_count). Visits left with no images
    are removed from the result.
    """
    filtered: dict = {}
    excluded = 0
    for visit_key, images in all_visit_images.items():
        kept = [img for img in images if f"{visit_key}:{img.get('blob_id')}" not in prior_index]
        excluded += len(images) - len(kept)
        if kept:
            filtered[visit_key] = kept
    return filtered, excluded


def generate_audit_description(criteria: AuditCriteria) -> str:
    """Generate human-readable description of audit criteria."""
    parts = []

    if criteria.audit_type == "date_range":
        if criteria.start_date and criteria.end_date:
            parts.append(f"Visits from {criteria.start_date} to {criteria.end_date}")
        elif criteria.start_date:
            parts.append(f"Visits from {criteria.start_date}")
        elif criteria.end_date:
            parts.append(f"Visits until {criteria.end_date}")
        else:
            parts.append("All visits (date range)")
    elif criteria.audit_type == "last_n_per_flw":
        parts.append(f"Last {criteria.count_per_flw} visits per FLW")
    elif criteria.audit_type == "last_n_per_opp":
        parts.append(f"Last {criteria.count_per_opp} visits per opportunity")
    elif criteria.audit_type == "last_n_across_all":
        parts.append(f"Last {criteria.count_across_all} visits across all")
    else:
        parts.append(f"Audit type: {criteria.audit_type}")

    if criteria.sample_percentage < 100:
        parts.append(f"({criteria.sample_percentage}% sample)")

    return " ".join(parts)


# =============================================================================
# Main Data Access Class
# =============================================================================


class AuditDataAccess(BaseDataAccess):
    """
    Optimized data access layer for audit operations.

    Uses the AnalysisPipeline for raw data access (backend-agnostic),
    with optimized caching for memory efficiency.
    """

    def __init__(
        self,
        opportunity_id: int | None = None,
        organization_id: int | None = None,
        program_id: int | None = None,
        access_token: str | None = None,
        request: HttpRequest | None = None,
    ):
        super().__init__(
            opportunity_id=opportunity_id,
            organization_id=organization_id,
            program_id=program_id,
            request=request,
            access_token=access_token,
        )
        self._pipeline: AnalysisPipeline | None = None

    @property
    def pipeline(self) -> AnalysisPipeline:
        """Get or create AnalysisPipeline for raw data access."""
        if self._pipeline is None:
            if self.request is None:
                raise ValueError("Request required for pipeline access")
            self._pipeline = AnalysisPipeline(self.request)
        return self._pipeline

    # =========================================================================
    # Visit Fetching (via AnalysisPipeline)
    # =========================================================================

    def fetch_visits_slim(self, opportunity_id: int | None = None) -> list[dict]:
        """Fetch visits WITHOUT form_json (~20MB for 10k visits vs ~350MB)."""
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            raise ValueError("opportunity_id required")

        return self.pipeline.fetch_raw_visits(
            opportunity_id=opp_id,
            skip_form_json=True,
        )

    def fetch_visits_for_ids(self, visit_ids: list[int], opportunity_id: int | None = None) -> list[dict]:
        """Fetch visits WITH form_json for specific IDs only (chunked, memory efficient)."""
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            raise ValueError("opportunity_id required")

        return self.pipeline.fetch_raw_visits(
            opportunity_id=opp_id,
            filter_visit_ids=set(visit_ids),
            include_images=True,
        )

    # =========================================================================
    # Visit Selection (uses backend-optimized filtering)
    # =========================================================================

    def get_visit_ids_for_audit(
        self,
        opportunity_ids: list[int],
        audit_type: str | None = None,
        criteria: AuditCriteria | dict | None = None,
        visits_cache: dict[int, list[dict]] | None = None,
        return_visits: bool = False,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> list[int] | tuple[list[int], list[dict]]:
        """
        Get visit IDs matching audit criteria.

        Uses backend-optimized filtering:
        - SQL backend: Pushes filtering into PostgreSQL (much faster)
        - Python/Redis backend: Uses pandas on cached data

        Supports both old signature (audit_type + criteria dict) and new (AuditCriteria).

        Args:
            return_visits: If True, returns (visit_ids, filtered_visits) tuple to avoid re-fetching
            progress_callback: Optional callback for progress updates (processed, total, message).
        """
        # Handle both old and new calling patterns
        if criteria is None:
            criteria = AuditCriteria()
        elif isinstance(criteria, dict):
            # Merge audit_type into criteria dict if provided separately
            if audit_type and "audit_type" not in criteria:
                criteria["audit_type"] = audit_type
            criteria = AuditCriteria.from_dict(criteria)

        # Convert AuditCriteria to pipeline filter parameters
        # Map audit_type to appropriate filter parameters
        last_n_per_user = None
        last_n_total = None
        start_date = None
        end_date = None
        days_of_week = None

        if criteria.audit_type == "last_n_per_flw":
            last_n_per_user = criteria.count_per_flw
        elif criteria.audit_type == "last_n_across_all":
            last_n_total = criteria.count_across_all
        elif criteria.audit_type == "date_range":
            # Only apply date/weekday filters for date_range audit type
            start_date = criteria.start_date
            end_date = criteria.end_date
            days_of_week = criteria.days_of_week
        # Note: "last_n_per_opp" is handled at the aggregate level below

        # DEBUG: Log filter parameters
        logger.info(
            f"[get_visit_ids_for_audit] audit_type={criteria.audit_type}, "
            f"last_n_total={last_n_total}, last_n_per_user={last_n_per_user}, "
            f"count_across_all={criteria.count_across_all}, "
            f"opportunity_ids={opportunity_ids}"
        )

        all_visit_ids = []
        all_visits = []
        total_opps = len(opportunity_ids)

        for idx, opp_id in enumerate(opportunity_ids):
            # Report progress per opportunity
            if progress_callback:
                progress_callback(idx, total_opps, f"Fetching visits for opportunity {idx + 1}/{total_opps}...")
            # Use visits_cache if available (for backward compat)
            if visits_cache and opp_id in visits_cache:
                # Fall back to pandas filtering for cached data
                visits = visits_cache[opp_id]
                filtered = filter_visits_for_audit(visits, criteria, return_visits=True)
                visit_ids = [v["id"] for v in filtered]
                all_visit_ids.extend(visit_ids)
                if return_visits:
                    all_visits.extend(filtered)
            else:
                # Use backend-optimized filtering (SQL or pandas depending on backend)
                effective_last_n = last_n_total if len(opportunity_ids) == 1 else None
                logger.info(
                    f"[get_visit_ids_for_audit] Calling pipeline.filter_visits_for_audit: "
                    f"opp_id={opp_id}, last_n_total={effective_last_n}, "
                    f"num_opps={len(opportunity_ids)}"
                )
                result = self.pipeline.filter_visits_for_audit(
                    opportunity_id=opp_id,
                    usernames=criteria.selected_flw_user_ids or None,
                    start_date=start_date,
                    end_date=end_date,
                    last_n_per_user=last_n_per_user,
                    last_n_total=effective_last_n,
                    sample_percentage=criteria.sample_percentage if len(opportunity_ids) == 1 else 100,
                    deliver_unit_types=criteria.deliver_unit_types or None,
                    visit_statuses=criteria.visit_statuses or None,
                    days_of_week=days_of_week or None,
                    return_visit_data=return_visits,
                )
                if return_visits:
                    visit_ids, visits = result
                    all_visit_ids.extend(visit_ids)
                    all_visits.extend(visits)
                    logger.info(f"[get_visit_ids_for_audit] Backend returned {len(visit_ids)} visit IDs")
                else:
                    all_visit_ids.extend(result)
                    logger.info(f"[get_visit_ids_for_audit] Backend returned {len(result)} visit IDs")

        # Report final count
        if progress_callback:
            progress_callback(
                total_opps, total_opps, f"Found {len(all_visit_ids)} visits across {total_opps} opportunities"
            )

        # Apply last_n_per_opp filtering (works for single or multiple opportunities)
        if criteria.audit_type == "last_n_per_opp":
            # Group by opportunity and take N per opp
            # This requires post-filtering since the backend doesn't support per-opp limits
            if return_visits and all_visits:
                df = pd.DataFrame(all_visits)
                if "opportunity_id" in df.columns and "visit_date" in df.columns:
                    df["visit_date"] = pd.to_datetime(df["visit_date"], format="mixed", utc=True, errors="coerce")
                    df = df.sort_values("visit_date", ascending=False)
                    df = df.groupby("opportunity_id").head(criteria.count_per_opp)
                    if "visit_date" in df.columns:
                        df["visit_date"] = df["visit_date"].apply(lambda x: x.isoformat() if pd.notna(x) else None)
                    all_visits = df.to_dict("records")
                    all_visit_ids = [v["id"] for v in all_visits]
            elif not return_visits and all_visit_ids:
                # Need to fetch visit data to apply per-opp grouping
                # For now, use a simple limit as approximation for single opp
                if len(opportunity_ids) == 1:
                    all_visit_ids = all_visit_ids[: criteria.count_per_opp]

        # Apply cross-opportunity limits if multiple opportunities
        if len(opportunity_ids) > 1:
            if criteria.audit_type == "last_n_across_all":
                # Sort by date and take top N
                if return_visits and all_visits:
                    df = pd.DataFrame(all_visits)
                    if "visit_date" in df.columns:
                        df["visit_date"] = pd.to_datetime(df["visit_date"], format="mixed", utc=True, errors="coerce")
                        df = df.sort_values("visit_date", ascending=False).head(criteria.count_across_all)
                        if "visit_date" in df.columns:
                            df["visit_date"] = df["visit_date"].apply(lambda x: x.isoformat() if pd.notna(x) else None)
                        all_visits = df.to_dict("records")
                        all_visit_ids = [v["id"] for v in all_visits]
                elif not return_visits:
                    # Just limit the IDs
                    all_visit_ids = all_visit_ids[: criteria.count_across_all]

            # Apply sampling across all opportunities
            if criteria.sample_percentage < 100 and all_visit_ids:
                sample_size = max(1, int(len(all_visit_ids) * criteria.sample_percentage / 100))
                import random

                random.seed(42)
                sampled_indices = random.sample(range(len(all_visit_ids)), min(sample_size, len(all_visit_ids)))
                all_visit_ids = [all_visit_ids[i] for i in sorted(sampled_indices)]
                if return_visits:
                    all_visits = [all_visits[i] for i in sorted(sampled_indices)]

        if return_visits:
            return all_visit_ids, all_visits
        return all_visit_ids

    # =========================================================================
    # Visit Data Methods
    # =========================================================================

    def _fetch_visits_for_opportunity(self, opportunity_id: int) -> list[dict]:
        """Fetch all visits for an opportunity (with form_json for backward compat)."""
        return self.pipeline.fetch_raw_visits(opportunity_id=opportunity_id)

    def get_visit_data(
        self, visit_id: int, opportunity_id: int | None = None, visit_cache: dict | None = None
    ) -> dict | None:
        """Get detailed data for a single visit."""
        if visit_cache and visit_id in visit_cache:
            return visit_cache[visit_id]

        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            raise ValueError("opportunity_id required when visit_cache not provided")

        visits = self._fetch_visits_for_opportunity(opp_id)
        for visit in visits:
            if visit["id"] == visit_id:
                return visit

        return None

    def get_visits_batch(self, visit_ids: list[int], opportunity_id: int) -> list[dict]:
        """Batch fetch multiple visits.

        Normalizes to str for comparison since RawVisitCache.visit_id is a CharField
        (cache-hit visits return str ids) while callers pass int visit_ids (cache-miss
        visits return int ids from the raw API response) -- see the identical fix at
        ExperimentBulkAssessmentDataView.get's additional_case_info backfill.
        """
        all_visits = self._fetch_visits_for_opportunity(opportunity_id)
        visit_id_strs = {str(vid) for vid in visit_ids}
        return [v for v in all_visits if str(v["id"]) in visit_id_strs]

    # =========================================================================
    # Image Extraction (uses analysis pipeline's FieldComputation)
    # =========================================================================

    @staticmethod
    def _extract_field_value(data: dict, path: str) -> str | None:
        """
        Extract a value from form data using slash-separated path or field name search.

        Args:
            data: Form data dict to traverse
            path: Slash-separated path (e.g., "form/building_area") or just a field name
                  (e.g., "child_weight_visit") to search for in the tree

        Returns:
            Extracted value as string, or None if not found
        """
        if not path or not data:
            return None

        # Strip leading/trailing slashes
        path = path.strip("/")

        # If path contains slashes, try exact path traversal first
        if "/" in path:
            parts = path.split("/")
            current = data

            for part in parts:
                if not part:  # Skip empty parts
                    continue
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    current = None
                    break

            if current is not None and isinstance(current, (str, int, float, bool)):
                return str(current)

        # If exact path failed or no slashes, search the tree for the field name
        field_name = path.split("/")[-1] if "/" in path else path
        result = AuditDataAccess._find_field_in_tree(data, field_name)
        if result is not None:
            return str(result)

        return None

    @staticmethod
    def _find_field_in_tree(data: dict, field_name: str) -> str | int | float | bool | None:
        """
        Recursively search for a field name in a nested dict structure.

        Args:
            data: Dict to search
            field_name: Field name to find

        Returns:
            The first matching primitive value found, or None
        """
        if not isinstance(data, dict):
            return None

        # Check if field exists at this level
        if field_name in data:
            value = data[field_name]
            if isinstance(value, (str, int, float, bool)):
                return value

        # Recursively search nested dicts
        for key, value in data.items():
            if isinstance(value, dict):
                result = AuditDataAccess._find_field_in_tree(value, field_name)
                if result is not None:
                    return result

        return None

    def _add_related_fields_to_images(
        self,
        visit_images: dict[str, list],
        visit_dicts: list[dict],
        related_fields: list[dict],
    ) -> dict[str, list]:
        """
        Add related field values to extracted images.

        For each image, looks up related field rules that match the image's question_id
        and extracts the corresponding field values from the visit's form_json.

        Args:
            visit_images: Dict mapping visit_id to list of image dicts
            visit_dicts: List of visit dicts with form_json
            related_fields: List of {image_path, field_path, label} rules

        Returns:
            Updated visit_images with related_fields added to each image
        """
        if not related_fields:
            return visit_images

        # Build visit_id -> form_json lookup
        visit_form_data = {}
        for v in visit_dicts:
            vid = str(v.get("id", ""))
            form_json = v.get("form_json", {})
            # Get the form data (handle both direct and nested structures)
            visit_form_data[vid] = form_json.get("form", form_json)

        # Process each visit's images
        for visit_id, images in visit_images.items():
            form_data = visit_form_data.get(visit_id, {})

            for image in images:
                question_id = image.get("question_id")
                if not question_id:
                    image["related_fields"] = []
                    continue

                # Find matching related field rules and extract values
                image_related_fields = []
                for rule in related_fields:
                    if rule.get("image_path") == question_id:
                        field_path = rule.get("field_path", "")
                        value = self._extract_field_value(form_data, field_path)
                        if value is not None:
                            image_related_fields.append(
                                {
                                    "path": field_path,
                                    # Falls back to the raw field path when a
                                    # reviewer's config omits "label" -- see
                                    # connect_labs/audit/ai_review_config.py's
                                    # module docstring for how to set one.
                                    "label": rule.get("label") or field_path,
                                    "value": value,
                                }
                            )

                image["related_fields"] = image_related_fields

        return visit_images

    def extract_images_for_visits(
        self,
        visit_ids: list[int],
        opportunity_id: int | None = None,
        related_fields: list[dict] | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, list]:
        """
        Extract images with question IDs for selected visits.

        Uses the analysis pipeline's compute_visit_fields with custom extractor.
        Memory efficient - only loads form_json for selected visits.

        Args:
            visit_ids: List of visit IDs to extract images for
            opportunity_id: Optional opportunity ID (uses self.opportunity_id if not provided)
            related_fields: Optional list of related field rules to extract and attach to images.
                           Each rule is a dict with {image_path, field_path, label}.
            progress_callback: Optional callback for progress updates (processed, total, message).

        Returns:
            Dict mapping visit_id (str) to list of image dicts
        """
        if not visit_ids:
            return {}

        opp_id = opportunity_id or self.opportunity_id
        total_visits = len(visit_ids)

        # Report progress: fetching visits
        if progress_callback:
            progress_callback(0, total_visits, f"Fetching {total_visits} visits...")

        # Fetch visits WITH form_json for selected IDs only
        visit_dicts = self.fetch_visits_for_ids(visit_ids, opp_id)

        # Report progress: processing
        if progress_callback:
            progress_callback(0, total_visits, f"Processing {len(visit_dicts)} visits...")

        # Convert to LocalUserVisit for pipeline compatibility
        visits = [LocalUserVisit(v) for v in visit_dicts]

        # Use the analysis pipeline's compute_visit_fields with our audit config
        computed = compute_visit_fields(visits, AUDIT_EXTRACTION_CONFIG.fields)

        # Build result mapping with progress updates
        result = {}
        report_interval = max(1, total_visits // 20)  # Report every 5% or at least every visit

        for i, visit in enumerate(visits):
            visit_id = visit.id
            if computed and i < len(computed):
                images = computed[i].get("images_with_questions", [])
            else:
                images = []
            result[str(visit_id)] = images

            # Report progress periodically
            if progress_callback and (i + 1) % report_interval == 0:
                progress_callback(i + 1, total_visits, f"Extracted images from {i + 1}/{total_visits} visits")

        # Add empty lists for any visit_ids not found
        for vid in visit_ids:
            if str(vid) not in result:
                result[str(vid)] = []

        # Add related field values if rules provided
        if related_fields:
            if progress_callback:
                progress_callback(total_visits, total_visits, "Adding related fields...")
            result = self._add_related_fields_to_images(result, visit_dicts, related_fields)
            # Filter visits based on related field filter rules
            result = self._filter_visits_by_related_fields(result, related_fields)

        if progress_callback:
            progress_callback(total_visits, total_visits, f"Extracted images from {total_visits} visits")

        return result

    def _filter_visits_by_related_fields(
        self,
        visit_images: dict[str, list],
        related_fields: list[dict],
    ) -> dict[str, list]:
        """
        Filter visits based on related field filter rules.

        If any rule has filter_by_image=True, only include visits with that image.
        If any rule has filter_by_field=True, only include visits with that field value.

        Args:
            visit_images: Dict mapping visit_id to list of image dicts (with related_fields attached)
            related_fields: List of related field rules with filter options

        Returns:
            Filtered visit_images dict
        """
        # Check if any filtering is enabled
        filter_rules = [r for r in related_fields if r.get("filter_by_image") or r.get("filter_by_field")]
        if not filter_rules:
            return visit_images

        image_filter_paths = [r.get("image_path", "") for r in filter_rules if r.get("filter_by_image")]
        field_filter_rules = [r for r in filter_rules if r.get("filter_by_field")]

        filtered_result = {}
        for visit_id, images in visit_images.items():
            include_visit = True

            # OR logic: include visit if it has ANY of the required image types
            if image_filter_paths:
                question_ids = {img.get("question_id") for img in images}
                if not any(p in question_ids for p in image_filter_paths):
                    include_visit = False

            # AND logic: visit must satisfy every field filter rule
            if include_visit:
                for rule in field_filter_rules:
                    field_path = rule.get("field_path", "")
                    has_field_value = False
                    for img in images:
                        for rf in img.get("related_fields", []):
                            if rf.get("path") == field_path and rf.get("value"):
                                has_field_value = True
                                break
                        if has_field_value:
                            break
                    if not has_field_value:
                        include_visit = False
                        break

            if include_visit:
                if image_filter_paths:
                    filtered_images = [img for img in images if img.get("question_id") in image_filter_paths]
                    if filtered_images:
                        filtered_result[visit_id] = filtered_images
                else:
                    filtered_result[visit_id] = images

        return filtered_result

    # =========================================================================
    # Session Management
    # =========================================================================

    def create_audit_session(
        self,
        username: str,
        visit_ids: list[int],
        title: str,
        tag: str = "",
        opportunity_id: int | None = None,
        audit_type: str | None = None,
        criteria: AuditCriteria | dict | None = None,
        opportunity_name: str | None = None,  # Pass to avoid redundant API call
        visit_images: dict[str, list] | None = None,  # Pass pre-extracted images for batch operations
        related_fields: list[dict] | None = None,  # Related field rules for image extraction
        workflow_run_id: int | None = None,  # Optional link to workflow run that created this session
        pass_threshold: int = 100,  # Min % of assessments that must pass for the audit to pass overall
        visit_clusters: list[dict] | None = None,  # Optional visit-clustering groupings (see visit_clustering.py)
        has_ai_reviewer: bool = False,  # Whether any image in this session has an AI reviewer attached
    ) -> AuditSessionRecord:
        """
        Create an audit session with extracted image metadata.

        Sessions are self-contained and store their own criteria for traceability.
        If created from a workflow, workflow_run_id links to the workflow run record.
        If created from the wizard UI, workflow_run_id is None.

        Args:
            username: User creating the session
            visit_ids: List of visit IDs to include
            title: Session title
            tag: Optional tag for categorization
            opportunity_id: Opportunity ID
            audit_type: Type of audit (date_range, last_n_per_flw, etc.)
            criteria: AuditCriteria or dict with filter settings
            opportunity_name: Pre-fetched opportunity name (avoids API call)
            visit_images: Pre-extracted images dict (avoids re-extraction)
            related_fields: Related field rules for image extraction
            workflow_run_id: Optional workflow run ID if created from a workflow
            pass_threshold: Min % of assessments that must pass for the audit to pass overall (75-100)
            visit_clusters: Optional visit-clustering groupings; stored as-is, never computed here.
            has_ai_reviewer: Whether this session's images were ever eligible for AI review (any
                image_audits entry with a non-empty reviewers list). Used by the shared FLW
                breakdown widget to decide whether to show AI stats, instead of guessing from the
                track's display label.
        """
        opp_id = opportunity_id or self.opportunity_id

        # Get opportunity name (use passed value to avoid redundant API calls in batch operations)
        if opportunity_name is None:
            opportunity_name = ""
            if opp_id:
                opp_details = self.get_opportunity_details(opp_id)
                if opp_details:
                    opportunity_name = opp_details.get("name", "")

        # Generate description and normalize criteria
        description = ""
        criteria_dict = None
        if criteria:
            if isinstance(criteria, dict):
                if audit_type and "audit_type" not in criteria:
                    criteria["audit_type"] = audit_type
                criteria_obj = AuditCriteria.from_dict(criteria)
                criteria_dict = criteria  # Store original dict
            else:
                criteria_obj = criteria
                # Convert AuditCriteria to dict for storage
                criteria_dict = {
                    "audit_type": criteria_obj.audit_type,
                    "start_date": criteria_obj.start_date,
                    "end_date": criteria_obj.end_date,
                    "count_per_flw": criteria_obj.count_per_flw,
                    "count_per_opp": criteria_obj.count_per_opp,
                    "count_across_all": criteria_obj.count_across_all,
                    "sample_percentage": criteria_obj.sample_percentage,
                    "related_fields": criteria_obj.related_fields,
                    # Without these, AuditSessionRecord.to_summary_dict()'s
                    # visit_clustering_used always reads as "disabled" even
                    # when clustering genuinely produced this session's
                    # visit_clusters -- run_audit_creation (tasks.py) always
                    # passes an AuditCriteria object here, never a raw dict,
                    # so this branch is the one that actually runs in production.
                    "enable_time_gap": criteria_obj.enable_time_gap,
                    "time_gap_minutes": criteria_obj.time_gap_minutes,
                    "enable_distance": criteria_obj.enable_distance,
                    "distance_meters": criteria_obj.distance_meters,
                }
            description = generate_audit_description(criteria_obj)
            # Use related_fields from criteria if not passed directly
            if related_fields is None:
                related_fields = criteria_obj.related_fields

        # Extract images (use passed value to avoid redundant CSV parsing in batch operations)
        if visit_images is None:
            visit_images = self.extract_images_for_visits(visit_ids, opp_id, related_fields=related_fields)

        image_count = sum(len(imgs) for imgs in (visit_images or {}).values())

        data = {
            "title": title,
            "tag": tag,
            "status": "in_progress",
            "overall_result": None,
            "pass_threshold": pass_threshold,
            "notes": "",
            "kpi_notes": "",
            "visit_ids": visit_ids,
            "visit_results": {},
            "opportunity_id": opp_id,
            "opportunity_name": opportunity_name,
            "description": description,
            "visit_images": visit_images,
            "image_count": image_count,
            "related_fields": related_fields or [],  # Store config for reference
            "criteria": criteria_dict,  # Store criteria for traceability
            "visit_clusters": visit_clusters or [],
            "has_ai_reviewer": has_ai_reviewer,
        }

        record = self.labs_api.create_record(
            experiment="audit",
            type="AuditSession",
            data=data,
            labs_record_id=workflow_run_id,  # Link to workflow run (or None)
            username=username,
        )

        return AuditSessionRecord(
            {
                "id": record.id,
                "experiment": record.experiment,
                "type": record.type,
                "data": record.data,
                "username": record.username,
                "opportunity_id": record.opportunity_id,
                "organization_id": record.organization_id,
                "program_id": record.program_id,
                "labs_record_id": record.labs_record_id,
            }
        )

    def get_audit_session(self, session_id: int) -> AuditSessionRecord | None:
        """Fetch an audit session by id. **This is the only way to do it.**

        There used to be a ``try_multiple_opportunities`` flag guarding the
        cross-opportunity fallback. Every one of the eleven call sites passed
        ``True``, so the flag only ever offered callers a way to get the lookup
        wrong; it is gone, and the efficient path is the single path.

        Why a lookup by primary key needs a strategy at all: the production
        export API's GET handler authorizes and filters on whichever scope
        param it is given, and with no scope at all it serves only *public*
        records. An audit session is tagged with the opportunity it was created
        under, so one the caller isn't currently scoped to cannot be fetched by
        id alone — it has to be located first. (If a scope-free by-id endpoint
        lands on production, this method is the one place that has to change.)

        Resolution order, cheapest first:

        1. **Remembered location.** A session's storage opportunity is
           immutable, so it is memoised and the common case is one request.
        2. **Ambient scope**, for the ordinary "viewing a session in the
           opportunity that owns it" case.
        3. **A bounded sweep** of the caller's other opportunities. Both
           outcomes are memoised — the hit globally (where a record lives is
           the same fact for everyone), the miss per caller and briefly (a
           sweep only probes the caller's OWN opportunities, so "not found" is
           a statement about their access, not about the session).

        The sweep is what caused the 2026-07-29 incident: it was running per
        page-open with no memoisation and a fresh client — and therefore a
        fresh TLS handshake — per candidate opportunity, at ~700 requests/min
        against production Connect. It now reuses one pooled connection and
        runs at most once per session id per TTL.

        Memoising only the *hit* was not enough, and #1060 caught the rest on
        2026-07-30: 23,445 scoped probes in a day, peaking ~1,370/min, one
        session swept 447 times. A sweep that failed cached nothing and so
        re-ran in full on the very next request, and a caller who could not
        read the remembered opportunity evicted that shared entry for everyone
        else. Both are fixed here; between them they are why this endpoint kept
        re-paying a cost #1037 had already been written to remove.

        Only the session's LOCATION is ever cached, never the record itself.
        Every fetch still goes to the server with the caller's own token and
        the server still runs its per-user opportunity authorization, so a
        cache hit cannot widen what a user is allowed to read.
        """
        cache_key = _session_opp_cache_key(session_id)
        remembered_opp_id = cache.get(cache_key)

        if remembered_opp_id is not None:
            session = self._fetch_session(session_id, opportunity_id=remembered_opp_id)
            if session:
                return session
            # Fall through to a re-resolve, but do NOT evict the memo here. This
            # miss is per-caller: the entry is shared across users, and a user
            # who simply lacks access to the remembered opportunity gets None
            # too. Evicting on that let one unauthorized reader wipe the memo
            # for everyone who could use it, and two people on the same session
            # then thrashed it — each re-sweep repopulating, the other evicting.
            # A genuine relocation still self-heals: a successful resolve below
            # overwrites the entry, and it expires on its own regardless.

        session = self._fetch_session(session_id)
        if session:
            self._remember_session_location(session_id, session)
            return session

        # A sweep that finds nothing used to memoise nothing, so an unresolvable
        # session re-ran the entire fan-out on every single request — 23,445
        # scoped probes/day against production Connect by 2026-07-30 (#1060).
        miss_key = _session_miss_cache_key(session_id, self.access_token)
        if cache.get(miss_key):
            return None

        try:
            found = self._sweep_opportunities_for_session(session_id, skip=remembered_opp_id)
        except Exception:
            logger.debug("Cross-opportunity session search failed for session %s", session_id, exc_info=True)
            return None

        if found is None:
            cache.set(miss_key, True, _SESSION_MISS_CACHE_TTL)
        return found

    def _fetch_session(self, session_id: int, opportunity_id: int | None = None) -> AuditSessionRecord | None:
        """One round-trip for a session by id, optionally under an explicit scope."""
        return self.labs_api.get_record_by_id(
            session_id,
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
            opportunity_id=opportunity_id,
        )

    def _remember_session_location(
        self, session_id: int, session: AuditSessionRecord, found_under: int | None = None
    ) -> None:
        """Memoise where a session actually lives, read off the record itself.

        Reads ``storage_opportunity_id`` — where the record is FILED — not
        ``opportunity_id``, which on this class is the opportunity being
        *audited*. ``found_under`` is the scope the fetch succeeded under, used
        when the record carries no storage opportunity of its own.
        """
        storage_opp_id = _coerce_int(session.storage_opportunity_id)
        if storage_opp_id is None:
            storage_opp_id = found_under
        if storage_opp_id is not None:
            cache.set(_session_opp_cache_key(session_id), storage_opp_id, _SESSION_OPP_CACHE_TTL)

    def _sweep_opportunities_for_session(self, session_id: int, skip: int | None = None) -> AuditSessionRecord | None:
        """Last resort: probe the caller's other opportunities, then memoise the hit."""
        ambient_opp_id = _coerce_int(self.opportunity_id)

        for opp in self.search_opportunities(query="", limit=_SESSION_SEARCH_OPP_LIMIT):
            opp_id = _coerce_int(opp.get("id"))
            # Ambient scope and the remembered id were both already tried above.
            if opp_id is None or opp_id == ambient_opp_id or opp_id == skip:
                continue

            session = self._fetch_session(session_id, opportunity_id=opp_id)
            if session:
                self._remember_session_location(session_id, session, found_under=opp_id)
                return session

        return None

    def get_audit_sessions(
        self,
        username: str | None = None,
        status: str | None = None,
    ) -> list[AuditSessionRecord]:
        """Query audit sessions.

        Program-scoped callers (self.program_id set, no self.opportunity_id)
        get the union of every session across the program's member
        opportunities, not just sessions that happen to carry a program_id
        field themselves. Audit sessions are almost always created while a
        single OPPORTUNITY is selected — the common case — so they're tagged
        with opportunity_id only. The production API does a literal field
        match; it doesn't resolve the opportunity->program hierarchy. A plain
        program_id-scoped query therefore silently misses every session
        created under one of the program's opportunities, which is exactly
        the "some but not all audits show under the program" bug this fixes.
        """
        kwargs = {}
        if status:
            kwargs["status"] = status
        return self._query_audit_sessions(username=username, **kwargs)

    def get_prior_audited_images(self, opportunity_id, exclude_session_id=None) -> dict:
        """Prior-audit index for one opportunity, from its completed sessions.

        Filters to this opportunity even under program scope (get_audit_sessions
        fans out across a program's opportunities).
        """
        sessions = [s for s in self.get_audit_sessions() if s.opportunity_id == opportunity_id]
        return build_prior_audit_index(sessions, exclude_session_id=exclude_session_id)

    def _query_audit_sessions(self, username: str | None = None, **kwargs) -> list[AuditSessionRecord]:
        """Fetch every AuditSession record visible to this DataAccess's scope.

        Shared by get_audit_sessions() and get_sessions_by_workflow_run() —
        both need the full unfiltered set in scope before applying their own
        client-side filter (query params / labs_record_id match). See
        get_audit_sessions's docstring for why program-scoped callers fan out
        across member opportunities instead of a single scoped query.
        """
        if self.program_id and not self.opportunity_id:
            return self._get_audit_sessions_for_program(username=username, **kwargs)

        return self.labs_api.get_records(
            experiment="audit",
            type="AuditSession",
            username=username,
            model_class=AuditSessionRecord,
            **kwargs,
        )

    def _get_audit_sessions_for_program(self, username: str | None = None, **kwargs) -> list[AuditSessionRecord]:
        """Fan out across every opportunity in self.program_id and merge, deduped by id.

        Also includes anything already queryable under self.program_id
        directly, in case some sessions do carry that field.
        """
        from connect_labs.labs.context import get_org_data

        org_data = get_org_data(self.request)  # safe on request=None — returns {}
        opp_ids = [
            o.get("id")
            for o in org_data.get("opportunities", [])
            if o.get("program") == self.program_id and o.get("id") is not None
        ]

        by_id: dict[int, AuditSessionRecord] = {}

        for session in self.labs_api.get_records(
            experiment="audit",
            type="AuditSession",
            username=username,
            model_class=AuditSessionRecord,
            **kwargs,
        ):
            by_id[session.id] = session

        for opp_id in opp_ids:
            opp_access = AuditDataAccess(opportunity_id=opp_id, access_token=self.access_token)
            try:
                for session in opp_access.labs_api.get_records(
                    experiment="audit",
                    type="AuditSession",
                    username=username,
                    model_class=AuditSessionRecord,
                    **kwargs,
                ):
                    by_id[session.id] = session
            finally:
                opp_access.close()

        return list(by_id.values())

    def get_sessions_by_workflow_run(self, workflow_run_id: int) -> list[AuditSessionRecord]:
        """
        Get all audit sessions linked to a workflow run.

        Sessions created from a workflow have their labs_record_id pointing to
        the workflow run record. This method queries all sessions in scope
        and filters by that link — the API doesn't support filtering by
        labs_record_id server-side. Uses the same program-scoped fan-out as
        get_audit_sessions(): a multi-opp workflow run's linked sessions are
        each individually opportunity-tagged (whichever opp was active when
        that particular session was created), so a program-scoped caller
        needs every member opportunity's sessions, not just ones that
        happen to carry a program_id field themselves.

        Args:
            workflow_run_id: ID of the workflow run record

        Returns:
            List of AuditSessionRecord objects linked to the workflow run
        """
        all_sessions = self._query_audit_sessions()

        # Filter to sessions linked to this workflow run
        return [s for s in all_sessions if s.labs_record_id == workflow_run_id]

    def save_audit_session(self, session: AuditSessionRecord) -> AuditSessionRecord:
        """Persist an audit session, scoped to the session's OWN opportunity.

        Two things here defend against a cross-opportunity save silently
        failing (symptom: "Complete Review" doesn't stick):

        1. ``current_record=session`` — we already hold the fully-fetched
           record, so skip ``update_record``'s internal re-fetch. That
           re-fetch is scoped by the client's *ambient* opportunity_id —
           whatever the user's Django session last selected — not necessarily
           the opportunity that owns this session. When they differ the
           lookup returns nothing and update_record raises
           "Record {id} not found", aborting the whole save.
        2. A client scoped to the session's own STORAGE opportunity when that
           differs from the ambient scope. ``get_audit_session`` deliberately
           loads sessions from other opportunities, so the read side already
           crosses that boundary and the write side has to follow it —
           otherwise the labs-only/synthetic backend dispatch
           (``_is_labs_only()``, keyed on the client's opportunity_id) also
           routes to the wrong target.

        Note the storage/target distinction: ``AuditSessionRecord`` overrides
        ``opportunity_id`` with a property over ``data["opportunity_id"]`` —
        the opportunity being AUDITED — which shadows the LabsRecord's real
        storage scope. See ``_storage_record``.

        Same root cause as the workflow-side scope fix in #933; that one
        never reached the audit app.
        """
        current = _storage_record(session)
        session_opp_id = _coerce_int(current.opportunity_id)
        ambient_opp_id = _coerce_int(self.opportunity_id)

        labs_api = self.labs_api
        scoped_api = None
        if session_opp_id is not None and session_opp_id != ambient_opp_id:
            logger.info(
                "[Audit] Saving session %s under its own opportunity %s (ambient scope is %s)",
                session.id,
                session_opp_id,
                ambient_opp_id,
            )
            scoped_api = LabsRecordAPIClient(self.access_token, session_opp_id)
            labs_api = scoped_api

        try:
            updated = labs_api.update_record(
                record_id=session.id,
                experiment="audit",
                type="AuditSession",
                data=session.data,
                username=session.username,
                current_record=current,
            )
        finally:
            if scoped_api is not None:
                scoped_api.close()

        return AuditSessionRecord(
            {
                "id": updated.id,
                "experiment": updated.experiment,
                "type": updated.type,
                "data": updated.data,
                "username": updated.username,
                "opportunity_id": updated.opportunity_id,
                "organization_id": updated.organization_id,
                "program_id": updated.program_id,
                "labs_record_id": updated.labs_record_id,
            }
        )

    def complete_audit_session(
        self,
        session: AuditSessionRecord,
        overall_result: str,
        notes: str = "",
        kpi_notes: str = "",
    ) -> AuditSessionRecord:
        session.data["status"] = "completed"
        session.data["overall_result"] = overall_result
        session.data["notes"] = notes
        session.data["kpi_notes"] = kpi_notes
        return self.save_audit_session(session)

    # =========================================================================
    # Opportunity/Image APIs
    # =========================================================================

    def get_opportunity_details(self, opportunity_id: int) -> dict | None:
        url = f"{self.production_url}/export/opp_org_program_list/"
        try:
            response = self.http_client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"[Audit] HTTP {e.response.status_code} fetching opportunity details: {e}")
            return None
        except httpx.RequestError as e:
            logger.error(f"[Audit] Request error fetching opportunity details: {e}")
            return None

        for opp in response.json().get("opportunities", []):
            if opp.get("id") == opportunity_id:
                return opp
        return None

    def search_opportunities(self, query: str = "", limit: int = 100, program_id: int | None = None) -> list[dict]:
        """Search for opportunities."""
        url = f"{self.production_url}/export/opp_org_program_list/"
        try:
            response = self.http_client.get(url)
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"[Audit] HTTP {e.response.status_code} searching opportunities: {e}")
            return []
        except httpx.RequestError as e:
            logger.error(f"[Audit] Request error searching opportunities: {e}")
            return []

        results = []
        query_lower = query.lower().strip()

        for opp in response.json().get("opportunities", []):
            # Filter by program_id if provided
            if program_id and opp.get("program") != program_id:
                continue

            if query_lower:
                if not (
                    (query_lower.isdigit() and int(query_lower) == opp.get("id"))
                    or query_lower in opp.get("name", "").lower()
                ):
                    continue
            results.append(opp)
            if len(results) >= limit:
                break

        return results

    # Bounded retry for the image proxy. Transient upstream hiccups (connection
    # resets, 5xx) are retried with exponential backoff; 4xx responses fail fast
    # (retrying a genuine "not found" / "forbidden" only wastes time).
    IMAGE_DOWNLOAD_MAX_ATTEMPTS = 3
    IMAGE_DOWNLOAD_BACKOFF_BASE = 0.3  # seconds; 0.3, 0.6, ...

    def download_image_from_connect(self, blob_id: str, opportunity_id: int) -> bytes:
        """Download image from Connect API, retrying transient upstream failures."""
        last_exc: Exception | None = None
        for attempt in range(1, self.IMAGE_DOWNLOAD_MAX_ATTEMPTS + 1):
            try:
                response = self.http_client.get(
                    f"{self.production_url}/export/opportunity/{opportunity_id}/image/",
                    params={"blob_id": blob_id},
                )
                response.raise_for_status()
                return response.content
            except httpx.HTTPStatusError as e:
                status = e.response.status_code
                # Client errors are not going to fix themselves on retry.
                if status < 500:
                    logger.error(
                        f"[Audit] HTTP {status} downloading image blob_id={blob_id} opp={opportunity_id}: {e}"
                    )
                    raise ImageDownloadError(f"Failed to download image (HTTP {status})", status_code=status) from e
                last_exc = e
                logger.warning(
                    f"[Audit] HTTP {status} downloading image blob_id={blob_id} opp={opportunity_id} "
                    f"(attempt {attempt}/{self.IMAGE_DOWNLOAD_MAX_ATTEMPTS})"
                )
            except httpx.RequestError as e:
                last_exc = e
                logger.warning(
                    f"[Audit] Request error downloading image blob_id={blob_id} opp={opportunity_id} "
                    f"(attempt {attempt}/{self.IMAGE_DOWNLOAD_MAX_ATTEMPTS}): {e}"
                )
            if attempt < self.IMAGE_DOWNLOAD_MAX_ATTEMPTS:
                time.sleep(self.IMAGE_DOWNLOAD_BACKOFF_BASE * (2 ** (attempt - 1)))

        logger.error(
            f"[Audit] Giving up on image blob_id={blob_id} opp={opportunity_id} "
            f"after {self.IMAGE_DOWNLOAD_MAX_ATTEMPTS} attempts: {last_exc}"
        )
        if isinstance(last_exc, httpx.HTTPStatusError):
            status = last_exc.response.status_code
            raise ImageDownloadError(f"Failed to download image (HTTP {status})", status_code=status) from last_exc
        raise ImageDownloadError("Failed to download image due to a connection error") from last_exc

    def get_attachment_signed_url(self, blob_id: str, opportunity_id: int) -> str | None:
        """Resolve a world-readable, time-limited URL for blob_id via the
        /export/opportunity/<id>/attachment_signed_url/ endpoint (commcare-connect
        PR #1415) -- used by Duplicate Detection to hand images directly to the
        external gateway instead of round-tripping bytes through labs.

        Returns None (caller skips this blob) on any failure -- this endpoint
        isn't live on prod Connect until PR #1415's deploy, so errors are
        expected and logged rather than raised.
        """
        try:
            response = self.http_client.get(
                f"{self.production_url}/export/opportunity/{opportunity_id}/attachment_signed_url/",
                params={"blob_id": blob_id},
            )
            response.raise_for_status()
            return response.json().get("attachment_signed_url")
        except httpx.HTTPError as e:
            logger.warning(f"[Audit] Failed to get signed URL for blob_id={blob_id} opp={opportunity_id}: {e}")
            return None

    def get_flw_names(self, opportunity_id: int | None = None) -> dict[str, str]:
        """
        Get FLW display names for the opportunity.

        Convenience method that uses the shared fetch_flw_names utility.

        Args:
            opportunity_id: Opportunity ID (defaults to self.opportunity_id)

        Returns:
            Dictionary mapping username to display name.
            Falls back to username if display name is empty.
            Example: {"e5e685ae3f024fb6848d0d87138d526f": "John Doe"}
        """
        from connect_labs.labs.analysis import fetch_flw_names

        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            logger.warning("[FLWNames] No opportunity ID provided")
            return {}

        try:
            return fetch_flw_names(self.access_token, opp_id)
        except Exception as e:
            logger.warning(f"[FLWNames] Failed to fetch FLW names for opportunity {opp_id}: {e}")
            return {}

    def get_deliver_unit_types(self, opportunity_id: int | None = None) -> list[str]:
        """
        Get distinct deliver unit types (form.@name) seen in an opportunity's visits.

        Used to populate the "Deliver Unit Type" filter dropdown in the audit
        creation wizard. Backed by the raw visit cache (populated on demand).
        Connect never exposes a deliver-unit name (only the numeric FK id), so
        this uses the submitted form's own display name as a proxy.

        Returns:
            List of deliver unit type names.
        """
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            return []

        try:
            return self.pipeline.get_deliver_unit_types_for_opportunity(opp_id)
        except Exception as e:
            logger.warning(f"[DeliverUnitTypes] Failed to fetch deliver unit types for opportunity {opp_id}: {e}")
            return []

    # =========================================================================
    # Audit Creation Job Management (for async creation tracking)
    # =========================================================================

    def create_audit_creation_job(
        self,
        username: str,
        task_id: str,
        title: str,
        criteria: dict,
        opportunities: list[dict],
    ) -> dict:
        """Create an audit creation job record for tracking async creation."""
        from datetime import datetime, timezone

        data = {
            "task_id": task_id,
            "title": title,
            "status": "pending",
            "criteria": criteria,
            "opportunities": opportunities,
            "progress": {
                "current_stage": 0,
                "total_stages": 4,
                "stage_name": "",
                "message": "Starting...",
                "processed": 0,
                "total": 0,
            },
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        record = self.labs_api.create_record(
            experiment="audit",
            type="AuditCreationJob",
            data=data,
            username=username,
        )

        return {
            "id": record.id,
            "task_id": task_id,
            "data": record.data,
        }

    def get_audit_creation_jobs(
        self,
        username: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Get audit creation jobs, optionally filtered by username or status."""
        from connect_labs.labs.models import LocalLabsRecord

        records = self.labs_api.get_records(
            experiment="audit",
            type="AuditCreationJob",
            username=username,
            model_class=LocalLabsRecord,
        )

        jobs = []
        for record in records:
            job_data = record.data
            # Filter by status if specified
            if status and job_data.get("status") != status:
                continue
            jobs.append(
                {
                    "id": record.id,
                    "task_id": job_data.get("task_id"),
                    "title": job_data.get("title"),
                    "status": job_data.get("status"),
                    "progress": job_data.get("progress", {}),
                    "result": job_data.get("result"),
                    "error": job_data.get("error"),
                    "created_at": job_data.get("created_at"),
                    "updated_at": job_data.get("updated_at"),
                }
            )

        return jobs

    def get_audit_creation_job_by_task_id(self, task_id: str) -> dict | None:
        """Get an audit creation job by its Celery task ID."""
        from connect_labs.labs.models import LocalLabsRecord

        records = self.labs_api.get_records(
            experiment="audit",
            type="AuditCreationJob",
            model_class=LocalLabsRecord,
        )

        for record in records:
            if record.data.get("task_id") == task_id:
                return {
                    "id": record.id,
                    "task_id": task_id,
                    "data": record.data,
                }
        return None

    def update_audit_creation_job(
        self,
        job_id: int,
        username: str,
        status: str | None = None,
        progress: dict | None = None,
        result: dict | None = None,
        error: str | None = None,
    ) -> dict | None:
        """Update an audit creation job record."""
        from datetime import datetime, timezone

        from connect_labs.labs.models import LocalLabsRecord

        # Get current record
        records = self.labs_api.get_records(
            experiment="audit",
            type="AuditCreationJob",
            model_class=LocalLabsRecord,
        )

        current_record = None
        for record in records:
            if record.id == job_id:
                current_record = record
                break

        if not current_record:
            return None

        # Update fields
        data = current_record.data
        if status is not None:
            data["status"] = status
        if progress is not None:
            data["progress"] = progress
        if result is not None:
            data["result"] = result
        if error is not None:
            data["error"] = error
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

        # Save
        updated = self.labs_api.update_record(
            record_id=job_id,
            experiment="audit",
            type="AuditCreationJob",
            data=data,
            username=username,
        )

        return {
            "id": updated.id,
            "task_id": data.get("task_id"),
            "data": updated.data,
        }

    def delete_audit_creation_job(self, job_id: int) -> bool:
        """Delete an audit creation job record."""
        try:
            self.labs_api.delete_record(job_id)
            return True
        except Exception as e:
            logger.warning("Failed to delete audit creation job %s: %s", job_id, e)
            return False

    def delete_audit_session(self, session_id: int) -> bool:
        """Delete an audit session record."""
        try:
            self.labs_api.delete_record(session_id)
            logger.info(f"[AuditDataAccess] Deleted session {session_id}")
            return True
        except Exception as e:
            logger.warning(f"[AuditDataAccess] Failed to delete session {session_id}: {e}")
            return False

    def cancel_audit_creation(
        self,
        task_id: str | None = None,
        job_id: int | None = None,
        cleanup_objects: bool = True,
    ) -> dict:
        """
        Cancel an audit creation task and optionally clean up created objects.

        Can be called with either task_id or job_id. If job_id is provided,
        the task_id is looked up from the job record.

        Args:
            task_id: Celery task ID (optional if job_id provided)
            job_id: AuditCreationJob record ID (optional)
            cleanup_objects: Whether to delete created sessions

        Returns:
            Dict with cancellation results:
            - success: bool
            - task_id: str (the task that was cancelled)
            - previous_state: str (Celery state before cancellation)
            - cleaned_up: list of cleaned up object IDs
            - error: str (if failed)
        """
        from celery.result import AsyncResult

        from config.celery_app import app as celery_app
        from connect_labs.labs.models import LocalLabsRecord

        result = {
            "success": False,
            "task_id": task_id,
            "previous_state": None,
            "cleaned_up": [],
            "job_deleted": False,
        }

        try:
            # If job_id provided, look up the task_id and validate status
            job_record = None
            if job_id:
                records = self.labs_api.get_records(
                    experiment="audit",
                    type="AuditCreationJob",
                    model_class=LocalLabsRecord,
                )
                for record in records:
                    if record.id == job_id:
                        job_record = record
                        break

                if not job_record:
                    result["error"] = "Job not found"
                    return result

                task_id = job_record.data.get("task_id")
                result["task_id"] = task_id
                current_status = job_record.data.get("status")

                # Only allow cancelling pending/running jobs
                if current_status not in ("pending", "running"):
                    result["error"] = f"Cannot cancel job with status '{current_status}'"
                    return result

            if not task_id:
                result["error"] = "No task_id provided or found"
                return result

            # Set the cooperative-cancel flag FIRST so a worker mid-run can
            # abort before creating a session (the revoke below can race it).
            mark_audit_creation_cancelled(task_id)

            # Check task state and revoke if running
            celery_result = AsyncResult(task_id)
            state = celery_result.state
            result["previous_state"] = state

            if state in ("PENDING", "STARTED", "PROGRESS"):
                celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")
                logger.info(f"[CancelAudit] Revoked Celery task {task_id}")

            # Clean up created sessions if requested
            if cleanup_objects:
                # celery_result.info may be an exception object if task failed,
                # so we need to check it's actually a dict before calling .get()
                # The task records created sessions under the "sessions" key
                # (list of {"id", ...} dicts); read that, with legacy/nested
                # fallbacks. (The old code read a "session_ids" key the task
                # never writes, so cleanup was a silent no-op and orphaned the
                # session.)
                task_info = celery_result.info if isinstance(celery_result.info, dict) else {}
                session_ids = _created_session_ids(task_info)

                # Also check the job record's stored payload if available
                if not session_ids and job_record:
                    session_ids = _created_session_ids(job_record.data or {})

                # Delete sessions
                for session_id in session_ids:
                    if self.delete_audit_session(session_id):
                        result["cleaned_up"].append("session:" + str(session_id))

            # Delete job record if job_id was provided
            if job_id:
                if self.delete_audit_creation_job(job_id):
                    result["job_deleted"] = True
                    result["cleaned_up"].append("job:" + str(job_id))

            result["success"] = True
            logger.info(f"[CancelAudit] Cancelled task {task_id}, " f"cleaned up: {result['cleaned_up']}")
            return result

        except Exception as e:
            logger.error(f"[CancelAudit] Error cancelling task {task_id}: {e}")
            result["error"] = str(e)
            return result
