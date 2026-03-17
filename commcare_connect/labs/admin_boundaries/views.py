"""
Views for Admin Boundaries management UI.
"""

import json
import logging
import re
from collections.abc import Generator

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.serializers import serialize
from django.db import models
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.labs.admin_boundaries.models import AdminBoundary
from commcare_connect.labs.admin_boundaries.services import (
    CountrySourceRegistry,
    GeoPoDELoader,
    get_loader,
    stream_load_country,
)
from commcare_connect.labs.analysis.sse_streaming import BaseSSEStreamView, send_sse_event

logger = logging.getLogger(__name__)


class AdminBoundariesView(LoginRequiredMixin, TemplateView):
    """
    Admin Boundaries management page.

    Shows loaded boundaries with ability to view, delete, and load new data.
    Uses single-country workflow with registry-based source selection.
    """

    template_name = "labs/explorer/admin_boundaries.html"

    def get_context_data(self, **kwargs):
        """Provide boundary statistics, registry data, and filtered boundaries."""
        context = super().get_context_data(**kwargs)

        # Get filter parameters
        country_filter = self.request.GET.get("country", "")
        level_filter = self.request.GET.get("level", "")
        source_filter = self.request.GET.get("source", "")

        context["country_filter"] = country_filter
        context["level_filter"] = level_filter
        context["source_filter"] = source_filter

        # Provide source choices for the template
        context["source_choices"] = AdminBoundary.Source.choices

        # Get country source registry data for the load form
        registry = CountrySourceRegistry()
        configured_countries = registry.get_all_countries()

        # Build country configs for JavaScript (serialize to JSON-friendly format)
        country_configs = {}
        for country in configured_countries:
            country_configs[country.iso_code] = {
                "name": country.name,
                "sources": {},
                "recommended": country.recommended,
            }
            for source_id in country.get_available_sources():
                source_config = country.get_source_config(source_id)
                if source_config:
                    country_configs[country.iso_code]["sources"][source_id] = {
                        "type": source_config.source_type,
                        "max_level": source_config.max_level,
                        "levels": list(country.get_available_levels(source_id)),
                    }

        context["configured_countries"] = configured_countries
        context["country_configs_json"] = json.dumps(country_configs)

        try:
            # Get summary by country and source
            countries_summary = list(AdminBoundary.get_countries_summary())

            # Calculate totals
            total_boundaries = sum(c["total"] for c in countries_summary)
            # Count unique countries (not country+source combinations)
            unique_countries = len({c["iso_code"] for c in countries_summary})

            context["countries_summary"] = countries_summary
            context["total_boundaries"] = total_boundaries
            context["total_countries"] = unique_countries

            # Get all distinct countries for filter (from loaded data)
            context["all_countries"] = sorted({c["iso_code"] for c in countries_summary})

            # Get filtered boundaries for table (limit to 100 for performance)
            boundaries_qs = AdminBoundary.objects.all()

            if country_filter:
                boundaries_qs = boundaries_qs.filter(iso_code=country_filter)
            if level_filter:
                boundaries_qs = boundaries_qs.filter(admin_level=int(level_filter))
            if source_filter:
                boundaries_qs = boundaries_qs.filter(source=source_filter)

            # Only show first 100 for table view
            context["boundaries"] = boundaries_qs[:100]
            context["boundaries_count"] = boundaries_qs.count()
            context["showing_limited"] = boundaries_qs.count() > 100

        except Exception as e:
            logger.error(f"[AdminBoundaries] Failed to load data: {e}")
            messages.error(self.request, f"Failed to load boundary data: {e}")
            context["countries_summary"] = []
            context["total_boundaries"] = 0
            context["total_countries"] = 0
            context["all_countries"] = []
            context["boundaries"] = []
            context["boundaries_count"] = 0
            context["showing_limited"] = False

        return context


