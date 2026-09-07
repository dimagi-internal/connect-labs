"""Shared Overture Maps access via DuckDB over the public S3 Parquet.

Both building footprints (footprints.py) and admin boundaries (boundaries.py)
query Overture the same way: DuckDB with the spatial + httpfs extensions,
pointed at a writable extension dir (the labs container runs with
HOME=/nonexistent, so DuckDB can't use the default ~/.duckdb).
"""

from __future__ import annotations

import logging
import os
import tempfile

# Overture release. Bump as Overture cuts monthly releases; the S3 layout is stable.
#
# Overture PRUNES old releases from the bucket — they are not kept forever. A pin
# left behind long enough stops resolving, and the failure is not obvious: the
# extract path keeps working for a country that has one (Nigeria), while every
# other country's live read dies on `No files found that match the pattern`,
# which reads like a query bug rather than an expired pin. `verify_release()`
# turns that into a sentence naming what is actually available.
OVERTURE_RELEASE = "2026-08-19.0"
_S3_BASE = f"s3://overturemaps-us-west-2/release/{OVERTURE_RELEASE}"


logger = logging.getLogger(__name__)


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
# Same-region country extracts. A country listed here whose ``release`` matches
# OVERTURE_RELEASE is read from our own us-east-1 copy (sub-second) instead of
# the cross-region public bucket (~6 min cold on the worker).
#
# The release must match to be used, so bumping OVERTURE_RELEASE without
# re-extracting degrades to the live read rather than serving stale buildings —
# safe, but it silently gives up the speedup. Nigeria is on 2026-05-20.0 and so
# is currently NOT being used; re-extract it onto the current release to restore
# the fast path (see the module docstring in the extract runbook).
# Every country we hold admin boundaries for, so a planner can never pick an area
# that lands on the slow cross-region live read. bbox values are the Extent() of
# OUR OWN AdminBoundary geometry per iso_code, padded 0.1deg for float noise —
# derived rather than copied off a map, so an extract covers exactly what is
# pickable and nothing more.
#
# ``release`` is the Overture release the extract was CUT from:
#   a matching release  -> read our us-east-1 copy (sub-second)
#   a different release -> STALE; bypassed, and warned about on every fetch
#   None                -> declared but never cut yet; bypassed, NOT warned
# The None case is the difference between "someone needs to re-cut this" and
# "we have not got to this country yet", which are not the same problem.
EXTRACT_REGIONS: dict[str, dict] = {
    "nigeria": {"release": "2026-08-19.0", "bbox": (2.6, 4.2, 14.7, 13.9)},
    # The next five hold, with Nigeria, 91% of every boundary row we serve.
    "liberia": {"release": "2026-08-19.0", "bbox": (-11.6, 4.3, -7.3, 8.7)},
    "drc": {"release": "2026-08-19.0", "bbox": (12.1, -13.6, 31.4, 5.5)},
    "kenya": {"release": "2026-08-19.0", "bbox": (33.8, -4.8, 42.0, 5.5)},
    "zambia": {"release": "2026-08-19.0", "bbox": (21.9, -18.2, 33.8, -8.2)},
    "ethiopia": {"release": "2026-08-19.0", "bbox": (32.9, 3.3, 48.1, 15.0)},
    # The rest of the countries with a configured boundary source.
    "tanzania": {"release": "2026-08-19.0", "bbox": (29.2, -11.9, 40.5, -0.9)},
    "cote_divoire": {"release": "2026-08-19.0", "bbox": (-8.7, 4.2, -2.4, 10.8)},
    "mozambique": {"release": "2026-08-19.0", "bbox": (30.1, -27.0, 40.9, -10.4)},
    "malawi": {"release": "2026-08-19.0", "bbox": (32.6, -17.2, 36.0, -9.3)},
    "sierra_leone": {"release": "2026-08-19.0", "bbox": (-13.4, 6.8, -10.2, 10.1)},
}


