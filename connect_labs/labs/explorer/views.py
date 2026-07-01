"""
Views for Labs Data Explorer
"""

import json
import logging
from collections.abc import Generator

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView
from django_tables2 import SingleTableView

from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, BaseSSEStreamView, send_sse_event
from connect_labs.labs.explorer.analysis_config import VISIT_INSPECTOR_CONFIG
from connect_labs.labs.explorer.data_access import RecordExplorerDataAccess
from connect_labs.labs.explorer.forms import (
    RecordEditForm,
    RecordFilterForm,
    RecordUploadForm,
    VisitInspectorFilterForm,
)
from connect_labs.labs.explorer.sql_validator import build_safe_query
from connect_labs.labs.explorer.tables import LabsRecordTable
from connect_labs.labs.explorer.utils import (
    export_records_to_json,
    filter_records_by_date,
    format_json,
    generate_export_filename,
    parse_date_range,
    validate_import_data,
)

logger = logging.getLogger(__name__)


class ExplorerIndexView(LoginRequiredMixin, TemplateView):
    """
    Landing page for Labs Explorer.

    Shows cards for Labs Record and Visit Inspector.
    """

    template_name = "labs/explorer/index.html"


class RecordListView(LoginRequiredMixin, SingleTableView):
    """Main list view for browsing LabsRecord data."""

    table_class = LabsRecordTable
    template_name = "labs/explorer/list.html"
    paginate_by = 25
    _all_records_cache = None  # Cache for all unfiltered records

    def _get_all_records_once(self):
        """Get all records from API once and cache them."""
        if self._all_records_cache is not None:
            return self._all_records_cache

        # Check for context - if no context, fetch public records instead
        labs_context = getattr(self.request, "labs_context", {})
        has_context = (
            labs_context.get("opportunity_id") or labs_context.get("program_id") or labs_context.get("organization_id")
        )

        # Fetch all records once (no filters - we'll filter in Python)
        try:
            data_access = RecordExplorerDataAccess(request=self.request)
            try:
                if has_context:
                    self._all_records_cache = data_access.get_all_records()
                else:
                    # No context selected - fetch public records
                    self._all_records_cache = data_access.get_public_records()
            finally:
                data_access.close()
        except Exception as e:
            logger.error(f"Failed to get records: {e}")
            messages.error(self.request, f"Failed to load records: {e}")
            self._all_records_cache = []

        return self._all_records_cache

    def get_queryset(self):
        """Get filtered records from cached data."""
        # Get all records (cached)
        all_records = self._get_all_records_once()
        if not all_records:
            return []

        # Get filter parameters
        experiment = self.request.GET.get("experiment", "")
        type_filter = self.request.GET.get("type", "")
        username = self.request.GET.get("username", "")
        date_start = self.request.GET.get("date_created_start", "")
        date_end = self.request.GET.get("date_created_end", "")

        # Filter records in Python
        filtered_records = all_records

        if experiment:
            filtered_records = [r for r in filtered_records if r.experiment == experiment]
        if type_filter:
            filtered_records = [r for r in filtered_records if r.type == type_filter]
        if username:
            filtered_records = [r for r in filtered_records if r.username == username]

        # Apply date filtering
        if date_start or date_end:
            start_date, end_date = parse_date_range(date_start, date_end)
            filtered_records = filter_records_by_date(filtered_records, start_date, end_date, "date_created")

        # Sort by id descending (most recent records first)
        return sorted(filtered_records, key=lambda x: x.id, reverse=True)

    def get_context_data(self, **kwargs):
        """Add filter form and other context."""
        context = super().get_context_data(**kwargs)

        # Check for context
        labs_context = getattr(self.request, "labs_context", {})
        context["has_context"] = bool(
            labs_context.get("opportunity_id") or labs_context.get("program_id") or labs_context.get("organization_id")
        )

        # Check for OAuth token
        labs_oauth = self.request.session.get("labs_oauth", {})
        context["has_connect_token"] = bool(labs_oauth.get("access_token"))

        # Get distinct values for filter choices from cached records
        experiment_choices = []
        type_choices = []
        if context["has_context"] and context["has_connect_token"]:
            try:
                # Use cached records to get distinct values (no extra API call)
                all_records = self._get_all_records_once()
                experiment_choices = sorted(list({r.experiment for r in all_records if r.experiment}))
                type_choices = sorted(list({r.type for r in all_records if r.type}))
            except Exception as e:
                logger.error(f"Failed to get distinct values: {e}")

        # Create filter form with choices
        context["filter_form"] = RecordFilterForm(
            data=self.request.GET,
            experiment_choices=experiment_choices,
            type_choices=type_choices,
        )

        return context


