"""Attach multi-source per-ward populations to AdminBoundary rows (#6).

Reads a precomputed CSV (zonal stats over WorldPop/Meta/GRID3 rasters for every ward
in the 4 CHC states, keyed by ward_code) and stores the per-source numbers in each
matching boundary's ``extra.populations`` bag, so the microplan population picker can
offer a source dropdown and fill the number for the selected wards.

Matches by ``extra.own_code`` (the ward code GeoPoDe + GRID3 boundaries both carry),
so the same numbers attach to both ward sources. GeoPoDe's own scalar figure (the
boundary ``population`` field, sourced from GeoPoDe ``population_1``) is added to the
bag as ``geopode_total`` where present.

NOTE on the key name: this was historically stored as ``geopode_u5`` on the assumption
that GeoPoDe ``population_1`` was an under-5 estimate. The data disproves that — e.g.
ward "Zankan" carries geopode 22,926 against a worldpop_total of ~28,543, i.e. a
whole-area TOTAL, not the ~5k under-5. So the key is now ``geopode_total`` and is
treated as a valid total-population fallback. Re-run this command on each deployment
to rewrite the bags (the resolver accepts either key during the transition).

Usage:
    python manage.py load_ward_populations            # ingest the bundled fixture
    python manage.py load_ward_populations --dry-run  # report match counts only
"""

from __future__ import annotations

import csv
from pathlib import Path

from django.core.management.base import BaseCommand

from commcare_connect.labs.admin_boundaries.models import AdminBoundary

FIXTURE = Path(__file__).resolve().parent.parent.parent / "fixtures" / "ward_populations_4states.csv"
SOURCE_COLS = ["worldpop_total", "worldpop_u5", "meta_total", "meta_u5", "grid3_v3_total"]


class Command(BaseCommand):
    help = "Attach multi-source per-ward populations (extra.populations) to AdminBoundary rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report match counts only; no writes.")
        parser.add_argument("--path", type=str, default=str(FIXTURE), help="CSV path (default: bundled fixture).")

    def handle(self, *args, **opts):
        by_code: dict[str, dict] = {}
        with open(opts["path"], newline="") as fh:
            for row in csv.DictReader(fh):
                code = (row.get("ward_code") or "").strip()
                if not code:
                    continue
                pops = {}
                for col in SOURCE_COLS:
                    try:
                        pops[col] = round(float(row[col]), 1)
                    except (TypeError, ValueError, KeyError):
                        pass
                by_code[code] = pops
        self.stdout.write(f"Loaded {len(by_code)} ward populations from {opts['path']}.")

        boundaries = list(AdminBoundary.objects.filter(iso_code="NGA", admin_level=3, source__in=["geopode", "grid3"]))
        matched, updates = 0, []
        for b in boundaries:
            extra = b.extra or {}
            code = str(extra.get("own_code") or "").strip()
            pops = by_code.get(code)
            if not pops:
                continue
            matched += 1
            merged = dict(pops)
            # GeoPoDe's scalar population_1 (a whole-area TOTAL — see module docstring)
            # lives on the boundary's population field. Stored under geopode_total so
            # resolve_population can use it as a legitimate total fallback.
            if b.source == "geopode" and b.population is not None:
                merged["geopode_total"] = round(float(b.population), 1)
            extra = {**extra, "populations": merged}
            b.extra = extra
            updates.append(b)

        self.stdout.write(f"Matched {matched} of {len(boundaries)} geopode/grid3 NGA ward rows.")
        if opts["dry_run"]:
            self.stdout.write("DRY RUN — no writes.")
            return
        AdminBoundary.objects.bulk_update(updates, ["extra"], batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Updated extra.populations on {len(updates)} boundaries."))
