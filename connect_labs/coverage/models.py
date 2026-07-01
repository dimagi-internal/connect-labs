"""
Data models for coverage visualization.

These are in-memory proxy classes wrapping API responses (no database storage).
Follows the LocalLabsRecord pattern from labs/models.py.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from shapely import wkt
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry


class LocalUserVisit:
    """Proxy wrapper for UserVisit API data (follows LocalLabsRecord pattern)"""

    def __init__(self, data: dict):
        self._data = data
        self._latitude = None
        self._longitude = None
        self._accuracy = None
        self._parsed_gps = False

    def _parse_gps(self):
        """Lazy parse GPS from form_json.metadata.location"""
        if not self._parsed_gps:
            form_json = self._data.get("form_json", {})
            if isinstance(form_json, str):
                import json

                form_json = json.loads(form_json)

            location_str = form_json.get("metadata", {}).get("location", "")
            parts = location_str.split()

            self._latitude = float(parts[0]) if len(parts) > 0 else 0.0
            self._longitude = float(parts[1]) if len(parts) > 1 else 0.0
            self._accuracy = float(parts[3]) if len(parts) > 3 else None
            self._parsed_gps = True

    @property
    def id(self) -> str:
        return str(self._data.get("xform_id", ""))

    @property
    def user_id(self) -> str:
        return str(self._data.get("user_id", ""))

    @property
    def username(self) -> str:
        return self._data.get("username", "")

    @property
    def deliver_unit_name(self) -> str:
        # Handle both direct field and nested object
        du = self._data.get("deliver_unit")
        if isinstance(du, dict):
            return du.get("name", "")
        return str(du) if du else ""

    @property
    def deliver_unit_id(self) -> str:
        du = self._data.get("deliver_unit")
        if isinstance(du, dict):
            return str(du.get("id", ""))
        return str(self._data.get("deliver_unit_id", ""))

    @property
    def status(self) -> str:
        return self._data.get("status", "")

    @property
    def visit_date(self) -> datetime:
        date_str = self._data.get("visit_date")
        if date_str:
            return pd.to_datetime(date_str)
        return None

    @property
    def flagged(self) -> bool:
        return bool(self._data.get("flagged", False))

    @property
    def latitude(self) -> float:
        self._parse_gps()
        return self._latitude

    @property
    def longitude(self) -> float:
        self._parse_gps()
        return self._longitude

    @property
    def accuracy_in_m(self) -> float | None:
        self._parse_gps()
        return self._accuracy

    @property
    def geometry(self) -> Point:
        return Point(self.longitude, self.latitude)


@dataclass
class DeliveryUnit:
    """DU case from CommCare (NOT Connect's DeliverUnit model)"""

    # Core identification
    id: str  # case_id
    du_name: str
    service_area_id: str  # Service area identifier
    flw_commcare_id: str
    du_id: str | None = None  # Human-readable DU identifier (e.g., "DU123")

    # Status and workflow
    status: str | None = None  # completed, visited, None (unvisited)
    visited: bool = False
    checked_in_date: str | None = None
    checked_out_date: str | None = None
    last_modified_date: datetime | None = None

    # Geometry
    wkt: str = ""  # WKT polygon geometry
    centroid: str | None = None  # "lat lon" format from CommCare
    bounding_box: str | None = None
    radius: float | None = None

    # Counts and measurements
    buildings: int = 0
    surface_area: float = 0.0
    delivery_count: int = 0
    delivery_target: int = 0

    # Distance measurements
    distance_btw_adj_1: float | None = None
    distance_btw_adj_2: float | None = None
    distance_to_nearest_multi_building_du: float | None = None

    # Administrative/geographic metadata
    oa: str | None = None  # Operational Area
    ward_name: str | None = None
    llo: str | None = None  # Local Level Organization
    service_area_unlock_order: int | None = None  # Order in which SA is unlocked (1st, 2nd, 3rd, etc)

    # Additional metadata
    max_round: int | None = None
    is_single_building: bool = False
    nearest_multi_building_du_id: str | None = None
    nearest_multi_building_du_name: str | None = None
    nearest_multi_building_du_count: int | None = None

    # Raw data storage
    raw_properties: dict = field(default_factory=dict)

    # Visit tracking
    service_points: list[LocalUserVisit] = field(default_factory=list)

    @property
    def geometry(self) -> BaseGeometry:
        """Convert WKT to Shapely geometry"""
        if not self.wkt or self.wkt == "":
            raise ValueError(f"Empty WKT string for delivery unit {self.id}")
        return wkt.loads(self.wkt)

    @classmethod
    def from_commcare_case(cls, case_data: dict):
        """
        Parse CommCare case API response using field mapping system.

        Stores all properties in raw_properties and maps known fields to typed attributes.
        """
        from connect_labs.coverage.field_mappings import get_property_with_fallback

        properties = case_data.get("properties", {})

        # Helper to safely convert to int
        def safe_int(val) -> int | None:
            if val is None or val == "":
                return None
            try:
                return int(float(val))  # Handle "123.0" strings
            except (ValueError, TypeError):
                return None

        # Helper to safely convert to float
        def safe_float(val) -> float | None:
            if val is None or val == "":
                return None
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        # Helper to safely convert to bool
        def safe_bool(val) -> bool:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "yes", "1")
            return bool(val)

        return cls(
            # Core identification
            id=case_data.get("case_id", ""),
            du_name=case_data.get("case_name", ""),
            service_area_id=get_property_with_fallback(properties, "service_area_id") or "",
            flw_commcare_id=case_data.get("owner_id", ""),
            du_id=get_property_with_fallback(properties, "du_id"),
            # Status and workflow
            status=get_property_with_fallback(properties, "du_status"),
            visited=safe_bool(get_property_with_fallback(properties, "visited")),
            checked_in_date=get_property_with_fallback(properties, "checked_in_date"),
            checked_out_date=get_property_with_fallback(properties, "checked_out_date"),
            last_modified_date=pd.to_datetime(case_data.get("last_modified"))
            if case_data.get("last_modified")
            else None,
            # Geometry
            wkt=get_property_with_fallback(properties, "wkt") or "",
            centroid=get_property_with_fallback(properties, "centroid"),
            bounding_box=get_property_with_fallback(properties, "bounding_box"),
            radius=safe_float(get_property_with_fallback(properties, "radius")),
            # Counts and measurements
            buildings=safe_int(get_property_with_fallback(properties, "buildings")) or 0,
            surface_area=safe_float(get_property_with_fallback(properties, "surface_area")) or 0.0,
            delivery_count=safe_int(get_property_with_fallback(properties, "delivery_count")) or 0,
            delivery_target=safe_int(get_property_with_fallback(properties, "delivery_target")) or 0,
            # Distance measurements
            distance_btw_adj_1=safe_float(get_property_with_fallback(properties, "distance_btw_adj_1")),
            distance_btw_adj_2=safe_float(get_property_with_fallback(properties, "distance_btw_adj_2")),
            distance_to_nearest_multi_building_du=safe_float(
                get_property_with_fallback(properties, "distance_to_nearest_multi_building_du")
            ),
            # Administrative/geographic metadata
            oa=get_property_with_fallback(properties, "oa"),
            ward_name=get_property_with_fallback(properties, "ward_name"),
            llo=get_property_with_fallback(properties, "llo"),
            service_area_unlock_order=safe_int(get_property_with_fallback(properties, "service_area_unlock_order")),
            # Additional metadata
            max_round=safe_int(get_property_with_fallback(properties, "max_round")),
            is_single_building=safe_bool(get_property_with_fallback(properties, "is_single_building")),
            nearest_multi_building_du_id=get_property_with_fallback(properties, "nearest_multi_building_du_id"),
            nearest_multi_building_du_name=get_property_with_fallback(properties, "nearest_multi_building_du_name"),
            nearest_multi_building_du_count=safe_int(
                get_property_with_fallback(properties, "nearest_multi_building_du_count")
            ),
            # Store all raw properties
            raw_properties=dict(properties),
        )