class RecordEditView(LoginRequiredMixin, TemplateView):
    """Dedicated page for editing a record's JSON data."""

    template_name = "labs/explorer/edit.html"

    def get_context_data(self, **kwargs):
        """Load record and create edit form."""
        context = super().get_context_data(**kwargs)
        record_id = kwargs.get("pk")

        # Get record from API
        try:
            data_access = RecordExplorerDataAccess(request=self.request)
            try:
                record = data_access.get_record_by_id(record_id)
                if not record:
                    messages.error(self.request, f"Record {record_id} not found")
                    return context

                context["record"] = record

                # Create form with current data
                if self.request.method == "GET":
                    form = RecordEditForm(initial={"data": format_json(record.data)})
                    context["form"] = form
            finally:
                data_access.close()
        except Exception as e:
            logger.error(f"Failed to load record: {e}")
            messages.error(self.request, f"Failed to load record: {e}")

        return context

    def post(self, request, *args, **kwargs):
        """Handle form submission to update record."""
        record_id = kwargs.get("pk")
        form = RecordEditForm(data=request.POST)

        if form.is_valid():
            # Get updated data from form
            new_data = form.cleaned_data["data"]

            # Update record via API
            try:
                data_access = RecordExplorerDataAccess(request=request)
                try:
                    data_access.update_record(
                        record_id=record_id,
                        data=new_data,
                    )
                    messages.success(request, f"Record {record_id} updated successfully")
                    return redirect(reverse("explorer:list"))
                finally:
                    data_access.close()
            except Exception as e:
                logger.error(f"Failed to update record: {e}")
                messages.error(request, f"Failed to update record: {e}")

        # If validation failed or update failed, show form with errors
        context = self.get_context_data(**kwargs)
        context["form"] = form
        return render(request, self.template_name, context)


class DownloadRecordsView(LoginRequiredMixin, View):
    """Download records as JSON file."""

    def get(self, request):
        """Handle download request."""
        # Get selected record IDs or download all filtered
        selected_ids = request.GET.getlist("selected_ids")

        # Get filter parameters
        experiment = request.GET.get("experiment", "")
        type_filter = request.GET.get("type", "")
        username = request.GET.get("username", "")
        date_start = request.GET.get("date_created_start", "")
        date_end = request.GET.get("date_created_end", "")

        try:
            data_access = RecordExplorerDataAccess(request=request)
            try:
                # Get records based on filters or selected IDs
                if selected_ids:
                    # Download only selected records
                    all_records = data_access.get_all_records()
                    records = [r for r in all_records if str(r.id) in selected_ids]
                else:
                    # Download all filtered records
                    records = data_access.get_all_records(
                        experiment=experiment if experiment else None,
                        type=type_filter if type_filter else None,
                        username=username if username else None,
                    )

                    # Apply date filtering
                    if date_start or date_end:
                        start_date, end_date = parse_date_range(date_start, date_end)
                        records = filter_records_by_date(records, start_date, end_date, "date_created")

                # Export to JSON
                json_content = export_records_to_json(records)
                filename = generate_export_filename(experiment if experiment else None)

                # Create response
                response = HttpResponse(json_content, content_type="application/json")
                response["Content-Disposition"] = f'attachment; filename="{filename}"'
                return response
            finally:
                data_access.close()
        except Exception as e:
            logger.error(f"Failed to download records: {e}")
            messages.error(request, f"Failed to download records: {e}")
            return redirect(reverse("explorer:list"))


class UploadRecordsView(LoginRequiredMixin, View):
    """Upload/import records from JSON file."""

    def post(self, request):
        """Handle file upload."""
        form = RecordUploadForm(data=request.POST, files=request.FILES)

        if form.is_valid():
            # Get parsed data from form
            records_data = form.parsed_data

            # Validate structure
            is_valid, error, validated_data = validate_import_data(json.dumps(records_data))
            if not is_valid:
                messages.error(request, f"Invalid data: {error}")
                return redirect(reverse("explorer:list"))

            # Import records
            try:
                data_access = RecordExplorerDataAccess(request=request)
                try:
                    created_records = data_access.bulk_create_records(validated_data)
                    messages.success(request, f"Successfully imported {len(created_records)} record(s)")
                finally:
                    data_access.close()
            except Exception as e:
                logger.error(f"Failed to import records: {e}")
                messages.error(request, f"Failed to import records: {e}")
        else:
            # Show form errors
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field}: {error}")

        return redirect(reverse("explorer:list"))


class DeleteRecordsView(LoginRequiredMixin, View):
    """Delete selected records."""

    def post(self, request):
        """Handle delete request."""
        # Get selected record IDs from POST data
        selected_ids = request.POST.getlist("selected_ids")

        if not selected_ids:
            messages.error(request, "No records selected for deletion")
            return redirect(reverse("explorer:list"))

        # Convert to integers
        try:
            record_ids = [int(id_str) for id_str in selected_ids]
        except ValueError:
            messages.error(request, "Invalid record IDs")
            return redirect(reverse("explorer:list"))

        # Delete records via API
        try:
            data_access = RecordExplorerDataAccess(request=request)
            try:
                data_access.delete_records(record_ids)
                messages.success(request, f"Successfully deleted {len(record_ids)} record(s)")
            finally:
                data_access.close()
        except Exception as e:
            logger.error(f"Failed to delete records: {e}")
            messages.error(request, f"Failed to delete records: {e}")

        return redirect(reverse("explorer:list"))


