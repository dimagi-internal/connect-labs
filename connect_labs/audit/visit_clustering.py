"""
Visit Clustering — groups consecutive visits (by the same FLW, within the same
audit track) that are close together in time and/or GPS location. Purely
additive metadata: never changes which visits/images an audit session
includes. See docs/superpowers/specs/2026-07-16-visit-clustering-design.md.
"""

from django.utils.dateparse import parse_date, parse_datetime
from geopy.distance import distance as geopy_distance


def _parse_visit_datetime(value):
    """Parse an ISO datetime or bare date string. Returns None if unparseable/missing."""
    if not value:
        return None
    dt = parse_datetime(value)
    if dt is not None:
        return dt
    d = parse_date(value)
    if d is not None:
        from datetime import datetime, timezone

        return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return None


def _parse_location(value):
    """Parse a 'lat lon alt precision' string into (lat, lon). None if missing/malformed."""
    if not value:
        return None
    parts = value.split()
    if len(parts) < 2:
        return None
    try:
        return (float(parts[0]), float(parts[1]))
    except ValueError:
        return None


def _pair_qualifies(a, b, *, enable_time_gap, time_gap_minutes, enable_distance, distance_meters):
    """Whether two visits (each a dict with parsed 'dt' and 'latlon') cluster together."""
    if enable_time_gap:
        if a["dt"] is None or b["dt"] is None:
            return False
        delta_minutes = abs((b["dt"] - a["dt"]).total_seconds()) / 60.0
        if delta_minutes > time_gap_minutes:
            return False
    if enable_distance:
        if a["latlon"] is None or b["latlon"] is None:
            return False
        meters = geopy_distance(a["latlon"], b["latlon"]).meters
        if meters > distance_meters:
            return False
    return True


def compute_visit_clusters(
    visits,
    *,
    enable_time_gap=False,
    time_gap_minutes=10,
    enable_distance=False,
    distance_meters=10,
):
    """
    Group consecutive visits (sorted by visit_date) into clusters when every
    enabled criterion holds between adjacent visits (AND semantics). Chains
    transitively; drops singletons (no qualifying neighbor).

    Args:
        visits: list of {"id": int, "visit_date": str | None, "location": str | None}.

    Returns:
        list of {"group_id": str, "visit_ids": list[int]}, ordered by first
        appearance in the sorted visit list. Empty if neither criterion is enabled.
    """
    if not enable_time_gap and not enable_distance:
        return []

    parsed = []
    for v in visits:
        dt = _parse_visit_datetime(v.get("visit_date"))
        latlon = _parse_location(v.get("location"))
        parsed.append({"id": v["id"], "dt": dt, "latlon": latlon})

    # Missing dates sort last so they never get placed adjacent to a real
    # neighbor by sort order alone -- the fail-safe check in _pair_qualifies
    # still applies regardless, but this keeps grouping deterministic.
    from datetime import datetime, timezone

    parsed.sort(key=lambda p: p["dt"] or datetime.max.replace(tzinfo=timezone.utc))

    groups = []
    current = [parsed[0]] if parsed else []
    for prev, curr in zip(parsed, parsed[1:]):
        if _pair_qualifies(
            prev,
            curr,
            enable_time_gap=enable_time_gap,
            time_gap_minutes=time_gap_minutes,
            enable_distance=enable_distance,
            distance_meters=distance_meters,
        ):
            current.append(curr)
        else:
            if len(current) >= 2:
                groups.append(current)
            current = [curr]
    if len(current) >= 2:
        groups.append(current)

    return [{"group_id": f"g{i + 1}", "visit_ids": [p["id"] for p in group]} for i, group in enumerate(groups)]


def build_flw_visit_clusters(
    flw_visit_ids,
    visit_meta_by_id,
    flw_images,
    *,
    enable_time_gap=False,
    time_gap_minutes=10,
    enable_distance=False,
    distance_meters=10,
):
    """
    Compute visit clusters for one FLW's track session and fill in image_count
    from that FLW's already-extracted image data (see compute_visit_clusters
    for the grouping algorithm).

    Args:
        flw_visit_ids: the FLW's final sampled visit_ids for this track.
        visit_meta_by_id: {str(visit_id): {"visit_date": ..., "location": ...}} --
            missing entries are treated as no visit_date/location (fail-safe).
        flw_images: {str(visit_id): list[image_dict]} for this FLW's track.

    Returns:
        Same shape as compute_visit_clusters, plus "image_count" per group.
    """
    if not enable_time_gap and not enable_distance:
        return []

    visits = [
        {
            "id": vid,
            "visit_date": visit_meta_by_id.get(str(vid), {}).get("visit_date"),
            "location": visit_meta_by_id.get(str(vid), {}).get("location"),
        }
        for vid in flw_visit_ids
    ]
    clusters = compute_visit_clusters(
        visits,
        enable_time_gap=enable_time_gap,
        time_gap_minutes=time_gap_minutes,
        enable_distance=enable_distance,
        distance_meters=distance_meters,
    )
    for cluster in clusters:
        cluster["image_count"] = sum(len(flw_images.get(str(vid), [])) for vid in cluster["visit_ids"])
    return clusters