@dataclass
class ServiceArea:
    """Collection of DUs grouped by service_area_id"""

    id: str
    delivery_units: list[DeliveryUnit] = field(default_factory=list)

    # Metadata (aggregated from DUs)
    unlock_order: int | None = None  # Order in which SA is unlocked (1st, 2nd, 3rd, etc)
    name: str | None = None  # Human-readable name
    oa: str | None = None  # Operational Area
    ward_name: str | None = None
    llo: str | None = None  # Local Level Organization

    # Raw properties storage
    raw_properties: dict = field(default_factory=dict)

    def aggregate_metadata_from_dus(self) -> None:
        """
        Aggregate metadata from delivery units.

        Takes values from the first DU since all DUs in same SA should share these attributes.
        """
        if not self.delivery_units:
            return

        # Take metadata from first DU (all DUs in SA should have same values)
        first_du = self.delivery_units[0]

        self.unlock_order = first_du.service_area_unlock_order
        self.oa = first_du.oa
        self.ward_name = first_du.ward_name
        self.llo = first_du.llo

        # Create a human-readable name from available metadata
        name_parts = []
        if self.ward_name:
            name_parts.append(self.ward_name)
        if self.oa:
            name_parts.append(f"OA{self.oa}")
        if self.id:
            name_parts.append(f"SA{self.id}")

        self.name = " - ".join(name_parts) if name_parts else self.id

    @property
    def total_buildings(self) -> int:
        return sum(du.buildings for du in self.delivery_units)

    @property
    def total_units(self) -> int:
        return len(self.delivery_units)

    @property
    def completed_units(self) -> int:
        return sum(1 for du in self.delivery_units if du.status == "completed")

    @property
    def completion_percentage(self) -> float:
        if not self.delivery_units:
            return 0.0
        return (self.completed_units / len(self.delivery_units)) * 100


