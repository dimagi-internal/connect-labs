"""Build labs' snapshot of an Overture release, check it, and switch labs to it.

    manage.py overture_refresh                       # the newest release Overture publishes
    manage.py overture_refresh 2026-11-18.0
    manage.py overture_refresh 2026-11-18.0 --steps copy,plan
    manage.py overture_refresh 2026-11-18.0 --steps cut --kinds segments,water --blocks 6,8
    manage.py overture_refresh 2026-11-18.0 --dry-run

Steps, in order (see overture.py for the layout and why it exists):

    copy    server-side copy of Overture's whole release/<R>/ into snapshots/<R>/raw/,
            then the object count and bytes must match Overture's listing
    plan    the coverage: every 1-degree cell touching a COVERAGE_COUNTRIES outline in
            this release's divisions, written to snapshots/<R>/tiles/cells.json
    cut     the planned cells' tiles, per kind, one BLOCK_DEGREES-wide strip at a time,
            read from the raw copy (same region)
    check   every strip cut for every kind, and every SAMPLES city: buildings and roads
            in its box, its divisions resolve, buildings within +/-25% of the current release
    ready   write snapshots/<R>/READY.json: the grid's cells and kinds, their filters, the checks
    switch  write CURRENT.json (release R, previous = the release labs read until now)
    prune   delete snapshots that are neither the current release nor the previous one

Nothing labs reads changes until ``switch``, and ``switch`` refuses a snapshot
with no READY.json, so a failure anywhere before it leaves labs on the snapshot it
already holds. Every step can be rerun: a copy skips objects already held at the
same size, and a cut skips a kind/strip that has its marker. Strips are
independent, so a refresh can cut them in parallel tasks (``--blocks``).
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from django.core.management.base import BaseCommand, CommandError

from connect_labs.microplans.core import barriers, overture

STEPS = ("copy", "plan", "cut", "check", "ready", "switch", "prune")
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


def _delete_prefix(dst, prefix: str) -> int:
    keys = list(_listing(dst, overture.EXTRACT_BUCKET, prefix))
    for i in range(0, len(keys), 1000):
        dst.delete_objects(
            Bucket=overture.EXTRACT_BUCKET,
            Delete={"Objects": [{"Key": prefix + k} for k in keys[i : i + 1000]], "Quiet": True},
        )
    return len(keys)


def _tiles_key(release: str) -> str:
    return f"{overture.snapshot_prefix(release)}/tiles"


def _cells_key(release: str) -> str:
    return f"{_tiles_key(release)}/cells.json"


def _block_key(kind: str, block: int, release: str) -> str:
    return f"{_tiles_key(release)}/{kind}/blocks/{block}.json"


def _legacy_marker(kind: str, region: str, release: str) -> str:
    """A per-country tile set from before the grid. Only READY's ``tiles`` lists
    them, for readers that have not moved to the grid yet."""
    return f"{_tiles_key(release)}/{kind}/{region}/_DONE.json"


def _plan_sql(release: str) -> str:
    """The cells whose 1x1-degree square touches a covered country's outline."""
    codes = ", ".join(f"'{c}'" for c in overture.COVERAGE_COUNTRIES)  # constant list, safe to inline
    return f"""
        SELECT DISTINCT a.tx, b.ty
        FROM (
            SELECT geometry, bbox
            FROM read_parquet('{overture.theme_path("divisions", "division_area", release)}',
                              filename=false, hive_partitioning=true)
            WHERE subtype = 'country' AND country IN ({codes})
        ) c,
        LATERAL (SELECT UNNEST(range(FLOOR(c.bbox.xmin)::INT, FLOOR(c.bbox.xmax)::INT + 1)) AS tx) a,
        LATERAL (SELECT UNNEST(range(FLOOR(c.bbox.ymin)::INT, FLOOR(c.bbox.ymax)::INT + 1)) AS ty) b
        WHERE ST_Intersects(c.geometry, ST_MakeEnvelope(a.tx, b.ty, a.tx + 1, b.ty + 1))
        ORDER BY 1, 2
    """


