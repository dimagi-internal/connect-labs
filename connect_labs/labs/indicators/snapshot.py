"""Snapshot the targeting dataset so it need not be re-fetched per environment.

Rebuilding from source takes 30-45 minutes of API calls — most of it
geoBoundaries downloads — and the WorldPop half has an undocumented daily quota
that a second environment can exhaust for the first. Doing that once per machine
is waste, and the quota makes it worse than waste.

A snapshot is a single ZIP holding both halves:

    manifest.json     counts, checksums, when and from what it was built
    boundaries.csv    2,350 boundary attributes, each pointing into geometry.bin
    geometry.bin      their polygons, concatenated WKB
    values.csv        32,000 indicator values — the rate-limited half

About 50 MB, and it imports in seconds. Values alone are under a megabyte; the
geometry carries the weight, so it gets two treatments the values do not.

It lives in a binary sidecar rather than as hex inside the CSV, because hex
doubles it before the ZIP ever sees it — 60 MB, when first written that way.

And its coordinates are quantized to ``COORD_PRECISION`` decimal places before
export. This is the one lossy step here, so it is declared in the manifest
rather than left to be discovered: six places is about 11 cm at the equator,
against source boundaries digitised at a scale nearer 100 m, and it halves the
compressed size because the discarded mantissa bits were incompressible noise.

A boundary that quantization would render *invalid* is exported unquantized
instead — see ``_boundary_rows``. Losing 11 cm is fine; turning a valid polygon
into a self-intersecting one is not, and it happened to five real ones.

**Why this is safe to redistribute.** Every source is open and permits it —
geoBoundaries and World Bank CC BY 4.0, WorldPop CC BY 4.0, DHS and HAPI open
API, IGME CC BY 3.0 IGO. No IHME, whose agreement forbids exactly this. The
per-row ``license_code`` travels in the snapshot, so a future non-commercial
source cannot be silently laundered through it — check
``manifest["licenses"]`` before sharing one outside Dimagi.

Natural keys, not primary keys: boundaries are identified by
``(source, boundary_id)`` and values reference them the same way, so a snapshot
imports into a database whose sequences are nothing like the exporter's.
"""

from __future__ import annotations

import binascii
import csv
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

from django.contrib.gis.geos import GEOSGeometry
from django.db.models.expressions import RawSQL

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators.africa import ISO_CODES
from connect_labs.labs.indicators.models import NON_COMMERCIAL, IndicatorValue

#: Bump when a column changes so an old snapshot fails loudly rather than
#: importing into the wrong shape.
SCHEMA_VERSION = 1

#: Decimal places kept on exported coordinates. Six is ~11 cm at the equator —
#: far finer than the source data is accurate — and halves the ZIP.
COORD_PRECISION = 6

BOUNDARY_COLUMNS = [
    "source",
    "boundary_id",
    "iso_code",
    "admin_level",
    "name",
    "name_local",
    "parent_boundary_id",
    "population",
    "extra",
    # Offset and length into geometry.bin, rather than the geometry itself.
    "geom_offset",
    "geom_length",
]

VALUE_COLUMNS = [
    "boundary_source",
    "boundary_id",
    "indicator",
    "iso_code",
    "admin_level",
    "year",
    "value",
    "ci_low",
    "ci_high",
    "source",
    "source_ref",
    "source_url",
    "license_code",
    "method",
    "extra",
]


def _csv(rows, columns) -> bytes:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue().encode("utf-8")