@dataclass
class FLW:
    """Field Level Worker stats"""

    # Identity fields
    commcare_id: str | None = None  # CommCare user ID (from DU owner_id or form.meta.userID)
    connect_id: str | None = None  # Connect username (from visit.username)
    display_name: str | None = None  # Human-readable name (from get_flw_names_for_opportunity)

    # Stats and relationships
    service_areas: list[str] = field(default_factory=list)
    assigned_units: int = 0
    completed_units: int = 0
    total_visits: int = 0
    dates_active: list[datetime] = field(default_factory=list)
    service_points: list[LocalUserVisit] = field(default_factory=list)
    delivery_units: list[DeliveryUnit] = field(default_factory=list)

    @property
    def key(self) -> str:
        """Return commcare_id (the consistent dictionary key for FLWs)"""
        return self.commcare_id or "unknown"

    @property
    def name(self) -> str:
        """Return display name if available, otherwise fall back to IDs"""
        return self.display_name or self.connect_id or self.commcare_id or "Unknown"

    @property
    def completion_rate(self) -> float:
        if self.assigned_units == 0:
            return 0.0
        return (self.completed_units / self.assigned_units) * 100


class CoverageData:
    """Main container - mirrors coverage project structure"""

    def __init__(self):
        self.opportunity_id: int | None = None
        self.opportunity_name: str | None = None
        self.commcare_domain: str | None = None

        self.service_areas: dict[str, ServiceArea] = {}
        self.delivery_units: dict[str, DeliveryUnit] = {}
        self.service_points: list[LocalUserVisit] = []
        self.flws: dict[str, FLW] = {}

        # Cached metadata
        self.total_buildings: int = 0
        self.total_completed_dus: int = 0
        self.total_visited_dus: int = 0
        self.completion_percentage: float = 0.0

    def _compute_metadata(self):
        """Pre-compute stats (mirrors coverage project)"""
        # Link visits to DUs
        for point in self.service_points:
            du_name = point.deliver_unit_name
            if du_name and du_name in self.delivery_units:
                self.delivery_units[du_name].service_points.append(point)

        # Compute aggregates
        self.total_buildings = sum(du.buildings for du in self.delivery_units.values())
        self.total_completed_dus = sum(1 for du in self.delivery_units.values() if du.status == "completed")
        self.total_visited_dus = sum(
            1 for du in self.delivery_units.values() if du.status in ["visited", "in_progress"]
        )

        if self.delivery_units:
            self.completion_percentage = (self.total_completed_dus / len(self.delivery_units)) * 100