class VisitInspectorView(LoginRequiredMixin, TemplateView):
    """Visit Inspector page for downloading and querying raw visit data."""

    template_name = "labs/explorer/visit_inspector.html"

    def get_context_data(self, **kwargs):
        """Provide initial page context - actual data loading happens via SSE."""
        context = super().get_context_data(**kwargs)

        # Check for context
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        context["has_context"] = bool(opportunity_id)
        context["opportunity_id"] = opportunity_id

        # Check for OAuth token
        labs_oauth = self.request.session.get("labs_oauth", {})
        context["has_connect_token"] = bool(labs_oauth.get("access_token"))

        # Provide the SSE stream API URL
        context["stream_api_url"] = reverse("explorer:visit_inspector_stream")

        # Create filter form
        context["filter_form"] = VisitInspectorFilterForm()

        return context


class VisitInspectorQueryView(LoginRequiredMixin, View):
    """Handle SQL query execution for visit filtering."""

    def post(self, request):
        """Execute user's SQL WHERE clause and return results."""
        form = VisitInspectorFilterForm(data=request.POST)

        if not form.is_valid():
            # Return errors as JSON
            errors = []
            for field, field_errors in form.errors.items():
                for error in field_errors:
                    errors.append(f"{field}: {error}")
            return JsonResponse({"success": False, "errors": errors}, status=400)

        # Get validated WHERE clause
        where_clause = form.cleaned_data["where_clause"]

        # Get opportunity_id from context
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            return JsonResponse({"success": False, "errors": ["No opportunity selected"]}, status=400)

        try:
            # Build safe query
            query, params = build_safe_query(opportunity_id, where_clause, limit=1000)

            # Execute query in read-only transaction
            # This provides an additional safety layer - even if validation is bypassed,
            # PostgreSQL will reject any write operations (INSERT, UPDATE, DELETE, etc.)
            with transaction.atomic():
                with connection.cursor() as cursor:
                    # Set transaction to read-only mode
                    cursor.execute("SET TRANSACTION READ ONLY")

                    # Execute the user's query
                    cursor.execute(query, params)
                    columns = [col[0] for col in cursor.description]
                    rows = cursor.fetchall()

            # Convert to list of dicts
            results = []
            for row in rows:
                row_dict = {}
                for col, val in zip(columns, row):
                    # Convert date/datetime to string for JSON serialization
                    if hasattr(val, "isoformat"):
                        row_dict[col] = val.isoformat()
                    else:
                        row_dict[col] = val
                results.append(row_dict)

            logger.info(f"[VisitInspector] Query returned {len(results)} results")

            return JsonResponse(
                {
                    "success": True,
                    "count": len(results),
                    "results": results,
                }
            )

        except Exception as e:
            logger.error(f"[VisitInspector] Query failed: {e}")
            return JsonResponse({"success": False, "errors": [f"Query failed: {str(e)}"]}, status=500)


class VisitViewView(LoginRequiredMixin, TemplateView):
    """View a single visit's data in a formatted page."""

    template_name = "labs/explorer/visit_view.html"

    def get_context_data(self, **kwargs):
        """Load visit and display in formatted view."""
        context = super().get_context_data(**kwargs)
        visit_id = kwargs.get("visit_id")

        # Get opportunity_id from context
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            messages.error(self.request, "No opportunity selected")
            return context

        try:
            # Query the SQL cache for this visit
            query = """
                SELECT visit_id, username, visit_date, status, deliver_unit,
                       entity_id, entity_name, flagged, form_json
                FROM labs_raw_visit_cache
                WHERE opportunity_id = %s AND visit_id = %s
                LIMIT 1
            """

            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(query, [opportunity_id, visit_id])
                    row = cursor.fetchone()

            if not row:
                messages.error(self.request, f"Visit {visit_id} not found")
                return context

            # Extract data
            context["visit_id"] = row[0]
            context["username"] = row[1]
            context["visit_date"] = row[2]
            context["status"] = row[3]
            context["deliver_unit"] = row[4]
            context["entity_id"] = row[5]
            context["entity_name"] = row[6]
            context["flagged"] = row[7]
            form_json = row[8]

            # Format JSON for display
            if isinstance(form_json, str):
                form_json = json.loads(form_json)

            context["visit_json"] = json.dumps(form_json, indent=2, ensure_ascii=False, separators=(",", ": "))
            context["visit_data"] = True

            logger.info(f"[VisitInspector] Viewing visit {visit_id}")

        except Exception as e:
            logger.error(f"[VisitInspector] Failed to load visit {visit_id}: {e}")
            messages.error(self.request, f"Failed to load visit: {e}")
            context["visit_data"] = False

        return context


