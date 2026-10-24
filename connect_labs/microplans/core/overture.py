"""Overture Maps data that labs holds, read with DuckDB.

Microplans reads four Overture themes: buildings (footprints.py), admin
divisions (boundaries.py), and major roads + water (barriers.py). Labs never
reads them from Overture's public bucket at request time. Each Overture release
we use is copied into labs' own bucket as a **snapshot**, and a manifest names
the one labs reads. See docs/superpowers/specs/2026-10-24-overture-mirror-design.md.

    s3://<EXTRACT_BUCKET>/overture/
      CURRENT.json                               {"release": R, "previous": P, ...}
      snapshots/<R>/raw/theme=.../type=.../*      byte-for-byte copy of Overture's release/<R>/
      snapshots/<R>/tiles/cells.json              the coverage: every 1-degree cell over Africa
      snapshots/<R>/tiles/<kind>/grid/tx=/ty=     our 1-degree tiles of those cells, the fast path
      snapshots/<R>/tiles/<kind>/blocks/<x>.json  one finished strip of the cut
      snapshots/<R>/READY.json                    written last: which tiles exist, and the checks

Why: Overture PRUNES old releases. When the code pinned a release and read it
from Overture's bucket, the pin was a timer — in October 2026 it ran out, every
live read died on `No files found that match the pattern`, and barriers
swallowed the error, so grouping ran without roads or rivers. Now a release
only stops being readable when labs deletes it, and a failed refresh leaves
labs reading the snapshot it already holds: stale, never broken.

The refresh (copy -> cut -> check -> READY -> switch) is
``manage.py overture_refresh``. Everything that reads Overture goes through
``active_release()`` / ``theme_path()`` / ``tile_cells()`` here.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# The release read when there is no manifest at all: a cold cache with S3
# unreadable, or a test run. NOT the normal pin — CURRENT.json is. Keep it on a
# release labs holds a snapshot of, or a cold start reads nothing.
OVERTURE_RELEASE = "2026-10-21.0"

SOURCE_BUCKET = "overturemaps-us-west-2"
EXTRACT_BUCKET = "labs-jj-exports-dev-858923557655-us-east-1-an"
OVERTURE_PREFIX = "overture"
MANIFEST_KEY = f"{OVERTURE_PREFIX}/CURRENT.json"

# How long a read trusts the manifest before asking S3 again. A switch reaches
# every process within this; a rollback too.
MANIFEST_TTL_SECONDS = 600
_CACHE_KEY = "microplans:overture:json:{}"
_LAST_GOOD_KEY = "microplans:overture:last-good:{}"

# Coverage, as the refresh cuts it: one 1-degree grid over every cell that touches
# one of these countries' Overture outlines (``overture_refresh --steps plan``
# writes the cell list per release). One grid, not a rectangle per country:
# neighbouring rectangles overlap, so a border area was stored twice, and an area
# straddling two of them fit in neither and fell to the raw read (2.5 minutes,
# measured). Overture's divisions codes are ISO 3166-1 alpha-2: the 54 African
# states, Western Sahara, and the African territories Overture lists apart.
COVERAGE_COUNTRIES = (
    "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG "
    "MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW EH RE YT SH"
).split()
# The grid is cut in north-south strips this many degrees wide, one Fargate task
# each, so a strip is the unit a cut resumes from.
BLOCK_DEGREES = 2

# What the refresh's check samples each release: a city per country (so buildings
# and roads are certain to be there), and the country's code in Overture's
# divisions. The first 11 are the countries labs serves boundaries for.
SAMPLES: dict[str, dict] = {
    "nigeria": {"iso2": "NG", "sample": (8.50, 11.98, 8.54, 12.02)},
    "liberia": {"iso2": "LR", "sample": (-10.81, 6.28, -10.77, 6.32)},
    "drc": {"iso2": "CD", "sample": (15.29, -4.34, 15.33, -4.30)},
    "kenya": {"iso2": "KE", "sample": (36.80, -1.31, 36.84, -1.27)},
    "zambia": {"iso2": "ZM", "sample": (28.26, -15.44, 28.30, -15.40)},
    "ethiopia": {"iso2": "ET", "sample": (38.74, 9.01, 38.78, 9.05)},
    "tanzania": {"iso2": "TZ", "sample": (39.26, -6.81, 39.30, -6.77)},
    "cote_divoire": {"iso2": "CI", "sample": (-4.03, 5.33, -3.99, 5.37)},
    "mozambique": {"iso2": "MZ", "sample": (32.56, -25.99, 32.60, -25.95)},
    "malawi": {"iso2": "MW", "sample": (33.77, -13.98, 33.81, -13.94)},
    "sierra_leone": {"iso2": "SL", "sample": (-13.25, 8.46, -13.21, 8.50)},
    "ghana": {"iso2": "GH", "sample": (-0.22, 5.54, -0.18, 5.58)},
}

# What gets tiled. Divisions are read from the raw snapshot instead: their reads
# are by country/name/id rather than by area (so a tile router has nothing to
# route on), they are cached for 30 days, and the whole theme is a few GB.
TILE_KINDS = ("buildings", "segments", "water")


# ---------------------------------------------------------------------------
# Manifest


def _read_json(key: str) -> dict | None:
    """One JSON object from labs' bucket, or None if it is absent or unreadable."""
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        body = boto3.client("s3", region_name="us-east-1").get_object(Bucket=EXTRACT_BUCKET, Key=key)["Body"]
        return json.loads(body.read())
    except (BotoCoreError, ClientError, ValueError) as exc:
        logger.info("overture: could not read s3://%s/%s (%s)", EXTRACT_BUCKET, key, exc)
        return None


