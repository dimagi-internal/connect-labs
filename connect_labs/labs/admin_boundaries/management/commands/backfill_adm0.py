"""
Management command to backfill ADM0 (country-level) boundaries into existing enrichments.

Since all visits for an opportunity are in a single country (identified by iso_code),
we can add ADM0 data without re-running the full spatial query - we just need to
look up the ADM0 boundary for each country and add it with the total visit count.

Usage:
    python manage.py backfill_adm0

    # Dry run
    python manage.py backfill_adm0 --dry-run
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from connect_labs.labs.admin_boundaries.models import AdminBoundary


class Command(BaseCommand):
    help = "Backfill ADM0 boundaries into existing enrichment records"

    ENRICHMENT_FIXTURE_PATH = (
        Path(__file__).parent.parent.parent.parent.parent / "solicitations/fixtures/opp_org_enrichment.json"
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be updated without making changes",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]

        # Load enrichment fixture
        if not self.ENRICHMENT_FIXTURE_PATH.exists():
            self.stdout.write(self.style.ERROR(f"Enrichment fixture not found: {self.ENRICHMENT_FIXTURE_PATH}"))
            return

        with open(self.ENRICHMENT_FIXTURE_PATH) as f:
            enrichment_data = json.load(f)

        enrichments = enrichment_data.get("enrichments", [])
        self.stdout.write(f"Loaded {len(enrichments)} enrichment records")

        # Get all unique ISO codes that have admin_boundaries
        iso_codes_with_boundaries = set()
        for e in enrichments:
            if e.get("admin_boundaries") and e.get("iso_code"):
                iso_codes_with_boundaries.add(e["iso_code"])

        self.stdout.write(f"Countries with enriched boundaries: {sorted(iso_codes_with_boundaries)}")

        # Look up ADM0 boundaries for each country
        adm0_by_iso = {}
        for iso_code in iso_codes_with_boundaries:
            adm0 = AdminBoundary.objects.filter(iso_code=iso_code, admin_level=0).first()
            if adm0:
                adm0_by_iso[iso_code] = {
                    "name": adm0.name,
                    "boundary_id": adm0.boundary_id,
                }
                self.stdout.write(f"  {iso_code}: Found ADM0 '{adm0.name}' ({adm0.boundary_id})")
            else:
                self.stdout.write(self.style.WARNING(f"  {iso_code}: No ADM0 boundary found in database"))

        if not adm0_by_iso:
            self.stdout.write(self.style.ERROR("No ADM0 boundaries found. Load ADM0 data first."))
            return

        # Update enrichments
        updated_count = 0
        skipped_count = 0

        for enrichment in enrichments:
            iso_code = enrichment.get("iso_code")
            admin_boundaries = enrichment.get("admin_boundaries")

            # Skip if no admin_boundaries or no iso_code
            if not admin_boundaries or not iso_code:
                continue

            # Skip if already has ADM0
            if "0" in admin_boundaries:
                skipped_count += 1
                continue

            # Skip if we don't have ADM0 for this country
            if iso_code not in adm0_by_iso:
                self.stdout.write(
                    self.style.WARNING(f"  Opp {enrichment.get('opportunity_id')}: No ADM0 for {iso_code}")
                )
                continue

            # Calculate total visits from boundary_coverage
            boundary_coverage = enrichment.get("boundary_coverage", {})
            total_visits = boundary_coverage.get("visits_matched", 0)

            # If no boundary_coverage, sum from existing boundaries
            if total_visits == 0:
                for level_boundaries in admin_boundaries.values():
                    for b in level_boundaries:
                        total_visits = max(total_visits, b.get("visit_count", 0))

            # Add ADM0
            adm0_info = adm0_by_iso[iso_code]
            admin_boundaries["0"] = [
                {
                    "name": adm0_info["name"],
                    "boundary_id": adm0_info["boundary_id"],
                    "visit_count": total_visits,
                }
            ]
            updated_count += 1

            if dry_run:
                self.stdout.write(
                    f"  Would update opp {enrichment.get('opportunity_id')}: "
                    f"Add ADM0 '{adm0_info['name']}' with {total_visits} visits"
                )

        self.stdout.write("")
        self.stdout.write("=" * 50)
        self.stdout.write(f"Updated: {updated_count}")
        self.stdout.write(f"Skipped (already has ADM0): {skipped_count}")

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY RUN - no changes made"))
        else:
            # Save updated enrichments
            with open(self.ENRICHMENT_FIXTURE_PATH, "w") as f:
                json.dump(enrichment_data, f, indent=2)
            self.stdout.write(self.style.SUCCESS(f"Saved to {self.ENRICHMENT_FIXTURE_PATH}"))
