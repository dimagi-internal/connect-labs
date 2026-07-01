"""Dev-only national AdminBoundary seeder for LOCAL scale measurement.

The real Nigeria GeoPoDe boundaries (~37 states / ~774 LGAs / ~9,300 wards) are
loaded in labs via ``load_geopode_from_drive --iso NGA`` (needs the connect-labs-sa
Drive key). For LOCAL cliff measurement that key isn't available, so this seeds a
structurally-equivalent national hierarchy: the real 36 states + FCT, with nested
box polygons for LGAs/wards at true scale. Geometry is simplified (the cliff is
driven by worker COUNT, not polygon detail — the bootstrap serializes workers, not
ward geometry), so this is purely a local stand-in. NEVER run it where the real
GeoPoDe data is loaded.
"""
from __future__ import annotations

from django.contrib.gis.geos import GEOSGeometry

from connect_labs.campaign.services import geography
from connect_labs.labs.admin_boundaries.models import AdminBoundary

# Nigeria's 36 states + FCT.
NIGERIA_STATES = [
    "Abia",
    "Adamawa",
    "Akwa Ibom",
    "Anambra",
    "Bauchi",
    "Bayelsa",
    "Benue",
    "Borno",
    "Cross River",
    "Delta",
    "Ebonyi",
    "Edo",
    "Ekiti",
    "Enugu",
    "Gombe",
    "Imo",
    "Jigawa",
    "Kaduna",
    "Kano",
    "Katsina",
    "Kebbi",
    "Kogi",
    "Kwara",
    "Lagos",
    "Nasarawa",
    "Niger",
    "Ogun",
    "Ondo",
    "Osun",
    "Oyo",
    "Plateau",
    "Rivers",
    "Sokoto",
    "Taraba",
    "Yobe",
    "Zamfara",
    "Federal Capital Territory",
]

# Nigeria bounding box (approx): lon 2.7–14.7, lat 4.3–13.9.
_LON0, _LAT0 = 2.7, 4.3
_COLS = 6  # states laid out on a grid across the bbox


def _box(lon, lat, d):
    poly = GEOSGeometry(
        f"POLYGON(({lon-d} {lat-d}, {lon+d} {lat-d}, {lon+d} {lat+d}, {lon-d} {lat+d}, {lon-d} {lat-d}))",
        srid=4326,
    )
    return GEOSGeometry(f"MULTIPOLYGON({poly.wkt[len('POLYGON'):]})", srid=4326)


def seed_demo_boundaries(*, lgas_per_state=21, wards_per_lga=12, clear=True):
    """Seed a national NGA hierarchy into AdminBoundary (source='geopode')."""
    if clear:
        AdminBoundary.objects.filter(iso_code="NGA", source="geopode").delete()

    rows = []
    rows.append(
        AdminBoundary(
            iso_code="NGA",
            admin_level=0,
            name="Nigeria",
            boundary_id="nga",
            geometry=_box(8.7, 9.1, 6.0),
            source="geopode",
            population=210_000_000,
        )
    )
    for si, sname in enumerate(NIGERIA_STATES):
        slon = _LON0 + (si % _COLS) * 1.9 + 0.9
        slat = _LAT0 + (si // _COLS) * 1.4 + 0.7
        sid = f"nga-s{si:02d}"
        rows.append(
            AdminBoundary(
                iso_code="NGA",
                admin_level=1,
                name=sname,
                boundary_id=sid,
                parent_boundary_id="nga",
                geometry=_box(slon, slat, 0.85),
                source="geopode",
                population=6_000_000,
            )
        )
        for li in range(lgas_per_state):
            llon = slon - 0.7 + (li % 5) * 0.28
            llat = slat - 0.6 + (li // 5) * 0.28
            lid = f"{sid}-l{li:02d}"
            rows.append(
                AdminBoundary(
                    iso_code="NGA",
                    admin_level=2,
                    name=f"{sname} LGA {li + 1}",
                    boundary_id=lid,
                    parent_boundary_id=sid,
                    geometry=_box(llon, llat, 0.12),
                    source="geopode",
                    population=250_000,
                )
            )
            for wi in range(wards_per_lga):
                wlon = llon - 0.09 + (wi % 4) * 0.05
                wlat = llat - 0.09 + (wi // 4) * 0.05
                rows.append(
                    AdminBoundary(
                        iso_code="NGA",
                        admin_level=3,
                        name=f"{sname} LGA {li + 1} Ward {wi + 1}",
                        boundary_id=f"{lid}-w{wi:02d}",
                        parent_boundary_id=lid,
                        geometry=_box(wlon, wlat, 0.02),
                        source="geopode",
                        population=18_000,
                    )
                )
    AdminBoundary.objects.bulk_create(rows, batch_size=2000)
    return {
        "states": len(NIGERIA_STATES),
        "lgas": len(NIGERIA_STATES) * lgas_per_state,
        "wards": len(NIGERIA_STATES) * lgas_per_state * wards_per_lga,
        "loaded": geography.is_loaded(),
    }
