"""Load ADM0 + ADM1 boundaries for every African country from geoBoundaries.

ADM1 is where subnational under-5 mortality lives, so that is as deep as the
targeting analysis goes. Deeper levels would inherit the same rate downward and
add no information about need.

    python manage.py load_africa_boundaries
    python manage.py load_africa_boundaries --iso NGA,KEN --clear
    python manage.py load_africa_boundaries --missing-only
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.admin_boundaries.services import GeoBoundariesLoader
from connect_labs.labs.indicators.africa import ISO_CODES, name_for

LEVELS = [0, 1]

#: Countries where IGME publishes subnational child mortality at ADM2. Loading
#: ADM2 everywhere would treble the boundary table for data we cannot use; these
#: are the ones where a deeper boundary is actually backed by a deeper estimate.
ADM2_COUNTRIES = [
    "AGO",
    "BEN",
    "CMR",
    "ETH",
    "GIN",
    "LBR",
    "MDG",
    "MLI",
    "MRT",
    "MWI",
    "NAM",
    "RWA",
    "SEN",
    "SLE",
    "TZA",
    "UGA",
    "ZMB",
    "ZWE",
]


class Command(BaseCommand):
    help = "Load ADM0 and ADM1 boundaries for African countries from geoBoundaries (CC BY 4.0)"

    def add_arguments(self, parser):
        parser.add_argument("--iso", help="Comma-separated ISO-3 codes; default is all of Africa")
        parser.add_argument("--clear", action="store_true", help="Delete existing rows for each country first")
        parser.add_argument(
            "--adm2",
            action="store_true",
            help="Load ADM2 as well, for the countries with ADM2-level mortality estimates",
        )
        parser.add_argument(
            "--missing-only",
            action="store_true",
            help="Skip countries that already have ADM1 boundaries loaded",
        )

    def handle(self, *args, **opts):
        codes = (
            [c.strip().upper() for c in opts["iso"].split(",") if c.strip()] if opts.get("iso") else list(ISO_CODES)
        )

        if opts["missing_only"]:
            have = set(
                AdminBoundary.objects.filter(admin_level=1, iso_code__in=codes)
                .values_list("iso_code", flat=True)
                .distinct()
            )
            skipped = [c for c in codes if c in have]
            codes = [c for c in codes if c not in have]
            if skipped:
                self.stdout.write(f"Skipping {len(skipped)} country(ies) already loaded")

        loader = GeoBoundariesLoader()
        ok, failed = 0, []

        for i, iso in enumerate(codes, 1):
            label = f"[{i}/{len(codes)}] {iso} {name_for(iso)}"
            levels = list(LEVELS)
            if opts.get("adm2") and iso in ADM2_COUNTRIES:
                levels.append(2)
            try:
                result = loader.load_country(iso, levels=levels, clear=opts["clear"])
                total = getattr(result, "total_loaded", 0)
                if total:
                    ok += 1
                    self.stdout.write(self.style.SUCCESS(f"{label}: {total} boundaries"))
                else:
                    failed.append(iso)
                    self.stdout.write(self.style.WARNING(f"{label}: nothing loaded"))
            except Exception as exc:  # noqa: BLE001 — one country must not stop the sweep
                failed.append(iso)
                self.stdout.write(self.style.ERROR(f"{label}: {exc}"))

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"Loaded {ok} country(ies)"))
        if failed:
            self.stdout.write(self.style.WARNING(f"No data for: {', '.join(failed)}"))

        counts = AdminBoundary.objects.filter(iso_code__in=ISO_CODES, admin_level__in=LEVELS)
        self.stdout.write(
            f"Africa now holds {counts.filter(admin_level=0).count()} ADM0 "
            f"and {counts.filter(admin_level=1).count()} ADM1 boundaries"
        )
