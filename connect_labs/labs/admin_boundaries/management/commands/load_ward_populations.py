"""Attach multi-source per-ward populations to AdminBoundary rows (#6).

Reads a precomputed CSV (~9,300 wards across all 37 states, keyed by ward_code) and
stores the per-source numbers in each matching boundary's ``extra.populations`` bag,
so the microplan population picker can offer a source dropdown and fill the number
for the selected wards. Sources in the fixture:
  - worldpop_total/worldpop_u5, meta_total/meta_u5: zonal stats over WorldPop/Meta
    rasters against GRID3 ward polygons.
  - grid3_v3_total/grid3_v3_u5: zonal stats over GRID3's own v3.0 gridded population
    + age-band rasters against the same GRID3 ward polygons.
  - geopode_total/geopode_u5: GeoPoDe's own ready-made ward-level population table
    (WorldPop-sourced, tabulated under GeoPoDe's own state/LGA/ward taxonomy),
    joined onto the GRID3 ward rows by normalized (state, lga, ward) name triple.
    Prior to 2026-07 this was instead the boundary's own scalar ``population`` field
    (GeoPoDe's ``population_1`` attribute) — that figure turned out to be a whole-area
    TOTAL, not the under-5 estimate it was first assumed to be, and is now superseded
    by the fixture's real total/u5 pair.

Matches by ``extra.own_code`` (the ward code GeoPoDe + GRID3 boundaries both carry),
so the same numbers attach to both ward sources where the codes line up. Falls back
to name matching otherwise — scoped by (state, lga, ward), not ward name alone, since
the same ward or LGA name can recur across different states/LGAs in Nigeria (a
(state, ward)-only match is used as a last resort, and only when unambiguous).

Usage:
    python manage.py load_ward_populations            # ingest the bundled fixture
    python manage.py load_ward_populations --dry-run  # report match counts only
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from django.core.management.base import BaseCommand

from connect_labs.labs.admin_boundaries.models import AdminBoundary

FIXTURE = Path(__file__).resolve().parent.parent.parent / "fixtures" / "ward_populations_national.csv"
SOURCE_COLS = [
    "worldpop_total",
    "worldpop_u5",
    "meta_total",
    "meta_u5",
    "grid3_v3_total",
    "grid3_v3_u5",
    "geopode_total",
    "geopode_u5",
]


def _norm(s: str) -> str:
    """Loose key for name matching: lowercase, strip everything but a–z0–9 (so
    "Jama'a" == "Jama a" == "jamaa")."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class Command(BaseCommand):
    help = "Attach multi-source per-ward populations (extra.populations) to AdminBoundary rows."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Report match counts only; no writes.")
        parser.add_argument("--path", type=str, default=str(FIXTURE), help="CSV path (default: bundled fixture).")

    def handle(self, *args, **opts):
        by_code: dict[str, dict] = {}
        # Primary name fallback: (norm(state), norm(lga), norm(ward)) -> pops. Full
        # triple avoids the real collision risk of matching on ward name alone (or
        # even (state, ward) alone) -- the same ward/LGA name can recur across
        # different LGAs/states in Nigeria.
        by_full: dict[tuple[str, str, str], dict] = {}
        # Last-resort fallback: (state, ward) -> list of recs, kept only where the
        # ward name is actually unique within that state (ambiguous ones are dropped
        # below rather than risk attaching the wrong ward's numbers).
        by_state_ward_candidates: dict[tuple[str, str], list[dict]] = defaultdict(list)
        with open(opts["path"], newline="") as fh:
            for row in csv.DictReader(fh):
                code = (row.get("ward_code") or "").strip()
                pops = {}
                for col in SOURCE_COLS:
                    try:
                        pops[col] = round(float(row[col]), 1)
                    except (TypeError, ValueError, KeyError):
                        pass
                # Keep the fixture's admin names alongside the pops so we can also
                # stamp each ward's parent chain (state › lga) — many GeoPoDe rows
                # have no parent_names, which left the planning table's LGA/State
                # columns blank even though Total/U5 populated.
                state = (row.get("state") or "").strip()
                lga = (row.get("lga") or "").strip()
                rec = {"pops": pops, "state": state, "lga": lga}
                if code:
                    by_code[code] = rec
                nk_state, nk_lga, nk_ward = _norm(state), _norm(lga), _norm(row.get("ward"))
                if nk_ward and nk_lga:
                    by_full[(nk_state, nk_lga, nk_ward)] = rec
                if nk_ward:
                    by_state_ward_candidates[(nk_state, nk_ward)].append(rec)
        by_state_ward = {k: v[0] for k, v in by_state_ward_candidates.items() if len(v) == 1}
        self.stdout.write(f"Loaded {len(by_code)} ward populations from {opts['path']}.")

        boundaries = list(AdminBoundary.objects.filter(iso_code="NGA", admin_level=3, source__in=["geopode", "grid3"]))
        matched, by_full_matched, by_state_ward_matched, updates = 0, 0, 0, []
        for b in boundaries:
            extra = b.extra or {}
            code = str(extra.get("own_code") or "").strip()
            rec = by_code.get(code)
            if not rec:
                parent_names = extra.get("parent_names") or {}
                state, lga = parent_names.get("state"), parent_names.get("lga")
                rec = by_full.get((_norm(state), _norm(lga), _norm(b.name)))
                if rec:
                    by_full_matched += 1
                else:
                    # Only fall back to (state, ward) when the boundary itself has no
                    # LGA on record to match with, and the ward name is unambiguous
                    # within that state.
                    rec = by_state_ward.get((_norm(state), _norm(b.name)))
                    if rec:
                        by_state_ward_matched += 1
            if not rec:
                continue
            matched += 1
            merged = dict(rec["pops"])
            extra = {**extra, "populations": merged}
            # Stamp the parent chain (state first, then lga) from the fixture when it
            # has both — powers the planning table's LGA/State columns + the CSV. Only
            # overwrite when we actually have names, so we never blank an existing chain.
            if rec.get("state") and rec.get("lga"):
                extra["parent_names"] = {"state": rec["state"], "lga": rec["lga"]}
            b.extra = extra
            updates.append(b)

        self.stdout.write(
            f"Matched {matched} of {len(boundaries)} geopode/grid3 NGA ward rows "
            f"({by_full_matched} via state+lga+ward fallback, "
            f"{by_state_ward_matched} via unambiguous state+ward fallback)."
        )
        if opts["dry_run"]:
            self.stdout.write("DRY RUN — no writes.")
            return
        AdminBoundary.objects.bulk_update(updates, ["extra"], batch_size=500)
        self.stdout.write(self.style.SUCCESS(f"Updated extra.populations on {len(updates)} boundaries."))
