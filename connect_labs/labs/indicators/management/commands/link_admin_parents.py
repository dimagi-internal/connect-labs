"""Fill in each boundary's parent link, which geoBoundaries does not supply.

``AdminBoundary.parent_boundary_id`` exists so callers can walk the hierarchy,
but the geoBoundaries loader has nothing to put in it: the source publishes each
level as a standalone layer with no pointer upward. The field is therefore empty
for every African boundary we hold, and ``resolve.ancestors()`` falls back to
"the country with this ISO" — which skips ADM1 entirely.

The cost of that is quiet and large. Inheritance is meant to take the *nearest*
ancestor with a value, so an Angolan district lacking its own survey figure
should read its province's. Instead it reaches past the province to the national
number, and any indicator held only at ADM1 — mean household size, and with it
every household count — cannot reach ADM2 at all, because no ADM0 row exists to
inherit from.

Containment is not in the data but it is in the geometry, so derive it once and
store it rather than paying for a spatial join on every resolve. A unit's
representative point (guaranteed inside its own polygon, unlike a centroid on a
crescent-shaped coastline) is matched against the level above; where no polygon
contains it — a district whose border was digitised from a different vintage
than its province — the parent with the largest area of overlap wins instead.

Idempotent: only fills links that are missing, unless ``--relink`` is passed.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand

from connect_labs.labs.admin_boundaries.models import AdminBoundary


class Command(BaseCommand):
    help = "Derive parent_boundary_id from geometry for boundaries whose source omits it"

    def add_arguments(self, parser):
        parser.add_argument("--iso", help="Comma-separated ISO-3 codes; default is every country loaded")
        parser.add_argument("--relink", action="store_true", help="Recompute links that are already set")
        parser.add_argument("--dry-run", action="store_true", help="Report what would change, write nothing")

    def handle(self, *args, **opts):
        codes = [c.strip().upper() for c in (opts.get("iso") or "").split(",") if c.strip()]

        linked = 0
        by_overlap = 0
        unmatched: list[str] = []

        # Deepest first is not required — each level is matched independently
        # against the one above — but it keeps the reporting readable.
        for level in (1, 2):
            children = AdminBoundary.objects.filter(admin_level=level)
            if codes:
                children = children.filter(iso_code__in=codes)
            if not opts["relink"]:
                children = children.filter(parent_boundary_id="")

            for child in children.iterator(chunk_size=200):
                parents = AdminBoundary.objects.filter(
                    iso_code=child.iso_code,
                    admin_level=level - 1,
                    source=child.source,
                )
                point = child.geometry.point_on_surface
                match = parents.filter(geometry__contains=point).first()

                if match is None:
                    # Borders digitised from different vintages do not nest
                    # cleanly. Largest overlap is the honest tie-break.
                    best_area = 0.0
                    for candidate in parents.filter(geometry__intersects=child.geometry):
                        area = candidate.geometry.intersection(child.geometry).area
                        if area > best_area:
                            best_area, match = area, candidate
                    if match is not None:
                        by_overlap += 1

                if match is None:
                    unmatched.append(f"{child.iso_code}/{child.name} (ADM{level})")
                    continue

                if not opts["dry_run"]:
                    child.parent_boundary_id = match.boundary_id
                    child.save(update_fields=["parent_boundary_id"])
                linked += 1

            self.stdout.write(f"ADM{level}: {linked} linked so far")

        verb = "would link" if opts["dry_run"] else "linked"
        self.stdout.write(self.style.SUCCESS(f"{verb} {linked} boundaries ({by_overlap} by largest overlap)"))
        if unmatched:
            self.stdout.write(f"no parent found for {len(unmatched)}: {', '.join(unmatched[:15])}")
