"""
Result container models for analysis framework.

Provides dataclasses for storing analysis results and the LocalUserVisit proxy.
"""

import ast
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd

from commcare_connect.labs.analysis.utils import extract_json_path

logger = logging.getLogger(__name__)


class LocalUserVisit:
    """
    Proxy wrapper for UserVisit data from API.

    Provides property-based access to visit fields including form_json.
    Includes lazy-parsed GPS coordinates from form_json.metadata.location.
    """

    def __init__(self, data: dict):
        """
        Initialize from CSV row or dict.

        Args:
            data: Dictionary of visit data from API
        """
        self._data = data
        # GPS fields (lazy parsed)
        self._latitude: float | None = None
        self._longitude: float | None = None
        self._accuracy: float | None = None
        self._gps_parsed: bool = False

    @property
    def id(self) -> str:
        return str(self._data.get("id", ""))

    @property
    def user_id(self) -> int | None:
        user_id = self._data.get("user_id")
        return int(user_id) if user_id else None

    @property
    def username(self) -> str:
        return self._data.get("username", "")

    @property
    def commcare_userid(self) -> str:
        """CommCare user ID from form.meta.userID in form_json."""
        form_json = self.form_json
        return form_json.get("form", {}).get("meta", {}).get("userID", "")

    @property
    def deliver_unit_id(self) -> int | None:
        du_id = self._data.get("deliver_unit_id")
        return int(du_id) if du_id else None

    @property
    def deliver_unit_name(self) -> str:
        return self._data.get("deliver_unit", "")

    @property
    def entity_id(self) -> str:
        return str(self._data.get("entity_id", ""))

    @property
    def entity_name(self) -> str:
        return self._data.get("entity_name", "")

    @property
    def visit_date(self) -> pd.Timestamp | None:
        date_str = self._data.get("visit_date")
        if date_str:
            return pd.to_datetime(date_str)
        return None

    @property
    def status(self) -> str:
        return self._data.get("status", "")

    @property
    def flagged(self) -> bool:
        return bool(self._data.get("flagged", False))

    def _parse_gps(self) -> None:
        """
        Lazy parse GPS coordinates from form_json.metadata.location.

        Location format: "latitude longitude altitude accuracy"
        Example: "12.9716 77.5946 0.0 10.0"
        """
        if self._gps_parsed:
            return

        self._gps_parsed = True

        try:
            form_json = self.form_json
            location_str = form_json.get("metadata", {}).get("location", "")

            if location_str:
                parts = location_str.split()
                self._latitude = float(parts[0]) if len(parts) > 0 else None
                self._longitude = float(parts[1]) if len(parts) > 1 else None
                self._accuracy = float(parts[3]) if len(parts) > 3 else None
        except (ValueError, TypeError, AttributeError) as e:
            logger.debug(f"Failed to parse GPS for visit {self.id}: {e}")

    @property
    def latitude(self) -> float | None:
        """Latitude from form_json.metadata.location (lazy parsed)."""
        self._parse_gps()
        return self._latitude

    @property
    def longitude(self) -> float | None:
        """Longitude from form_json.metadata.location (lazy parsed)."""
        self._parse_gps()
        return self._longitude

    @property
    def accuracy_in_m(self) -> float | None:
        """GPS accuracy in meters from form_json.metadata.location (lazy parsed)."""
        self._parse_gps()
        return self._accuracy

    @property
    def has_gps(self) -> bool:
        """Check if visit has valid GPS coordinates."""
        self._parse_gps()
        return self._latitude is not None and self._longitude is not None

    @property
    def form_json(self) -> dict:
        """
        Get parsed form_json.

        Handles both dict (already parsed) and string (needs parsing).
        NOTE: API may return Python repr format (single quotes) instead of JSON.
        """
        form_json = self._data.get("form_json", {})
        if isinstance(form_json, str):
            # First try json.loads for valid JSON
            try:
                form_json = json.loads(form_json)
            except json.JSONDecodeError:
                # Fall back to ast.literal_eval for Python dict repr format
                try:
                    form_json = ast.literal_eval(form_json)
                except (ValueError, SyntaxError):
                    logger.warning(f"Failed to parse form_json for visit {self.id}")
                    form_json = {}
        return form_json

    def extract_field(self, path: str) -> Any:
        """
        Extract field from form_json using dot notation path.

        Args:
            path: Dot-separated path (e.g., "form.building_count")

        Returns:
            Extracted value or None
        """
        return extract_json_path(self.form_json, path)

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "deliver_unit_id": self.deliver_unit_id,
            "deliver_unit_name": self.deliver_unit_name,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "visit_date": self.visit_date.isoformat() if self.visit_date else None,
            "status": self.status,
            "flagged": self.flagged,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "accuracy_in_m": self.accuracy_in_m,
            "form_json": self.form_json,
        }