class LoadBoundariesView(LoginRequiredMixin, View):
    """Handle boundary loading requests from the web UI."""

    def post(self, request):
        """Load boundaries for specified countries and levels."""
        try:
            # Parse request body
            data = json.loads(request.body)
            iso_codes_raw = data.get("iso_codes", "")
            levels = data.get("levels", [0, 1, 2])
            clear = data.get("clear", False)
            source = data.get("source", "geoboundaries")

            # Parse ISO codes - support comma/space separated string or list
            if isinstance(iso_codes_raw, str):
                # Split by comma, space, or newline and clean up
                iso_codes = [code.strip().upper() for code in re.split(r"[,\s\n]+", iso_codes_raw) if code.strip()]
            else:
                iso_codes = [code.upper() for code in iso_codes_raw if code]

            # Validate
            if not iso_codes:
                return JsonResponse(
                    {"success": False, "error": "No country codes provided"},
                    status=400,
                )

            # Validate ISO codes (should be 3 uppercase letters)
            invalid_codes = [code for code in iso_codes if not re.match(r"^[A-Z]{3}$", code)]
            if invalid_codes:
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Invalid ISO codes: {', '.join(invalid_codes)}. Must be 3-letter codes.",
                    },
                    status=400,
                )

            # Ensure levels are integers
            try:
                levels = [int(level) for level in levels]
            except (TypeError, ValueError):
                return JsonResponse(
                    {"success": False, "error": "Invalid admin levels"},
                    status=400,
                )

            # Validate source
            valid_sources = [choice[0] for choice in AdminBoundary.Source.choices]
            if source not in valid_sources:
                return JsonResponse(
                    {"success": False, "error": f"Invalid source: {source}. Must be one of: {valid_sources}"},
                    status=400,
                )

            logger.info(f"[AdminBoundaries] Loading from {source}: {iso_codes}, levels={levels}, clear={clear}")

            # Get the appropriate loader for the source
            loader = get_loader(source)
            results = []
            total_loaded = 0
            progress_messages = []

            def on_progress(msg: str):
                progress_messages.append(msg)

            for iso_code in iso_codes:
                result = loader.load_country(
                    iso_code=iso_code,
                    levels=levels,
                    clear=clear,
                    on_progress=on_progress,
                )

                country_result = {
                    "iso_code": iso_code,
                    "source": source,
                    "total_loaded": result.total_loaded,
                    "cleared": result.cleared,
                    "success": result.success,
                    "levels": [],
                }

                for level_result in result.levels:
                    country_result["levels"].append(
                        {
                            "level": level_result.level,
                            "success": level_result.success,
                            "count": level_result.count,
                            "message": level_result.message,
                            "error": level_result.error,
                        }
                    )

                results.append(country_result)
                total_loaded += result.total_loaded

            return JsonResponse(
                {
                    "success": True,
                    "source": source,
                    "total_loaded": total_loaded,
                    "results": results,
                    "progress": progress_messages,
                }
            )

        except json.JSONDecodeError:
            return JsonResponse(
                {"success": False, "error": "Invalid JSON in request body"},
                status=400,
            )
        except Exception as e:
            logger.error(f"[AdminBoundaries] Load failed: {e}", exc_info=True)
            return JsonResponse(
                {"success": False, "error": f"Loading failed: {str(e)}"},
                status=500,
            )


class DeleteBoundariesView(LoginRequiredMixin, View):
    """Handle boundary deletion requests."""

    def post(self, request):
        """Delete boundaries by country and optionally source."""
        iso_code = request.POST.get("iso_code")
        source = request.POST.get("source", "")  # Optional: delete only from specific source

        if not iso_code:
            return JsonResponse({"success": False, "error": "No country specified"}, status=400)

        try:
            iso_code = iso_code.upper()
            qs = AdminBoundary.objects.filter(iso_code=iso_code)

            if source:
                qs = qs.filter(source=source)
                source_label = f" ({source})"
            else:
                source_label = ""

            deleted_count, _ = qs.delete()

            logger.info(f"[AdminBoundaries] Deleted {deleted_count} boundaries for {iso_code}{source_label}")

            return JsonResponse(
                {
                    "success": True,
                    "message": f"Deleted {deleted_count} boundaries for {iso_code}{source_label}",
                    "deleted_count": deleted_count,
                }
            )

        except Exception as e:
            logger.error(f"[AdminBoundaries] Delete failed: {e}")
            return JsonResponse({"success": False, "error": f"Deletion failed: {str(e)}"}, status=500)