def _cached_json(key: str) -> dict | None:
    """``_read_json`` behind the cache, keeping the last good value through an S3 outage.

    A manifest read that fails must not change what labs reads: the last value
    any process saw is kept with no expiry and used until S3 answers again.
    """
    if not getattr(settings, "OVERTURE_SNAPSHOTS_READ_MANIFEST", True):
        return None
    hit = cache.get(_CACHE_KEY.format(key))
    if hit is not None:
        return hit or None  # {} caches "absent"
    value = _read_json(key)
    if value is None:
        last = cache.get(_LAST_GOOD_KEY.format(key))
        if last is not None:
            return last
    cache.set(_CACHE_KEY.format(key), value or {}, MANIFEST_TTL_SECONDS)
    if value:
        cache.set(_LAST_GOOD_KEY.format(key), value, None)
    return value


def manifest() -> dict:
    """CURRENT.json: ``{"release", "previous", "switched_at", ...}``, or {}."""
    return _cached_json(MANIFEST_KEY) or {}


def active_release() -> str:
    """The release labs reads now: the manifest's, else the floor."""
    return manifest().get("release") or OVERTURE_RELEASE


def ready(release: str | None = None) -> dict | None:
    """A snapshot's READY.json (which tiles exist, and the checks it passed), or None."""
    release = release or active_release()
    return _cached_json(f"{snapshot_prefix(release)}/READY.json")


def forget_cached_manifest(release: str | None = None) -> None:
    """Drop the cached manifest (and a release's READY) so the next read asks S3."""
    keys = [MANIFEST_KEY] + ([f"{snapshot_prefix(release)}/READY.json"] if release else [])
    for key in keys:
        cache.delete(_CACHE_KEY.format(key))


# ---------------------------------------------------------------------------
# Paths


def snapshot_prefix(release: str) -> str:
    """Key prefix (no bucket) of one release's snapshot."""
    return f"{OVERTURE_PREFIX}/snapshots/{release}"


def snapshot_base(release: str | None = None) -> str:
    return f"s3://{EXTRACT_BUCKET}/{snapshot_prefix(release or active_release())}"


def theme_path(theme: str, type_: str, release: str | None = None) -> str:
    """S3 glob for an Overture theme/type in labs' raw snapshot.

    ``release`` defaults to the active one; the refresh names the release it is
    building so it can read a snapshot before labs switches to it.
    """
    return f"{snapshot_base(release)}/raw/theme={theme}/type={type_}/*"


def source_path(theme: str, type_: str, release: str) -> str:
    """The same glob in Overture's own bucket. Only the refresh reads this."""
    return f"s3://{SOURCE_BUCKET}/release/{release}/theme={theme}/type={type_}/*"


def grid_prefix(kind: str, release: str | None = None) -> str:
    """The continental grid of one kind: ``<prefix>/tx=<lon>/ty=<lat>/*.parquet``."""
    return f"{snapshot_base(release)}/tiles/{kind}/grid"


def cell_glob(kind: str, tx: int, ty: int, release: str | None = None) -> str:
    return f"{grid_prefix(kind, release)}/tx={tx}/ty={ty}/*.parquet"