@dataclass
class AnalysisResult:
    """
    Base container for analysis results.

    Attributes:
        opportunity_id: Opportunity ID for this analysis
        opportunity_name: Opportunity name
        rows: List of result rows (dicts or dataclass instances)
        metadata: Additional metadata about the analysis
        computed_at: When this analysis was computed
        row_count: Number of rows in results
    """

    opportunity_id: int | None = None
    opportunity_name: str | None = None
    rows: list[dict | Any] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=datetime.now)

    @property
    def row_count(self) -> int:
        """Number of rows in the result."""
        return len(self.rows)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "opportunity_id": self.opportunity_id,
            "opportunity_name": self.opportunity_name,
            "rows": [row if isinstance(row, dict) else row.__dict__ for row in self.rows],
            "metadata": self.metadata,
            "computed_at": self.computed_at.isoformat(),
            "row_count": self.row_count,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "AnalysisResult":
        """Create from dictionary."""
        return cls(
            opportunity_id=data.get("opportunity_id"),
            opportunity_name=data.get("opportunity_name"),
            rows=data.get("rows", []),
            metadata=data.get("metadata", {}),
            computed_at=datetime.fromisoformat(data["computed_at"]) if "computed_at" in data else datetime.now(),
        )


@dataclass
class FLWRow:
    """
    Analysis row for FLW-level analysis (one row per worker).

    Standard fields included for all FLW analyses:
    - Identification: username, user_id, flw_name
    - Visit counts: total_visits, approved_visits, pending_visits, rejected_visits
    - Date tracking: first_visit_date, last_visit_date, dates_active
    - Custom fields: Any additional fields from FieldComputations

    Custom fields are stored in the custom_fields dict and can be accessed via getattr.
    """

    # Core identification
    username: str
    user_id: int | None = None
    flw_name: str | None = None

    # Visit counts
    total_visits: int = 0
    approved_visits: int = 0
    pending_visits: int = 0
    rejected_visits: int = 0
    flagged_visits: int = 0

    # Date tracking
    first_visit_date: date | None = None
    last_visit_date: date | None = None
    dates_active: list[date] = field(default_factory=list)

    # Custom computed fields from config
    custom_fields: dict[str, Any] = field(default_factory=dict)

    @property
    def days_active(self) -> int:
        """Number of unique days this FLW was active."""
        return len(self.dates_active)

    @property
    def date_range_days(self) -> int | None:
        """Number of days between first and last visit."""
        if not self.first_visit_date or not self.last_visit_date:
            return None
        return (self.last_visit_date - self.first_visit_date).days

    @property
    def approval_rate(self) -> float:
        """Percentage of visits that were approved."""
        if self.total_visits == 0:
            return 0.0
        return (self.approved_visits / self.total_visits) * 100

    def __getattr__(self, name: str) -> Any:
        """Allow accessing custom fields as attributes."""
        if name == "custom_fields":
            # Avoid recursion
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return self.custom_fields.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Store unknown attributes in custom_fields."""
        # Check if this is a known field
        if name in self.__dataclass_fields__:
            object.__setattr__(self, name, value)
        else:
            # Store in custom_fields
            if not hasattr(self, "custom_fields"):
                object.__setattr__(self, "custom_fields", {})
            self.custom_fields[name] = value

    def to_dict(self) -> dict:
        """Convert to dictionary, merging custom fields."""
        result = {
            "username": self.username,
            "user_id": self.user_id,
            "flw_name": self.flw_name,
            "total_visits": self.total_visits,
            "approved_visits": self.approved_visits,
            "pending_visits": self.pending_visits,
            "rejected_visits": self.rejected_visits,
            "flagged_visits": self.flagged_visits,
            "first_visit_date": self.first_visit_date.isoformat() if self.first_visit_date else None,
            "last_visit_date": self.last_visit_date.isoformat() if self.last_visit_date else None,
            "dates_active": [d.isoformat() for d in self.dates_active],
            "days_active": self.days_active,
            "date_range_days": self.date_range_days,
            "approval_rate": self.approval_rate,
        }
        # Merge custom fields
        result.update(self.custom_fields)
        return result


@dataclass
class FLWAnalysisResult(AnalysisResult):
    """
    Specialized result container for FLW analysis.

    Rows are guaranteed to be FLWRow instances.
    """

    rows: list[FLWRow] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "FLWAnalysisResult":
        """Create from dictionary, properly deserializing FLWRow objects."""
        rows = []
        for row_data in data.get("rows", []):
            if isinstance(row_data, FLWRow):
                rows.append(row_data)
            elif isinstance(row_data, dict):
                # Reconstruct FLWRow from dict
                rows.append(
                    FLWRow(
                        username=row_data.get("username", ""),
                        user_id=row_data.get("user_id"),
                        flw_name=row_data.get("flw_name"),
                        total_visits=row_data.get("total_visits", 0),
                        approved_visits=row_data.get("approved_visits", 0),
                        pending_visits=row_data.get("pending_visits", 0),
                        rejected_visits=row_data.get("rejected_visits", 0),
                        flagged_visits=row_data.get("flagged_visits", 0),
                        first_visit_date=date.fromisoformat(row_data["first_visit_date"])
                        if row_data.get("first_visit_date")
                        else None,
                        last_visit_date=date.fromisoformat(row_data["last_visit_date"])
                        if row_data.get("last_visit_date")
                        else None,
                        dates_active=[date.fromisoformat(d) for d in row_data.get("dates_active", [])],
                        custom_fields={
                            k: v
                            for k, v in row_data.items()
                            if k
                            not in [
                                "username",
                                "user_id",
                                "flw_name",
                                "total_visits",
                                "approved_visits",
                                "pending_visits",
                                "rejected_visits",
                                "flagged_visits",
                                "first_visit_date",
                                "last_visit_date",
                                "dates_active",
                                "days_active",
                                "date_range_days",
                                "approval_rate",
                            ]
                        },
                    )
                )

        return cls(
            opportunity_id=data.get("opportunity_id"),
            opportunity_name=data.get("opportunity_name"),
            rows=rows,
            metadata=data.get("metadata", {}),
            computed_at=datetime.fromisoformat(data["computed_at"]) if "computed_at" in data else datetime.now(),
        )

    def get_flw(self, username: str) -> FLWRow | None:
        """Get FLW row by username."""
        for row in self.rows:
            if row.username == username:
                return row
        return None

    def get_top_performers(self, n: int = 10, metric: str = "total_visits") -> list[FLWRow]:
        """
        Get top N FLWs by metric.

        Args:
            n: Number of top performers to return
            metric: Metric to sort by (default: "total_visits")

        Returns:
            List of top N FLWRow instances
        """
        return sorted(self.rows, key=lambda row: getattr(row, metric, 0), reverse=True)[:n]

    def get_summary_stats(self) -> dict[str, Any]:
        """Get summary statistics across all FLWs."""
        if not self.rows:
            return {}

        total_flws = len(self.rows)
        total_visits = sum(row.total_visits for row in self.rows)
        avg_visits_per_flw = total_visits / total_flws if total_flws > 0 else 0

        return {
            "total_flws": total_flws,
            "total_visits": total_visits,
            "avg_visits_per_flw": round(avg_visits_per_flw, 2),
            "max_visits": max((row.total_visits for row in self.rows), default=0),
            "min_visits": min((row.total_visits for row in self.rows), default=0),
            "total_approved": sum(row.approved_visits for row in self.rows),
            "total_pending": sum(row.pending_visits for row in self.rows),
            "total_rejected": sum(row.rejected_visits for row in self.rows),
        }


@dataclass
class EntityRow:
    """
    Analysis row for entity-level analysis (one row per linking_field value).

    Used by analyses whose unit of interest is a tracked thing (a beneficiary case,
    a child, a household) rather than the worker who served them. Mirrors FLWRow's
    shape but keyed on `entity_id` instead of `username`.

    Standard fields:
    - Identification: entity_id, entity_name, username (representative FLW)
    - Visit count: total_visits
    - Date tracking: first_visit_date, last_visit_date
    - Custom fields: any additional FieldComputations declared on the config
    """

    entity_id: str
    entity_name: str = ""
    username: str = ""

    total_visits: int = 0

    first_visit_date: date | None = None
    last_visit_date: date | None = None

    custom_fields: dict[str, Any] = field(default_factory=dict)

    @property
    def date_range_days(self) -> int | None:
        """Number of days between first and last visit."""
        if not self.first_visit_date or not self.last_visit_date:
            return None
        return (self.last_visit_date - self.first_visit_date).days

    def __getattr__(self, name: str) -> Any:
        """Allow accessing custom fields as attributes."""
        if name == "custom_fields":
            raise AttributeError(f"'{type(self).__name__}' object has no attribute '{name}'")
        return self.custom_fields.get(name)

    def __setattr__(self, name: str, value: Any) -> None:
        """Store unknown attributes in custom_fields."""
        if name in self.__dataclass_fields__:
            object.__setattr__(self, name, value)
        else:
            if not hasattr(self, "custom_fields"):
                object.__setattr__(self, "custom_fields", {})
            self.custom_fields[name] = value

    def to_dict(self) -> dict:
        """Convert to dictionary, merging custom fields."""
        result = {
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "username": self.username,
            "total_visits": self.total_visits,
            "first_visit_date": self.first_visit_date.isoformat() if self.first_visit_date else None,
            "last_visit_date": self.last_visit_date.isoformat() if self.last_visit_date else None,
            "date_range_days": self.date_range_days,
        }
        result.update(self.custom_fields)
        return result


@dataclass
class EntityAnalysisResult(AnalysisResult):
    """
    Specialized result container for entity-level analysis.

    Rows are guaranteed to be EntityRow instances.
    """

    rows: list[EntityRow] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "EntityAnalysisResult":
        """Create from dictionary, properly deserializing EntityRow objects."""
        _STD_FIELDS = {
            "entity_id",
            "entity_name",
            "username",
            "total_visits",
            "first_visit_date",
            "last_visit_date",
            "date_range_days",
        }
        rows = []
        for row_data in data.get("rows", []):
            if isinstance(row_data, EntityRow):
                rows.append(row_data)
            elif isinstance(row_data, dict):
                rows.append(
                    EntityRow(
                        entity_id=row_data.get("entity_id", ""),
                        entity_name=row_data.get("entity_name", "") or "",
                        username=row_data.get("username", "") or "",
                        total_visits=row_data.get("total_visits", 0),
                        first_visit_date=date.fromisoformat(row_data["first_visit_date"])
                        if row_data.get("first_visit_date")
                        else None,
                        last_visit_date=date.fromisoformat(row_data["last_visit_date"])
                        if row_data.get("last_visit_date")
                        else None,
                        custom_fields={k: v for k, v in row_data.items() if k not in _STD_FIELDS},
                    )
                )

        return cls(
            opportunity_id=data.get("opportunity_id"),
            opportunity_name=data.get("opportunity_name"),
            rows=rows,
            metadata=data.get("metadata", {}),
            computed_at=datetime.fromisoformat(data["computed_at"]) if "computed_at" in data else datetime.now(),
        )

    def get_entity(self, entity_id: str) -> EntityRow | None:
        """Get entity row by entity_id."""
        for row in self.rows:
            if row.entity_id == entity_id:
                return row
        return None

    def filter_by_username(self, username: str) -> list[EntityRow]:
        """Get all entities whose representative FLW matches username."""
        return [row for row in self.rows if row.username == username]

    def get_summary_stats(self) -> dict[str, Any]:
        """Get summary statistics across all entities."""
        if not self.rows:
            return {}

        total_entities = len(self.rows)
        total_visits = sum(row.total_visits for row in self.rows)
        avg_visits = total_visits / total_entities if total_entities > 0 else 0

        return {
            "total_entities": total_entities,
            "total_visits": total_visits,
            "avg_visits_per_entity": round(avg_visits, 2),
            "max_visits": max((row.total_visits for row in self.rows), default=0),
            "min_visits": min((row.total_visits for row in self.rows), default=0),
        }


@dataclass
class VisitRow:
    """
    Analysis row for visit-level analysis (one row per visit).

    Contains base visit properties plus computed fields from config.
    Unlike FLWRow which aggregates, this preserves individual visit data.
    """

    # Core identification
    id: str
    user_id: int | None = None
    username: str = ""

    # Visit metadata
    visit_date: datetime | None = None
    status: str = ""
    flagged: bool = False

    # GPS coordinates
    latitude: float | None = None
    longitude: float | None = None
    accuracy_in_m: float | None = None

    # Entity/DU info
    deliver_unit_id: int | None = None
    deliver_unit_name: str = ""
    entity_id: str = ""
    entity_name: str = ""

    # Coverage context (can be enriched by subclasses)
    service_area_id: str = ""

    # Computed fields from config
    computed: dict[str, Any] = field(default_factory=dict)

    @property
    def has_gps(self) -> bool:
        """Check if visit has valid GPS coordinates."""
        return self.latitude is not None and self.longitude is not None

    def to_dict(self) -> dict:
        """Convert to dictionary, merging computed fields."""
        result = {
            "id": self.id,
            "user_id": self.user_id,
            "username": self.username,
            "visit_date": self.visit_date.isoformat() if self.visit_date else None,
            "status": self.status,
            "flagged": self.flagged,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "accuracy_in_m": self.accuracy_in_m,
            "deliver_unit_id": self.deliver_unit_id,
            "deliver_unit_name": self.deliver_unit_name,
            "entity_id": self.entity_id,
            "entity_name": self.entity_name,
            "service_area_id": self.service_area_id,
            "has_gps": self.has_gps,
        }
        # Merge computed fields
        result.update(self.computed)
        return result

    def to_geojson_properties(self) -> dict:
        """Convert to GeoJSON properties dict for map visualization."""
        props = {
            "id": self.id,
            "username": self.username,
            "status": self.status,
            "flagged": self.flagged,
            "date": self.visit_date.isoformat() if self.visit_date else "",
            "accuracy": self.accuracy_in_m,
            "du_name": self.deliver_unit_name,
            "du_id": self.deliver_unit_id,
            "service_area_id": self.service_area_id,
        }
        # Add all computed fields
        props.update(self.computed)
        return props


@dataclass
class VisitAnalysisResult(AnalysisResult):
    """
    Specialized result container for visit-level analysis.

    Rows are VisitRow instances (one per visit, not aggregated).
    """

    rows: list[VisitRow] = field(default_factory=list)
    field_metadata: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> "VisitAnalysisResult":
        """Create from dictionary, properly deserializing VisitRow objects."""
        rows = []
        for row_data in data.get("rows", []):
            if isinstance(row_data, VisitRow):
                rows.append(row_data)
            elif isinstance(row_data, dict):
                # Reconstruct VisitRow from dict
                computed = {
                    k: v
                    for k, v in row_data.items()
                    if k
                    not in [
                        "id",
                        "user_id",
                        "username",
                        "commcare_userid",
                        "visit_date",
                        "status",
                        "flagged",
                        "latitude",
                        "longitude",
                        "accuracy_in_m",
                        "deliver_unit_id",
                        "deliver_unit_name",
                        "entity_id",
                        "entity_name",
                        "service_area_id",
                        "has_gps",
                    ]
                }

                rows.append(
                    VisitRow(
                        id=row_data.get("id", ""),
                        user_id=row_data.get("user_id"),
                        username=row_data.get("username", ""),
                        visit_date=datetime.fromisoformat(row_data["visit_date"])
                        if row_data.get("visit_date")
                        else None,
                        status=row_data.get("status", ""),
                        flagged=row_data.get("flagged", False),
                        latitude=row_data.get("latitude"),
                        longitude=row_data.get("longitude"),
                        accuracy_in_m=row_data.get("accuracy_in_m"),
                        deliver_unit_id=row_data.get("deliver_unit_id"),
                        deliver_unit_name=row_data.get("deliver_unit_name", ""),
                        entity_id=row_data.get("entity_id", ""),
                        entity_name=row_data.get("entity_name", ""),
                        service_area_id=row_data.get("service_area_id", ""),
                        computed=computed,
                    )
                )

        return cls(
            opportunity_id=data.get("opportunity_id"),
            opportunity_name=data.get("opportunity_name"),
            rows=rows,
            metadata=data.get("metadata", {}),
            computed_at=datetime.fromisoformat(data["computed_at"]) if "computed_at" in data else datetime.now(),
            field_metadata=data.get("field_metadata", []),
        )

    def get_visit(self, visit_id: str) -> VisitRow | None:
        """Get visit row by ID."""
        for row in self.rows:
            if row.id == visit_id:
                return row
        return None

    def filter_by_username(self, username: str) -> list[VisitRow]:
        """Get all visits for a specific user."""
        return [row for row in self.rows if row.username == username]

    def filter_by_status(self, status: str) -> list[VisitRow]:
        """Get all visits with a specific status."""
        return [row for row in self.rows if row.status == status]

    def filter_with_gps(self) -> list[VisitRow]:
        """Get all visits that have GPS coordinates."""
        return [row for row in self.rows if row.has_gps]

    def get_summary_stats(self) -> dict[str, Any]:
        """Get summary statistics across all visits."""
        if not self.rows:
            return {}

        total_visits = len(self.rows)
        with_gps = sum(1 for row in self.rows if row.has_gps)
        unique_users = len({row.username for row in self.rows if row.username})

        status_counts = {}
        for row in self.rows:
            status_counts[row.status] = status_counts.get(row.status, 0) + 1

        return {
            "total_visits": total_visits,
            "visits_with_gps": with_gps,
            "gps_percentage": round((with_gps / total_visits) * 100, 1) if total_visits > 0 else 0,
            "unique_users": unique_users,
            "status_counts": status_counts,
            "computed_fields": [f["name"] for f in self.field_metadata],
        }