class DownloadVisitView(LoginRequiredMixin, View):
    """Download a single visit's form_json as a .json file."""

    def get(self, request, visit_id):
        """Retrieve visit and return as downloadable JSON."""
        # Get opportunity_id from context
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            messages.error(request, "No opportunity selected")
            return redirect(reverse("explorer:visit_inspector"))

        try:
            # Query the SQL cache for this visit
            query = """
                SELECT visit_id, username, visit_date, status, form_json
                FROM labs_raw_visit_cache
                WHERE opportunity_id = %s AND visit_id = %s
                LIMIT 1
            """

            with transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute("SET TRANSACTION READ ONLY")
                    cursor.execute(query, [opportunity_id, visit_id])
                    row = cursor.fetchone()

            if not row:
                messages.error(request, f"Visit {visit_id} not found")
                return redirect(reverse("explorer:visit_inspector"))

            # Extract form_json (5th column)
            form_json = row[4]

            # Check if form_json is already a dict or needs parsing
            if isinstance(form_json, str):
                # If it's a string, parse it first
                form_json = json.loads(form_json)

            # Format as pretty JSON with minimal escaping
            json_content = json.dumps(form_json, indent=2, ensure_ascii=False, separators=(",", ": "))

            # Create response with download header and UTF-8 encoding
            response = HttpResponse(json_content, content_type="application/json; charset=utf-8")
            response["Content-Disposition"] = f'attachment; filename="opp{opportunity_id}-visit_{visit_id}.json"'

            logger.info(f"[VisitInspector] Downloaded visit {visit_id}")
            return response

        except Exception as e:
            logger.error(f"[VisitInspector] Failed to download visit {visit_id}: {e}")
            messages.error(request, f"Failed to download visit: {e}")
            return redirect(reverse("explorer:visit_inspector"))


class VisitInspectorStreamView(AnalysisPipelineSSEMixin, BaseSSEStreamView):
    """
    SSE streaming endpoint for Visit Inspector with real-time progress.

    Uses the base SSE infrastructure to stream analysis pipeline events.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream visit data loading progress via SSE."""
        try:
            # Check for context
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id")

            if not opportunity_id:
                yield send_sse_event("Error", error="No opportunity selected")
                return

            # Check for OAuth token
            labs_oauth = request.session.get("labs_oauth", {})
            if not labs_oauth.get("access_token"):
                yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                return

            # Run analysis pipeline with streaming using mixin
            pipeline = AnalysisPipeline(request)
            pipeline_stream = pipeline.stream_analysis(VISIT_INSPECTOR_CONFIG)

            # Stream all pipeline events as SSE (using improved mixin)
            yield from self.stream_pipeline_events(pipeline_stream)

            # Result is now available in self._pipeline_result
            result = self._pipeline_result
            from_cache = self._pipeline_from_cache

            # Build completion response
            if result:
                visit_count = len(result.rows) if hasattr(result, "rows") else 0
                logger.info(f"[VisitInspector] Cached {visit_count} visits for opportunity {opportunity_id}")

                yield send_sse_event(
                    "Complete",
                    data={
                        "visit_count": visit_count,
                        "opportunity_id": opportunity_id,
                        "from_cache": from_cache,
                    },
                )

        except Exception as e:
            logger.error(f"[VisitInspector] Stream failed: {e}")
            yield send_sse_event("Error", error=f"Failed to load visit data: {str(e)}")


# =============================================================================
# Cache Manager Views
# =============================================================================


class CacheManagerView(LoginRequiredMixin, TemplateView):
    """
    Cache Manager page for inspecting and managing analysis cache.

    Shows all cache entries with details and provides filtering and deletion options.
    """

    template_name = "labs/explorer/cache_manager.html"

    def get_context_data(self, **kwargs):
        """Provide initial page context."""
        context = super().get_context_data(**kwargs)

        # Get filter parameters
        opportunity_filter = self.request.GET.get("opportunity_id", "")
        cache_type_filter = self.request.GET.get("cache_type", "")
        show_expired_only = self.request.GET.get("show_expired", "") == "on"

        context["opportunity_filter"] = opportunity_filter
        context["cache_type_filter"] = cache_type_filter
        context["show_expired_only"] = show_expired_only

        # Get cache details from SQL backend
        from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
        from connect_labs.labs.explorer.utils import (
            get_cache_type_display,
            is_cache_expired,
            is_cache_expiring_soon,
            truncate_config_hash,
        )

        try:
            all_entries = SQLCacheManager.get_cache_details()

            # Apply filters
            filtered_entries = all_entries

            if opportunity_filter:
                try:
                    opp_id = int(opportunity_filter)
                    filtered_entries = [e for e in filtered_entries if e["opportunity_id"] == opp_id]
                except ValueError:
                    pass

            if cache_type_filter:
                filtered_entries = [e for e in filtered_entries if e["cache_type"] == cache_type_filter]

            if show_expired_only:
                filtered_entries = [e for e in filtered_entries if is_cache_expired(e["expires_at"])]

            # Add display fields
            for entry in filtered_entries:
                entry["cache_type_display"] = get_cache_type_display(entry["cache_type"])
                entry["config_hash_short"] = truncate_config_hash(entry["config_hash"])
                entry["is_expired"] = is_cache_expired(entry["expires_at"])
                entry["is_expiring_soon"] = is_cache_expiring_soon(entry["expires_at"])

            # Calculate summary stats
            total_entries = len(filtered_entries)
            total_rows = sum(e["row_count"] for e in filtered_entries)
            expired_count = sum(1 for e in filtered_entries if e["is_expired"])

            context["cache_entries"] = filtered_entries
            context["total_entries"] = total_entries
            context["total_rows"] = total_rows
            context["expired_count"] = expired_count

            # Get distinct opportunities and cache types for filters
            all_opportunities = SQLCacheManager.get_all_opportunities_with_cache()
            cache_types = ["raw", "computed_visit", "computed_flw"]

            context["all_opportunities"] = all_opportunities
            context["cache_types"] = cache_types

        except Exception as e:
            logger.error(f"[CacheManager] Failed to load cache details: {e}")
            messages.error(self.request, f"Failed to load cache details: {e}")
            context["cache_entries"] = []
            context["total_entries"] = 0
            context["total_rows"] = 0
            context["expired_count"] = 0
            context["all_opportunities"] = []
            context["cache_types"] = []

        return context


