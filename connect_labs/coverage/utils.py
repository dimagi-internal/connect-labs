"""
Utility functions for coverage visualization.
"""


def extract_gps_from_form_json(form_json: dict) -> tuple[float, float, float | None]:
    """
    Extract lat, lon, accuracy from UserVisit form_json.

    Args:
        form_json: Form JSON dict containing metadata.location

    Returns:
        Tuple of (latitude, longitude, accuracy_in_meters)
    """
    location_str = form_json.get("metadata", {}).get("location", "")
    parts = location_str.split()

    lat = float(parts[0]) if len(parts) > 0 else 0.0
    lon = float(parts[1]) if len(parts) > 1 else 0.0
    accuracy = float(parts[3]) if len(parts) > 3 else None

    return lat, lon, accuracy


def simplify_geometry_for_zoom(wkt_string: str, zoom_level: int) -> str:
    """
    Simplify polygon based on zoom level for better performance.

    Args:
        wkt_string: Well-Known Text geometry string
        zoom_level: Map zoom level (7=country, 10=region, 13=city, 15=street)

    Returns:
        Simplified WKT string
    """
    from shapely import wkt

    geom = wkt.loads(wkt_string)

    # Tolerance increases as zoom decreases
    tolerance_map = {
        7: 0.01,  # Country view
        10: 0.001,  # Region view
        13: 0.0001,  # City view
        15: 0.0,  # Street view (no simplification)
    }

    tolerance = tolerance_map.get(zoom_level, 0.0001)
    simplified = geom.simplify(tolerance, preserve_topology=True)

    return simplified.wkt
