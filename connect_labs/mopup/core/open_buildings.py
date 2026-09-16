"""Google Open Buildings, fetched directly from Google's own public dataset —
NOT via Overture's conflation.

Why this exists as a third Step 2 building-data source alongside Overture and
"upload your own": Overture's buildings theme conflates Google Open Buildings
with OSM/Microsoft, and only tags a building `"Google Open Buildings"` when
Google was picked as that building's PRIMARY source during conflation (see
`microplans.core.footprints.SOURCE_GOOGLE`/`_apply_filters`) — a building
Google detected but where OSM/Microsoft won primacy doesn't surface under
Overture mode's own "Google Open Buildings" checkbox. Overture also
republishes on its own release cadence, independent of Google's, with no
visibility into how stale "Google Open Buildings via Overture" is at any
given moment. This module fetches the same v3 Open Buildings dataset
straight from Google's public GCS bucket instead, bypassing conflation and
Overture's release cadence entirely.

Ported from Google's own example notebook
(google-research/building_detection/open_buildings_download_region_polygons.ipynb),
rewritten for direct per-ward use inside this app's existing per-ward Step 2
loop (`mopup.tasks.preview_planning_gaps`) rather than the notebook's
Colab-only, single-region, Drive-upload flow:
  - No TensorFlow — the gs:// bucket is public; a plain `requests` GET
    against its HTTPS mirror (storage.googleapis.com) works with no auth
    (confirmed live).
  - `s2sphere`, not `s2geometry` — same S2 covering math (verified: tokens
    it computes for a real Kano-area bbox matched real files in the
    bucket), but installs via plain pip — no `swig`/C++ toolchain, so no
    Dockerfile.base change needed (this repo's Docker base image doesn't
    have `swig`).
  - Points only (`points_s2_level_6_gzip_no_header`), not polygons — a
    single S2 level-6 shard is large (confirmed live: ~75-115MB compressed
    for real Nigeria-area cells) and this app's map only ever plots
    building POINTS regardless of source (see analysis.js's
    `renderBuildingPoints`) — the same shape "upload your own CSV" mode
    already provides, so there's no map-rendering reason to pay for the
    ~4x larger polygon shards.
  - One ward per call — this app already loops wards one at a time in
    `preview_planning_gaps` (see `work_areas.resolve_ward_boundaries`), so
    there's no multi-ward batching/token-dedup here like the original
    notebook's rewrite had — it would optimize a usage pattern this app
    doesn't have.
  - Caches the WARD-FILTERED result in Postgres (`OpenBuildingsArea`/
    `OpenBuildingsBuilding`, see `mopup.models`), not the raw S2 shard —
    mirrors `microplans.models.FootprintArea`/`FootprintBuilding`'s own
    per-area caching, for the same reason: a raw shard can be ~1M
    rows/100MB+ spanning many wards, but a ward's own buildings are
    usually a few hundred to a few thousand rows.
"""

from __future__ import annotations

import hashlib
import io
import logging
from datetime import timedelta

import pandas as pd
import requests
import s2sphere
from django.db import IntegrityError, transaction
from django.utils import timezone
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry
from shapely.prepared import prep

logger = logging.getLogger(__name__)

BUCKET_BASE = "https://storage.googleapis.com/open-buildings-data/v3"
S2_LEVEL = 6
DATASET_LABEL = "Google Open Buildings (direct)"

# Raw column order in the upstream points shard (no header row in the file).
_RAW_COLUMNS = ["lat", "lon", "area_m2", "confidence", "full_plus_code"]

# A malformed/oversized ward boundary could otherwise trigger dozens of
# ~100MB shard downloads — same spirit as footprints.py's MAX_AREA_KM2 guard,
# just expressed in shard count since S2 cell size varies by latitude. Not a
# throttling concern (GCS won't rate-limit a handful of sequential GETs) —
# this is purely a sanity check on the input geometry.
MAX_SHARDS_PER_WARD = 12

# Google's dataset updates far less often than Overture's rolling release
# (that gap is the whole reason this module exists — see module docstring),
# so a longer TTL than footprints.py's 7-day Overture cache is appropriate:
# it's the main lever that keeps a repeat Recompute on the same run cheap.
CACHE_MAX_AGE = timedelta(days=30)


def _area_cache_key(ward_boundary_wkt: str) -> str:
    return hashlib.sha256(f"open_buildings_v1|{ward_boundary_wkt}".encode()).hexdigest()


def _s2_covering_tokens(bounds: tuple[float, float, float, float]) -> list[str]:
    """S2 cell tokens (at S2_LEVEL) covering a bounding box. Raises
    `ValueError` if the covering needs more than MAX_SHARDS_PER_WARD shards."""
    minx, miny, maxx, maxy = bounds
    rect = s2sphere.LatLngRect.from_point_pair(
        s2sphere.LatLng.from_degrees(miny, minx),
        s2sphere.LatLng.from_degrees(maxy, maxx),
    )
    coverer = s2sphere.RegionCoverer()
    coverer.min_level = S2_LEVEL
    coverer.max_level = S2_LEVEL
    # Generous relative to the hard cap below, so a genuinely oversized
    # boundary's real shard count is visible in the error rather than
    # silently truncated by the coverer itself.
    coverer.max_cells = MAX_SHARDS_PER_WARD * 4
    tokens = [cell_id.to_token() for cell_id in coverer.get_covering(rect)]
    if len(tokens) > MAX_SHARDS_PER_WARD:
        raise ValueError(
            f"ward boundary spans {len(tokens)} Open Buildings shards (max {MAX_SHARDS_PER_WARD}) — "
            "likely a malformed or oversized boundary."
        )
    return tokens


