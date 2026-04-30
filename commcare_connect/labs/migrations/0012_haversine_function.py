"""Add a `haversine_meters(lat1, lon1, lat2, lon2)` Postgres function.

Foundation for the GPS pipeline aggregations that compute distance between
sequential visits to the same beneficiary (per-mother revisit distance) and
between consecutive visits within a worker's day (daily-travel chain).

The function returns NULL on any NULL input — required because GPS data is
sparse (visits often have no location). Marked IMMUTABLE so Postgres can
inline it inside SELECT-list window expressions like:

    haversine_meters(
        LAG(lat) OVER w, LAG(lon) OVER w,
        lat, lon
    )

The MBW v1 implementation lives in templates/mbw_monitoring/gps_utils.py
(Python). v3 will use this SQL function so the math runs once per visit
in the database rather than over re-serialized JSON in the worker.

A Python mirror exists at runners.haversine_meters for the parity harness.
"""

from django.db import migrations

CREATE_HAVERSINE_FUNCTION = """
CREATE OR REPLACE FUNCTION haversine_meters(
    lat1 DOUBLE PRECISION,
    lon1 DOUBLE PRECISION,
    lat2 DOUBLE PRECISION,
    lon2 DOUBLE PRECISION
) RETURNS DOUBLE PRECISION AS $$
DECLARE
    -- Earth radius in meters. Same constant the v1 Python helper uses
    -- (templates/mbw_monitoring/gps_utils.py:haversine_distance) so v1
    -- and v3 produce numerically identical distances modulo float epsilon.
    R DOUBLE PRECISION := 6371000;
    phi1 DOUBLE PRECISION;
    phi2 DOUBLE PRECISION;
    dphi DOUBLE PRECISION;
    dlam DOUBLE PRECISION;
    a DOUBLE PRECISION;
    c DOUBLE PRECISION;
BEGIN
    -- Sparse-GPS-friendly: any missing coordinate yields NULL distance.
    -- Callers (LAG-based window queries) treat NULL as "no previous visit
    -- to compare against" rather than zero, which would otherwise corrupt
    -- the median and flag-rate aggregations downstream.
    IF lat1 IS NULL OR lon1 IS NULL OR lat2 IS NULL OR lon2 IS NULL THEN
        RETURN NULL;
    END IF;

    phi1 := radians(lat1);
    phi2 := radians(lat2);
    dphi := phi2 - phi1;
    dlam := radians(lon2 - lon1);
    a := sin(dphi / 2) ^ 2 + cos(phi1) * cos(phi2) * sin(dlam / 2) ^ 2;
    c := 2 * atan2(sqrt(a), sqrt(1 - a));
    RETURN R * c;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
"""

DROP_HAVERSINE_FUNCTION = """
DROP FUNCTION IF EXISTS haversine_meters(
    DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION, DOUBLE PRECISION
);
"""


class Migration(migrations.Migration):

    dependencies = [
        ("labs", "0011_cache_unique_constraints"),
    ]

    operations = [
        migrations.RunSQL(
            sql=CREATE_HAVERSINE_FUNCTION,
            reverse_sql=DROP_HAVERSINE_FUNCTION,
        ),
    ]
