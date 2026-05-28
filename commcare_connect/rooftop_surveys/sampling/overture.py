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
    return con
