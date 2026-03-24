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

import logging
from collections.abc import Callable
from dataclasses import dataclass

import httpx
import pandas as pd
from django.http import HttpRequest

from commcare_connect.audit.analysis_config import AUDIT_EXTRACTION_CONFIG
from commcare_connect.audit.models import AuditSessionRecord
from commcare_connect.labs.analysis.computations import compute_visit_fields
from commcare_connect.labs.analysis.models import LocalUserVisit
from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.workflow.data_access import BaseDataAccess

logger = logging.getLogger(__name__)


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
    related_fields: list[dict] | None = None  # List of {image_path, field_path, label}

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

        return cls(
            audit_type=data.get("audit_type") or data.get("type", "date_range"),
            start_date=data.get("start_date") or data.get("startDate"),
            end_date=data.get("end_date") or data.get("endDate"),
            count_per_flw=data.get("count_per_flw") or data.get("countPerFlw", 10),
            count_per_opp=data.get("count_per_opp") or data.get("countPerOpp", 10),
            count_across_all=data.get("count_across_all") or data.get("countAcrossAll", 100),
            sample_percentage=data.get("sample_percentage") or data.get("samplePercentage", 100),
            selected_flw_user_ids=data.get("selected_flw_user_ids") or data.get("selected_usernames", []),
            related_fields=related_fields or None,
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

        if criteria.audit_type == "last_n_per_flw":
            last_n_per_user = criteria.count_per_flw
        elif criteria.audit_type == "last_n_across_all":
            last_n_total = criteria.count_across_all
        elif criteria.audit_type == "date_range":
            # Only apply date filters for date_range audit type
            start_date = criteria.start_date
            end_date = criteria.end_date
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
        """Batch fetch multiple visits."""
        all_visits = self._fetch_visits_for_opportunity(opportunity_id)
        visit_id_set = set(visit_ids)
        return [v for v in all_visits if v["id"] in visit_id_set]

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

    def get_audit_session(
        self, session_id: int, try_multiple_opportunities: bool = False
    ) -> AuditSessionRecord | None:
        """Get an audit session by ID."""
        # First try with current opportunity_id
        sessions = self.labs_api.get_records(
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
        )

        for session in sessions:
            if session.id == session_id:
                return session

        # If not found and try_multiple_opportunities is True, search other opportunities
        if try_multiple_opportunities:
            try:
                opportunities = self.search_opportunities(query="", limit=1000)

                for opp in opportunities:
                    opp_id = opp.get("id")
                    if opp_id == self.opportunity_id:
                        continue

                    temp_labs_api = LabsRecordAPIClient(self.access_token, opp_id)
                    try:
                        sessions = temp_labs_api.get_records(
                            experiment="audit",
                            type="AuditSession",
                            model_class=AuditSessionRecord,
                        )
                        for session in sessions:
                            if session.id == session_id:
                                return session
                    finally:
                        temp_labs_api.close()
            except Exception:
                logger.debug("Cross-opportunity session search failed for session %s", session_id)

        return None

    def get_audit_sessions(
        self,
        username: str | None = None,
        status: str | None = None,
    ) -> list[AuditSessionRecord]:
        """Query audit sessions."""
        kwargs = {}
        if status:
            kwargs["status"] = status

        return self.labs_api.get_records(
            experiment="audit",
            type="AuditSession",
            username=username,
            model_class=AuditSessionRecord,
            **kwargs,
        )

    def get_sessions_by_workflow_run(self, workflow_run_id: int) -> list[AuditSessionRecord]:
        """
        Get all audit sessions linked to a workflow run.

        Sessions created from a workflow have their labs_record_id pointing to
        the workflow run record. This method queries all sessions and filters
        by that link.

        Args:
            workflow_run_id: ID of the workflow run record

        Returns:
            List of AuditSessionRecord objects linked to the workflow run
        """
        # Get all sessions (the API doesn't support filtering by labs_record_id)
        all_sessions = self.labs_api.get_records(
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
        )

        # Filter to sessions linked to this workflow run
        return [s for s in all_sessions if s.labs_record_id == workflow_run_id]

    def save_audit_session(self, session: AuditSessionRecord) -> AuditSessionRecord:
        updated = self.labs_api.update_record(
            record_id=session.id,
            experiment="audit",
            type="AuditSession",
            data=session.data,
            username=session.username,
        )

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

    def download_image_from_connect(self, blob_id: str, opportunity_id: int) -> bytes:
        """Download image from Connect API."""
        try:
            response = self.http_client.get(
                f"{self.production_url}/export/opportunity/{opportunity_id}/image/",
                params={"blob_id": blob_id},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(
                f"[Audit] HTTP {e.response.status_code} downloading image blob_id={blob_id} "
                f"opp={opportunity_id}: {e}"
            )
            raise ValueError(f"Failed to download image (HTTP {e.response.status_code})") from e
        except httpx.RequestError as e:
            logger.error(f"[Audit] Request error downloading image blob_id={blob_id} opp={opportunity_id}: {e}")
            raise ValueError("Failed to download image due to a connection error") from e
        return response.content

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
        from commcare_connect.labs.analysis import fetch_flw_names

        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            logger.warning("[FLWNames] No opportunity ID provided")
            return {}

        try:
            return fetch_flw_names(self.access_token, opp_id)
        except Exception as e:
            logger.warning(f"[FLWNames] Failed to fetch FLW names for opportunity {opp_id}: {e}")
            return {}

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
        from commcare_connect.labs.models import LocalLabsRecord

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
        from commcare_connect.labs.models import LocalLabsRecord

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

        from commcare_connect.labs.models import LocalLabsRecord

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

        from commcare_connect.labs.models import LocalLabsRecord
        from config.celery_app import app as celery_app

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
                task_info = celery_result.info if isinstance(celery_result.info, dict) else {}
                session_ids = task_info.get("session_ids", [])

                # Also check job record for IDs if available
                if job_record:
                    job_data = job_record.data or {}
                    if not session_ids:
                        session_ids = job_data.get("session_ids", [])

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