class CacheDeleteView(LoginRequiredMixin, View):
    """Handle cache deletion requests."""

    def post(self, request):
        """Execute cache deletion based on mode."""
        from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
        from connect_labs.labs.analysis.backends.sql.models import ComputedFLWCache, ComputedVisitCache, RawVisitCache

        mode = request.POST.get("mode")

        try:
            if mode == "opportunity":
                # Delete all cache for an opportunity
                opportunity_id = int(request.POST.get("opportunity_id"))
                result = SQLCacheManager.delete_all_cache(opportunity_id)

                return JsonResponse(
                    {
                        "success": True,
                        "message": f"Deleted all cache for opportunity {opportunity_id}",
                        "deleted": result,
                    }
                )

            elif mode == "config":
                # Delete cache for specific opportunity + config
                opportunity_id = int(request.POST.get("opportunity_id"))
                config_hash = request.POST.get("config_hash")

                result = SQLCacheManager.delete_config_cache(opportunity_id, config_hash)

                return JsonResponse(
                    {
                        "success": True,
                        "message": f"Deleted config cache for opportunity {opportunity_id}, config {config_hash[:8]}",
                        "deleted": result,
                    }
                )

            elif mode == "expired":
                # Delete all expired entries
                raw_deleted = RawVisitCache.cleanup_expired()
                visit_deleted = ComputedVisitCache.cleanup_expired()
                flw_deleted = ComputedFLWCache.cleanup_expired()

                return JsonResponse(
                    {
                        "success": True,
                        "message": "Deleted all expired cache entries",
                        "deleted": {
                            "raw": raw_deleted,
                            "computed_visit": visit_deleted,
                            "computed_flw": flw_deleted,
                        },
                    }
                )

            elif mode == "selective":
                # Delete selected entries
                selected_entries = json.loads(request.POST.get("selected_entries", "[]"))

                deleted_counts = {"raw": 0, "computed_visit": 0, "computed_flw": 0}

                for entry in selected_entries:
                    cache_type = entry["cache_type"]
                    opportunity_id = entry["opportunity_id"]
                    config_hash = entry.get("config_hash")

                    if cache_type == "raw":
                        count = RawVisitCache.objects.filter(opportunity_id=opportunity_id).delete()[0]
                        deleted_counts["raw"] += count
                    elif cache_type == "computed_visit":
                        if config_hash:
                            count = ComputedVisitCache.objects.filter(
                                opportunity_id=opportunity_id, config_hash=config_hash
                            ).delete()[0]
                            deleted_counts["computed_visit"] += count
                    elif cache_type == "computed_flw":
                        if config_hash:
                            count = ComputedFLWCache.objects.filter(
                                opportunity_id=opportunity_id, config_hash=config_hash
                            ).delete()[0]
                            deleted_counts["computed_flw"] += count

                return JsonResponse(
                    {
                        "success": True,
                        "message": f"Deleted {len(selected_entries)} cache entries",
                        "deleted": deleted_counts,
                    }
                )

            else:
                return JsonResponse(
                    {
                        "success": False,
                        "error": f"Invalid deletion mode: {mode}",
                    },
                    status=400,
                )

        except Exception as e:
            logger.error(f"[CacheManager] Delete failed: {e}")
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Deletion failed: {str(e)}",
                },
                status=500,
            )


class CacheStatsAPIView(LoginRequiredMixin, View):
    """AJAX endpoint for cache statistics."""

    def get(self, request):
        """Return cache statistics as JSON."""
        from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager

        try:
            opportunity_id = request.GET.get("opportunity_id")

            if opportunity_id:
                # Get stats for specific opportunity
                stats = SQLCacheManager.get_cache_stats(int(opportunity_id))

                return JsonResponse(
                    {
                        "success": True,
                        "opportunity_id": int(opportunity_id),
                        "stats": stats,
                    }
                )
            else:
                # Get global stats
                all_entries = SQLCacheManager.get_cache_details()

                total_entries = len(all_entries)
                total_rows = sum(e["row_count"] for e in all_entries)

                return JsonResponse(
                    {
                        "success": True,
                        "total_entries": total_entries,
                        "total_rows": total_rows,
                    }
                )

        except Exception as e:
            logger.error(f"[CacheManager] Stats API failed: {e}")
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Failed to get cache stats: {str(e)}",
                },
                status=500,
            )


# =============================================================================
# Task Manager Views
# =============================================================================