class BoundaryGeoJSONView(LoginRequiredMixin, View):
    """Serve boundaries as GeoJSON for map visualization."""

    def get(self, request, iso_code, admin_level):
        """Return GeoJSON FeatureCollection for specified country and level."""
        iso_code = iso_code.upper()
        source = request.GET.get("source", "")  # Optional source filter

        try:
            boundaries = AdminBoundary.objects.filter(iso_code=iso_code, admin_level=admin_level)

            if source:
                boundaries = boundaries.filter(source=source)

            if not boundaries.exists():
                return JsonResponse({"error": f"No boundaries found for {iso_code} ADM{admin_level}"}, status=404)

            # Serialize to GeoJSON
            geojson_str = serialize(
                "geojson",
                boundaries,
                geometry_field="geometry",
                fields=["name", "name_local", "boundary_id", "admin_level", "iso_code", "source"],
            )

            return JsonResponse(json.loads(geojson_str), safe=False)

        except Exception as e:
            logger.error(f"[AdminBoundaries] GeoJSON export failed: {e}")
            return JsonResponse({"error": f"Failed to export GeoJSON: {str(e)}"}, status=500)


class BoundaryStatsAPIView(LoginRequiredMixin, View):
    """AJAX endpoint for boundary statistics."""

    def get(self, request):
        """Return boundary statistics as JSON."""
        try:
            iso_code = request.GET.get("iso_code")

            if iso_code:
                # Get stats for specific country
                iso_code = iso_code.upper()
                boundaries = AdminBoundary.objects.filter(iso_code=iso_code)

                stats = {
                    "iso_code": iso_code,
                    "total": boundaries.count(),
                    "by_level": {},
                    "by_source": {},
                }

                for level in range(5):
                    count = boundaries.filter(admin_level=level).count()
                    if count > 0:
                        stats["by_level"][f"ADM{level}"] = count

                for source_choice in AdminBoundary.Source.choices:
                    count = boundaries.filter(source=source_choice[0]).count()
                    if count > 0:
                        stats["by_source"][source_choice[1]] = count

                return JsonResponse({"success": True, "stats": stats})
            else:
                # Get global stats
                countries_summary = list(AdminBoundary.get_countries_summary())

                return JsonResponse(
                    {
                        "success": True,
                        "total_countries": len({c["iso_code"] for c in countries_summary}),
                        "total_boundaries": sum(c["total"] for c in countries_summary),
                        "countries": countries_summary,
                    }
                )

        except Exception as e:
            logger.error(f"[AdminBoundaries] Stats API failed: {e}")
            return JsonResponse({"success": False, "error": f"Failed to get stats: {str(e)}"}, status=500)


