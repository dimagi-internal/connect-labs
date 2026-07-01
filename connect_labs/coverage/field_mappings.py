"""
Field mapping system for handling CommCare property name variations.

CommCare transforms uploaded column names (case changes, underscores, abbreviations).
This module provides a centralized mapping to handle variations across opportunities.
"""

from typing import Any

# Canonical field name -> list of known aliases
# First alias in list is preferred canonical name
DELIVERY_UNIT_FIELD_MAP = {
    # Core identification
    "service_area_id": ["service_area_number"],  # Actual unique SA identifier
    "service_area_unlock_order": ["service_area"],  # Order in which SAs are unlocked (1st, 2nd, 3rd, etc)
    "flw": ["flw", "FLW"],
    "du_id": ["du_id", "du_identifier"],
    # Geometry fields
    "wkt": ["WKT", "wkt", "geometry"],
    "centroid": ["centroid"],
    "bounding_box": ["bounding_box", "bbox"],
    "radius": ["radius"],
    # Counts and measurements
    "buildings": ["buildings", "building_count"],
    "surface_area": ["surface_area", "area"],
    "delivery_count": ["delivery_count"],
    "delivery_target": ["delivery_target", "target"],
    # Distance fields
    "distance_btw_adj_1": ["distance_btw_adj_1", "distance_between_adj_sides_1"],
    "distance_btw_adj_2": ["distance_btw_adj_2", "distance_between_adj_sides_2"],
    # Administrative/geographic metadata
    "oa": ["oa", "OA", "operational_area"],
    "ward_name": ["ward_name", "Ward Name", "ward"],
    "llo": ["llo", "LLO", "local_level_org"],
    # Status/workflow
    "du_status": ["du_status", "status"],
    "visited": ["visited"],
    "checked_in_date": ["checked_in_date", "check_in_date"],
    "checked_out_date": ["checked_out_date", "check_out_date"],
    "du_checkout_remark": ["du_checkout_remark", "checkout_remark"],
    "force_close_status": ["force_close_status"],
    "force_closure_date": ["force_closure_date"],
    "force_closure_request_status": ["force_closure_request_status"],
    # Additional metadata
    "max_round": ["max_round", "rounds"],
    "is_single_building": ["is_single_building"],
    "distance_to_nearest_multi_building_du": ["distance_to_nearest_multi_building_du"],
    "nearest_multi_building_du_id": ["nearest_multi_building_du_id"],
    "nearest_multi_building_du_name": ["nearest_multi_building_du_name"],
    "nearest_multi_building_du_count": ["nearest_multi_building_du_count"],
}


def get_property_with_fallback(properties: dict[str, Any], canonical_name: str) -> Any:
    """
    Get a property value using canonical name with fallback to known aliases.

    Args:
        properties: Dictionary of CommCare case properties
        canonical_name: Canonical field name (key from DELIVERY_UNIT_FIELD_MAP)

    Returns:
        Property value if found, None otherwise

    Example:
        >>> props = {"service_area": "74", "FLW": "worker1"}
        >>> get_property_with_fallback(props, "service_area")
        "74"
        >>> get_property_with_fallback(props, "flw")
        "worker1"
    """
    aliases = DELIVERY_UNIT_FIELD_MAP.get(canonical_name, [canonical_name])

    for alias in aliases:
        if alias in properties:
            return properties[alias]

    return None


def get_unmapped_properties(properties: dict[str, Any]) -> set[str]:
    """
    Find property keys that aren't mapped in DELIVERY_UNIT_FIELD_MAP.

    Useful for detecting new fields added to CommCare that need mapping.

    Args:
        properties: Dictionary of CommCare case properties

    Returns:
        Set of property keys not found in any field mapping
    """
    all_known_aliases = set()
    for aliases in DELIVERY_UNIT_FIELD_MAP.values():
        all_known_aliases.update(aliases)

    unmapped = set(properties.keys()) - all_known_aliases
    return unmapped
