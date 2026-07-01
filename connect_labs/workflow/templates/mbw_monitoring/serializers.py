"""
Serializers for GPS analysis data, used by the MBW monitoring workflow template.
"""

from datetime import date

from connect_labs.workflow.templates.mbw_monitoring.gps_analysis import (
    DailyTravel,
    FLWSummary,
    GPSAnalysisResult,
    VisitWithGPS,
)


def filter_visits_by_date(visits: list, start_date: date, end_date: date) -> list:
    """
    Filter visits by date range.

    This is fast in-memory filtering after pipeline has cached the data.
    For 37k visits, this takes ~50ms.
    """
    filtered = []
    for visit in visits:
        visit_date = visit.visit_date
        if visit_date and start_date <= visit_date <= end_date:
            filtered.append(visit)
    return filtered


def serialize_visit(visit: VisitWithGPS) -> dict:
    """Serialize a visit for JSON response."""
    return {
        "visit_id": visit.visit_id,
        "username": visit.username,
        "case_id": visit.case_id,
        "mother_case_id": visit.mother_case_id,
        "entity_name": visit.entity_name,
        "form_name": visit.form_name,
        "visit_date": visit.visit_date.isoformat() if visit.visit_date else None,
        "gps": {
            "latitude": visit.gps.latitude,
            "longitude": visit.gps.longitude,
            "accuracy": visit.gps.accuracy,
        }
        if visit.gps
        else None,
        "distance_from_prev_km": round(visit.distance_from_prev_case_visit / 1000, 2)
        if visit.distance_from_prev_case_visit
        else None,
        "is_flagged": visit.is_flagged,
        "flag_reason": visit.flag_reason,
    }


def serialize_daily_travel(dt: DailyTravel) -> dict:
    """Serialize daily travel for JSON response."""
    return {
        "date": dt.travel_date.isoformat(),
        "distance_km": round(dt.total_distance_km, 2),
        "visit_count": dt.visit_count,
    }


def serialize_flw_summary(flw: FLWSummary) -> dict:
    """Serialize FLW summary for JSON response."""
    return {
        "username": flw.username,
        "display_name": flw.display_name,
        "total_visits": flw.total_visits,
        "visits_with_gps": flw.visits_with_gps,
        "flagged_visits": flw.flagged_visits,
        "unique_cases": flw.unique_cases,
        "avg_case_distance_km": round(flw.avg_case_distance_km, 2) if flw.avg_case_distance_km else None,
        "max_case_distance_km": round(flw.max_case_distance_km, 2) if flw.max_case_distance_km else None,
        "cases_with_revisits": flw.cases_with_revisits,
        "trailing_7_days": [serialize_daily_travel(dt) for dt in flw.trailing_7_days],
        "avg_daily_travel_km": round(flw.avg_daily_travel_km, 2) if flw.avg_daily_travel_km else None,
    }


def serialize_result(result: GPSAnalysisResult, include_visits: bool = False) -> dict:
    """Serialize GPS analysis result for JSON response."""
    data = {
        "total_visits": result.total_visits,
        "total_flagged": result.total_flagged,
        "date_range_start": result.date_range_start.isoformat() if result.date_range_start else None,
        "date_range_end": result.date_range_end.isoformat() if result.date_range_end else None,
        "flw_summaries": [serialize_flw_summary(flw) for flw in result.flw_summaries],
    }
    if include_visits:
        data["visits"] = [serialize_visit(v) for v in result.visits]
    return data