class LoadBoundariesStreamView(BaseSSEStreamView):
    """
    SSE streaming endpoint for boundary loading with real-time progress.

    Uses Server-Sent Events to stream progress messages while downloading
    boundary data from geoBoundaries, OpenStreetMap, GRID3, or HDX.

    Single-country workflow: loads one country at a time for better UX.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream boundary loading progress via SSE."""
        try:
            # Parse query parameters (single country workflow)
            iso_code = request.GET.get("iso_code", "").strip().upper()
            levels_raw = request.GET.get("levels", "0,1,2")
            source = request.GET.get("source", "geoboundaries")
            clear = request.GET.get("clear", "false").lower() == "true"

            # Validate ISO code
            if not iso_code:
                yield send_sse_event("Error: No country code provided", error="No country code provided")
                return

            if not re.match(r"^[A-Z]{3}$", iso_code):
                error = f"Invalid ISO code: {iso_code}"
                yield send_sse_event(f"Error: {error}", error=error)
                return

            # Parse levels
            try:
                levels = [int(level.strip()) for level in levels_raw.split(",") if level.strip()]
            except ValueError:
                yield send_sse_event("Error: Invalid admin levels", error="Invalid admin levels")
                return

            # Validate source
            valid_sources = [choice[0] for choice in AdminBoundary.Source.choices]
            if source not in valid_sources:
                error = f"Invalid source: {source}"
                yield send_sse_event(f"Error: {error}", error=error)
                return

            logger.info(f"[AdminBoundaries/SSE] Starting stream: {iso_code}, levels={levels}, source={source}")

            # Stream the loading process (single country)
            for event_type, event_data in stream_load_country(iso_code, levels, source, clear):
                if event_type == "status":
                    yield send_sse_event(event_data.get("message", "Processing..."))
                elif event_type == "result":
                    # Final result - include the data
                    yield send_sse_event(
                        f"Complete! Loaded {event_data.get('total_loaded', 0)} boundaries",
                        data=event_data,
                    )
                elif event_type == "error":
                    yield send_sse_event(
                        f"Error: {event_data.get('error', 'Unknown error')}",
                        error=event_data.get("error"),
                    )

        except Exception as e:
            logger.error(f"[AdminBoundaries/SSE] Stream error: {e}", exc_info=True)
            yield send_sse_event(f"Error: {str(e)}", error=str(e))


class UploadGeoPoDEView(LoginRequiredMixin, View):
    """Handle GeoPoDe ZIP file uploads."""

    def post(self, request):
        """Process uploaded GeoPoDe ZIP file."""
        try:
            uploaded_file = request.FILES.get("file")
            clear = request.POST.get("clear", "false").lower() == "true"

            if not uploaded_file:
                return JsonResponse(
                    {"success": False, "error": "No file uploaded"},
                    status=400,
                )

            # Check file extension
            if not uploaded_file.name.endswith(".zip"):
                return JsonResponse(
                    {"success": False, "error": "File must be a ZIP file"},
                    status=400,
                )

            logger.info(f"[GeoPoDe] Processing upload: {uploaded_file.name}")

            # Process the ZIP file
            loader = GeoPoDELoader()
            result = loader.load_from_zip(uploaded_file, clear=clear)

            if result.success:
                return JsonResponse(
                    {
                        "success": True,
                        "message": f"Loaded {result.total_loaded} boundaries for {result.iso_code}",
                        "iso_code": result.iso_code,
                        "total_loaded": result.total_loaded,
                        "levels": [
                            {
                                "level": r.level,
                                "success": r.success,
                                "count": r.count,
                                "message": r.message,
                                "error": r.error,
                            }
                            for r in result.levels
                        ],
                    }
                )
            else:
                errors = [r.error for r in result.levels if r.error]
                return JsonResponse(
                    {
                        "success": False,
                        "error": errors[0] if errors else "Failed to load boundaries",
                        "levels": [
                            {
                                "level": r.level,
                                "success": r.success,
                                "count": r.count,
                                "message": r.message,
                                "error": r.error,
                            }
                            for r in result.levels
                        ],
                    },
                    status=400,
                )

        except Exception as e:
            logger.error(f"[GeoPoDe] Upload failed: {e}", exc_info=True)
            return JsonResponse(
                {"success": False, "error": f"Upload failed: {str(e)}"},
                status=500,
            )


class UploadGeoPoDEStreamView(BaseSSEStreamView):
    """
    SSE streaming endpoint for GeoPoDe file upload with real-time progress.

    Note: This view expects the file to be uploaded first via UploadGeoPoDEView,
    which stores it temporarily. For now, we use a simpler approach where the
    upload happens via regular POST and this view isn't used.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream GeoPoDe loading progress via SSE."""
        # For file uploads, we can't use SSE directly since the file needs
        # to be uploaded first. The UploadGeoPoDEView handles this synchronously.
        yield send_sse_event(
            "GeoPoDe uploads are processed synchronously. Use the upload form.",
            error="Use POST to /upload/ instead",
        )


