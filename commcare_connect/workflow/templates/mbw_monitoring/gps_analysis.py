"""
GPS analysis service for MBW.

Computes GPS-based metrics:
- Distance between sequential visits to the same case
- Daily travel distance per FLW
- Outlier detection for suspicious patterns
"""

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from commcare_connect.workflow.templates.mbw_monitoring.gps_utils import (
    GPSCoordinate,
    calculate_path_distance,
    haversine_distance,
    meters_to_km,
    parse_gps_location,
)

logger = logging.getLogger(__name__)

# Default threshold for flagging suspicious case distances (5 km)
DEFAULT_CASE_DISTANCE_THRESHOLD_METERS = 5000


@dataclass
class VisitWithGPS:
    """Visit data with parsed GPS and computed metrics."""

    visit_id: int
    username: str
    case_id: str | None
    mother_case_id: str | None
    entity_name: str | None
    form_name: str | None
    visit_date: date | None
    visit_datetime: datetime | None
    gps: GPSCoordinate | None
    gps_raw: str | None
    app_build_version: int | None = None

    # Computed metrics
    distance_from_prev_case_visit: float | None = None  # meters
    is_flagged: bool = False
    flag_reason: str | None = None


@dataclass
class DailyTravel:
    """Daily travel summary for an FLW."""

    username: str
    travel_date: date
    total_distance_meters: float
    visit_count: int
    visits: list[VisitWithGPS] = field(default_factory=list)

    @property
    def total_distance_km(self) -> float:
        return meters_to_km(self.total_distance_meters)


@dataclass
class FLWSummary:
    """Summary metrics for an FLW."""

    username: str
    display_name: str
    total_visits: int
    visits_with_gps: int
    flagged_visits: int
    unique_cases: int
    avg_case_distance_km: float | None
    max_case_distance_km: float | None
    cases_with_revisits: int  # mothers with >1 GPS visit (revisit distance denominator)
    trailing_7_days: list[DailyTravel]
    avg_daily_travel_km: float | None


@dataclass
class CaseSummary:
    """Summary metrics for a case."""

    case_id: str
    entity_name: str | None
    visit_count: int
    avg_distance_meters: float | None
    max_distance_meters: float | None


@dataclass
class GPSAnalysisResult:
    """Complete GPS analysis result."""

    visits: list[VisitWithGPS]
    flw_summaries: list[FLWSummary]
    case_summaries: list[CaseSummary]
    total_visits: int
    total_flagged: int
    date_range_start: date | None
    date_range_end: date | None


