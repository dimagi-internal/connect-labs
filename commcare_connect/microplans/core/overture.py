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