class BoundaryCoverageAPIView(LoginRequiredMixin, View):
    """
    API endpoint to get admin boundary coverage for an opportunity.

    Returns which admin boundaries contain visits for the specified opportunity,
    using efficient PostGIS spatial queries on cached visit data.

    Requires that the analysis pipeline has been run for the opportunity
    (visits must be cached in ComputedVisitCache).
    """

    def get(self, request):
        """
        Get boundary coverage for an opportunity.

        Query params:
            opportunity_id (required): Opportunity ID to analyze
            iso_code (required): Country ISO code (e.g., KEN, NGA)
            levels (optional): Comma-separated admin levels (default: 1,2)

        Returns:
            JSON with boundary coverage summary
        """
        from commcare_connect.labs.admin_boundaries.services import get_opp_boundary_coverage

        # Parse required params
        opportunity_id = request.GET.get("opportunity_id")
        iso_code = request.GET.get("iso_code", "").upper()

        if not opportunity_id:
            return JsonResponse(
                {"success": False, "error": "opportunity_id is required"},
                status=400,
            )

        if not iso_code:
            return JsonResponse(
                {"success": False, "error": "iso_code is required"},
                status=400,
            )

        try:
            opportunity_id = int(opportunity_id)
        except ValueError:
            return JsonResponse(
                {"success": False, "error": "opportunity_id must be an integer"},
                status=400,
            )

        # Parse optional levels
        levels_raw = request.GET.get("levels", "1,2")
        try:
            admin_levels = [int(level.strip()) for level in levels_raw.split(",") if level.strip()]
        except ValueError:
            return JsonResponse(
                {"success": False, "error": "levels must be comma-separated integers"},
                status=400,
            )

        # Check if boundaries exist for this country
        boundaries_exist = AdminBoundary.objects.filter(iso_code=iso_code, admin_level__in=admin_levels).exists()

        if not boundaries_exist:
            return JsonResponse(
                {
                    "success": False,
                    "error": f"No admin boundaries found for {iso_code} at levels {admin_levels}. "
                    f"Load boundaries first.",
                },
                status=404,
            )

        try:
            result = get_opp_boundary_coverage(
                opportunity_id=opportunity_id,
                iso_code=iso_code,
                admin_levels=admin_levels,
            )

            return JsonResponse(
                {
                    "success": True,
                    **result.to_dict(),
                }
            )

        except ValueError as e:
            # No cached data for opportunity
            return JsonResponse(
                {"success": False, "error": str(e)},
                status=404,
            )
        except Exception as e:
            logger.error(f"[BoundaryCoverage] Error: {e}", exc_info=True)
            return JsonResponse(
                {"success": False, "error": f"Failed to get boundary coverage: {str(e)}"},
                status=500,
            )


class AvailableCountriesAPIView(LoginRequiredMixin, View):
    """
    API endpoint to get list of countries with loaded admin boundaries.

    Used by UI components to populate country selector dropdowns.
    """

    def get(self, request):
        """
        Get list of countries with loaded boundaries.

        Returns:
            JSON with list of ISO codes and boundary counts
        """
        try:
            # Get unique countries with their boundary counts
            countries = (
                AdminBoundary.objects.values("iso_code")
                .annotate(
                    total=models.Count("id"),
                    max_level=models.Max("admin_level"),
                )
                .order_by("iso_code")
            )

            return JsonResponse(
                {
                    "success": True,
                    "countries": [
                        {
                            "iso_code": c["iso_code"],
                            "total_boundaries": c["total"],
                            "max_level": c["max_level"],
                        }
                        for c in countries
                    ],
                }
            )

        except Exception as e:
            logger.error(f"[AvailableCountries] Error: {e}", exc_info=True)
            return JsonResponse(
                {"success": False, "error": f"Failed to get countries: {str(e)}"},
                status=500,
            )


