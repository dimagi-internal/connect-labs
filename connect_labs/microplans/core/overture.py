"""Shared Overture Maps access via DuckDB over the public S3 Parquet.

Both building footprints (footprints.py) and admin boundaries (boundaries.py)
query Overture the same way: DuckDB with the spatial + httpfs extensions,
pointed at a writable extension dir (the labs container runs with
HOME=/nonexistent, so DuckDB can't use the default ~/.duckdb).
"""

from __future__ import annotations

import os
import tempfile

# Overture release. Bump as Overture cuts monthly releases; the S3 layout is stable.
OVERTURE_RELEASE = "2026-05-20.0"
_S3_BASE = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}"


def theme_path(theme: str, type_: str) -> str:
    """S3 glob for an Overture theme/type, e.g. theme_path('buildings', 'building')."""
    return f"{_S3_BASE}/theme={theme}/type={type_}/*"


# ---------------------------------------------------------------------------
# Same-region (us-east-1) full-country building extracts.
#
# Reading the planet-scale Overture release lives in us-west-2; the labs worker
# is in us-east-1, and the cross-region parallel footer reads don't overlap, so a
# first-seen area costs ~4-8 min. We pre-extract whole countries into the labs
# us-east-1 bucket (one Parquet per 1-degree tile, carrying lon/lat/area_m2/
# dataset/confidence/bbox/geom_wkb). An area fully inside a listed region — and on
# the matching Overture release — is served from a couple of same-region tiles in
# well under a second; anything else falls back to the live Overture read. Because
# we cache the *source* (the whole country), every first-seen ward inside it is
# already fast — there is nothing to pre-warm per area.
#
# Refresh per Overture release: re-run the extract into a new
# `<bucket>/overture/<region>/<release>/` prefix and bump the release here.
EXTRACT_BUCKET = "labs-jj-exports-dev-858923557655-us-east-1-an"
_EXTRACT_BASE = f"s3://{EXTRACT_BUCKET}/overture"

# region name -> (release, bbox as (minx, miny, maxx, maxy)).
EXTRACT_REGIONS: dict[str, dict] = {
    "nigeria": {"release": "2026-05-20.0", "bbox": (2.6, 4.2, 14.7, 13.9)},
}


def extract_glob(region_name: str) -> str:
    """The hive-partitioned tile glob for a region's same-region extract."""
    rel = EXTRACT_REGIONS[region_name]["release"]
    return f"{_EXTRACT_BASE}/{region_name}/{rel}/**/*.parquet"


def covering_region(bounds: tuple[float, float, float, float]) -> str | None:
    """Name of the extract region whose bbox fully contains `bounds`
    (minx, miny, maxx, maxy) on the active Overture release, else None.

    The release check means bumping ``OVERTURE_RELEASE`` without re-extracting
    safely falls back to the live read rather than serving stale buildings.
    """
    minx, miny, maxx, maxy = bounds
    for name, meta in EXTRACT_REGIONS.items():
        if meta["release"] != OVERTURE_RELEASE:
            continue
        bx0, by0, bx1, by1 = meta["bbox"]
        if minx >= bx0 and miny >= by0 and maxx <= bx1 and maxy <= by1:
            return name
    return None


def connect():
    """Return a DuckDB connection with spatial + httpfs loaded and a writable home."""
    import duckdb

    ext_dir = os.path.join(tempfile.gettempdir(), "duckdb_ext")
    os.makedirs(ext_dir, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET home_directory='{tempfile.gettempdir()}';")
    con.execute(f"SET extension_directory='{ext_dir}';")
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    # Credentials for the private same-region extract bucket (us-east-1). Scoped so
    # only those reads use these creds + region; the public Overture release reads
    # keep the global us-west-2 region above. credential_chain resolves to the AWS
    # env/profile locally and the Fargate task role on the worker.
    con.execute(
        "CREATE OR REPLACE SECRET labs_extract "
        "(TYPE s3, PROVIDER credential_chain, REGION 'us-east-1', "
        f"SCOPE 's3://{EXTRACT_BUCKET}');"
    )
    # A footprint/boundary read globs the planet-scale Overture release (~512 Parquet
    # files) and prunes row groups by the bbox column statistics — work dominated by
    # reading each file's footer over S3 (network I/O), not CPU. The labs worker runs
    # on 1 vCPU, so DuckDB defaults its thread pool to a single thread and reads those
    # 512 footers sequentially, cross-region (worker us-east-1 -> bucket us-west-2):
    # a cold Madobi-scale area takes ~4-8 min. Because the reads are I/O-bound, extra
    # threads overlap the network waits even on a single core, so we raise the pool
    # explicitly — measured: the same cold query drops from ~230s (threads=1) to ~20s.
    # http_metadata_cache lets the second arm in a two-arm generate reuse the first
    # arm's already-read Parquet footers within the same connection.
    con.execute("SET threads TO 8;")
    con.execute("SET enable_http_metadata_cache=true;")
    return con
