"""
Check if CommCare DU cases have service_area_id populated.

Usage:
    python manage.py check_du_service_areas --opportunity-id 814
"""

import logging

from django.core.management.base import BaseCommand

from connect_labs.coverage.data_access import CoverageDataAccess
from connect_labs.labs.integrations.connect.cli import create_cli_request

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Check if DU cases from CommCare have service_area_id field populated"

    def add_arguments(self, parser):
        parser.add_argument(
            "--opportunity-id",
            type=int,
            required=True,
            help="Opportunity ID",
        )

    def handle(self, *args, **options):
        opportunity_id = options["opportunity_id"]

        self.stdout.write("=" * 80)
        self.stdout.write(self.style.SUCCESS(f"Checking DU Service Areas - Opportunity {opportunity_id}"))
        self.stdout.write("=" * 80)

        # Create CLI request
        request = create_cli_request()
        request.labs_context = {"opportunity_id": opportunity_id}

        # Fetch DU cases
        self.stdout.write("\nFetching DU cases from CommCare...")
        data_access = CoverageDataAccess(request)
        du_cases = data_access.fetch_delivery_units_from_commcare()

        self.stdout.write(self.style.SUCCESS(f"✓ Fetched {len(du_cases)} DU cases\n"))

        # Check first few for service_area_id
        self.stdout.write("-" * 80)
        self.stdout.write("Checking service_area_id field in first 10 DUs:")
        self.stdout.write("-" * 80)

        has_sa = 0
        no_sa = 0

        for i, case_data in enumerate(du_cases[:10], 1):
            case_name = case_data.get("case_name", "N/A")
            properties = case_data.get("properties", {})
            sa_id = properties.get("service_area_id", "")

            if sa_id:
                self.stdout.write(f"{i:2d}. {case_name:20s} SA: {self.style.SUCCESS(sa_id)}")
                has_sa += 1
            else:
                self.stdout.write(f"{i:2d}. {case_name:20s} SA: {self.style.WARNING('EMPTY/NULL')}")
                no_sa += 1

            # Show all properties for first case
            if i == 1:
                self.stdout.write(f"\n    Properties (first DU): {list(properties.keys())[:20]}\n")

        # Check all DUs
        self.stdout.write("\n" + "=" * 80)
        self.stdout.write("Scanning all DUs...")
        self.stdout.write("=" * 80)

        total_has_sa = 0
        total_no_sa = 0
        sample_sa_ids = set()

        for case_data in du_cases:
            properties = case_data.get("properties", {})
            sa_id = properties.get("service_area_id", "")

            if sa_id:
                total_has_sa += 1
                if len(sample_sa_ids) < 10:
                    sample_sa_ids.add(sa_id)
            else:
                total_no_sa += 1

        self.stdout.write("\nResults:")
        self.stdout.write(f"  DUs with service_area_id: {self.style.SUCCESS(total_has_sa)}/{len(du_cases)}")
        self.stdout.write(f"  DUs without service_area_id: {self.style.WARNING(total_no_sa)}/{len(du_cases)}")

        if sample_sa_ids:
            self.stdout.write(f"\n  Sample service_area_ids: {sorted(list(sample_sa_ids))}")
        else:
            self.stdout.write(self.style.ERROR("\n  ✗ NO service_area_ids found in ANY DU cases!"))
            self.stdout.write("\n  This explains why service areas are not being populated.")
            self.stdout.write("  The DU cases in CommCare need to have 'service_area_id' property set.")

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS("Check complete!"))
        self.stdout.write("=" * 80)