class BoundaryMapView(LoginRequiredMixin, TemplateView):
    """
    Map visualization for admin boundaries across multiple opportunities.

    Displays a Leaflet choropleth map showing boundaries colored by visit count.
    Supports filtering by opportunity IDs, country, or funder.
    """

    template_name = "labs/explorer/boundary_map.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get filter parameters
        opp_ids_param = self.request.GET.get("opps", "")
        country_filter = self.request.GET.get("country", "")
        funder_filter = self.request.GET.get("funder", "")
        admin_level = self.request.GET.get("level", "1")

        context["opp_ids_param"] = opp_ids_param
        context["country_filter"] = country_filter
        context["funder_filter"] = funder_filter
        context["admin_level"] = admin_level

        # Load enrichment data to get available filters
        enrichment_data = self._load_enrichment_data()

        # Get unique countries and funders for filter dropdowns
        countries = sorted({e.get("iso_code", "") for e in enrichment_data if e.get("iso_code")})
        funders = sorted({e.get("funder", "") for e in enrichment_data if e.get("funder")})

        context["available_countries"] = countries
        context["available_funders"] = funders
        context["enrichment_count"] = len(enrichment_data)

        # Get available admin levels from loaded boundaries
        available_levels = (
            AdminBoundary.objects.values_list("admin_level", flat=True).distinct().order_by("admin_level")
        )
        context["available_levels"] = list(available_levels)

        return context

    def _load_enrichment_data(self) -> list:
        """Load enrichment data from production LabsRecord.

        NOTE: The enrichment feature was removed when the old solicitations app
        was deleted in the rename refactor. See the old implementation at:
        https://github.com/jjackson/connect-labs/blob/8da9b746/commcare_connect/solicitations/data_access.py
        (SolicitationDataAccess.get_enrichment_record and OppOrgEnrichmentRecord model)
        """
        return []