class TaskManagerView(LoginRequiredMixin, TemplateView):
    """
    Task Manager page for inspecting and managing Celery tasks.

    Shows active, scheduled, and recent tasks with the ability to terminate them.
    """

    template_name = "labs/explorer/task_manager.html"

    def get_context_data(self, **kwargs):
        """Provide task information context."""
        context = super().get_context_data(**kwargs)

        from config.celery_app import app as celery_app

        # Get filter parameters
        task_name_filter = self.request.GET.get("task_name", "")
        status_filter = self.request.GET.get("status", "")

        context["task_name_filter"] = task_name_filter
        context["status_filter"] = status_filter

        try:
            # Get active tasks from all workers
            inspect = celery_app.control.inspect()

            active_tasks = []
            scheduled_tasks = []
            reserved_tasks = []

            # Active tasks (currently executing)
            active = inspect.active() or {}
            for worker, tasks in active.items():
                for task in tasks:
                    task_info = self._format_task_info(task, worker, "active")
                    if self._matches_filters(task_info, task_name_filter, status_filter):
                        active_tasks.append(task_info)

            # Scheduled tasks (with ETA/countdown)
            scheduled = inspect.scheduled() or {}
            for worker, tasks in scheduled.items():
                for task in tasks:
                    task_info = self._format_task_info(task, worker, "scheduled")
                    if self._matches_filters(task_info, task_name_filter, status_filter):
                        scheduled_tasks.append(task_info)

            # Reserved tasks (received but not started)
            reserved = inspect.reserved() or {}
            for worker, tasks in reserved.items():
                for task in tasks:
                    task_info = self._format_task_info(task, worker, "reserved")
                    if self._matches_filters(task_info, task_name_filter, status_filter):
                        reserved_tasks.append(task_info)

            # Get recent task results from backend (if available)
            recent_tasks = self._get_recent_tasks(task_name_filter, status_filter)

            context["active_tasks"] = active_tasks
            context["scheduled_tasks"] = scheduled_tasks
            context["reserved_tasks"] = reserved_tasks
            context["recent_tasks"] = recent_tasks

            # Summary stats
            context["active_count"] = len(active_tasks)
            context["scheduled_count"] = len(scheduled_tasks)
            context["reserved_count"] = len(reserved_tasks)
            context["recent_count"] = len(recent_tasks)

            # Get unique task names for filter dropdown
            all_task_names = set()
            for task in active_tasks + scheduled_tasks + reserved_tasks + recent_tasks:
                all_task_names.add(task.get("name", "unknown"))
            context["task_names"] = sorted(all_task_names)

            context["workers_online"] = bool(active or scheduled or reserved)

        except Exception as e:
            logger.error(f"[TaskManager] Failed to get task info: {e}")
            context["error"] = f"Failed to connect to Celery workers: {e}"
            context["active_tasks"] = []
            context["scheduled_tasks"] = []
            context["reserved_tasks"] = []
            context["recent_tasks"] = []
            context["active_count"] = 0
            context["scheduled_count"] = 0
            context["reserved_count"] = 0
            context["recent_count"] = 0
            context["task_names"] = []
            context["workers_online"] = False

        return context

    def _format_task_info(self, task: dict, worker: str, status: str) -> dict:
        """Format task info for display."""
        from datetime import datetime

        # Handle different task info formats
        task_id = task.get("id") or task.get("request", {}).get("id", "unknown")
        task_name = task.get("name") or task.get("request", {}).get("name", "unknown")
        args = task.get("args") or task.get("request", {}).get("args", [])
        kwargs_data = task.get("kwargs") or task.get("request", {}).get("kwargs", {})

        # Get timing info
        time_start = task.get("time_start")
        eta = task.get("eta")

        # Calculate runtime if active
        runtime = None
        if time_start:
            try:
                start_time = datetime.fromtimestamp(time_start)
                runtime = (datetime.now() - start_time).total_seconds()
            except (TypeError, ValueError):
                pass

        return {
            "id": task_id,
            "name": task_name,
            "name_short": task_name.split(".")[-1] if "." in task_name else task_name,
            "args": args,
            "kwargs": kwargs_data,
            "worker": worker,
            "status": status,
            "time_start": time_start,
            "eta": eta,
            "runtime": runtime,
            "runtime_display": self._format_runtime(runtime) if runtime else None,
        }

    def _format_runtime(self, seconds: float) -> str:
        """Format runtime in human-readable form."""
        if seconds < 60:
            return f"{seconds:.1f}s"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}m"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}h"

    def _matches_filters(self, task: dict, task_name_filter: str, status_filter: str) -> bool:
        """Check if task matches current filters."""
        if task_name_filter and task.get("name") != task_name_filter:
            return False
        if status_filter and task.get("status") != status_filter:
            return False
        return True

    def _get_recent_tasks(self, task_name_filter: str, status_filter: str) -> list:
        """Get recent tasks from workflow run states."""
        # This gets recent workflow jobs from the database
        # since Celery's Redis backend doesn't keep a full history
        from django.db import connection

        recent = []

        try:
            # Query workflow runs that have active_job state
            # This gives us visibility into workflow-related tasks
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT
                        id,
                        data->'state'->'active_job'->>'job_id' as task_id,
                        data->'state'->'active_job'->>'job_type' as job_type,
                        data->'state'->'active_job'->>'status' as status,
                        data->'state'->'active_job'->>'started_at' as started_at,
                        data->'state'->'active_job'->>'completed_at' as completed_at,
                        data->'state'->'active_job'->>'failed_at' as failed_at,
                        data->'state'->'active_job'->>'error' as error,
                        data->'state'->'active_job'->'summary' as summary
                    FROM labs_labsrecord
                    WHERE data->'state'->'active_job'->>'job_id' IS NOT NULL
                    ORDER BY (data->'state'->'active_job'->>'started_at')::timestamp DESC NULLS LAST
                    LIMIT 50
                """
                )

                columns = [col[0] for col in cursor.description]
                for row in cursor.fetchall():
                    row_dict = dict(zip(columns, row))

                    task_info = {
                        "id": row_dict.get("task_id"),
                        "name": "connect_labs.workflow.tasks.run_workflow_job",
                        "name_short": row_dict.get("job_type") or "workflow_job",
                        "status": row_dict.get("status") or "unknown",
                        "started_at": row_dict.get("started_at"),
                        "completed_at": row_dict.get("completed_at"),
                        "failed_at": row_dict.get("failed_at"),
                        "error": row_dict.get("error"),
                        "summary": row_dict.get("summary"),
                        "run_id": row_dict.get("id"),
                        "source": "workflow_run",
                    }

                    # Apply filters
                    if task_name_filter and task_info["name"] != task_name_filter:
                        continue
                    if status_filter:
                        if status_filter == "completed" and task_info["status"] != "completed":
                            continue
                        if status_filter == "failed" and task_info["status"] != "failed":
                            continue
                        if status_filter == "running" and task_info["status"] != "running":
                            continue

                    recent.append(task_info)

        except Exception as e:
            logger.warning(f"[TaskManager] Could not fetch recent workflow tasks: {e}")

        return recent


class TaskKillView(LoginRequiredMixin, View):
    """Handle task termination requests."""

    def post(self, request):
        """Terminate a task by ID."""
        from config.celery_app import app as celery_app

        task_id = request.POST.get("task_id")
        terminate = request.POST.get("terminate", "false") == "true"

        if not task_id:
            return JsonResponse(
                {"success": False, "error": "task_id is required"},
                status=400,
            )

        try:
            # Revoke the task
            # terminate=True sends SIGTERM to the worker process
            # terminate=False just prevents the task from executing if not started
            celery_app.control.revoke(task_id, terminate=terminate)

            action = "terminated" if terminate else "revoked"
            logger.info(f"[TaskManager] Task {task_id} {action}")

            return JsonResponse(
                {
                    "success": True,
                    "message": f"Task {task_id} has been {action}",
                    "task_id": task_id,
                    "action": action,
                }
            )

        except Exception as e:
            logger.error(f"[TaskManager] Failed to kill task {task_id}: {e}")
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Failed to kill task: {str(e)}",
                },
                status=500,
            )


class TaskStatusAPIView(LoginRequiredMixin, View):
    """AJAX endpoint for task status."""

    def get(self, request, task_id):
        """Get status of a specific task."""
        from celery.result import AsyncResult

        try:
            result = AsyncResult(task_id)
            meta = result._get_task_meta()

            return JsonResponse(
                {
                    "success": True,
                    "task_id": task_id,
                    "status": meta.get("status"),
                    "result": meta.get("result") if meta.get("status") == "SUCCESS" else None,
                    "error": str(meta.get("result")) if meta.get("status") == "FAILURE" else None,
                    "info": result.info if isinstance(result.info, dict) else None,
                }
            )

        except Exception as e:
            logger.error(f"[TaskManager] Failed to get task status: {e}")
            return JsonResponse(
                {
                    "success": False,
                    "error": f"Failed to get task status: {str(e)}",
                },
                status=500,
            )


# =============================================================================
# App Downloader Views
# =============================================================================


class AppDownloaderView(LoginRequiredMixin, TemplateView):
    """
    View for downloading CommCare apps (CCZ files) for active opportunities.

    Lists all active opportunities (is_active=True, end_date >= today) and allows
    users to select opportunities and download their learn or deliver apps.
    """

    template_name = "labs/explorer/app_downloader.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check for OAuth token
        labs_oauth = self.request.session.get("labs_oauth", {})
        has_token = bool(labs_oauth.get("access_token"))
        context["has_connect_token"] = has_token

        if not has_token:
            context["opportunities"] = []
            context["error"] = "Please connect to CommCare Connect to view opportunities."
            return context

        # Fetch active opportunities from API
        try:
            from connect_labs.labs.explorer.app_data_access import AppDownloaderDataAccess

            with AppDownloaderDataAccess(request=self.request) as data_access:
                opportunities = data_access.get_active_opportunities()
                context["opportunities"] = opportunities
                context["opportunity_count"] = len(opportunities)
        except Exception as e:
            logger.error(f"[AppDownloader] Failed to fetch opportunities: {e}")
            context["opportunities"] = []
            context["error"] = f"Failed to load opportunities: {str(e)}"

        return context


class DownloadAppView(LoginRequiredMixin, View):
    """
    Redirect to CommCare HQ to download a CCZ file directly.

    This avoids proxying the download through our server - the browser
    downloads directly from CommCare HQ.
    """

    def get(self, request, opp_id: int, app_type: str):
        """
        Redirect to CommCare HQ CCZ download URL.

        Args:
            opp_id: Opportunity ID
            app_type: "learn" or "deliver"
        """
        if app_type not in ("learn", "deliver"):
            return JsonResponse({"error": "Invalid app_type. Must be 'learn' or 'deliver'"}, status=400)

        # Check for OAuth token
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Not authenticated"}, status=401)

        try:
            from connect_labs.labs.explorer.app_data_access import AppDownloaderDataAccess

            with AppDownloaderDataAccess(request=request) as data_access:
                # Get opportunity details
                details = data_access.get_opportunity_details(opp_id)
                if not details:
                    return JsonResponse({"error": f"Opportunity {opp_id} not found"}, status=404)

                # Get the app
                app_key = "learn_app" if app_type == "learn" else "deliver_app"
                app_data = details.get(app_key)

                if not app_data:
                    return JsonResponse(
                        {"error": f"No {app_type} app configured for this opportunity"},
                        status=404,
                    )

                # Extract app details
                domain = app_data.get("cc_domain")
                cc_app_id = app_data.get("cc_app_id")

                # Get HQ server URL
                hq_server_url = "https://www.commcarehq.org"
                if app_data.get("hq_server"):
                    hq_server_url = app_data["hq_server"].get("url", hq_server_url)

                if not domain or not cc_app_id:
                    return JsonResponse(
                        {"error": f"Missing domain or app_id for {app_type} app"},
                        status=400,
                    )

                # Build the direct CommCare HQ download URL
                ccz_url = f"{hq_server_url}/a/{domain}/apps/api/download_ccz/?app_id={cc_app_id}&latest=release"

                logger.info(f"[AppDownloader] Redirecting to CCZ download: {ccz_url}")

                # Redirect browser directly to CommCare HQ
                return redirect(ccz_url)

        except Exception as e:
            logger.error(f"[AppDownloader] Failed to get app info: {e}")
            return JsonResponse({"error": f"Failed to get app info: {str(e)}"}, status=500)


class BulkDownloadAppsView(LoginRequiredMixin, View):
    """
    Get direct CommCare HQ download URLs for multiple apps.

    Returns JSON with URLs that JavaScript can use to trigger
    multiple direct downloads from CommCare HQ.
    """

    def post(self, request):
        """
        Get download URLs for multiple apps.

        POST params:
            opp_ids: Comma-separated list of opportunity IDs
            app_type: "learn" or "deliver"

        Returns:
            JSON with list of download URLs and any errors
        """
        app_type = request.POST.get("app_type", "learn")
        opp_ids_str = request.POST.get("opp_ids", "")

        if app_type not in ("learn", "deliver"):
            return JsonResponse({"error": "Invalid app_type. Must be 'learn' or 'deliver'"}, status=400)

        if not opp_ids_str:
            return JsonResponse({"error": "No opportunities selected"}, status=400)

        # Parse opportunity IDs
        try:
            opp_ids = [int(x.strip()) for x in opp_ids_str.split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"error": "Invalid opportunity ID format"}, status=400)

        if not opp_ids:
            return JsonResponse({"error": "No valid opportunity IDs provided"}, status=400)

        # Check for OAuth token
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Not authenticated"}, status=401)

        try:
            from connect_labs.labs.explorer.app_data_access import AppDownloaderDataAccess

            with AppDownloaderDataAccess(request=request) as data_access:
                downloads = []
                errors = []

                for opp_id in opp_ids:
                    # Get opportunity details
                    details = data_access.get_opportunity_details(opp_id)
                    if not details:
                        errors.append(f"Opportunity {opp_id} not found")
                        continue

                    opp_name = details.get("name", f"opp_{opp_id}")

                    # Get the app
                    app_key = "learn_app" if app_type == "learn" else "deliver_app"
                    app_data = details.get(app_key)

                    if not app_data:
                        errors.append(f"No {app_type} app for: {opp_name}")
                        continue

                    # Extract app details
                    domain = app_data.get("cc_domain")
                    cc_app_id = app_data.get("cc_app_id")
                    app_name = app_data.get("name", f"{app_type}_app")

                    # Get HQ server URL
                    hq_server_url = "https://www.commcarehq.org"
                    if app_data.get("hq_server"):
                        hq_server_url = app_data["hq_server"].get("url", hq_server_url)

                    if not domain or not cc_app_id:
                        errors.append(f"Missing domain/app_id for: {opp_name}")
                        continue

                    # Build the direct CommCare HQ download URL
                    ccz_url = f"{hq_server_url}/a/{domain}/apps/api/download_ccz/?app_id={cc_app_id}&latest=release"

                    downloads.append(
                        {
                            "opp_id": opp_id,
                            "opp_name": opp_name,
                            "app_name": app_name,
                            "url": ccz_url,
                        }
                    )

                logger.info(f"[AppDownloader] Returning {len(downloads)} download URLs for {app_type} apps")

                return JsonResponse(
                    {
                        "success": True,
                        "downloads": downloads,
                        "errors": errors,
                    }
                )

        except Exception as e:
            logger.error(f"[AppDownloader] Failed to get download URLs: {e}")
            return JsonResponse({"error": f"Failed to get download URLs: {str(e)}"}, status=500)
