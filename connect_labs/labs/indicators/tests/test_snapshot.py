"""Tests for the snapshot round trip.

A snapshot exists so a second environment need not re-fetch anything, which
means the failure mode that matters is a *quiet* one: an import that reports
success while dropping values, mangling a polygon, or losing the licence a row
travels under. Everything here aims at that.

The database is wiped between export and import so nothing can pass by virtue of
already being there.
"""

from __future__ import annotations

import io
import json
import zipfile
from unittest import mock

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.utils import timezone

from connect_labs.labs.admin_boundaries.models import AdminBoundary
from connect_labs.labs.indicators import snapshot
from connect_labs.labs.indicators.models import IndicatorValue, License, Source

pytestmark = pytest.mark.django_db


def _square(x: float, y: float) -> MultiPolygon:
    # Deliberately more precision than the export keeps, so quantization is
    # exercised rather than sidestepped.
    return MultiPolygon(
        Polygon(
            (
                (x + 0.123456789, y),
                (x + 1, y),
                (x + 1, y + 1),
                (x, y + 1),
                (x + 0.123456789, y),
            )
        ),
        srid=4326,
    )


def _seed() -> tuple[AdminBoundary, AdminBoundary]:
    ken = AdminBoundary.objects.create(
        iso_code="KEN",
        admin_level=0,
        name="Kenya",
        boundary_id="KEN-ADM0",
        geometry=_square(0, 0),
        source=AdminBoundary.Source.GEOBOUNDARIES,
    )
    turkana = AdminBoundary.objects.create(
        iso_code="KEN",
        admin_level=1,
        name="Turkana",
        boundary_id="KEN-ADM1-turkana",
        geometry=_square(2, 0),
        source=AdminBoundary.Source.GEOBOUNDARIES,
        parent_boundary_id="KEN-ADM0",
        extra={"shape_group": "KEN"},
    )
    IndicatorValue.objects.create(
        indicator="u5mr",
        boundary=turkana,
        iso_code="KEN",
        admin_level=1,
        year=2022,
        value=61.3,
        ci_low=54.0,
        ci_high=70.1,
        source=Source.IGME_SUBNATIONAL,
        source_ref="igme-2022",
        source_url="https://childmortality.org/",
        license_code=License.CC_BY_3_IGO,
        method="subnational_igme",
        retrieved_at=timezone.now(),
        extra={"note": "kept"},
    )
    IndicatorValue.objects.create(
        indicator="births",
        boundary=turkana,
        iso_code="KEN",
        admin_level=1,
        year=2022,
        value=42000.0,
        source=Source.DERIVED,
        source_ref="derived-2022",
        license_code=License.DERIVED,
        method="derived",
        retrieved_at=timezone.now(),
    )
    return ken, turkana


def _wipe() -> None:
    IndicatorValue.objects.all().delete()
    AdminBoundary.objects.all().delete()