def available_releases(con=None) -> list[str]:
    """Release ids currently present in the public Overture bucket, newest first."""
    own = con is None
    con = con or connect()
    try:
        rows = con.execute(
            "SELECT DISTINCT regexp_extract(file, 'release/([^/]+)/', 1) AS r "
            "FROM glob('s3://overturemaps-us-west-2/release/*/theme=buildings/type=building/*.parquet') "
            "ORDER BY r DESC"
        ).fetchall()
        return [r[0] for r in rows if r[0]]
    finally:
        if own:
            con.close()


def stale_extracts() -> list[str]:
    """Extract regions cut from a release we are no longer pinned to.

    A stale extract is not an error — the router degrades to the live read, which
    is correct. It is a silent loss of the whole point of the extract (Nigeria:
    ~5s becomes ~350s), so it is worth being able to see rather than rediscover
    from a user complaining that sampling got slow.
    """
    return [
        name
        for name, meta in EXTRACT_REGIONS.items()
        if meta["release"] is not None and meta["release"] != OVERTURE_RELEASE
    ]


def uncut_regions() -> list[str]:
    """Regions declared but never extracted (``release is None``).

    Not an error and deliberately NOT part of ``stale_extracts()``: a country we
    have not cut yet is a backlog item, while a country cut from a release we no
    longer read is a regression someone introduced. Warning about both on every
    fetch would bury the second in the first.
    """
    return [name for name, meta in EXTRACT_REGIONS.items() if meta["release"] is None]


def verify_release_quietly() -> None:
    """The stale-extract half of :func:`verify_release`, with no bucket listing.

    ``verify_release`` also probes the public bucket to name what is available,
    which is a network round-trip and can raise. That is right for a CLI and
    wrong for a hot fetch path, so the cheap local check lives here and gets
    called on every fetch — a warning that never runs is not a warning.
    """
    stale = stale_extracts()
    if stale:
        logger.warning(
            "Overture extracts %s are cut from an older release than the pinned %s, so they are "
            "bypassed for the slow live read (~350s vs ~5s per uncached area). "
            "Re-cut them: manage.py microplans_build_extract %s",
            ", ".join(stale),
            OVERTURE_RELEASE,
            " ".join(stale),
        )


def verify_release(con=None) -> None:
    """Raise if the pinned release is no longer published.

    Cheap enough to call before a long fetch, and the only thing that makes an
    expired pin legible: the raw DuckDB error names a glob, not the cause.
    """
    stale = stale_extracts()
    if stale:
        logger.warning(
            "Overture extracts %s were cut from an older release than the pinned %s, so they "
            "are being bypassed for the slow live read. Re-extract them onto %s.",
            ", ".join(stale),
            OVERTURE_RELEASE,
            OVERTURE_RELEASE,
        )

    releases = available_releases(con)
    if releases and OVERTURE_RELEASE not in releases:
        raise RuntimeError(
            f"Overture release {OVERTURE_RELEASE!r} is no longer published — it has been "
            f"pruned from the bucket. Available: {', '.join(releases[:4])}. "
            "Bump OVERTURE_RELEASE, and re-extract any EXTRACT_REGIONS country onto the "
            "new release or it silently falls back to the slow live read."
        )


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
    # Best-effort: the extract is an optimization, and the public Overture bucket
    # needs no credentials at all. DuckDB validates a credential_chain secret at
    # CREATE time, so on a machine with no default AWS credential this raised and
    # took the whole connection down with it — killing the public read that would
    # have worked. A missing credential now costs the fast path, nothing more.
    try:
        con.execute(
            "CREATE OR REPLACE SECRET labs_extract "
            "(TYPE s3, PROVIDER credential_chain, REGION 'us-east-1', "
            f"SCOPE 's3://{EXTRACT_BUCKET}');"
        )
    except Exception as exc:  # noqa: BLE001 — any credential failure degrades the same way
        logger.info("Overture extract credentials unavailable (%s); using the public read only", exc)
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
