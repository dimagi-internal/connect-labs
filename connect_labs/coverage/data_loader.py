"""
Coverage map data loading and processing.

This module contains all the business logic for loading, processing, and formatting
coverage map data. It's used by both the synchronous and streaming API endpoints.
"""

import colorsys
import json
import logging
import random

from django.http import HttpRequest
from shapely.geometry import mapping

from connect_labs.coverage.analysis import get_coverage_visit_analysis
from connect_labs.coverage.models import FLW, CoverageData
from connect_labs.labs.analysis.config import AnalysisPipelineConfig
from connect_labs.labs.analysis.models import VisitRow

logger = logging.getLogger(__name__)


class CoverageMapDataLoader:
    """
    Helper class for loading and processing coverage map data.

    Handles:
    - Fetching DU polygons and visit data
    - Processing and enriching visits
    - Building GeoJSON layers
    - Generating FLW colors
    """

    def __init__(self, request: HttpRequest):
        """
        Initialize loader with request context.

        Args:
            request: HttpRequest with session, user, and query params
        """
        self.request = request

    def get_enriched_visits(
        self, config: AnalysisPipelineConfig, coverage: CoverageData
    ) -> tuple[list[VisitRow], list[dict]]:
        """
        Get visit analysis with coverage context, using cached data.

        Uses the pipeline pattern:
        1. Get cached VisitAnalysisResult (shared with CHC Nutrition and other views)
        2. Enrich with DU/SA geographic context

        Args:
            config: AnalysisPipelineConfig defining field computations
            coverage: CoverageData with DU info for service area lookup

        Returns:
            Tuple of (visit_rows, field_metadata)
        """
        # Build DU lookup for service area enrichment
        # Add BOTH id and name as keys since visits might use either
        du_lookup = {}
        for du in coverage.delivery_units.values():
            du_info = {"service_area_id": du.service_area_id}
            du_lookup[du.du_name] = du_info  # Add by name
            du_lookup[du.id] = du_info  # Add by case_id
            try:
                du_lookup[int(du.id)] = du_info  # Add as integer if possible
            except (ValueError, TypeError):
                pass

        # Get cached visit analysis with coverage enrichment
        result = get_coverage_visit_analysis(request=self.request, config=config, du_lookup=du_lookup, use_cache=True)

        return result.rows, result.field_metadata

    def populate_coverage_visits(self, coverage: CoverageData, visit_rows: list[VisitRow]) -> None:
        """
        Populate coverage data with visits from analysis results.

        Links visits to FLWs and DUs, enriching FLW objects with Connect data.
        FLWs are keyed by commcare_id throughout.

        Args:
            coverage: CoverageData to populate
            visit_rows: Analyzed visit rows from get_enriched_visits()
        """
        from connect_labs.coverage.models import LocalUserVisit
        from connect_labs.labs.analysis.data_access import get_flw_names_for_opportunity

        flw_display_names = get_flw_names_for_opportunity(self.request)
        skipped_count = 0
        enriched_count = 0
        created_count = 0

        for visit_row in visit_rows:
            point = LocalUserVisit(visit_row.to_dict())
            coverage.service_points.append(point)

            commcare_id = visit_row.computed.get("commcare_userid", "")
            connect_id = visit_row.username

            if not commcare_id:
                skipped_count += 1
                continue

            if commcare_id in coverage.flws:
                # Existing FLW (from DU data) - enrich with Connect data
                flw = coverage.flws[commcare_id]
                if not flw.connect_id:  # Only enrich once
                    flw.connect_id = connect_id
                    flw.display_name = flw_display_names.get(connect_id)
                    enriched_count += 1
            else:
                # New FLW (no DU data) - create with all data
                flw = FLW(
                    commcare_id=commcare_id, connect_id=connect_id, display_name=flw_display_names.get(connect_id)
                )
                coverage.flws[commcare_id] = flw
                created_count += 1

            # Update FLW with visit data
            flw.total_visits += 1
            flw.service_points.append(point)

            if visit_row.visit_date:
                visit_date = visit_row.visit_date.date()
                if visit_date not in flw.dates_active:
                    flw.dates_active.append(visit_date)

        coverage._compute_metadata()

        logger.info(
            f"[Coverage] Populated {len(coverage.service_points)} visits, "
            f"{len(coverage.flws)} active FLWs ({enriched_count} enriched, {created_count} created, "
            f"{len(coverage.flws) - enriched_count - created_count} DU-only)"
        )
        if skipped_count > 0:
            logger.warning(f"Skipped {skipped_count} visits without commcare_userid (no FLW link)")

    def build_du_geojson(self, coverage: CoverageData) -> str:
        """Convert DUs to GeoJSON (basic, no colors)"""
        features = []

        for du in coverage.delivery_units.values():
            try:
                if not du.wkt or du.wkt == "":
                    continue

                geometry = du.geometry
                features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(geometry),
                        "properties": {
                            "name": du.du_name,
                            "service_area": du.service_area_id,
                            "status": du.status or "unvisited",
                            "buildings": du.buildings,
                            "visits": len(du.service_points),
                            "flw_id": du.flw_commcare_id,
                        },
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to process DU {du.du_name} for GeoJSON: {e}")
                continue

        return json.dumps({"type": "FeatureCollection", "features": features})

    def build_colored_du_geojson(self, coverage: CoverageData, flw_colors: dict, commcare_to_username: dict) -> str:
        """Build DU GeoJSON with FLW colors (flw_colors keyed by commcare_id)"""
        features = []

        for du in coverage.delivery_units.values():
            try:
                if not du.wkt or du.wkt == "":
                    continue

                geometry = du.geometry

                # flw_colors is keyed by commcare_id
                flw_color = flw_colors.get(du.flw_commcare_id, "#999999")

                features.append(
                    {
                        "type": "Feature",
                        "geometry": mapping(geometry),
                        "properties": {
                            "name": du.du_name,
                            "service_area": du.service_area_id,
                            "status": du.status or "unvisited",
                            "buildings": du.buildings,
                            "visits": len(du.service_points),
                            "flw_id": du.flw_commcare_id,  # Use commcare_id for filtering
                            "color": flw_color,  # Frontend reads props.color
                        },
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to process DU {du.du_name} for colored GeoJSON: {e}")
                continue

        return json.dumps({"type": "FeatureCollection", "features": features})

    def build_enriched_points_geojson(self, visit_rows: list[VisitRow]) -> str:
        """Convert visit rows to GeoJSON with computed fields"""
        features = []

        for visit in visit_rows:
            try:
                if not visit.has_gps:
                    continue

                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [visit.longitude, visit.latitude]},
                        "properties": visit.to_geojson_properties(),
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to process enriched visit {visit.id} for GeoJSON: {e}")
                continue

        return json.dumps({"type": "FeatureCollection", "features": features})

    def build_colored_points_geojson(self, visit_rows: list[VisitRow], flws: dict, flw_colors: dict) -> str:
        """Build service points GeoJSON with FLW colors (flw_colors keyed by commcare_id)"""
        features = []

        for visit in visit_rows:
            try:
                if not visit.has_gps:
                    continue

                # flw_colors is keyed by commcare_id
                flw_id = visit.computed.get("commcare_userid", "")
                flw_color = flw_colors.get(flw_id, "#999999")

                properties = visit.to_geojson_properties()
                properties["flw_id"] = flw_id
                properties["color"] = flw_color  # Frontend reads props.color

                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [visit.longitude, visit.latitude]},
                        "properties": properties,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to process colored visit {visit.id} for GeoJSON: {e}")
                continue

        return json.dumps({"type": "FeatureCollection", "features": features})

    @staticmethod
    def generate_flw_colors(flw_dict: dict) -> dict:
        """
        Generate contrasting colors for FLWs using HSV color space.

        Args:
            flw_dict: Dictionary of FLWs {flw_id: FLW object}

        Returns:
            Dictionary mapping flw_id to hex color
        """
        flw_ids = list(flw_dict.keys())
        n = len(flw_ids)

        # Predefined high-contrast colors for first few FLWs
        base_colors = [
            "#FF0000",
            "#00FF00",
            "#0000FF",
            "#FFFF00",
            "#FF00FF",
            "#00FFFF",
            "#FF8000",
            "#FF0080",
            "#80FF00",
            "#00FF80",
            "#8000FF",
            "#0080FF",
            "#804000",
            "#FF4080",
            "#8080FF",
            "#80FF80",
            "#804080",
            "#408080",
        ]

        colors = []
        if n <= len(base_colors):
            colors = base_colors[:n]
        else:
            colors = list(base_colors)
            needed = n - len(base_colors)

            # Generate additional colors using HSV for good distribution
            for i in range(needed):
                hue = (i * 0.618033988749895) % 1.0  # Golden ratio
                saturation = 0.7 + (i % 3) * 0.15
                value = 0.6 + (i % 4) * 0.13

                r, g, b = colorsys.hsv_to_rgb(hue, saturation, value)
                color = f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
                colors.append(color)

        # Shuffle for better adjacent color contrast
        random.shuffle(colors)

        return {flw_id: colors[i] for i, flw_id in enumerate(flw_ids)}