def _boundary_rows(iso_codes: list[str] | None):
    """Attribute rows plus the concatenated geometry they index into."""
    qs = AdminBoundary.objects.filter(admin_level__in=(0, 1, 2))
    qs = qs.filter(iso_code__in=iso_codes or ISO_CODES)
    # Quantize in the database rather than in Python: PostGIS zeroes the low
    # mantissa bits in place, and GEOS has no equivalent that keeps the WKB
    # byte-identical to what a re-import will produce.
    # Two things happen in this one expression, and both matter.
    #
    # Quantization can turn a valid polygon invalid: collapsing coordinates
    # merges near-coincident vertices, and on an intricate coastline that
    # creates a self-intersection. Measured on the real continent, 5 of 2,350
    # boundaries did exactly that — Sudan, Comoros, Benin's Littoral. So a
    # boundary keeps its quantized form only if that form is still valid;
    # otherwise it is exported whole, at full size. Compression is never
    # allowed to cost correctness, and the 5 that opt out cost ~2% of the file.
    #
    # Hex, because Django coerces a BinaryField annotation through force_str
    # and chokes on the first non-UTF-8 byte. It is undone below, so nothing
    # reaches the file doubled.
    qs = qs.annotate(
        quantized_wkb_hex=RawSQL(
            """
            encode(
                ST_AsBinary(
                    CASE
                        WHEN ST_IsValid(ST_QuantizeCoordinates(geometry, %s))
                             OR NOT ST_IsValid(geometry)
                        THEN ST_QuantizeCoordinates(geometry, %s)
                        ELSE geometry
                    END
                ),
                'hex'
            )
            """,
            (COORD_PRECISION, COORD_PRECISION),
        )
    )

    geometry = bytearray()
    rows = []
    for b in qs.iterator(chunk_size=200):
        wkb = bytes.fromhex(b.quantized_wkb_hex)
        offset = len(geometry)
        geometry.extend(wkb)
        rows.append(
            {
                "source": b.source,
                "boundary_id": b.boundary_id,
                "iso_code": b.iso_code,
                "admin_level": b.admin_level,
                "name": b.name,
                "name_local": b.name_local,
                "parent_boundary_id": b.parent_boundary_id,
                "population": b.population if b.population is not None else "",
                "extra": json.dumps(b.extra or {}),
                "geom_offset": offset,
                "geom_length": len(wkb),
            }
        )
    return rows, bytes(geometry)


def _value_rows(iso_codes: list[str] | None):
    qs = IndicatorValue.objects.filter(iso_code__in=iso_codes or ISO_CODES)
    for v in qs.select_related("boundary").iterator(chunk_size=2000):
        yield {
            "boundary_source": v.boundary.source,
            "boundary_id": v.boundary.boundary_id,
            "indicator": v.indicator,
            "iso_code": v.iso_code,
            "admin_level": v.admin_level,
            "year": v.year,
            "value": v.value,
            "ci_low": "" if v.ci_low is None else v.ci_low,
            "ci_high": "" if v.ci_high is None else v.ci_high,
            "source": v.source,
            "source_ref": v.source_ref,
            "source_url": v.source_url,
            "license_code": v.license_code,
            "method": v.method,
            "extra": json.dumps(v.extra or {}),
        }


def export(iso_codes: list[str] | None = None, include_geometry: bool = True) -> bytes:
    """Build a snapshot ZIP in memory."""
    values = _csv(_value_rows(iso_codes), VALUE_COLUMNS)
    if include_geometry:
        boundary_rows, geometry = _boundary_rows(iso_codes)
        boundaries = _csv(boundary_rows, BOUNDARY_COLUMNS)
    else:
        boundary_rows, geometry, boundaries = [], b"", b""

    licenses = sorted(
        set(
            IndicatorValue.objects.filter(iso_code__in=iso_codes or ISO_CODES)
            .values_list("license_code", flat=True)
            .distinct()
        )
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).isoformat(),
        "countries": sorted(iso_codes or ISO_CODES),
        "counts": {
            "boundaries": len(boundary_rows),
            "values": IndicatorValue.objects.filter(iso_code__in=iso_codes or ISO_CODES).count(),
            "indicators": IndicatorValue.objects.filter(iso_code__in=iso_codes or ISO_CODES)
            .values("indicator")
            .distinct()
            .count(),
        },
        "licenses": licenses,
        # Loud, because a snapshot is the easiest way to accidentally pass on
        # something that may not be passed on.
        "contains_non_commercial": bool(set(licenses) & set(NON_COMMERCIAL)),
        "includes_geometry": include_geometry,
        "coordinate_precision": COORD_PRECISION if include_geometry else None,
        "sha256": {
            "values.csv": hashlib.sha256(values).hexdigest(),
            "boundaries.csv": hashlib.sha256(boundaries).hexdigest(),
            "geometry.bin": hashlib.sha256(geometry).hexdigest(),
        },
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=2))
        z.writestr("values.csv", values)
        if include_geometry:
            z.writestr("boundaries.csv", boundaries)
            z.writestr("geometry.bin", geometry)
    return buf.getvalue()