def block_of(tx: int) -> int:
    """The strip a grid column is cut in: its west edge, a multiple of BLOCK_DEGREES."""
    return (tx // BLOCK_DEGREES) * BLOCK_DEGREES


def cells_touching(bounds: tuple[float, float, float, float]) -> list[tuple[int, int]]:
    """Every 1-degree cell a bbox touches: the cells a read has to open."""
    import math

    minx, miny, maxx, maxy = bounds
    return [
        (tx, ty)
        for tx in range(math.floor(minx), math.floor(maxx) + 1)
        for ty in range(math.floor(miny), math.floor(maxy) + 1)
    ]


def cell_files(con, kind: str, cells, release: str | None = None) -> list[str]:
    """The Parquet files the grid holds for these cells.

    Listed rather than globbed into ``read_parquet``: a covered cell can hold
    nothing (open sea, empty desert), and a glob that matches no file is an
    error, where an empty cell is a true answer of "nothing here".
    """
    files = []
    for tx, ty in cells:
        files += [
            row[0] for row in con.execute("SELECT file FROM glob(?)", [cell_glob(kind, tx, ty, release)]).fetchall()
        ]
    return files


def tile_cells(
    bounds: tuple[float, float, float, float],
    kind: str,
    release: str | None = None,
    filters: dict | None = None,
) -> list[tuple[int, int]] | None:
    """The grid cells a read of ``kind`` over ``bounds`` opens, or None to read raw.

    Tiles are used only when every cell the bbox touches is in the snapshot's
    plan: an area reaching past the coverage would otherwise get a PARTIAL
    answer. And only for a kind READY.json lists, which it does once every strip
    is cut, so a snapshot mid-cut (or with no READY at all) reads raw.

    ``filters`` is what the reader would select (e.g. the road classes). Tiles
    cut under a different selection are not used: changing ``ROAD_CLASSES``
    must not keep serving tiles that left the new classes out.
    """
    grid = (ready(release) or {}).get("grid") or {}
    spec = (grid.get("kinds") or {}).get(kind)
    if spec is None or (filters is not None and spec.get("filters") != filters):
        return None
    cells = cells_touching(bounds)
    covered = {tuple(cell) for cell in grid.get("cells") or ()}
    return cells if all(cell in covered for cell in cells) else None


def available_releases(con=None) -> list[str]:
    """Release ids currently published in Overture's bucket, newest first."""
    own = con is None
    con = con or connect()
    try:
        rows = con.execute(
            f"SELECT DISTINCT regexp_extract(file, 'release/([^/]+)/', 1) AS r "
            f"FROM glob('s3://{SOURCE_BUCKET}/release/*/theme=buildings/type=building/*.parquet') "
            "ORDER BY r DESC"
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    finally:
        if own:
            con.close()


# ---------------------------------------------------------------------------
# DuckDB


def connect():
    """Return a DuckDB connection with spatial + httpfs loaded and a writable home.

    Labs' bucket (snapshots) is read with the AWS credential chain in us-east-1;
    Overture's public bucket, which only the refresh reads, needs no credentials
    and lives in us-west-2.
    """
    import duckdb

    ext_dir = os.path.join(tempfile.gettempdir(), "duckdb_ext")
    os.makedirs(ext_dir, exist_ok=True)
    con = duckdb.connect()
    con.execute(f"SET home_directory='{tempfile.gettempdir()}';")
    con.execute(f"SET extension_directory='{ext_dir}';")
    con.execute("INSTALL spatial; LOAD spatial; INSTALL httpfs; LOAD httpfs;")
    con.execute("SET s3_region='us-west-2';")
    # Scoped so only reads of labs' bucket use these credentials + region.
    # Best-effort: DuckDB validates a credential_chain secret at CREATE time, so on
    # a machine with no AWS credential this raises; the connection must survive it
    # (a test, or a laptop reading only Overture's public bucket).
    try:
        con.execute(
            "CREATE OR REPLACE SECRET labs_overture "
            "(TYPE s3, PROVIDER credential_chain, REGION 'us-east-1', "
            f"SCOPE 's3://{EXTRACT_BUCKET}');"
        )
    except Exception as exc:  # noqa: BLE001 — any credential failure degrades the same way
        logger.info("overture: labs bucket credentials unavailable (%s)", exc)
    # A raw read globs a planet-scale theme (~500 Parquet files) and prunes row
    # groups by the bbox column statistics: work dominated by reading each file's
    # footer over S3, not CPU. The labs worker has 1 vCPU, so DuckDB would default
    # to one thread and read those footers one after another; because the reads
    # are I/O-bound, more threads overlap the waits even on one core (measured
    # cross-region: ~230s at threads=1, ~20s at 8). The metadata cache lets a
    # second query on the same connection reuse footers already read.
    con.execute("SET threads TO 8;")
    con.execute("SET enable_http_metadata_cache=true;")
    return con
