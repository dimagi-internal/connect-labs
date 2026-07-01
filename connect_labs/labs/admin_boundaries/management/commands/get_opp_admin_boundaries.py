"""
Management command to get admin boundary coverage for opportunities.

Performs a spatial query to find which admin boundaries contain visits
for an opportunity. Requires that the analysis pipeline has been run
(visits are cached in ComputedVisitCache).

Usage:
    # Single opportunity
    python manage.py get_opp_admin_boundaries --opp-id 814 --iso-code KEN

    # Multiple opportunities
    python manage.py get_opp_admin_boundaries --opp-ids 814,822 --iso-code KEN

    # Specify admin levels
    python manage.py get_opp_admin_boundaries --opp-id 814 --iso-code KEN --levels 1,2,3

    # Verbose output with all boundaries
    python manage.py get_opp_admin_boundaries --opp-id 814 --iso-code KEN --verbose
"""

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.admin_boundaries.services import get_opp_boundary_coverage


class Command(BaseCommand):
    help = "Get admin boundary coverage for opportunity visits (requires cached visit data)"

    def add_arguments(self, parser):
        # Opportunity selection (mutually exclusive)
        opp_group = parser.add_mutually_exclusive_group(required=True)
        opp_group.add_argument(
            "--opp-id",
            type=int,
            help="Single opportunity ID to analyze",
        )
        opp_group.add_argument(
            "--opp-ids",
            type=str,
            help="Comma-separated list of opportunity IDs (e.g., 814,822,830)",
        )

        # Required arguments
        parser.add_argument(
            "--iso-code",
            type=str,
            required=True,
            help="ISO 3166-1 alpha-3 country code (e.g., KEN, NGA)",
        )

        # Optional arguments
        parser.add_argument(
            "--levels",
            type=str,
            default="1,2",
            help="Comma-separated admin levels to check (default: 1,2)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Show all boundaries (not just top 10 per level)",
        )

    def handle(self, *args, **options):
        # Parse opportunity IDs
        if options["opp_id"]:
            opp_ids = [options["opp_id"]]
        else:
            opp_ids = [int(x.strip()) for x in options["opp_ids"].split(",")]

        iso_code = options["iso_code"].upper()
        admin_levels = [int(x.strip()) for x in options["levels"].split(",")]
        verbose = options["verbose"]

        # Validate ISO code has boundaries
        boundary_count = AdminBoundary.objects.filter(iso_code=iso_code, admin_level__in=admin_levels).count()

        if boundary_count == 0:
            raise CommandError(
                f"No admin boundaries found for {iso_code} at levels {admin_levels}. "
                f"Load boundaries first with: python manage.py load_boundaries {iso_code}"
            )

        self.stdout.write("=" * 70)
        self.stdout.write("ADMIN BOUNDARY COVERAGE ANALYSIS")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Country: {iso_code}")
        self.stdout.write(f"Admin levels: {admin_levels}")
        self.stdout.write(f"Opportunities: {opp_ids}")
        self.stdout.write(f"Available boundaries: {boundary_count}")
        self.stdout.write("")

        for opp_id in opp_ids:
            self._analyze_opportunity(opp_id, iso_code, admin_levels, verbose)

    def _analyze_opportunity(self, opp_id: int, iso_code: str, admin_levels: list[int], verbose: bool):
        """Analyze a single opportunity."""
        self.stdout.write("-" * 70)
        self.stdout.write(f"Opportunity {opp_id}")
        self.stdout.write("-" * 70)

        try:
            result = get_opp_boundary_coverage(
                opportunity_id=opp_id,
                iso_code=iso_code,
                admin_levels=admin_levels,
            )
        except ValueError as e:
            self.stdout.write(self.style.ERROR(f"  Error: {e}"))
            self.stdout.write("")
            return

        # Summary stats
        gps_pct = (result.visits_with_gps / result.total_visits * 100) if result.total_visits > 0 else 0
        match_pct = (result.visits_matched / result.visits_with_gps * 100) if result.visits_with_gps > 0 else 0

        self.stdout.write(f"  Total visits: {result.total_visits:,}")
        self.stdout.write(f"  Visits with GPS: {result.visits_with_gps:,} ({gps_pct:.1f}%)")
        self.stdout.write(self.style.SUCCESS(f"  Visits matched: {result.visits_matched:,} ({match_pct:.1f}%)"))
        if result.visits_unmatched > 0:
            self.stdout.write(
                self.style.WARNING(f"  Visits unmatched: {result.visits_unmatched:,} (GPS outside known boundaries)")
            )
        self.stdout.write("")

        # Boundaries by level
        for level in sorted(result.boundaries_by_level.keys()):
            boundaries = result.boundaries_by_level[level]
            level_name = self._get_level_name(level)

            self.stdout.write(f"  ADM{level} ({level_name}): {len(boundaries)} boundaries with visits")

            # Show boundaries (all if verbose, top 10 otherwise)
            show_boundaries = boundaries if verbose else boundaries[:10]
            for boundary in show_boundaries:
                self.stdout.write(f"    - {boundary.name}: {boundary.visit_count:,} visits")

            if not verbose and len(boundaries) > 10:
                remaining = len(boundaries) - 10
                self.stdout.write(f"    ... and {remaining} more (use --verbose to see all)")

            self.stdout.write("")

        self.stdout.write("")

    def _get_level_name(self, level: int) -> str:
        """Get human-readable name for admin level."""
        names = {
            0: "Country",
            1: "State/Province",
            2: "District/County",
            3: "Sub-district/Ward",
            4: "Village/Locality",
        }
        return names.get(level, f"Level {level}")