class TestRoundTrip:
    def test_import_into_an_empty_database_restores_everything(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()

        result = snapshot.import_snapshot(blob)

        assert result["boundaries"] == 2
        assert result["values"] == 2
        assert result["values_skipped"] == 0
        assert AdminBoundary.objects.count() == 2
        assert IndicatorValue.objects.count() == 2

    def test_provenance_survives_the_round_trip(self):
        """A value without its licence and source URL is not reusable."""
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        snapshot.import_snapshot(blob)

        v = IndicatorValue.objects.get(indicator="u5mr")
        assert v.value == pytest.approx(61.3)
        assert (v.ci_low, v.ci_high) == (pytest.approx(54.0), pytest.approx(70.1))
        assert v.source == Source.IGME_SUBNATIONAL
        assert v.source_ref == "igme-2022"
        assert v.source_url == "https://childmortality.org/"
        assert v.license_code == License.CC_BY_3_IGO
        assert v.method == "subnational_igme"
        assert v.extra == {"note": "kept"}

    def test_null_confidence_bounds_stay_null(self):
        """Empty CSV cells must not become 0.0 — a fabricated bound is worse
        than an absent one."""
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        snapshot.import_snapshot(blob)

        v = IndicatorValue.objects.get(indicator="births")
        assert v.ci_low is None
        assert v.ci_high is None

    def test_geometry_survives_within_the_declared_precision(self):
        _, turkana = _seed()
        before = turkana.geometry
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        snapshot.import_snapshot(blob)

        after = AdminBoundary.objects.get(boundary_id="KEN-ADM1-turkana").geometry
        assert after.geom_type == "MultiPolygon"
        assert after.srid == 4326
        # Quantization is the only permitted change, and it is sub-metre.
        assert after.equals_exact(before, tolerance=1e-6)
        assert after.area == pytest.approx(before.area, rel=1e-6)

    def test_boundary_attributes_and_parentage_survive(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        snapshot.import_snapshot(blob)

        b = AdminBoundary.objects.get(boundary_id="KEN-ADM1-turkana")
        assert b.name == "Turkana"
        assert b.admin_level == 1
        assert b.parent_boundary_id == "KEN-ADM0"
        assert b.extra == {"shape_group": "KEN"}

    def test_values_relink_to_boundaries_by_natural_key(self):
        """Primary keys differ between environments; the link must not."""
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        # Burn the id sequence so the re-created rows cannot land on their old
        # primary keys by luck.
        for n in range(5):
            AdminBoundary.objects.create(
                iso_code="ZWE",
                admin_level=1,
                name=f"filler {n}",
                boundary_id=f"ZWE-filler-{n}",
                geometry=_square(10 + n, 10),
                source=AdminBoundary.Source.GEOBOUNDARIES,
            )
        snapshot.import_snapshot(blob)

        v = IndicatorValue.objects.get(indicator="u5mr")
        assert v.boundary.boundary_id == "KEN-ADM1-turkana"


class TestGeometryValidity:
    """Compression must never cost correctness.

    Quantizing coordinates can merge near-coincident vertices and turn a valid
    polygon into a self-intersecting one. On the real continent this happened to
    5 of 2,350 boundaries — Sudan, Comoros, Benin's Littoral — all intricate
    coastlines with thousands of vertices. Those shapes are not reproducible in
    a fixture, so these tests pin the invariant and the branches of the rule
    rather than that specific data.
    """

    def test_a_valid_geometry_is_still_valid_after_a_round_trip(self):
        _seed()
        for b in AdminBoundary.objects.all():
            assert b.geometry.valid, "fixture is not valid to begin with"

        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        snapshot.import_snapshot(blob)

        for b in AdminBoundary.objects.all():
            assert b.geometry.valid, f"{b.boundary_id} was invalidated by the round trip"

    def test_an_already_invalid_geometry_survives_rather_than_being_dropped(self):
        """The rule has a branch for input that is invalid before we touch it.
        Such a boundary must still export and import — silently losing it would
        be worse than carrying it as-is."""
        bowtie = MultiPolygon(Polygon(((0, 0), (1, 1), (1, 0), (0, 1), (0, 0))), srid=4326)
        AdminBoundary.objects.create(
            iso_code="KEN",
            admin_level=1,
            name="Bowtie",
            boundary_id="KEN-ADM1-bowtie",
            geometry=bowtie,
            source=AdminBoundary.Source.GEOBOUNDARIES,
        )
        assert not AdminBoundary.objects.get(boundary_id="KEN-ADM1-bowtie").geometry.valid

        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        result = snapshot.import_snapshot(blob)

        assert result["boundaries"] == 1
        assert AdminBoundary.objects.filter(boundary_id="KEN-ADM1-bowtie").exists()

    def test_geometry_is_not_silently_dropped_when_quantization_is_brutal(self):
        """Drive the precision hard enough to matter, and the export must still
        produce importable, valid geometry for valid input."""
        _seed()
        original = {b.boundary_id: b.geometry.area for b in AdminBoundary.objects.all()}

        with mock.patch.object(snapshot, "COORD_PRECISION", 1):
            blob = snapshot.export(iso_codes=["KEN"])
        _wipe()
        snapshot.import_snapshot(blob)

        for b in AdminBoundary.objects.all():
            assert b.geometry.valid
            # One decimal place is ~11 km, so the area may move; the shape must
            # still be a usable polygon rather than a collapsed sliver.
            assert b.geometry.area > 0
            assert b.boundary_id in original


class TestIdempotence:
    def test_importing_twice_updates_rather_than_duplicates(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()

        snapshot.import_snapshot(blob)
        snapshot.import_snapshot(blob)

        assert AdminBoundary.objects.count() == 2
        assert IndicatorValue.objects.count() == 2

    def test_import_over_a_stale_value_corrects_it(self):
        _, turkana = _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        IndicatorValue.objects.filter(indicator="u5mr").update(value=999.0)

        snapshot.import_snapshot(blob)

        assert IndicatorValue.objects.get(indicator="u5mr").value == pytest.approx(61.3)


class TestRefusals:
    def test_a_corrupt_member_is_refused_not_imported(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()

        original = zipfile.ZipFile(io.BytesIO(blob))
        tampered = io.BytesIO()
        with zipfile.ZipFile(tampered, "w") as out:
            for name in original.namelist():
                data = original.read(name)
                if name == "values.csv":
                    data = data.replace(b"61.3", b"99.9")
                out.writestr(name, data)

        with pytest.raises(ValueError, match="checksum mismatch"):
            snapshot.import_snapshot(tampered.getvalue())
        assert IndicatorValue.objects.count() == 0

    def test_a_future_schema_is_refused(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        _wipe()

        original = zipfile.ZipFile(io.BytesIO(blob))
        tampered = io.BytesIO()
        with zipfile.ZipFile(tampered, "w") as out:
            for name in original.namelist():
                data = original.read(name)
                if name == "manifest.json":
                    m = json.loads(data)
                    m["schema_version"] = snapshot.SCHEMA_VERSION + 1
                    # Checksums stay valid, so only the version can refuse it.
                    data = json.dumps(m).encode()
                out.writestr(name, data)

        with pytest.raises(ValueError, match="schema"):
            snapshot.import_snapshot(tampered.getvalue())


class TestManifest:
    def test_manifest_declares_licences_and_the_lossy_step(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"])
        m = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("manifest.json"))

        assert m["counts"] == {"boundaries": 2, "values": 2, "indicators": 2}
        assert set(m["licenses"]) == {License.CC_BY_3_IGO, License.DERIVED}
        assert m["coordinate_precision"] == snapshot.COORD_PRECISION
        # Nothing seeded here is non-commercial, and the flag must say so
        # rather than defaulting to a reassuring value.
        assert m["contains_non_commercial"] is False

    def test_non_commercial_data_is_flagged_for_the_person_sharing_it(self):
        _, turkana = _seed()
        IndicatorValue.objects.filter(indicator="births").update(
            license_code=next(iter(sorted(snapshot.NON_COMMERCIAL)))
        )
        blob = snapshot.export(iso_codes=["KEN"])
        m = json.loads(zipfile.ZipFile(io.BytesIO(blob)).read("manifest.json"))

        assert m["contains_non_commercial"] is True


class TestValuesOnly:
    def test_a_values_only_snapshot_skips_rather_than_inventing_boundaries(self):
        """Without geometry, a value whose boundary is absent has nowhere to
        go. Skipping it is the only honest option."""
        _seed()
        blob = snapshot.export(iso_codes=["KEN"], include_geometry=False)
        assert len(blob) < 5_000  # values only: kilobytes, not megabytes
        _wipe()

        result = snapshot.import_snapshot(blob)

        assert result["values"] == 0
        assert result["values_skipped"] == 2
        assert IndicatorValue.objects.count() == 0

    def test_a_values_only_snapshot_loads_against_existing_boundaries(self):
        _seed()
        blob = snapshot.export(iso_codes=["KEN"], include_geometry=False)
        IndicatorValue.objects.all().delete()  # boundaries stay

        result = snapshot.import_snapshot(blob)

        assert result["values"] == 2
        assert result["values_skipped"] == 0


class TestGeometryReadPath:
    """How a geometry is handed to GEOS, which is not a free choice.

    The obvious implementation — a memoryview slice into the concatenated WKB —
    copies nothing and passes every test on a machine with a current GEOS. On
    GEOS 3.11, which is what the container ships, it rejects 34 of the 2,350
    African boundaries outright. Hex is the path that reads the same on both.
    """

    def test_a_geometry_round_trips_through_the_hex_path(self):
        from django.contrib.gis.geos import MultiPolygon, Polygon

        from connect_labs.labs.indicators.snapshot import _geometry_at

        first = MultiPolygon(Polygon(((0, 0), (1, 0), (1, 1), (0, 1), (0, 0))), srid=4326)
        second = MultiPolygon(Polygon(((5, 5), (6, 5), (6, 6), (5, 6), (5, 5))), srid=4326)
        buffer = bytes(first.wkb) + bytes(second.wkb)

        got = _geometry_at(buffer, len(bytes(first.wkb)), len(bytes(second.wkb)))

        assert got.geom_type == "MultiPolygon"
        assert got.srid == 4326
        # The second geometry, not the first — the offset must be honoured.
        assert got.extent == (5.0, 5.0, 6.0, 6.0)

    def test_the_first_geometry_is_reachable_at_offset_zero(self):
        from django.contrib.gis.geos import MultiPolygon, Polygon

        from connect_labs.labs.indicators.snapshot import _geometry_at

        only = MultiPolygon(Polygon(((0, 0), (2, 0), (2, 2), (0, 2), (0, 0))), srid=4326)
        wkb = bytes(only.wkb)

        got = _geometry_at(wkb + b"trailing bytes that must not be read", 0, len(wkb))

        assert got.extent == (0.0, 0.0, 2.0, 2.0)
