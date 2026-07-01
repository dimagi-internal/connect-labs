"""
GPS utility functions for MBW analysis.

Provides functions for parsing GPS coordinates and calculating distances.
"""

import math
from dataclasses import dataclass


@dataclass
class GPSCoordinate:
    """Parsed GPS coordinate with latitude, longitude, altitude, and accuracy."""

    latitude: float
    longitude: float
    altitude: float | None = None
    accuracy: float | None = None

    def is_valid(self) -> bool:
        """Check if coordinates are within valid ranges."""
        return -90 <= self.latitude <= 90 and -180 <= self.longitude <= 180


def parse_gps_location(location_str: str | None) -> GPSCoordinate | None:
    """
    Parse GPS location string from CommCare format.

    Args:
        location_str: String in format "lat lon altitude accuracy"
                      e.g., "11.0618924 7.7045044 0.0 300.0"

    Returns:
        GPSCoordinate object or None if parsing fails
    """
    if not location_str:
        return None

    try:
        parts = str(location_str).strip().split()
        if len(parts) < 2:
            return None

        lat = float(parts[0])
        lon = float(parts[1])
        altitude = float(parts[2]) if len(parts) > 2 else None
        accuracy = float(parts[3]) if len(parts) > 3 else None

        coord = GPSCoordinate(
            latitude=lat,
            longitude=lon,
            altitude=altitude,
            accuracy=accuracy,
        )

        if not coord.is_valid():
            return None

        return coord
    except (ValueError, TypeError, IndexError):
        return None


# Earth's radius in meters
EARTH_RADIUS_METERS = 6_371_000


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """
    Calculate the great-circle distance between two GPS points using Haversine formula.

    Args:
        lat1, lon1: First point coordinates in decimal degrees
        lat2, lon2: Second point coordinates in decimal degrees

    Returns:
        Distance in meters
    """
    # Convert to radians
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)

    # Haversine formula
    a = math.sin(delta_lat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon / 2) ** 2

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return EARTH_RADIUS_METERS * c


def calculate_distance(coord1: GPSCoordinate, coord2: GPSCoordinate) -> float:
    """
    Calculate distance between two GPSCoordinate objects.

    Args:
        coord1: First GPS coordinate
        coord2: Second GPS coordinate

    Returns:
        Distance in meters
    """
    return haversine_distance(coord1.latitude, coord1.longitude, coord2.latitude, coord2.longitude)


def calculate_path_distance(coords: list[GPSCoordinate]) -> float:
    """
    Calculate total path distance through a sequence of coordinates.

    Args:
        coords: List of GPSCoordinate objects in order of travel

    Returns:
        Total distance in meters
    """
    if len(coords) < 2:
        return 0.0

    total = 0.0
    for i in range(1, len(coords)):
        total += calculate_distance(coords[i - 1], coords[i])

    return total


def meters_to_km(meters: float) -> float:
    """Convert meters to kilometers."""
    return meters / 1000.0


def format_distance(meters: float) -> str:
    """
    Format distance for display.

    Args:
        meters: Distance in meters

    Returns:
        Formatted string (e.g., "1.5 km" or "450 m")
    """
    if meters >= 1000:
        return f"{meters / 1000:.1f} km"
    return f"{int(meters)} m"
