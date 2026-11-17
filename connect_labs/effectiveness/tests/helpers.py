"""Boundaries and indicator values for the tests that need a place: the same few lines everywhere they are wanted."""

from django.contrib.gis.geos import MultiPolygon, Polygon
from django.utils import timezone

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import policy
from connect_labs.labs.indicators.models import IndicatorValue, License


def square(x0, y0, size=1.0):
    return MultiPolygon(
        Polygon(((x0, y0), (x0 + size, y0), (x0 + size, y0 + size), (x0, y0 + size), (x0, y0))), srid=4326
    )


def boundary(level, name, bid, x0, y0, size=1.0, iso="NGA"):
    return AdminBoundary.objects.create(
        iso_code=iso,
        admin_level=level,
        name=name,
        boundary_id=bid,
        geometry=square(x0, y0, size),
        source=AdminBoundary.Source.GEOBOUNDARIES,
    )


def value(boundary, indicator, value, year=2023, source=None, ci=None, extra=None):
    source = source or policy.sources(indicator)[0]
    return IndicatorValue.objects.create(
        indicator=indicator,
        boundary=boundary,
        iso_code=boundary.iso_code,
        admin_level=boundary.admin_level,
        year=year,
        value=value,
        source=source,
        source_ref=f"{source}-{year}",
        license_code=License.OPEN_API,
        retrieved_at=timezone.now(),
        ci_low=ci[0] if ci else None,
        ci_high=ci[1] if ci else None,
        extra=extra or {},
    )
