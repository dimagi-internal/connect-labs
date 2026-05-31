"""Celery tasks for microplans map generation.

`generate_frame` / `generate_coverage_frame` / `fetch_buildings` first-fetch an
area from Overture S3 (tens of seconds; subsequent runs hit the PG footprint
cache). Run synchronously inside a request they block a gunicorn gthread worker
for the whole fetch — with ``WEB_CONCURRENCY=3`` (see ``docker/start``) a few
concurrent cold previews exhaust the pool and stall every request, including
auth. These tasks move that work onto the Celery worker (``labs-jj-worker``):
the preview views enqueue and return ``202 {task_id, poll_url}``,
``PreviewStatusView`` reports progress/result, and the front-end polls.

Each task returns the same response envelope the old synchronous view returned —
``{"status": "ok", ...data}`` on success, or ``{"status": "error", "detail": …}``
for an *expected*, user-actionable ``ValueError`` (e.g. "area too large"). An
unexpected exception is allowed to propagate so Celery marks the task FAILURE
and the status view surfaces a generic message without leaking internals.
"""

import logging

from commcare_connect.utils.celery import set_task_progress
from config import celery_app

logger = logging.getLogger(__name__)

_FETCHING = "Fetching building footprints…"


@celery_app.task(bind=True)
def generate_frame_task(self, areas, config_payload):
    """Sampling-mode preview: footprints → PPS-sampled pins + cluster hulls."""
    from commcare_connect.microplans.sampling.frame import FrameConfig, generate_frame

    set_task_progress(self, _FETCHING)
    config = FrameConfig.from_payload(config_payload or {})
    try:
        result = generate_frame(areas, config)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {
        "status": "ok",
        "pins": result.pins_geojson,
        "hulls": result.hulls_geojson,
        "stats": result.stats,
    }


@celery_app.task(bind=True)
def generate_coverage_task(self, areas, config_payload):
    """Coverage-mode preview: footprints → balanced/grid cluster polygons."""
    from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

    set_task_progress(self, _FETCHING)
    config = CoverageConfig.from_payload(config_payload or {})
    try:
        result = generate_coverage_frame(areas, config)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    return {"status": "ok", "areas": result.areas_geojson, "stats": result.stats}


@celery_app.task(bind=True)
def fetch_footprints_task(self, areas):
    """Building footprints (as point features) inside the drawn area(s)."""
    from shapely.ops import unary_union

    from commcare_connect.microplans.core.area_input import resolve_area
    from commcare_connect.microplans.core.footprints import fetch_buildings

    set_task_progress(self, _FETCHING)
    try:
        geom = unary_union([resolve_area(a) for a in areas])
        df = fetch_buildings(geom, min_confidence=None)
    except ValueError as e:
        return {"status": "error", "detail": str(e)}
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
            "properties": {},
        }
        for _, row in df.iterrows()
    ]
    return {
        "status": "ok",
        "footprints": {"type": "FeatureCollection", "features": features},
        "count": len(features),
    }
