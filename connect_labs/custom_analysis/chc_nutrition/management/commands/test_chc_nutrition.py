"""
Django management command to test CHC Nutrition Analysis.

Similar to audit's run_audit_integration.py - runs analysis from command line with debugging.

Usage:
    # Run full analysis
    python manage.py test_chc_nutrition --opportunity-id 814

    # Debug options
    python manage.py test_chc_nutrition --opportunity-id 814 --debug-fields
    python manage.py test_chc_nutrition --opportunity-id 814 --show-form-structure
"""

import logging

from django.core.management.base import BaseCommand

from connect_labs.custom_analysis.chc_nutrition.analysis_config import CHC_NUTRITION_CONFIG
from connect_labs.labs.analysis.models import LocalUserVisit
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.integrations.connect.cli import create_cli_request

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Test CHC Nutrition Analysis with debugging options"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opportunity-id",
            type=int,
            required=True,
            help="Opportunity ID to analyze",
        )
        parser.add_argument(
            "--show-form-structure",
            action="store_true",
            help="Show the actual form_json structure to help debug paths",
        )
        parser.add_argument(
            "--debug-fields",
            action="store_true",
            help="Test each field extraction to see which ones work",
        )
        parser.add_argument(
            "--sample-size",
            type=int,
            default=5,
            help="Number of visits to sample for structure analysis (default: 5)",
        )

    def handle(self, *args, **options):
        opportunity_id = options["opportunity_id"]

        # Set up logging
        logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")

        # Create request with full labs context using shared utility
        # This handles OAuth token, user profile, and org data (including visit_count)
        request = create_cli_request(
            opportunity_id=opportunity_id,
            url_path=f"/custom_analysis/chc_nutrition/?opportunity_id={opportunity_id}",
        )

        if not request:
            self.stdout.write(self.style.ERROR("Failed to authenticate. Please run: python manage.py get_cli_token"))
            return

        self.stdout.write(self.style.SUCCESS(f"[OK] Authenticated as: {request.user.username}"))

        # Show opportunity info if available
        opp = request.labs_context.get("opportunity")
        if opp:
            logger.info(f"Opportunity: {opp.get('name')} (ID: {opportunity_id}, visits: {opp.get('visit_count', 0)})")

        # Run requested operations
        try:
            if options["show_form_structure"]:
                self.analyze_form_structure(request, opportunity_id, options["sample_size"])
            elif options["debug_fields"]:
                self.debug_field_extraction(request, opportunity_id)
            else:
                # Default: run full analysis
                self.run_full_analysis(request, opportunity_id)

        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\n\nInterrupted by user"))
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Error: {e}"))
            logger.exception("Command failed")

    def analyze_form_structure(self, request, opportunity_id, sample_size):
        """Analyze the actual form_json structure."""
        self.stdout.write("=" * 80)
        self.stdout.write(f"ANALYZING FORM STRUCTURE FOR OPPORTUNITY {opportunity_id}")
        self.stdout.write("=" * 80)
        self.stdout.write("")

        self.stdout.write("Fetching visits...")
        pipeline = AnalysisPipeline(request)
        visit_dicts = pipeline.fetch_raw_visits()
        visits = [LocalUserVisit(d) for d in visit_dicts]

        self.stdout.write(self.style.SUCCESS(f"[OK] Fetched {len(visits)} visits"))
        self.stdout.write("")

        if not visits:
            self.stdout.write(self.style.WARNING("No visits found!"))
            return

        # Analyze first few visits
        self.stdout.write(f"Analyzing first {sample_size} visits:")
        self.stdout.write("")

        for i, visit in enumerate(visits[:sample_size], 1):
            self.stdout.write(f"\n--- Visit {i} (ID: {visit.id}, Username: {visit.username}) ---")
            self.stdout.write(f"Status: {visit.status}")
            self.stdout.write(f"Date: {visit.visit_date}")

            form_json = visit.form_json
            if not form_json:
                self.stdout.write(self.style.WARNING("  [WARNING] Empty form_json"))
                continue

            self.stdout.write("\nTop-level keys in form_json:")
            for key in form_json.keys():
                self.stdout.write(f"  - {key}")

            # Show nested structure for key paths we're looking for
            # Includes paths for both opp 814 and opp 822 form structures
            paths_to_check = [
                "form",
                # opp 814 paths
                "form.additional_case_info",
                "form.case",
                "form.case.update",
                "form.muac_group",
                "form.ors_group",
                "form.pictures",
                "form.immunization_photo_group",
                # opp 822 paths
                "form.case_info",
                "form.child_registration",
                "form.subcase_0",
                "form.subcase_0.case",
                "form.subcase_0.case.update",
                "form.service_delivery",
                "form.service_delivery.muac_group",
                "form.service_delivery.ors_group",
            ]

            self.stdout.write("\nChecking expected paths:")
            for path in paths_to_check:
                parts = path.split(".")
                current = form_json
                exists = True
                for part in parts:
                    if isinstance(current, dict) and part in current:
                        current = current[part]
                    else:
                        exists = False
                        break

                if exists:
                    if isinstance(current, dict):
                        keys = list(current.keys())[:10]
                        self.stdout.write(self.style.SUCCESS(f"  [OK] {path}: {keys}"))
                    else:
                        self.stdout.write(self.style.SUCCESS(f"  [OK] {path}: {type(current).__name__}"))
                else:
                    self.stdout.write(self.style.ERROR(f"  [MISSING] {path}: NOT FOUND"))

    def debug_field_extraction(self, request, opportunity_id):
        """Test each field extraction from the config."""
        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(f"DEBUGGING FIELD EXTRACTION FOR OPPORTUNITY {opportunity_id}")
        self.stdout.write("=" * 80)
        self.stdout.write("")

        self.stdout.write("Fetching visits...")
        pipeline = AnalysisPipeline(request)
        visit_dicts = pipeline.fetch_raw_visits()
        visits = [LocalUserVisit(d) for d in visit_dicts]

        self.stdout.write(self.style.SUCCESS(f"[OK] Fetched {len(visits)} visits"))
        self.stdout.write("")

        if not visits:
            self.stdout.write(self.style.WARNING("No visits found!"))
            return

        # Test each field on first visit
        first_visit = visits[0]
        self.stdout.write(f"Testing field extraction on first visit (ID: {first_visit.id}):")
        self.stdout.write("")

        for field_comp in CHC_NUTRITION_CONFIG.fields:
            value = first_visit.extract_field(field_comp.path)

            # Apply transform if present
            if value is not None and field_comp.transform:
                try:
                    value = field_comp.transform(value)
                except Exception as e:
                    value = f"TRANSFORM_ERROR: {e}"

            if value is not None:
                self.stdout.write(self.style.SUCCESS(f"[OK] {field_comp.name:50s} = {value}"))
            else:
                self.stdout.write(self.style.ERROR(f"[MISSING] {field_comp.name:50s} = None"))

    def run_full_analysis(self, request, opportunity_id):
        """Run the full analysis and show results."""
        self.stdout.write("")
        self.stdout.write("=" * 80)
        self.stdout.write(f"RUNNING FULL ANALYSIS FOR OPPORTUNITY {opportunity_id}")
        self.stdout.write("=" * 80)
        self.stdout.write("")

        self.stdout.write("Computing FLW analysis via pipeline...")
        pipeline = AnalysisPipeline(request)
        result = pipeline.stream_analysis_ignore_events(CHC_NUTRITION_CONFIG)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("[OK] Analysis complete!"))
        self.stdout.write(f"  - Total FLWs: {len(result.rows)}")
        self.stdout.write(f"  - Total Visits: {result.metadata.get('total_visits', 0)}")

        if result.rows:
            self.stdout.write("\nResults (first 5 FLWs):")

            for i, flw in enumerate(result.rows[:5], 1):
                self.stdout.write(f"\n--- FLW {i}: {flw.username} ---")
                self.stdout.write(f"  Total Visits: {flw.total_visits}")
                self.stdout.write(f"  Approved: {flw.approved_visits}")
                self.stdout.write(f"  Days Active: {flw.days_active}")

                # Show non-zero custom fields
                non_zero_fields = {k: v for k, v in flw.custom_fields.items() if v and v != 0}
                if non_zero_fields:
                    self.stdout.write("\n  Custom Fields:")
                    for field_name, value in non_zero_fields.items():
                        self.stdout.write(f"    {field_name}: {value}")
                else:
                    self.stdout.write(self.style.WARNING("  No custom field data found"))

            # Summary stats
            self.stdout.write("")
            self.stdout.write("=" * 80)
            self.stdout.write("FIELD EXTRACTION SUCCESS RATES")
            self.stdout.write("=" * 80)
            self.stdout.write("")

            # Count non-zero fields across all FLWs
            field_stats = {}
            for flw in result.rows:
                for field_name, value in flw.custom_fields.items():
                    if field_name not in field_stats:
                        field_stats[field_name] = {"non_zero": 0, "total": 0}

                    field_stats[field_name]["total"] += 1
                    if value and value != 0:
                        field_stats[field_name]["non_zero"] += 1

            for field_name, stats in sorted(field_stats.items()):
                pct = (stats["non_zero"] / stats["total"] * 100) if stats["total"] > 0 else 0
                if pct > 0:
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"[OK] {field_name:50s}: {stats['non_zero']:3d}/{stats['total']:3d} ({pct:5.1f}%)"
                        )
                    )
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            f"[MISSING] {field_name:50s}: {stats['non_zero']:3d}/{stats['total']:3d} ({pct:5.1f}%)"
                        )
                    )
