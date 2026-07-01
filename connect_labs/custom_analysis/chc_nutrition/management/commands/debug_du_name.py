"""
Django management command to debug delivery unit name extraction.

Usage:
    python manage.py debug_du_name --opportunity-id 814
    python manage.py debug_du_name --opportunity-id 814 --sample-size 20
"""

import logging

from django.core.management.base import BaseCommand

from connect_labs.custom_analysis.chc_nutrition.analysis_config import CHC_NUTRITION_CONFIG
from connect_labs.labs.analysis.models import LocalUserVisit
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.integrations.connect.cli import create_cli_request

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Debug delivery unit name extraction from form JSON"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opportunity-id",
            type=int,
            required=True,
            help="Opportunity ID to analyze",
        )
        parser.add_argument(
            "--sample-size",
            type=int,
            default=10,
            help="Number of visits to check (default: 10)",
        )

    def handle(self, *args, **options):
        opportunity_id = options["opportunity_id"]
        sample_size = options["sample_size"]

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(f"DU Name Extraction Debug - Opportunity {opportunity_id}"))
        self.stdout.write("=" * 80)

        # Create CLI request (handles OAuth)
        request = create_cli_request()
        request.labs_context = {"opportunity_id": opportunity_id}

        # Fetch visits
        self.stdout.write("\nFetching visits...")
        pipeline = AnalysisPipeline(request)
        visit_dicts = pipeline.fetch_raw_visits()[:sample_size]
        visits = [LocalUserVisit(d) for d in visit_dicts]

        self.stdout.write(self.style.SUCCESS(f"✓ Fetched {len(visits)} visits\n"))

        # Find the deliver_unit_name field in config
        du_name_field = None
        for field in CHC_NUTRITION_CONFIG.fields:
            if field.name == "deliver_unit_name":
                du_name_field = field
                break

        if not du_name_field:
            self.stdout.write(self.style.ERROR("✗ deliver_unit_name field not found in CHC_NUTRITION_CONFIG!"))
            return

        self.stdout.write(f"Config path for deliver_unit_name: {self.style.WARNING(du_name_field.path)}\n")

        # Check various paths for each visit
        self.stdout.write("-" * 80)
        self.stdout.write("Checking multiple paths in form JSON:")
        self.stdout.write("-" * 80)

        paths_to_check = [
            ("form.du_name", "form.du_name"),
            ("form.case.update.du_name", "form.case.update.du_name"),
            ("form.case.@case_name", "form.case.@case_name"),
            ("form.case.update.du_case_id", "form.case.update.du_case_id"),
        ]

        extracted_values = {path: [] for path, _ in paths_to_check}

        for i, visit in enumerate(visits, 1):
            self.stdout.write(f"\nVisit {i} (xform_id: {visit.id[:16]}...):")

            for path_name, path in paths_to_check:
                value = visit.extract_field(path)
                extracted_values[path].append(value)

                if value:
                    self.stdout.write(f"  {path_name:30s} = {self.style.SUCCESS(value)}")
                else:
                    self.stdout.write(f"  {path_name:30s} = {self.style.WARNING('None/Empty')}")

            # Also show Connect's deliver_unit info
            self.stdout.write(f"  {'deliver_unit_id (Connect)':30s} = {visit.deliver_unit_id}")
            self.stdout.write(f"  {'entity_name (Connect)':30s} = {visit.entity_name}")

        # Show form JSON structure for first visit
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("Form JSON Structure (first visit):")
        self.stdout.write("=" * 80)

        if visits:
            form_json = visits[0].form_json
            if isinstance(form_json, dict):
                self.stdout.write(f"Root keys: {list(form_json.keys())}")

                if "form" in form_json:
                    form_keys = list(form_json.get("form", {}).keys())
                    self.stdout.write(f"form keys ({len(form_keys)}): {form_keys[:15]}...")

                    if "case" in form_json.get("form", {}):
                        case_data = form_json["form"]["case"]
                        if isinstance(case_data, dict):
                            self.stdout.write(f"form.case keys: {list(case_data.keys())}")

                            if "update" in case_data:
                                update_keys = list(case_data["update"].keys())
                                self.stdout.write(f"form.case.update keys ({len(update_keys)}): {update_keys[:20]}...")

        # Summary
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("Summary:")
        self.stdout.write("=" * 80)

        for path_name, path in paths_to_check:
            values = extracted_values[path]
            non_null = [v for v in values if v]
            null_count = len(values) - len(non_null)

            self.stdout.write(f"\n{path_name}:")
            self.stdout.write(f"  Non-null: {len(non_null)}/{len(values)}")
            self.stdout.write(f"  Null/empty: {null_count}/{len(values)}")

            if non_null:
                sample = non_null[:5]
                self.stdout.write(f"  Sample values: {sample}")

        # Test the actual config field
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(f"Testing config path: {du_name_field.path}")
        self.stdout.write("=" * 80)

        config_extracted = []
        for visit in visits:
            value = visit.extract_field(du_name_field.path)
            config_extracted.append(value)

        non_null_config = [v for v in config_extracted if v]
        self.stdout.write(f"\nExtracted {len(non_null_config)}/{len(config_extracted)} non-null values")

        if non_null_config:
            self.stdout.write(self.style.SUCCESS(f"Sample extracted values: {non_null_config[:5]}"))
        else:
            self.stdout.write(self.style.ERROR("✗ No values extracted! Path may be incorrect."))

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("Debug complete!"))
        self.stdout.write("=" * 80)
