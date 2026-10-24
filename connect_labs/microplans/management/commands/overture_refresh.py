"""Build labs' snapshot of an Overture release, check it, and switch labs to it.

    manage.py overture_refresh                       # the newest release Overture publishes
    manage.py overture_refresh 2026-11-18.0
    manage.py overture_refresh 2026-11-18.0 --steps copy,cut
    manage.py overture_refresh 2026-11-18.0 --steps cut --kinds segments,water --regions nigeria
    manage.py overture_refresh 2026-11-18.0 --dry-run

Steps, in order (see overture.py for the layout and why it exists):

    copy    server-side copy of Overture's whole release/<R>/ into snapshots/<R>/raw/,
            then the object count and bytes must match Overture's listing
    cut     1-degree tiles per kind per region, read from the raw copy (same region)
    check   every region: tiles present and non-empty, buildings and roads in its sample
            box, its divisions resolve, and buildings within +/-25% of the current release
    ready   write snapshots/<R>/READY.json: which tiles exist, their filters, the checks
    switch  write CURRENT.json (release R, previous = the release labs read until now)
    prune   delete snapshots that are neither the current release nor the previous one

Nothing labs reads changes until ``switch``, and ``switch`` refuses a snapshot
with no READY.json, so a failure anywhere before it leaves labs on the snapshot it
already holds. Every step can be rerun: a copy skips objects already held at the
same size, and a cut skips a kind/region that has its _DONE marker.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from connect_labs.microplans.core import barriers, overture

STEPS = ("copy", "cut", "check", "ready", "switch", "prune")
# October 2026's largest real release-to-release change in a sample box was +23%
# (Freetown); a swing past this is far likelier a broken cut than a building boom.
MAX_BUILDING_SWING = 0.25


def _s3():
    import boto3
    from botocore.config import Config

    cfg = Config(max_pool_connections=64, retries={"max_attempts": 10, "mode": "adaptive"})
    return boto3.client("s3", region_name="us-west-2", config=cfg), boto3.client(
        "s3", region_name="us-east-1", config=cfg
    )


def _listing(client, bucket: str, prefix: str) -> dict[str, int]:
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            out[o["Key"][len(prefix) :]] = o["Size"]
    return out


def _put_json(dst, key: str, value: dict) -> None:
    dst.put_object(
        Bucket=overture.EXTRACT_BUCKET,
        Key=key,
        Body=json.dumps(value, indent=2, sort_keys=True).encode(),
        ContentType="application/json",
    )


def _get_json(dst, key: str) -> dict | None:
    try:
        return json.loads(dst.get_object(Bucket=overture.EXTRACT_BUCKET, Key=key)["Body"].read())
    except dst.exceptions.NoSuchKey:
        return None


def _tile_key_prefix(kind: str, region: str, release: str) -> str:
    return f"{overture.snapshot_prefix(release)}/tiles/{kind}/{region}"


def _cut_sql(kind: str, region: str, release: str) -> str:
    """One kind's tiles for one region, read from the release's raw copy.

    Buildings are assigned to the tile their CENTROID falls in, the same floor()
    the reader bounds its scan with (footprints._query_tiles). Lines and lakes are
    written to EVERY tile their bbox touches, clipped to the region's tiles, so a
    road through a ward is in that ward's tiles however long it is; the reader
    dedupes on id (barriers._tile_sql).
    """
    x0, y0, x1, y1 = overture.REGIONS[region]["bbox"]
    dest = overture.tile_prefix(kind, region, release)
    copy_opts = "(FORMAT PARQUET, PARTITION_BY (tx, ty), OVERWRITE_OR_IGNORE 1, COMPRESSION ZSTD)"
    if kind == "buildings":
        return f"""
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
                FROM read_parquet('{overture.theme_path("buildings", "building", release)}',
                                  filename=false, hive_partitioning=true)
                WHERE bbox.xmin >= {x0} AND bbox.xmax <= {x1}
                  AND bbox.ymin >= {y0} AND bbox.ymax <= {y1}
            ) TO '{dest}' {copy_opts};
        """
    if kind == "segments":
        road_in = ", ".join(f"'{c}'" for c in barriers.ROAD_CLASSES)
        source = overture.theme_path("transportation", "segment", release)
        where = f"((subtype = 'road' AND class IN ({road_in})) OR subtype = 'rail')"
    elif kind == "water":
        water_in = ", ".join(f"'{c}'" for c in barriers.WATER_CLASSES)
        source = overture.theme_path("base", "water", release)
        where = f"class IN ({water_in})"
    else:
        raise CommandError(f"unknown tile kind {kind!r}")
    tx0, ty0, tx1, ty1 = int(x0 // 1), int(y0 // 1), int(x1 // 1), int(y1 // 1)
    return f"""
        COPY (
            SELECT s.id, ST_AsWKB(s.geometry) AS geom_wkb, s.bbox, a.tx, b.ty
            FROM read_parquet('{source}', filename=false, hive_partitioning=true) s,
                 LATERAL (SELECT UNNEST(range(GREATEST(FLOOR(s.bbox.xmin)::INT, {tx0}),
                                               LEAST(FLOOR(s.bbox.xmax)::INT, {tx1}) + 1)) AS tx) a,
                 LATERAL (SELECT UNNEST(range(GREATEST(FLOOR(s.bbox.ymin)::INT, {ty0}),
                                               LEAST(FLOOR(s.bbox.ymax)::INT, {ty1}) + 1)) AS ty) b
            WHERE s.bbox.xmin <= {x1} AND s.bbox.xmax >= {x0}
              AND s.bbox.ymin <= {y1} AND s.bbox.ymax >= {y0}
              AND {where}
        ) TO '{dest}' {copy_opts};
    """


def _filters(kind: str) -> dict | None:
    return {"segments": barriers.segment_filters(), "water": barriers.water_filters()}.get(kind)


class Command(BaseCommand):
    help = "Snapshot an Overture release into labs' bucket, check it, and switch labs to it"

    def add_arguments(self, parser):
        parser.add_argument("release", nargs="?", help="Overture release id (default: the newest published)")
        parser.add_argument("--steps", default=",".join(STEPS), help=f"comma list from {','.join(STEPS)}")
        parser.add_argument("--kinds", default=",".join(overture.TILE_KINDS), help="tile kinds to cut")
        parser.add_argument("--regions", default=",".join(overture.REGIONS), help="regions to cut and check")
        parser.add_argument("--dry-run", action="store_true", help="print the plan and SQL; read and write nothing")

    def handle(self, *args, **opts):
        steps = [s for s in opts["steps"].split(",") if s]
        kinds = [k for k in opts["kinds"].split(",") if k]
        regions = [r for r in opts["regions"].split(",") if r]
        for name, given, allowed in (
            ("step", steps, STEPS),
            ("kind", kinds, overture.TILE_KINDS),
            ("region", regions, overture.REGIONS),
        ):
            unknown = [g for g in given if g not in allowed]
            if unknown:
                raise CommandError(f"unknown {name} {', '.join(unknown)}; known: {', '.join(allowed)}")

        release = opts["release"]
        if not release:
            published = overture.available_releases()
            if not published:
                raise CommandError("could not list Overture's releases; name one")
            release = published[0]
        self.release, self.dry_run = release, opts["dry_run"]
        self.stdout.write(self.style.MIGRATE_HEADING(f"overture_refresh {release}: {', '.join(steps)}"))

        if self.dry_run:
            for kind in kinds if "cut" in steps else []:
                for region in regions:
                    self.stdout.write(f"-- cut {kind}/{region}\n{_cut_sql(kind, region, release)}")
            return

        self.src, self.dst = _s3()
        for step in STEPS:
            if step in steps:
                t0 = time.time()
                getattr(self, f"_{step}")(kinds, regions)
                self.stdout.write(f"  {step} done in {time.time() - t0:.0f}s")

    # -- copy ----------------------------------------------------------------

    def _copy(self, kinds, regions):
        from boto3.s3.transfer import TransferConfig

        src_prefix = f"release/{self.release}/"
        dst_prefix = f"{overture.snapshot_prefix(self.release)}/raw/"
        source = _listing(self.src, overture.SOURCE_BUCKET, src_prefix)
        if not source:
            raise CommandError(f"Overture has no release {self.release!r}")
        held = _listing(self.dst, overture.EXTRACT_BUCKET, dst_prefix)
        todo = [k for k, size in source.items() if held.get(k) != size]
        self.stdout.write(
            f"  copy: {len(source)} objects, {sum(source.values()) / 1e9:.1f} GB; {len(todo)} not yet held"
        )
        xfer = TransferConfig(multipart_threshold=256 * 2**20, multipart_chunksize=256 * 2**20, max_concurrency=8)

        def one(key):
            # Server-side: the bytes go bucket to bucket, never through this task.
            self.dst.copy(
                {"Bucket": overture.SOURCE_BUCKET, "Key": src_prefix + key},
                overture.EXTRACT_BUCKET,
                dst_prefix + key,
                SourceClient=self.src,
                Config=xfer,
            )

        failed = []
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {pool.submit(one, k): k for k in todo}
            for n, f in enumerate(as_completed(futures), 1):
                try:
                    f.result()
                except Exception as exc:  # noqa: BLE001 — reported, then the step fails
                    failed.append(f"{futures[f]}: {exc!r}")
                if n % 50 == 0:
                    self.stdout.write(f"    {n}/{len(todo)}")
        held = _listing(self.dst, overture.EXTRACT_BUCKET, dst_prefix)
        missing = [k for k, size in source.items() if held.get(k) != size]
        if failed or missing:
            raise CommandError(
                f"copy incomplete: {len(failed)} failed, {len(missing)} not held at Overture's size "
                f"(first: {(failed or missing)[0]}). Rerun to resume."
            )
        self.raw = {"objects": len(source), "bytes": sum(source.values())}

    # -- cut -----------------------------------------------------------------

    def _cut(self, kinds, regions):
        for kind in kinds:
            for region in regions:
                marker = f"{_tile_key_prefix(kind, region, self.release)}/_DONE.json"
                if _get_json(self.dst, marker) is not None:
                    self.stdout.write(f"  cut {kind}/{region}: already done")
                    continue
                t0 = time.time()
                con = overture.connect()
                try:
                    con.execute(_cut_sql(kind, region, self.release))
                    rows = con.execute(
                        f"SELECT count(*) FROM read_parquet('{overture.tile_glob(kind, region, self.release)}')"
                    ).fetchone()[0]
                finally:
                    con.close()
                _put_json(
                    self.dst,
                    marker,
                    {
                        "rows": rows,
                        "filters": _filters(kind),
                        "bbox": list(overture.REGIONS[region]["bbox"]),
                        "cut_at": datetime.now(timezone.utc).isoformat(),
                        "seconds": round(time.time() - t0),
                    },
                )
                self.stdout.write(f"  cut {kind}/{region}: {rows} rows in {time.time() - t0:.0f}s")

    # -- check ---------------------------------------------------------------

    def _check(self, kinds, regions):
        current = (_get_json(self.dst, overture.MANIFEST_KEY) or {}).get("release")
        before = (_get_json(self.dst, f"{overture.snapshot_prefix(current)}/READY.json") or {}) if current else {}
        con = overture.connect()
        problems, self.checks = [], {}
        try:
            for region in regions:
                meta = overture.REGIONS[region]
                x0, y0, x1, y1 = meta["sample"]
                result = {}
                for kind in overture.TILE_KINDS:
                    done = _get_json(self.dst, f"{_tile_key_prefix(kind, region, self.release)}/_DONE.json")
                    result[f"{kind}_rows"] = done and done["rows"]
                    if not done or not done["rows"]:
                        problems.append(f"{region}: no {kind} tiles")
                in_box = (
                    f"tx BETWEEN {int(x0 // 1)} AND {int(x1 // 1)} AND ty BETWEEN {int(y0 // 1)} AND {int(y1 // 1)}"
                )
                if result["buildings_rows"]:
                    result["sample_buildings"] = con.execute(
                        f"SELECT count(*) FROM read_parquet('{overture.tile_glob('buildings', region, self.release)}',"
                        f" hive_partitioning=true) WHERE {in_box}"
                        f" AND lon BETWEEN {x0} AND {x1} AND lat BETWEEN {y0} AND {y1}"
                    ).fetchone()[0]
                    if not result["sample_buildings"]:
                        problems.append(f"{region}: no buildings in its sample box")
                    prior = ((before.get("checks") or {}).get(region) or {}).get("sample_buildings")
                    if prior and result["sample_buildings"]:
                        swing = result["sample_buildings"] / prior - 1
                        result["sample_buildings_change"] = round(swing, 3)
                        if abs(swing) > MAX_BUILDING_SWING:
                            problems.append(
                                f"{region}: sample buildings {prior} -> {result['sample_buildings']} "
                                f"({swing:+.0%}, limit {MAX_BUILDING_SWING:.0%})"
                            )
                if result["segments_rows"]:
                    result["sample_segments"] = con.execute(
                        f"SELECT count(DISTINCT id) FROM read_parquet("
                        f"'{overture.tile_glob('segments', region, self.release)}', hive_partitioning=true)"
                        f" WHERE {in_box} AND bbox.xmin <= {x1} AND bbox.xmax >= {x0}"
                        f" AND bbox.ymin <= {y1} AND bbox.ymax >= {y0}"
                    ).fetchone()[0]
                    if not result["sample_segments"]:
                        problems.append(f"{region}: no major roads or rail in its sample box")
                result["divisions"] = con.execute(
                    f"SELECT count(*) FROM read_parquet("
                    f"'{overture.theme_path('divisions', 'division_area', self.release)}',"
                    f" filename=false, hive_partitioning=true) WHERE country = ? AND subtype = 'region'",
                    [meta["iso2"]],
                ).fetchone()[0]
                if not result["divisions"]:
                    problems.append(f"{region}: no {meta['iso2']} regions in divisions")
                self.checks[region] = result
                self.stdout.write(f"  check {region}: {result}")
        finally:
            con.close()
        if problems:
            raise CommandError("checks failed, labs is unchanged:\n  " + "\n  ".join(problems))

    # -- ready / switch / prune ---------------------------------------------

    def _ready(self, kinds, regions):
        if not hasattr(self, "checks"):
            raise CommandError("ready needs check in the same run (--steps check,ready)")
        prefix = overture.snapshot_prefix(self.release)
        raw = getattr(self, "raw", None)
        if raw is None:
            held = _listing(self.dst, overture.EXTRACT_BUCKET, f"{prefix}/raw/")
            raw = {"objects": len(held), "bytes": sum(held.values())}
        if not raw["objects"]:
            raise CommandError(f"no raw copy held for {self.release}; run copy first")
        tiles = {}
        for kind in overture.TILE_KINDS:
            tiles[kind] = {}
            for region in overture.REGIONS:
                done = _get_json(self.dst, f"{_tile_key_prefix(kind, region, self.release)}/_DONE.json")
                if done and done["rows"] and done.get("filters") == _filters(kind):
                    tiles[kind][region] = done["bbox"]
        ready = {
            "release": self.release,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "raw": raw,
            "tiles": tiles,
            "filters": {k: f for k in overture.TILE_KINDS if (f := _filters(k)) is not None},
            "checks": self.checks,
        }
        _put_json(self.dst, f"{prefix}/READY.json", ready)
        overture.forget_cached_manifest(self.release)
        self.stdout.write(f"  ready: {sum(len(v) for v in tiles.values())} kind/region tile sets")

    def _switch(self, kinds, regions):
        if _get_json(self.dst, f"{overture.snapshot_prefix(self.release)}/READY.json") is None:
            raise CommandError(f"{self.release} has no READY.json; refusing to switch labs to it")
        current = _get_json(self.dst, overture.MANIFEST_KEY) or {}
        if current.get("release") == self.release:
            self.stdout.write(f"  switch: labs already reads {self.release}")
            return
        _put_json(
            self.dst,
            overture.MANIFEST_KEY,
            {
                "release": self.release,
                "previous": current.get("release") or current.get("previous"),
                "switched_at": datetime.now(timezone.utc).isoformat(),
                "switched_by": "overture_refresh",
            },
        )
        overture.forget_cached_manifest(self.release)
        self.stdout.write(self.style.SUCCESS(f"  switch: labs now reads {self.release}"))

    def _prune(self, kinds, regions):
        current = _get_json(self.dst, overture.MANIFEST_KEY) or {}
        keep = {current.get("release"), current.get("previous")} - {None}
        if not keep:
            self.stdout.write("  prune: no manifest, nothing pruned")
            return
        base = f"{overture.OVERTURE_PREFIX}/snapshots/"
        held = set()
        for page in self.dst.get_paginator("list_objects_v2").paginate(
            Bucket=overture.EXTRACT_BUCKET, Prefix=base, Delimiter="/"
        ):
            held |= {p["Prefix"][len(base) :].rstrip("/") for p in page.get("CommonPrefixes", [])}
        for release in sorted(held - keep):
            keys = list(_listing(self.dst, overture.EXTRACT_BUCKET, f"{base}{release}/"))
            for i in range(0, len(keys), 1000):
                self.dst.delete_objects(
                    Bucket=overture.EXTRACT_BUCKET,
                    Delete={"Objects": [{"Key": f"{base}{release}/{k}"} for k in keys[i : i + 1000]], "Quiet": True},
                )
            self.stdout.write(f"  prune: deleted snapshot {release} ({len(keys)} objects)")