def _geometry_at(buffer: bytes, offset: int, length: int) -> GEOSGeometry:
    """Parse one geometry out of the concatenated WKB, via hex.

    Hex looks like the wasteful choice — it doubles the bytes on the way into
    GEOS — and the obvious alternative is a ``memoryview`` slice, which copies
    nothing. That is what this did, and it worked everywhere it was tested and
    failed in the one place it mattered.

    GEOS 3.11 (the deployed container; 3.14 locally) rejects 34 of the 2,350
    African boundaries read as **binary** WKB, with "WKB contains too many
    possible GeometryCollections" — on a header that plainly reads
    ``numGeoms=2``. Its guard against absurd collection counts compares the
    declared count against what the stream reports as available, and over an
    externally-supplied buffer that figure is not the buffer's length. An
    isolated ``bytes`` copy fails identically, so this is not slicing; the same
    bytes as hex parse correctly, and to the right geometry.

    So hex, deliberately. It is the one path that reads the same on both
    versions, the doubling is transient and per-geometry (median 16 KB, not the
    whole 127 MB), and a snapshot that imports on a laptop but not on the server
    is not a snapshot.
    """
    return GEOSGeometry(binascii.hexlify(buffer[offset : offset + length]).decode("ascii"), srid=4326)


def _read_manifest(z: zipfile.ZipFile) -> dict:
    manifest = json.loads(z.read("manifest.json"))
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(
            f"snapshot schema {manifest.get('schema_version')} != expected {SCHEMA_VERSION}; "
            "re-export it from a current checkout"
        )
    for name, expected in manifest.get("sha256", {}).items():
        if name in z.namelist():
            actual = hashlib.sha256(z.read(name)).hexdigest()
            if actual != expected:
                raise ValueError(f"{name} checksum mismatch — snapshot is corrupt")
    return manifest


def import_snapshot(blob: bytes, on_progress=None) -> dict:
    """Load a snapshot. Idempotent: everything upserts on its natural key."""

    def say(msg):
        if on_progress:
            on_progress(msg)

    z = zipfile.ZipFile(io.BytesIO(blob))
    manifest = _read_manifest(z)
    say(f"snapshot built {manifest['created_at']}, schema {manifest['schema_version']}")

    written = {"boundaries": 0, "values": 0, "values_skipped": 0}

    if "boundaries.csv" in z.namelist():
        geometry = z.read("geometry.bin")
        reader = csv.DictReader(io.StringIO(z.read("boundaries.csv").decode("utf-8")))
        for i, row in enumerate(reader, 1):
            AdminBoundary.objects.update_or_create(
                source=row["source"],
                boundary_id=row["boundary_id"],
                defaults={
                    "iso_code": row["iso_code"],
                    "admin_level": int(row["admin_level"]),
                    "name": row["name"],
                    "name_local": row["name_local"],
                    "parent_boundary_id": row["parent_boundary_id"],
                    "population": float(row["population"]) if row["population"] else None,
                    "extra": json.loads(row["extra"] or "{}"),
                    "geometry": _geometry_at(geometry, int(row["geom_offset"]), int(row["geom_length"])),
                },
            )
            written["boundaries"] += 1
            if i % 500 == 0:
                say(f"  boundaries {i}")

    # Boundaries resolved once by natural key rather than per value.
    lookup = {(b.source, b.boundary_id): b.pk for b in AdminBoundary.objects.only("id", "source", "boundary_id")}

    reader = csv.DictReader(io.StringIO(z.read("values.csv").decode("utf-8")))
    for i, row in enumerate(reader, 1):
        pk = lookup.get((row["boundary_source"], row["boundary_id"]))
        if pk is None:
            # A value whose boundary is absent — a geometry-less snapshot
            # imported into a database that does not hold that boundary.
            written["values_skipped"] += 1
            continue
        IndicatorValue.objects.update_or_create(
            indicator=row["indicator"],
            boundary_id=pk,
            year=int(row["year"]),
            source=row["source"],
            defaults={
                "iso_code": row["iso_code"],
                "admin_level": int(row["admin_level"]),
                "value": float(row["value"]),
                "ci_low": float(row["ci_low"]) if row["ci_low"] else None,
                "ci_high": float(row["ci_high"]) if row["ci_high"] else None,
                "source_ref": row["source_ref"],
                "source_url": row["source_url"],
                "license_code": row["license_code"],
                "method": row["method"],
                "retrieved_at": manifest["created_at"],
                "extra": json.loads(row["extra"] or "{}"),
            },
        )
        written["values"] += 1
        if i % 5000 == 0:
            say(f"  values {i}")

    return {"manifest": manifest, **written}