def _download_shard(token: str, session: requests.Session) -> pd.DataFrame | None:
    """One raw S2 shard (points, unfiltered — caller filters to the actual
    ward). Returns None if the shard doesn't exist (no buildings in that
    cell), which is a normal, expected outcome, not an error."""
    url = f"{BUCKET_BASE}/points_s2_level_{S2_LEVEL}_gzip_no_header/{token}_buildings.csv.gz"
    resp = session.get(url, timeout=120)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return pd.read_csv(io.BytesIO(resp.content), header=None, names=_RAW_COLUMNS, compression="gzip")


def _fetch_and_filter_to_ward(ward_boundary: BaseGeometry) -> pd.DataFrame:
    """Downloads every shard covering `ward_boundary`'s bbox and spatially
    filters to buildings whose centroid actually falls inside the ward
    polygon (a shard covers its whole S2 cell, far bigger than one ward)."""
    tokens = _s2_covering_tokens(ward_boundary.bounds)
    session = requests.Session()
    prepared = prep(ward_boundary)
    matched_frames = []
    for token in tokens:
        shard = _download_shard(token, session)
        if shard is None or shard.empty:
            continue
        inside = [prepared.contains(Point(lon, lat)) for lon, lat in zip(shard["lon"], shard["lat"])]
        matched = shard[pd.Series(inside, index=shard.index)]
        if len(matched):
            matched_frames.append(matched)
    if not matched_frames:
        return pd.DataFrame(columns=_RAW_COLUMNS)
    return pd.concat(matched_frames, ignore_index=True)


def fetch_open_buildings_for_ward(ward_boundary: BaseGeometry) -> pd.DataFrame:
    """Google Open Buildings points whose centroid falls inside
    `ward_boundary`, in the same `lon`/`lat`/`area_m2`/`confidence`/`dataset`
    shape `microplans.core.footprints.fetch_buildings` returns — ready to
    feed straight into `gaps.planning_gap_features(buildings=...)` (the same
    seam "upload your own CSV" mode uses; confidence filtering is applied
    generically downstream by `gaps.buildings_within_ward`, not here).

    Cached per ward (`OpenBuildingsArea`/`OpenBuildingsBuilding`,
    CACHE_MAX_AGE TTL) — a repeat Recompute on the same locked run is a
    Postgres read, not a re-fetch from Google's bucket.

    Raises `ValueError` if the boundary spans more than MAX_SHARDS_PER_WARD
    upstream shards.
    """
    from connect_labs.mopup.models import OpenBuildingsArea, OpenBuildingsBuilding

    area_hash = _area_cache_key(ward_boundary.wkt)
    cached = OpenBuildingsArea.objects.filter(area_hash=area_hash).first()
    if cached is not None:
        age = timezone.now() - cached.created_at
        if age <= CACHE_MAX_AGE:
            cols = ["lon", "lat", "area_m2", "confidence"]
            df = pd.DataFrame(list(cached.buildings.values(*cols)), columns=cols)
            logger.info("open buildings cache hit (%s, %d buildings)", area_hash[:12], len(df))
            df["dataset"] = DATASET_LABEL
            return df
        logger.info("open buildings cache stale (%s, age=%s) — refetching", area_hash[:12], age)
        cached.delete()  # cascades to this ward's OpenBuildingsBuilding rows

    raw = _fetch_and_filter_to_ward(ward_boundary)
    result = raw[["lon", "lat", "area_m2", "confidence"]].copy()

    try:
        with transaction.atomic():
            area = OpenBuildingsArea.objects.create(area_hash=area_hash, n_buildings=len(result))
            OpenBuildingsBuilding.objects.bulk_create(
                [
                    OpenBuildingsBuilding(
                        area=area,
                        lon=r.lon,
                        lat=r.lat,
                        area_m2=(None if pd.isna(r.area_m2) else r.area_m2),
                        confidence=(None if pd.isna(r.confidence) else r.confidence),
                    )
                    for r in result.itertuples(index=False)
                ],
                batch_size=2000,
            )
        logger.info("open buildings fetched + cached: %d buildings (%s)", len(result), area_hash[:12])
    except IntegrityError:
        # Concurrent miss for the same ward beat us to it — read what they wrote.
        logger.info("open buildings concurrent fill (%s); using stored rows", area_hash[:12])
        area = OpenBuildingsArea.objects.get(area_hash=area_hash)
        cols = ["lon", "lat", "area_m2", "confidence"]
        result = pd.DataFrame(list(area.buildings.values(*cols)), columns=cols)

    result["dataset"] = DATASET_LABEL
    return result