class BoundaryMapAPIView(LoginRequiredMixin, View):
    """
    API endpoint to get aggregated boundary GeoJSON for multiple opportunities.

    Aggregates visit counts across selected opportunities and returns GeoJSON
    suitable for choropleth visualization.
    """

    def get(self, request):
        """
        Get aggregated boundary GeoJSON.

        Query params:
            opps (optional): Comma-separated opportunity IDs
            countries (optional): Comma-separated ISO codes to include
            funders (optional): Comma-separated funder names to include
            levels (optional): Comma-separated admin levels or 'all'

        Returns:
            GeoJSON FeatureCollection with visit_count properties
        """
        # Parse parameters
        opp_ids_param = request.GET.get("opps", "")
        countries_param = request.GET.get("countries", "")
        funders_param = request.GET.get("funders", "")
        levels_param = request.GET.get("levels", "all")

        # Parse multi-value filters
        country_filters = [c.strip().upper() for c in countries_param.split(",") if c.strip()]
        funder_filters = [f.strip() for f in funders_param.split(",") if f.strip()]

        # Parse admin levels
        show_all_levels = levels_param.lower() == "all"
        selected_levels = []
        if not show_all_levels:
            try:
                selected_levels = [int(x.strip()) for x in levels_param.split(",") if x.strip()]
            except ValueError:
                return JsonResponse(
                    {"success": False, "error": "levels must be comma-separated integers or 'all'"},
                    status=400,
                )

        # Load enrichment data
        enrichment_data = self._load_enrichment_data()

        # Filter enrichments based on parameters
        filtered_enrichments = enrichment_data

        if opp_ids_param:
            try:
                opp_ids = [int(x.strip()) for x in opp_ids_param.split(",") if x.strip()]
                filtered_enrichments = [e for e in filtered_enrichments if e.get("opportunity_id") in opp_ids]
            except ValueError:
                return JsonResponse(
                    {"success": False, "error": "Invalid opportunity IDs"},
                    status=400,
                )

        # Filter by countries (if any selected)
        if country_filters:
            filtered_enrichments = [
                e for e in filtered_enrichments if e.get("iso_code", "").upper() in country_filters
            ]

        # Filter by funders (if any selected)
        if funder_filters:
            filtered_enrichments = [e for e in filtered_enrichments if e.get("funder", "") in funder_filters]

        # Only include enrichments that have admin_boundaries data
        filtered_enrichments = [e for e in filtered_enrichments if e.get("admin_boundaries")]

        if not filtered_enrichments:
            return JsonResponse(
                {
                    "success": True,
                    "type": "FeatureCollection",
                    "features": [],
                    "metadata": {
                        "total_opps": 0,
                        "admin_levels": levels_param,
                        "message": "No enriched opportunities match the filters",
                    },
                }
            )

        # Aggregate boundary visit counts across opportunities
        # boundary_id -> {"name": str, "visit_count": int, "opp_count": int, "admin_level": int}
        boundary_visits = {}

        for enrichment in filtered_enrichments:
            admin_boundaries = enrichment.get("admin_boundaries", {})

            # Determine which levels to process
            if show_all_levels:
                levels_to_process = admin_boundaries.keys()
            else:
                levels_to_process = [str(lvl) for lvl in selected_levels]

            for level_str in levels_to_process:
                boundaries_at_level = admin_boundaries.get(level_str, [])
                for boundary in boundaries_at_level:
                    bid = boundary.get("boundary_id", "")
                    if not bid:
                        continue

                    if bid not in boundary_visits:
                        boundary_visits[bid] = {
                            "name": boundary.get("name", ""),
                            "visit_count": 0,
                            "opp_count": 0,
                            "iso_code": enrichment.get("iso_code", ""),
                            "admin_level": int(level_str),
                        }

                    boundary_visits[bid]["visit_count"] += boundary.get("visit_count", 0)
                    boundary_visits[bid]["opp_count"] += 1

        if not boundary_visits:
            level_desc = "any level" if show_all_levels else f"ADM {levels_param}"
            return JsonResponse(
                {
                    "success": True,
                    "type": "FeatureCollection",
                    "features": [],
                    "metadata": {
                        "total_opps": len(filtered_enrichments),
                        "admin_levels": levels_param,
                        "message": f"No {level_desc} boundaries found in enrichment data",
                    },
                }
            )

        # Fetch actual geometries from database
        boundary_ids = list(boundary_visits.keys())
        if show_all_levels:
            boundaries = AdminBoundary.objects.filter(boundary_id__in=boundary_ids)
        else:
            boundaries = AdminBoundary.objects.filter(
                boundary_id__in=boundary_ids,
                admin_level__in=selected_levels,
            )

        # Build GeoJSON features
        features = []
        max_visits = max(b["visit_count"] for b in boundary_visits.values()) if boundary_visits else 1

        for boundary in boundaries:
            bid = boundary.boundary_id
            stats = boundary_visits.get(bid, {})

            # Calculate color intensity based on visit count
            visit_count = stats.get("visit_count", 0)
            intensity = visit_count / max_visits if max_visits > 0 else 0

            feature = {
                "type": "Feature",
                "properties": {
                    "boundary_id": bid,
                    "name": boundary.name,
                    "admin_level": stats.get("admin_level", boundary.admin_level),
                    "iso_code": boundary.iso_code,
                    "visit_count": visit_count,
                    "opp_count": stats.get("opp_count", 0),
                    "intensity": intensity,
                },
                "geometry": json.loads(boundary.geometry.geojson),
            }
            features.append(feature)

        return JsonResponse(
            {
                "success": True,
                "type": "FeatureCollection",
                "features": features,
                "metadata": {
                    "total_opps": len(filtered_enrichments),
                    "total_boundaries": len(features),
                    "admin_levels": levels_param,
                    "max_visits": max_visits,
                    "filters": {
                        "opps": opp_ids_param,
                        "countries": countries_param,
                        "funders": funders_param,
                    },
                },
            }
        )

    def _load_enrichment_data(self) -> list:
        """Load enrichment data from production LabsRecord.

        NOTE: The enrichment feature was removed when the old solicitations app
        was deleted in the rename refactor. See the old implementation at:
        https://github.com/jjackson/connect-labs/blob/8da9b746/commcare_connect/solicitations/data_access.py
        (SolicitationDataAccess.get_enrichment_record and OppOrgEnrichmentRecord model)
        """
        return []
