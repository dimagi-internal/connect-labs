"""
Management command to load admin boundaries from geoBoundaries API.

geoBoundaries (https://www.geoboundaries.org/) provides open-licensed (CC BY 4.0)
administrative boundary data in GeoJSON format.

Usage:
    # Load all levels for a single country
    python manage.py load_boundaries KEN

    # Load multiple countries
    python manage.py load_boundaries KEN NGA UGA

    # Load specific admin levels only
    python manage.py load_boundaries KEN --levels 0 1 2

    # Dry run - show what would be loaded
    python manage.py load_boundaries KEN --dry-run

    # Clear and reload a country
    python manage.py load_boundaries KEN --clear
"""

from django.core.management.base import BaseCommand

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.admin_boundaries.services import BoundaryLoader


class Command(BaseCommand):
    help = "Load admin boundaries from geoBoundaries API (CC BY 4.0 license)"

    DEFAULT_LEVELS = [0, 1, 2]  # Country, State/Province, District

    def add_arguments(self, parser):
        parser.add_argument(
            "iso_codes",
            nargs="+",
            type=str,
            help="ISO 3166-1 alpha-3 country codes (e.g., KEN NGA UGA)",
        )
        parser.add_argument(
            "--levels",
            nargs="*",
            type=int,
            default=None,
            help=f"Admin levels to load (default: {self.DEFAULT_LEVELS}). Use --levels 0 1 2 3 for more detail.",
        )
        parser.add_argument(
            "--clear",
            action="store_true",
            help="Clear existing boundaries for specified countries before loading",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be loaded without actually loading",
        )

    def handle(self, *args, **options):
        iso_codes = [code.upper() for code in options["iso_codes"]]
        levels = options["levels"] if options["levels"] is not None else self.DEFAULT_LEVELS
        clear = options["clear"]
        dry_run = options["dry_run"]

        self.stdout.write("=" * 70)
        self.stdout.write("LOAD ADMIN BOUNDARIES FROM GEOBOUNDARIES")
        self.stdout.write("=" * 70)
        self.stdout.write(f"Countries: {', '.join(iso_codes)}")
        self.stdout.write(f"Admin levels: {levels}")
        self.stdout.write(f"Clear existing: {clear}")
        self.stdout.write(f"Dry run: {dry_run}")
        self.stdout.write("")

        if dry_run:
            self._handle_dry_run(iso_codes, levels, clear)
        else:
            self._handle_load(iso_codes, levels, clear)

    def _handle_dry_run(self, iso_codes: list[str], levels: list[int], clear: bool):
        """Handle dry run - show what would be loaded."""
        self.stdout.write(self.style.WARNING("DRY RUN MODE - No data will be loaded"))
        self.stdout.write("")

        for iso_code in iso_codes:
            self.stdout.write(f"\n[{iso_code}] Would process {iso_code}...")

            if clear:
                existing = AdminBoundary.objects.filter(iso_code=iso_code).count()
                self.stdout.write(f"  Would clear {existing} existing boundaries")

            for level in levels:
                existing_level = AdminBoundary.objects.filter(iso_code=iso_code, admin_level=level).count()
                self.stdout.write(f"  ADM{level}: Would replace {existing_level} existing boundaries")

        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.WARNING("DRY RUN COMPLETE - No changes made"))
        self.stdout.write("=" * 70)

    def _handle_load(self, iso_codes: list[str], levels: list[int], clear: bool):
        """Handle actual loading of boundaries."""
        loader = BoundaryLoader()
        total_loaded = 0

        def on_progress(msg: str):
            self.stdout.write(f"  {msg}")

        for iso_code in iso_codes:
            self.stdout.write(f"\n[{iso_code}] Processing...")

            result = loader.load_country(
                iso_code=iso_code,
                levels=levels,
                clear=clear,
                on_progress=on_progress,
            )

            total_loaded += result.total_loaded

            # Show level results
            for level_result in result.levels:
                if level_result.success:
                    self.stdout.write(self.style.SUCCESS(f"  {level_result.message}"))
                elif level_result.error:
                    self.stdout.write(self.style.ERROR(f"  ADM{level_result.level}: {level_result.error}"))
                else:
                    self.stdout.write(f"  {level_result.message}")

        self.stdout.write("")
        self.stdout.write("=" * 70)
        self.stdout.write(self.style.SUCCESS(f"COMPLETE - loaded {total_loaded} boundaries"))
        self.stdout.write("=" * 70)
