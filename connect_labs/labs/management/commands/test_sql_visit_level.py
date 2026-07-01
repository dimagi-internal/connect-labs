"""
Django management command to test SQL backend visit-level analysis.

Tests that both FLW-level (nutrition dashboard) and visit-level (coverage map)
work correctly with the SQL backend.

Usage:
    python manage.py test_sql_visit_level --opportunity-id 814
"""

import logging
import time

from django.core.management.base import BaseCommand

from connect_labs.coverage.analysis import get_coverage_visit_analysis
from connect_labs.custom_analysis.chc_nutrition.analysis_config import CHC_NUTRITION_CONFIG
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.integrations.connect.cli import create_cli_request

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test SQL backend with both FLW-level and visit-level analysis"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opportunity-id",
            type=int,
            required=True,
            help="Opportunity ID to analyze",
        )
        parser.add_argument(
            "--force-refresh",
            action="store_true",
            help="Force cache refresh (add ?refresh=1 to request)",
        )

    def handle(self, *args, **options):
        opportunity_id = options["opportunity_id"]
        force_refresh = options["force_refresh"]

        # Set up logging to see SQL backend logs
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )

        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write("SQL BACKEND VISIT-LEVEL ANALYSIS TEST")
        self.stdout.write("=" * 80)
        self.stdout.write("")

        # Create request with full labs context
        url_path = f"/coverage/?opportunity_id={opportunity_id}"
        if force_refresh:
            url_path += "&refresh=1"

        request = create_cli_request(
            opportunity_id=opportunity_id,
            url_path=url_path,
        )

        if not request:
            self.stdout.write(self.style.ERROR("Failed to authenticate. Please run: python manage.py get_cli_token"))
            return

        self.stdout.write(self.style.SUCCESS(f"[OK] Authenticated as: {request.user.username}"))

        # Show opportunity info
        opp = request.labs_context.get("opportunity")
        if opp:
            self.stdout.write(f"Opportunity: {opp.get('name')} (ID: {opportunity_id})")
            self.stdout.write(f"Visit count: {opp.get('visit_count', 0)}")

        # Check which backend is active
        pipeline = AnalysisPipeline(request)
        self.stdout.write(f"Backend: {pipeline.backend_name}")
        self.stdout.write("")

        try:
            # Test 1: FLW-level analysis (nutrition dashboard path)
            self.test_flw_level(request, opportunity_id)

            # Test 2: Visit-level analysis (coverage map path)
            self.test_visit_level(request, opportunity_id)

            # Summary
            self.stdout.write("")
            self.stdout.write("=" * 80)
            self.stdout.write(self.style.SUCCESS("ALL TESTS PASSED"))
            self.stdout.write("=" * 80)

        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Test failed: {e}"))
            logger.exception("Test failed")

    def test_flw_level(self, request, opportunity_id):
        """Test FLW-level analysis (nutrition dashboard path)."""
        self.stdout.write("")
        self.stdout.write("-" * 80)
        self.stdout.write("TEST 1: FLW-Level Analysis (Nutrition Dashboard)")
        self.stdout.write("-" * 80)
        self.stdout.write("")

        self.stdout.write("Config: CHC_NUTRITION_CONFIG")
        self.stdout.write(f"  terminal_stage: {CHC_NUTRITION_CONFIG.terminal_stage.value}")
        self.stdout.write(f"  fields: {len(CHC_NUTRITION_CONFIG.fields)}")
        self.stdout.write(f"  histograms: {len(CHC_NUTRITION_CONFIG.histograms)}")
        self.stdout.write("")

        self.stdout.write("Running analysis pipeline...")
        start = time.time()

        pipeline = AnalysisPipeline(request)
        result = pipeline.stream_analysis_ignore_events(CHC_NUTRITION_CONFIG)

        duration = time.time() - start

        self.stdout.write(self.style.SUCCESS(f"[OK] Complete in {duration:.2f}s"))
        self.stdout.write(f"  Result type: {type(result).__name__}")
        self.stdout.write(f"  Row count: {len(result.rows)}")

        if hasattr(result, "rows") and result.rows:
            first_row = result.rows[0]
            self.stdout.write(f"  First row type: {type(first_row).__name__}")
            self.stdout.write(f"  First row username: {first_row.username}")

            # Check for custom fields
            if hasattr(first_row, "custom_fields"):
                non_zero = {k: v for k, v in first_row.custom_fields.items() if v}
                self.stdout.write(f"  Custom fields (non-zero): {len(non_zero)}")
                if non_zero:
                    # Show first 3
                    for k, v in list(non_zero.items())[:3]:
                        self.stdout.write(f"    - {k}: {v}")

        # Verify it's an FLW result
        from connect_labs.labs.analysis.models import FLWAnalysisResult

        if isinstance(result, FLWAnalysisResult):
            self.stdout.write(self.style.SUCCESS("[OK] Returned FLWAnalysisResult as expected"))
        else:
            self.stdout.write(self.style.ERROR(f"[ERROR] Expected FLWAnalysisResult, got {type(result).__name__}"))
            raise AssertionError("Wrong result type for FLW-level analysis")

    def test_visit_level(self, request, opportunity_id):
        """Test visit-level analysis (coverage map path)."""
        self.stdout.write("")
        self.stdout.write("-" * 80)
        self.stdout.write("TEST 2: Visit-Level Analysis (Coverage Map)")
        self.stdout.write("-" * 80)
        self.stdout.write("")

        # Use the SAME config as Test 1 to verify visit-level cache is shared
        # This mimics real usage where coverage uses ?config=chc_nutrition
        self.stdout.write("Config: CHC_NUTRITION_CONFIG (same as Test 1 for cache sharing)")
        self.stdout.write("  terminal_stage will be overridden to: visit_level")
        self.stdout.write(f"  fields: {len(CHC_NUTRITION_CONFIG.fields)}")
        self.stdout.write("")

        # Use the coverage analysis function (mimics what coverage view does)
        # This will override terminal_stage to VISIT_LEVEL internally
        self.stdout.write("Running get_coverage_visit_analysis (no DU lookup)...")
        self.stdout.write("Expected: CACHE HIT since Test 1 should have cached visit-level data")
        start = time.time()

        result = get_coverage_visit_analysis(
            request=request,
            config=CHC_NUTRITION_CONFIG,  # Use same config as Test 1
            du_lookup=None,  # No enrichment for this test
            use_cache=True,
        )

        duration = time.time() - start

        self.stdout.write(self.style.SUCCESS(f"[OK] Complete in {duration:.2f}s"))
        self.stdout.write(f"  Result type: {type(result).__name__}")
        self.stdout.write(f"  Row count: {len(result.rows)}")

        if hasattr(result, "rows") and result.rows:
            first_row = result.rows[0]
            self.stdout.write(f"  First row type: {type(first_row).__name__}")
            self.stdout.write(f"  First row id: {first_row.id}")
            self.stdout.write(f"  First row username: {first_row.username}")

            # THE KEY TEST: Check that computed is a dict, not None
            self.stdout.write("")
            self.stdout.write("Checking row.computed (this is what was failing)...")
            if first_row.computed is None:
                self.stdout.write(self.style.ERROR("[ERROR] row.computed is None!"))
                raise AssertionError("row.computed is None - this is the bug we're fixing")
            elif not isinstance(first_row.computed, dict):
                self.stdout.write(self.style.ERROR(f"[ERROR] row.computed is {type(first_row.computed).__name__}"))
                raise AssertionError("row.computed is not a dict")
            else:
                self.stdout.write(
                    self.style.SUCCESS(f"[OK] row.computed is a dict with {len(first_row.computed)} keys")
                )
                if first_row.computed:
                    for k, v in list(first_row.computed.items())[:3]:
                        self.stdout.write(f"    - {k}: {v}")

            # Test the actual operation that was failing
            try:
                du_name = first_row.computed.get("du_name", "")
                self.stdout.write(self.style.SUCCESS(f"[OK] row.computed.get('du_name') = '{du_name}'"))
            except AttributeError as e:
                self.stdout.write(self.style.ERROR(f"[ERROR] row.computed.get() failed: {e}"))
                raise

        # Verify it's a Visit result
        from connect_labs.labs.analysis.models import VisitAnalysisResult

        if isinstance(result, VisitAnalysisResult):
            self.stdout.write(self.style.SUCCESS("[OK] Returned VisitAnalysisResult as expected"))
        else:
            self.stdout.write(self.style.ERROR(f"[ERROR] Expected VisitAnalysisResult, got {type(result).__name__}"))
            raise AssertionError("Wrong result type for visit-level analysis")

        # Test field metadata
        if hasattr(result, "field_metadata"):
            self.stdout.write(f"  Field metadata: {len(result.field_metadata)} fields")