def _cut_sql(kind: str, block: int, cells: list[tuple[int, int]], release: str) -> str:
    """One kind's tiles for the planned cells of one strip, read from the raw copy.

    Buildings go to the cell their CENTROID falls in, the same floor() a reader
    bounds its scan with, so each is stored once. Lines and lakes are written to
    EVERY planned cell their bbox touches, so a road through a ward is in that
    ward's cells however long it is; readers dedupe on id (barriers._tile_sql).
    The bbox prefilter on the strip lets DuckDB skip the raw files' row groups
    that lie elsewhere on the planet.
    """
    x0, x1 = block, block + overture.BLOCK_DEGREES  # [x0, x1): the strip's columns
    y0 = min(ty for _, ty in cells)
    y1 = max(ty for _, ty in cells) + 1
    values = ", ".join(f"({tx}, {ty})" for tx, ty in cells)
    dest = overture.grid_prefix(kind, release)
    copy_opts = "(FORMAT PARQUET, PARTITION_BY (tx, ty), OVERWRITE_OR_IGNORE 1, COMPRESSION ZSTD)"
    if kind == "buildings":
        return f"""
            COPY (
                WITH cells(tx, ty) AS (VALUES {values})
                SELECT f.* FROM (
                    SELECT
                        ST_X(c)                    AS lon,
                        ST_Y(c)                    AS lat,
                        ST_Area_Spheroid(geometry) AS area_m2,
                        sources[1].confidence      AS confidence,
                        sources[1].dataset         AS dataset,
                        ST_AsWKB(geometry)         AS geom_wkb,
                        bbox,
                        FLOOR(ST_X(c))::INT        AS tx,
                        FLOOR(ST_Y(c))::INT        AS ty
                    FROM (
                        SELECT *, ST_Centroid(geometry) AS c
                        FROM read_parquet('{overture.theme_path("buildings", "building", release)}',
                                          filename=false, hive_partitioning=true)
                        WHERE bbox.xmax >= {x0} AND bbox.xmin < {x1}
                          AND bbox.ymax >= {y0} AND bbox.ymin < {y1}
                    )
                ) f
                SEMI JOIN cells USING (tx, ty)
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
    return f"""
        COPY (
            WITH cells(tx, ty) AS (VALUES {values})
            SELECT f.* FROM (
                SELECT s.id, ST_AsWKB(s.geometry) AS geom_wkb, s.bbox, a.tx, b.ty
                FROM read_parquet('{source}', filename=false, hive_partitioning=true) s,
                     LATERAL (SELECT UNNEST(range(GREATEST(FLOOR(s.bbox.xmin)::INT, {x0}),
                                                   LEAST(FLOOR(s.bbox.xmax)::INT, {x1 - 1}) + 1)) AS tx) a,
                     LATERAL (SELECT UNNEST(range(GREATEST(FLOOR(s.bbox.ymin)::INT, {y0}),
                                                   LEAST(FLOOR(s.bbox.ymax)::INT, {y1 - 1}) + 1)) AS ty) b
                WHERE s.bbox.xmax >= {x0} AND s.bbox.xmin < {x1}
                  AND s.bbox.ymax >= {y0} AND s.bbox.ymin < {y1}
                  AND {where}
            ) f
            SEMI JOIN cells USING (tx, ty)
        ) TO '{dest}' {copy_opts};
    """


def _filters(kind: str) -> dict | None:
    return {"segments": barriers.segment_filters(), "water": barriers.water_filters()}.get(kind)


def _count(con, files: list[str], what: str, where: str) -> int:
    """``SELECT <what>`` over some grid files; none means nothing there, not an error."""
    if not files:
        return 0
    return con.execute(f"SELECT {what} FROM read_parquet({files!r}) WHERE {where}").fetchone()[0]


def _blocks(cells) -> dict[int, list[tuple[int, int]]]:
    out: dict[int, list[tuple[int, int]]] = {}
    for tx, ty in cells:
        out.setdefault(overture.block_of(tx), []).append((tx, ty))
    return dict(sorted(out.items()))


class Command(BaseCommand):
    help = "Snapshot an Overture release into labs' bucket, check it, and switch labs to it"

    def add_arguments(self, parser):
        parser.add_argument("release", nargs="?", help="Overture release id (default: the newest published)")
        parser.add_argument("--steps", default=",".join(STEPS), help=f"comma list from {','.join(STEPS)}")
        parser.add_argument("--kinds", default=",".join(overture.TILE_KINDS), help="tile kinds to cut")
        parser.add_argument(
            "--blocks",
            default="",
            help=f"strips to cut, by west edge (multiples of {overture.BLOCK_DEGREES}); default every planned strip",
        )
        parser.add_argument("--dry-run", action="store_true", help="print the plan and SQL; read and write nothing")

    def handle(self, *args, **opts):
        steps = [s for s in opts["steps"].split(",") if s]
        kinds = [k for k in opts["kinds"].split(",") if k]
        for name, given, allowed in (("step", steps, STEPS), ("kind", kinds, overture.TILE_KINDS)):
            unknown = [g for g in given if g not in allowed]
            if unknown:
                raise CommandError(f"unknown {name} {', '.join(unknown)}; known: {', '.join(allowed)}")
        try:
            blocks = [int(b) for b in opts["blocks"].split(",") if b.strip()]
        except ValueError:
            raise CommandError(f"--blocks takes whole degrees, got {opts['blocks']!r}") from None
        uneven = [b for b in blocks if b != overture.block_of(b)]
        if uneven:
            raise CommandError(f"a strip starts on a multiple of {overture.BLOCK_DEGREES}; got {uneven}")

        release = opts["release"]
        if not release:
            published = overture.available_releases()
            if not published:
                raise CommandError("could not list Overture's releases; name one")
            release = published[0]
        self.release, self.dry_run = release, opts["dry_run"]
        self.stdout.write(self.style.MIGRATE_HEADING(f"overture_refresh {release}: {', '.join(steps)}"))

        if self.dry_run:
            if "plan" in steps:
                self.stdout.write(f"-- plan\n{_plan_sql(release)}")
            # The real cells come from the plan; a dry run shows one cell per strip.
            for kind in kinds if "cut" in steps else []:
                for block in blocks or [overture.block_of(8)]:
                    self.stdout.write(f"-- cut {kind}/{block}\n{_cut_sql(kind, block, [(block, 11)], release)}")
            return

        self.src, self.dst = _s3()
        for step in STEPS:
            if step in steps:
                t0 = time.time()
                getattr(self, f"_{step}")(kinds, blocks)
                self.stdout.write(f"  {step} done in {time.time() - t0:.0f}s")

    # -- copy ----------------------------------------------------------------

    def _copy(self, kinds, blocks):
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

    # -- plan / cut ------------------------------------------------------------

    def _plan(self, kinds, blocks):
        con = overture.connect()
        try:
            cells = [[tx, ty] for tx, ty in con.execute(_plan_sql(self.release)).fetchall()]
        finally:
            con.close()
        if not cells:
            raise CommandError(f"no covered country found in {self.release}'s divisions")
        _put_json(
            self.dst,
            _cells_key(self.release),
            {
                "cells": cells,
                "countries": list(overture.COVERAGE_COUNTRIES),
                "block_degrees": overture.BLOCK_DEGREES,
                "planned_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        self.stdout.write(f"  plan: {len(cells)} cells in {len(_blocks(cells))} strips")

    def _planned(self) -> dict[int, list[tuple[int, int]]]:
        plan = _get_json(self.dst, _cells_key(self.release))
        if plan is None:
            raise CommandError(f"{self.release} has no cells.json; run --steps plan first")
        if plan["block_degrees"] != overture.BLOCK_DEGREES:
            raise CommandError("cells.json was planned with other strip widths; run --steps plan again")
        return _blocks(tuple(c) for c in plan["cells"])

    def _cut(self, kinds, blocks):
        planned = self._planned()
        unplanned = [b for b in blocks if b not in planned]
        if unplanned:
            raise CommandError(f"no planned cells in strips {unplanned}; planned: {list(planned)}")
        for kind in kinds:
            for block in blocks or planned:
                cells = planned[block]
                marker = _block_key(kind, block, self.release)
                if _get_json(self.dst, marker) is not None:
                    self.stdout.write(f"  cut {kind}/{block}: already done")
                    continue
                # No marker means no finished cut, but possibly a half-written one
                # (a killed task). COPY ... OVERWRITE_OR_IGNORE replaces same-named
                # files and keeps the rest, so clear the strip's columns first or a
                # partial cut's extra files would be read alongside the new ones.
                grid = f"{_tiles_key(self.release)}/{kind}/grid"
                for tx in range(block, block + overture.BLOCK_DEGREES):
                    _delete_prefix(self.dst, f"{grid}/tx={tx}/")
                t0 = time.time()
                con = overture.connect()
                try:
                    rows = con.execute(_cut_sql(kind, block, cells, self.release)).fetchone()[0]
                finally:
                    con.close()
                _put_json(
                    self.dst,
                    marker,
                    {
                        "rows": rows,
                        "cells": len(cells),
                        "filters": _filters(kind),
                        "cut_at": datetime.now(timezone.utc).isoformat(),
                        "seconds": round(time.time() - t0),
                    },
                )
                self.stdout.write(
                    f"  cut {kind}/{block}: {rows} rows over {len(cells)} cells in {time.time() - t0:.0f}s"
                )

    # -- check ---------------------------------------------------------------

    def _check(self, kinds, blocks):
        planned = self._planned()
        covered = {cell for cells in planned.values() for cell in cells}
        current = (_get_json(self.dst, overture.MANIFEST_KEY) or {}).get("release")
        before = (_get_json(self.dst, f"{overture.snapshot_prefix(current)}/READY.json") or {}) if current else {}
        problems, self.checks = [], {}
        rows = {kind: 0 for kind in overture.TILE_KINDS}
        for kind in overture.TILE_KINDS:
            for block in planned:
                done = _get_json(self.dst, _block_key(kind, block, self.release))
                if done is None:
                    problems.append(f"strip {block}: no {kind} tiles")
                else:
                    rows[kind] += done["rows"]
        self.checks["_grid"] = {
            "cells": len(covered),
            "strips": len(planned),
            **{f"{k}_rows": v for k, v in rows.items()},
        }
        con = overture.connect()
        try:
            for name, meta in overture.SAMPLES.items():
                x0, y0, x1, y1 = meta["sample"]
                cells = overture.cells_touching(meta["sample"])
                result = {}
                if not set(cells) <= covered:
                    problems.append(f"{name}: its sample box is outside the planned cells")
                else:
                    files = overture.cell_files(con, "buildings", cells, self.release)
                    result["sample_buildings"] = _count(
                        con, files, "count(*)", f"lon BETWEEN {x0} AND {x1} AND lat BETWEEN {y0} AND {y1}"
                    )
                    if not result["sample_buildings"]:
                        problems.append(f"{name}: no buildings in its sample box")
                    prior = ((before.get("checks") or {}).get(name) or {}).get("sample_buildings")
                    if prior and result["sample_buildings"]:
                        swing = result["sample_buildings"] / prior - 1
                        result["sample_buildings_change"] = round(swing, 3)
                        if abs(swing) > MAX_BUILDING_SWING:
                            problems.append(
                                f"{name}: sample buildings {prior} -> {result['sample_buildings']} "
                                f"({swing:+.0%}, limit {MAX_BUILDING_SWING:.0%})"
                            )
                    files = overture.cell_files(con, "segments", cells, self.release)
                    result["sample_segments"] = _count(
                        con,
                        files,
                        "count(DISTINCT id)",
                        f"bbox.xmin <= {x1} AND bbox.xmax >= {x0} AND bbox.ymin <= {y1} AND bbox.ymax >= {y0}",
                    )
                    if not result["sample_segments"]:
                        problems.append(f"{name}: no major roads or rail in its sample box")
                result["divisions"] = con.execute(
                    f"SELECT count(*) FROM read_parquet("
                    f"'{overture.theme_path('divisions', 'division_area', self.release)}',"
                    f" filename=false, hive_partitioning=true) WHERE country = ? AND subtype = 'region'",
                    [meta["iso2"]],
                ).fetchone()[0]
                if not result["divisions"]:
                    problems.append(f"{name}: no {meta['iso2']} regions in divisions")
                self.checks[name] = result
                self.stdout.write(f"  check {name}: {result}")
        finally:
            con.close()
        self.stdout.write(f"  check grid: {self.checks['_grid']}")
        if problems:
            raise CommandError("checks failed, labs is unchanged:\n  " + "\n  ".join(problems))

    # -- ready / switch / prune ---------------------------------------------

    def _ready(self, kinds, blocks):
        if not hasattr(self, "checks"):
            raise CommandError("ready needs check in the same run (--steps check,ready)")
        prefix = overture.snapshot_prefix(self.release)
        raw = getattr(self, "raw", None)
        if raw is None:
            held = _listing(self.dst, overture.EXTRACT_BUCKET, f"{prefix}/raw/")
            raw = {"objects": len(held), "bytes": sum(held.values())}
        if not raw["objects"]:
            raise CommandError(f"no raw copy held for {self.release}; run copy first")
        planned = self._planned()
        grid_kinds = {}
        for kind in overture.TILE_KINDS:
            markers = [_get_json(self.dst, _block_key(kind, block, self.release)) for block in planned]
            # A kind is tiled only when every strip was cut under the filters the
            # readers select now: a partial or stale grid would answer "nothing here".
            if all(m and m.get("filters") == _filters(kind) for m in markers):
                grid_kinds[kind] = {"rows": sum(m["rows"] for m in markers), "filters": _filters(kind)}
        grid = {
            "cells": sorted([tx, ty] for cells in planned.values() for tx, ty in cells),
            "block_degrees": overture.BLOCK_DEGREES,
            "kinds": grid_kinds,
        }
        # The per-country tile sets cut before the grid, for readers that still
        # route by region. Goes when they read the grid.
        tiles = {}
        for kind in overture.TILE_KINDS:
            tiles[kind] = {}
            for region in overture.REGIONS:
                done = _get_json(self.dst, _legacy_marker(kind, region, self.release))
                if done and done["rows"] and done.get("filters") == _filters(kind):
                    tiles[kind][region] = done["bbox"]
        ready = {
            "release": self.release,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "raw": raw,
            "grid": grid,
            "tiles": tiles,
            "filters": {k: f for k in overture.TILE_KINDS if (f := _filters(k)) is not None},
            "checks": self.checks,
        }
        _put_json(self.dst, f"{prefix}/READY.json", ready)
        overture.forget_cached_manifest(self.release)
        self.stdout.write(f"  ready: grid of {len(grid['cells'])} cells for {', '.join(grid_kinds) or 'no kind'}")

    def _switch(self, kinds, blocks):
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

    def _prune(self, kinds, blocks):
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
            n = _delete_prefix(self.dst, f"{base}{release}/")
            self.stdout.write(f"  prune: deleted snapshot {release} ({n} objects)")