def parse_visit_date(date_str: str | None) -> date | None:
    """Parse date from ISO datetime string."""
    if not date_str:
        return None
    try:
        # Handle ISO format with or without microseconds
        if "T" in date_str:
            dt_str = date_str.split("T")[0]
            return datetime.strptime(dt_str, "%Y-%m-%d").date()
        return datetime.strptime(date_str[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_visit_datetime(dt_str: str | None) -> datetime | None:
    """Parse datetime from ISO datetime string."""
    if not dt_str:
        return None
    try:
        # Handle various ISO formats
        if "." in dt_str:
            return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def extract_visits_with_gps(visits: list[dict]) -> list[VisitWithGPS]:
    """
    Extract and parse GPS data from raw visit dicts.

    Args:
        visits: List of visit dicts from pipeline (with computed fields)

    Returns:
        List of VisitWithGPS objects
    """
    result = []

    for visit in visits:
        # Get computed fields (from pipeline) or fall back to raw data
        computed = visit.get("computed", {})

        # Try to get GPS from computed fields first, then from raw metadata
        gps_raw = computed.get("gps_location") or visit.get("metadata", {}).get("location")
        gps = parse_gps_location(gps_raw)

        # Get visit date - try multiple sources
        visit_date_str = computed.get("visit_datetime") or visit.get("visit_date")
        visit_date = parse_visit_date(visit_date_str)

        visit_with_gps = VisitWithGPS(
            visit_id=visit.get("id") or visit.get("visit_id", 0),
            username=visit.get("username", ""),
            case_id=computed.get("case_id"),
            mother_case_id=computed.get("mother_case_id"),
            entity_name=computed.get("entity_name") or visit.get("entity_name"),
            form_name=computed.get("form_name"),
            visit_date=visit_date,
            visit_datetime=parse_visit_datetime(visit_date_str),
            gps=gps,
            gps_raw=gps_raw,
            app_build_version=computed.get("app_build_version"),
        )
        result.append(visit_with_gps)

    return result


def analyze_case_distances(
    visits: list[VisitWithGPS],
    threshold_meters: float = DEFAULT_CASE_DISTANCE_THRESHOLD_METERS,
) -> list[VisitWithGPS]:
    """
    Calculate distance from previous visit to the same mother case.

    For MBW, each visit creates a new baby case_id but shares the mother_case_id.
    We group by mother_case_id to track visits to the same mother across time.

    Args:
        visits: List of visits with GPS data
        threshold_meters: Distance threshold for flagging (default 5km)

    Returns:
        Same visits with distance_from_prev_case_visit and is_flagged populated
    """
    # Group visits by mother_case_id (the common link across visits to same mother)
    visits_by_case: dict[str, list[VisitWithGPS]] = defaultdict(list)

    for visit in visits:
        # Use mother_case_id for grouping (falls back to case_id if no mother_case_id)
        linking_id = visit.mother_case_id or visit.case_id
        if linking_id:
            visits_by_case[linking_id].append(visit)

    # For each case, sort by datetime and calculate distances
    for case_id, case_visits in visits_by_case.items():
        # Sort by visit datetime
        sorted_visits = sorted(
            case_visits,
            key=lambda v: v.visit_datetime or datetime.min.replace(tzinfo=timezone.utc),
        )

        prev_visit: VisitWithGPS | None = None
        for visit in sorted_visits:
            if prev_visit and visit.gps and prev_visit.gps:
                distance = haversine_distance(
                    prev_visit.gps.latitude,
                    prev_visit.gps.longitude,
                    visit.gps.latitude,
                    visit.gps.longitude,
                )
                visit.distance_from_prev_case_visit = distance

                # Flag if exceeds threshold
                if distance > threshold_meters:
                    visit.is_flagged = True
                    visit.flag_reason = f"Distance from previous case visit: {meters_to_km(distance):.1f} km"

            prev_visit = visit

    return visits


def analyze_daily_travel(visits: list[VisitWithGPS]) -> dict[str, dict[date, DailyTravel]]:
    """
    Calculate total daily travel distance per FLW.

    Groups visits by FLW and date, then calculates the path distance
    through all visits for that day (in chronological order).

    Args:
        visits: List of visits with GPS data

    Returns:
        Dict mapping username -> date -> DailyTravel
    """
    # Group by username and date
    visits_by_flw_date: dict[str, dict[date, list[VisitWithGPS]]] = defaultdict(lambda: defaultdict(list))

    for visit in visits:
        if visit.visit_date:
            visits_by_flw_date[visit.username][visit.visit_date].append(visit)

    # Calculate daily travel for each FLW
    result: dict[str, dict[date, DailyTravel]] = {}

    for username, dates_dict in visits_by_flw_date.items():
        result[username] = {}

        for travel_date, day_visits in dates_dict.items():
            # Sort by datetime
            sorted_visits = sorted(
                day_visits,
                key=lambda v: v.visit_datetime or datetime.min.replace(tzinfo=timezone.utc),
            )

            # Extract GPS coordinates in order
            coords = [v.gps for v in sorted_visits if v.gps]

            # Calculate path distance
            total_distance = calculate_path_distance(coords) if len(coords) >= 2 else 0.0

            result[username][travel_date] = DailyTravel(
                username=username,
                travel_date=travel_date,
                total_distance_meters=total_distance,
                visit_count=len(sorted_visits),
                visits=sorted_visits,
            )

    return result


def get_trailing_7_days(
    daily_travel: dict[date, DailyTravel],
    end_date: date,
) -> list[DailyTravel]:
    """
    Get the trailing 7 days of daily travel ending at end_date.

    Args:
        daily_travel: Dict mapping date -> DailyTravel
        end_date: The end date of the range

    Returns:
        List of DailyTravel for the last 7 days (may be less if no data)
    """
    from datetime import timedelta

    result = []
    for i in range(7):
        check_date = end_date - timedelta(days=i)
        if check_date in daily_travel:
            result.append(daily_travel[check_date])

    # Reverse so oldest is first
    return list(reversed(result))


def get_case_summaries(visits: list[VisitWithGPS]) -> list[CaseSummary]:
    """
    Calculate summary statistics per mother case.

    Args:
        visits: List of visits with distance calculations done

    Returns:
        List of CaseSummary objects
    """
    # Group by mother_case_id (the common link across visits to same mother)
    visits_by_case: dict[str, list[VisitWithGPS]] = defaultdict(list)

    for visit in visits:
        linking_id = visit.mother_case_id or visit.case_id
        if linking_id:
            visits_by_case[linking_id].append(visit)

    summaries = []
    for case_id, case_visits in visits_by_case.items():
        distances = [
            v.distance_from_prev_case_visit for v in case_visits if v.distance_from_prev_case_visit is not None
        ]

        entity_name = next((v.entity_name for v in case_visits if v.entity_name), None)

        summaries.append(
            CaseSummary(
                case_id=case_id,
                entity_name=entity_name,
                visit_count=len(case_visits),
                avg_distance_meters=sum(distances) / len(distances) if distances else None,
                max_distance_meters=max(distances) if distances else None,
            )
        )

    return summaries


def build_result_from_analyzed_visits(
    visits: list[VisitWithGPS],
    flw_names: dict[str, str] | None = None,
) -> GPSAnalysisResult:
    """
    Build GPS analysis result from already-analyzed visits.

    Use this after filtering visits by date range - the visits already have
    case distances calculated, so we just need to aggregate into summaries.

    Args:
        visits: List of VisitWithGPS objects (with distances already calculated)
        flw_names: Optional dict mapping username -> display name

    Returns:
        GPSAnalysisResult with aggregated summaries
    """
    flw_names = flw_names or {}

    # Calculate daily travel for filtered visits
    daily_travel_by_flw = analyze_daily_travel(visits)

    # Get case summaries for filtered visits
    case_summaries = get_case_summaries(visits)

    # Determine date range from filtered visits
    dates = [v.visit_date for v in visits if v.visit_date]
    date_range_start = min(dates) if dates else None
    date_range_end = max(dates) if dates else None

    # Build FLW summaries
    flw_summaries = []
    visits_by_flw: dict[str, list[VisitWithGPS]] = defaultdict(list)
    for v in visits:
        visits_by_flw[v.username].append(v)

    for username, flw_visits in visits_by_flw.items():
        # Get distances for case visits (already calculated)
        case_distances = [v.distance_from_prev_case_visit for v in flw_visits if v.distance_from_prev_case_visit]

        # Get trailing 7 days
        daily_travel = daily_travel_by_flw.get(username, {})
        trailing_7 = get_trailing_7_days(daily_travel, date_range_end) if date_range_end else []

        # Calculate avg daily travel from trailing 7 days
        avg_daily_travel = None
        if trailing_7:
            total_km = sum(d.total_distance_km for d in trailing_7)
            avg_daily_travel = total_km / len(trailing_7)

        # Count mothers with >1 GPS visit (revisit distance denominator)
        cases_with_revisits = len(
            {v.mother_case_id or v.case_id for v in flw_visits if v.distance_from_prev_case_visit is not None}
        )

        flw_summaries.append(
            FLWSummary(
                username=username,
                display_name=flw_names.get(username, username),
                total_visits=len(flw_visits),
                visits_with_gps=sum(1 for v in flw_visits if v.gps),
                flagged_visits=sum(1 for v in flw_visits if v.is_flagged),
                unique_cases=len({v.case_id for v in flw_visits if v.case_id}),
                avg_case_distance_km=meters_to_km(sum(case_distances) / len(case_distances))
                if case_distances
                else None,
                max_case_distance_km=meters_to_km(max(case_distances)) if case_distances else None,
                cases_with_revisits=cases_with_revisits,
                trailing_7_days=trailing_7,
                avg_daily_travel_km=avg_daily_travel,
            )
        )

    # Sort by flagged visits descending
    flw_summaries.sort(key=lambda s: s.flagged_visits, reverse=True)

    return GPSAnalysisResult(
        visits=visits,
        flw_summaries=flw_summaries,
        case_summaries=case_summaries,
        total_visits=len(visits),
        total_flagged=sum(1 for v in visits if v.is_flagged),
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


def analyze_gps_metrics(
    visits: list[dict],
    flw_names: dict[str, str] | None = None,
    threshold_meters: float = DEFAULT_CASE_DISTANCE_THRESHOLD_METERS,
) -> GPSAnalysisResult:
    """
    Run complete GPS analysis on visits.

    Args:
        visits: List of visit dicts from pipeline
        flw_names: Optional dict mapping username -> display name
        threshold_meters: Distance threshold for flagging

    Returns:
        GPSAnalysisResult with all computed metrics
    """
    flw_names = flw_names or {}

    # Extract and parse visits
    visits_with_gps = extract_visits_with_gps(visits)

    # Calculate case distances and flag outliers
    visits_with_gps = analyze_case_distances(visits_with_gps, threshold_meters)

    # Calculate daily travel
    daily_travel_by_flw = analyze_daily_travel(visits_with_gps)

    # Get case summaries
    case_summaries = get_case_summaries(visits_with_gps)

    # Determine date range
    dates = [v.visit_date for v in visits_with_gps if v.visit_date]
    date_range_start = min(dates) if dates else None
    date_range_end = max(dates) if dates else None

    # Build FLW summaries
    flw_summaries = []
    visits_by_flw: dict[str, list[VisitWithGPS]] = defaultdict(list)
    for v in visits_with_gps:
        visits_by_flw[v.username].append(v)

    for username, flw_visits in visits_by_flw.items():
        # Get distances for case visits
        case_distances = [v.distance_from_prev_case_visit for v in flw_visits if v.distance_from_prev_case_visit]

        # Get trailing 7 days
        daily_travel = daily_travel_by_flw.get(username, {})
        trailing_7 = get_trailing_7_days(daily_travel, date_range_end) if date_range_end else []

        # Calculate avg daily travel from trailing 7 days
        avg_daily_travel = None
        if trailing_7:
            total_km = sum(d.total_distance_km for d in trailing_7)
            avg_daily_travel = total_km / len(trailing_7)

        # Count mothers with >1 GPS visit (revisit distance denominator)
        cases_with_revisits = len(
            {v.mother_case_id or v.case_id for v in flw_visits if v.distance_from_prev_case_visit is not None}
        )

        flw_summaries.append(
            FLWSummary(
                username=username,
                display_name=flw_names.get(username, username),
                total_visits=len(flw_visits),
                visits_with_gps=sum(1 for v in flw_visits if v.gps),
                flagged_visits=sum(1 for v in flw_visits if v.is_flagged),
                unique_cases=len({v.case_id for v in flw_visits if v.case_id}),
                avg_case_distance_km=meters_to_km(sum(case_distances) / len(case_distances))
                if case_distances
                else None,
                max_case_distance_km=meters_to_km(max(case_distances)) if case_distances else None,
                cases_with_revisits=cases_with_revisits,
                trailing_7_days=trailing_7,
                avg_daily_travel_km=avg_daily_travel,
            )
        )

    # Sort by flagged visits descending
    flw_summaries.sort(key=lambda s: s.flagged_visits, reverse=True)

    return GPSAnalysisResult(
        visits=visits_with_gps,
        flw_summaries=flw_summaries,
        case_summaries=case_summaries,
        total_visits=len(visits_with_gps),
        total_flagged=sum(1 for v in visits_with_gps if v.is_flagged),
        date_range_start=date_range_start,
        date_range_end=date_range_end,
    )


def _prepare_daily_visit_pairs(
    visits: list[VisitWithGPS],
) -> dict[str, list[tuple[VisitWithGPS, VisitWithGPS]]]:
    """
    Group visits by (FLW, day), deduplicate by mother_case_id (keep first
    visit per mother per day), and return consecutive visit pairs per FLW.

    Only includes days where the FLW visited 2+ unique mothers.
    Filters to visits with GPS, mother_case_id, and visit_date.

    Returns: {username: [(visit_a, visit_b), ...]}
    """
    valid = [v for v in visits if v.gps and v.mother_case_id and v.visit_date and v.visit_datetime]

    by_flw_day: dict[tuple[str, date], list[VisitWithGPS]] = defaultdict(list)
    for v in valid:
        by_flw_day[(v.username, v.visit_date)].append(v)

    pairs_by_flw: dict[str, list[tuple[VisitWithGPS, VisitWithGPS]]] = defaultdict(list)

    for (username, _day), day_visits in by_flw_day.items():
        day_visits.sort(key=lambda v: v.visit_datetime or datetime.min.replace(tzinfo=timezone.utc))

        # Dedup by mother_case_id (keep first visit per mother per day)
        seen_mothers: set[str] = set()
        unique_visits: list[VisitWithGPS] = []
        for v in day_visits:
            if v.mother_case_id not in seen_mothers:
                seen_mothers.add(v.mother_case_id)
                unique_visits.append(v)

        if len(unique_visits) < 2:
            continue

        for i in range(len(unique_visits) - 1):
            pairs_by_flw[username].append((unique_visits[i], unique_visits[i + 1]))

    return dict(pairs_by_flw)


def compute_median_meters_per_visit(
    visits: list[VisitWithGPS],
    min_app_version: int = 0,
) -> dict[str, float | None]:
    """
    Median haversine distance (meters) between consecutive visits to
    different mothers within a day. Only visits with a known app_build_version.
    """
    filtered = [v for v in visits if v.app_build_version is not None and v.app_build_version > min_app_version]
    pairs_by_flw = _prepare_daily_visit_pairs(filtered)

    result: dict[str, float | None] = {}
    for username, pairs in pairs_by_flw.items():
        distances = []
        for a, b in pairs:
            dist = haversine_distance(a.gps.latitude, a.gps.longitude, b.gps.latitude, b.gps.longitude)
            distances.append(dist)
        result[username] = round(statistics.median(distances)) if distances else None

    return result


def compute_median_minutes_per_visit(
    visits: list[VisitWithGPS],
) -> dict[str, float | None]:
    """
    Median time difference (minutes) between consecutive visits to
    different mothers within a day. All app versions included.
    """
    pairs_by_flw = _prepare_daily_visit_pairs(visits)

    result: dict[str, float | None] = {}
    for username, pairs in pairs_by_flw.items():
        time_diffs = []
        for a, b in pairs:
            if a.visit_datetime and b.visit_datetime:
                diff_minutes = abs((b.visit_datetime - a.visit_datetime).total_seconds()) / 60.0
                time_diffs.append(diff_minutes)
        result[username] = round(statistics.median(time_diffs)) if time_diffs else None

    return result
