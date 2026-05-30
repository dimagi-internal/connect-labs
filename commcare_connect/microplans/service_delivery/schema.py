"""The default service-delivery GPS pipeline schema.

Works for *any* CommCare app: every form submission carries a device GPS reading
at ``form_json.metadata.location`` (packed "lat lon altitude accuracy"), which the
analysis layer also exposes as the ``location`` base column. We extract it into
``latitude`` / ``longitude`` float columns via the existing gps_lat / gps_lon
transforms. Terminal stage is visit-level so each row is one geolocated visit.

Callers may pass a different ``pipeline_id`` to override this default (e.g. a
pipeline that filters by deliver_unit or status); see points.fetch_points.
"""

from __future__ import annotations

# Multi-path coalesce mirrors mbw_monitoring_v3: the GPS string lives in the
# element text (#text) on some forms and directly as a string on others.
SERVICE_DELIVERY_GPS_SCHEMA: dict = {
    "name": "Service Delivery GPS",
    "description": "One geolocated row per service-delivery visit (any app).",
    "terminal_stage": "visit_level",
    "fields": [
        {
            "name": "latitude",
            "paths": ["metadata.location", "form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lat",
        },
        {
            "name": "longitude",
            "paths": ["metadata.location", "form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lon",
        },
    ],
}
