"""Cut a same-region Overture country extract onto the CURRENT pinned release.

Why this exists: the extract was originally cut by hand. When Overture pruned
the release we were pinned to (2026-05-20.0) and the pin moved to 2026-08-19.0,
Nigeria's extract was left behind on the dead release — `covering_region()`
correctly ignores a mismatched release, so every uncached Nigerian ward silently
fell back to the cross-region live read at ~350s instead of ~5s. Nothing failed;
it just got slow, and stayed slow, because re-cutting it was undocumented work
nobody could repeat.

Reads the public Overture bucket once, writes one Parquet per 1-degree tile
(hive-partitioned tx/ty) to our own us-east-1 bucket, in exactly the column
shape `_query_extract` reads back.

    manage.py microplans_build_extract nigeria
    manage.py microplans_build_extract nigeria --dry-run

After it finishes, point EXTRACT_REGIONS at the new release — the command
prints the one-line edit — and the router picks it up on the next fetch.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from connect_labs.microplans.core import overture


class Command(BaseCommand):
    help = "Cut a same-region Overture country extract onto the current pinned release"

    def add_arguments(self, parser):
        parser.add_argument("regions", nargs="+", help="region name(s) from EXTRACT_REGIONS, e.g. nigeria")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="print the plan and the SQL without reading or writing anything",
        )

    def handle(self, *args, **opts):
        release = overture.OVERTURE_RELEASE
        for region in opts["regions"]:
            meta = overture.EXTRACT_REGIONS.get(region)
            if meta is None:
                raise CommandError(f"unknown region {region!r}; known: {', '.join(sorted(overture.EXTRACT_REGIONS))}")
            if meta["release"] == release:
                self.stdout.write(f"{region}: already on {release} — nothing to cut")
                continue

            bbox = meta["bbox"]
            dest = f"s3://{overture.EXTRACT_BUCKET}/overture/{region}/{release}"
            self.stdout.write(
                self.style.MIGRATE_HEADING(f"{region}: {meta['release']} -> {release}\n  bbox {bbox}\n  dest {dest}")
            )

            # One row per building, pre-projected to the columns the reader wants,
            # so a read is a partition-pruned scan and nothing more. tx/ty are the
            # 1-degree tile the CENTROID falls in — the same floor() the reader
            # bounds its scan with, or pruning would silently drop edge buildings.
            sql = f"""
                COPY (
                    SELECT
                        ST_X(ST_Centroid(geometry))  AS lon,
                        ST_Y(ST_Centroid(geometry))  AS lat,
                        ST_Area_Spheroid(geometry)   AS area_m2,
                        sources[1].confidence        AS confidence,
                        sources[1].dataset           AS dataset,
                        ST_AsWKB(geometry)           AS geom_wkb,
                        bbox,
                        FLOOR(ST_X(ST_Centroid(geometry)))::INT AS tx,
                        FLOOR(ST_Y(ST_Centroid(geometry)))::INT AS ty
                    FROM read_parquet('{overture.theme_path("buildings", "building")}',
                                      filename=false, hive_partitioning=true)
                    WHERE bbox.xmin >= {bbox[0]} AND bbox.xmax <= {bbox[2]}
                      AND bbox.ymin >= {bbox[1]} AND bbox.ymax <= {bbox[3]}
                ) TO '{dest}'
                (FORMAT PARQUET, PARTITION_BY (tx, ty), OVERWRITE_OR_IGNORE 1, COMPRESSION ZSTD);
            """

            if opts["dry_run"]:
                self.stdout.write(sql)
                continue

            self.stdout.write("  reading Overture (planet-scale; this takes a while)…")
            con = overture.connect()
            try:
                con.execute(sql)
            finally:
                con.close()

            self.stdout.write(self.style.SUCCESS(f"  wrote {dest}"))
            self.stdout.write(
                "  now pin it — in connect_labs/microplans/core/overture.py:\n"
                f'    EXTRACT_REGIONS["{region}"]["release"] = "{release}"'
            )
