"""Tests for the admin-boundary resolver, ISO helpers, and boundary endpoints.

Overture is mocked (no S3); the labs source runs against the real PostGIS test
DB so the spatial narrowing path is actually exercised.
"""

from __future__ import annotations

import json

import pytest

from commcare_connect.microplans.core import admin_boundaries as ab
from commcare_connect.microplans.core import iso
from commcare_connect.microplans.core.admin_boundaries import (
    AdminArea,
    BoundaryResolver,
    BoundarySource,
    LabsAdminBoundarySource,
    OvertureBoundarySource,
)


class TestIso:
    def test_roundtrip(self):
        assert iso.to_alpha2("NGA") == "NG"
        assert iso.to_alpha3("NG") == "NGA"
        assert iso.to_alpha2("KE") == "KE"  # already alpha-2
        assert iso.to_alpha3("KEN") == "KEN"
        assert iso.country_name("NGA") == "Nigeria"

    def test_unknown_is_none(self):
        assert iso.to_alpha2("ZZ") is None
        assert iso.to_alpha3("ZZZ") is None
        assert iso.country_name("nope") is None

    def test_all_countries_sorted_by_name(self):
        countries = iso.all_countries()
        names = [c["name"] for c in countries]
        assert names == sorted(names)
        assert {"alpha2", "alpha3", "name"} <= set(countries[0])
        assert any(c["alpha3"] == "NGA" for c in countries)


class _FakeSource(BoundarySource):
    def __init__(self, name, covered_levels):
        self.name = name
        self._covered = set(covered_levels)
        self.calls = []

    def covers(self, country3, level):
        return level in self._covered

    def list_areas(self, country3, level, *, name_contains=None, parent=None, parent_geom=None, limit=500):
        self.calls.append({"parent": parent, "parent_geom": parent_geom, "q": name_contains})
        return [AdminArea(name=f"{self.name}-{level}", level=level, source=self.name, country=country3)]

    def get_geometry(self, area):
        return {"type": "Point", "coordinates": [0, 0]}


class TestResolverSelection:
    def test_prefers_first_covering_source(self):
        labs = _FakeSource("labs", [1, 2])  # no level 3
        ovr = _FakeSource("overture", [1, 2, 3])
        r = BoundaryResolver(sources=[labs, ovr])
        # labs covers 1 & 2 -> labs; level 3 falls back to overture
        assert r.source_for("NGA", 2).name == "labs"
        assert r.source_for("NGA", 3).name == "overture"

    def test_cold_start_bbox_defaults_to_labs_not_overture(self):
        # No iso yet (cold viewport): default to labs (intersects by geometry, no iso
        # needed) so boundaries load + the country auto-detects. Overture needs an iso
        # up front and would return nothing, stranding the detect (chicken-and-egg).
        labs = _FakeSource("labs", [1, 2, 3])
        ovr = _FakeSource("overture", [1, 2, 3])
        r = BoundaryResolver(sources=[labs, ovr])
        assert r.bbox_source_name(None, None) == "labs"
        # When labs isn't configured, fall back to overture (no regression).
        assert BoundaryResolver(sources=[ovr]).bbox_source_name(None, None) == "overture"

    def test_country_override_order(self):
        labs = _FakeSource("labs", [1, 2, 3])
        ovr = _FakeSource("overture", [1, 2, 3])
        # Force overture for NGA even though labs covers it.
        r = BoundaryResolver(sources=[labs, ovr], country_order={"NGA": ("overture", "labs")})
        assert r.source_for("NGA", 2).name == "overture"
        assert r.source_for("KEN", 2).name == "labs"  # default order elsewhere

    def test_describe_reports_default_and_available_per_level(self):
        labs = _FakeSource("labs", [1, 2])
        ovr = _FakeSource("overture", [1, 2, 3])
        r = BoundaryResolver(sources=[labs, ovr])
        d = r.describe("NG")  # accepts alpha-2 too
        assert d["country"] == "NGA"
        assert d["levels"][2]["default_source"] == "labs"
        assert d["levels"][2]["available_sources"] == ["labs", "overture"]
        assert d["levels"][3]["default_source"] == "overture"
        assert d["levels"][3]["available_sources"] == ["overture"]  # labs has no level 3

    def test_sources_for_lists_only_covering(self):
        labs = _FakeSource("labs", [1])
        ovr = _FakeSource("overture", [1, 2, 3])
        r = BoundaryResolver(sources=[labs, ovr])
        assert r.sources_for("NGA", 1) == ["labs", "overture"]
        assert r.sources_for("NGA", 2) == ["overture"]

    def test_user_can_pick_a_source(self):
        labs = _FakeSource("labs", [1, 2])
        ovr = _FakeSource("overture", [1, 2])
        r = BoundaryResolver(sources=[labs, ovr])
        # default is labs (preference order), but the user can force overture
        assert r.source_for("NGA", 2).name == "labs"
        assert r.source_for("NGA", 2, prefer="overture").name == "overture"
        # an un-covering pick falls back to the default
        labs_only = BoundaryResolver(sources=[_FakeSource("labs", [1]), _FakeSource("overture", [1, 2])])
        assert labs_only.source_for("NGA", 2, prefer="labs").name == "overture"

    def test_same_source_narrows_without_spatial_roundtrip(self):
        ovr = _FakeSource("overture", [1, 2])
        r = BoundaryResolver(sources=[ovr])
        parent = AdminArea(name="Borno", level=1, source="overture", country="NGA", region="NG-BO")
        r.list_areas("NGA", 2, parent=parent)
        # parent is passed through; no geometry fetch for same-source narrowing
        assert ovr.calls[-1]["parent"] is parent
        assert ovr.calls[-1]["parent_geom"] is None

    def test_cross_source_narrowing_passes_parent_geom(self):
        labs = _FakeSource("labs", [2])
        ovr = _FakeSource("overture", [1])
        r = BoundaryResolver(sources=[labs, ovr])
        parent = AdminArea(name="Borno", level=1, source="overture", country="NGA")
        r.list_areas("NGA", 2, parent=parent)  # child=labs, parent=overture -> spatial
        assert labs.calls[-1]["parent_geom"] is not None


class TestOvertureSource:
    def test_level_to_subtype_and_iso(self, monkeypatch):
        captured = {}

        def fake_list(a2, subtype=None, region=None, name_contains=None, limit=500):
            captured.update(a2=a2, subtype=subtype, region=region)
            return [{"name": "Jere", "subtype": "county", "region": "NG-BO", "area_km2": 12.3}]

        monkeypatch.setattr(ab.boundaries, "list_admin_areas", fake_list)
        src = OvertureBoundarySource()
        areas = src.list_areas("NGA", ab.LEVEL_COUNTY)
        assert captured == {"a2": "NG", "subtype": "county", "region": None}
        assert len(areas) == 1
        a = areas[0]
        assert a.name == "Jere" and a.level == ab.LEVEL_COUNTY and a.source == "overture"
        assert a.ref["alpha2"] == "NG" and a.ref["subtype"] == "county"

    def test_get_geometry_passes_keys(self, monkeypatch):
        seen = {}

        def fake_geom(a2, name, subtype, region=None):
            seen.update(a2=a2, name=name, subtype=subtype, region=region)
            return {"type": "Polygon", "coordinates": []}

        monkeypatch.setattr(ab.boundaries, "get_admin_area_geojson", fake_geom)
        src = OvertureBoundarySource()
        area = AdminArea(
            name="Jere",
            level=2,
            source="overture",
            country="NGA",
            ref={"alpha2": "NG", "subtype": "county", "region": "NG-BO"},
        )
        assert src.get_geometry(area)["type"] == "Polygon"
        assert seen == {"a2": "NG", "name": "Jere", "subtype": "county", "region": "NG-BO"}


# ---- labs source: real PostGIS ----

_SQUARE = "POLYGON((13.0 11.0, 13.2 11.0, 13.2 11.2, 13.0 11.2, 13.0 11.0))"
_INSIDE = "POLYGON((13.05 11.05, 13.06 11.05, 13.06 11.06, 13.05 11.06, 13.05 11.05))"
_OUTSIDE = "POLYGON((20.0 20.0, 20.1 20.0, 20.1 20.1, 20.0 20.1, 20.0 20.0))"


def _make_boundary(name, level, wkt, bid, *, parent_boundary_id="", population=None, source="grid3"):
    from django.contrib.gis.geos import GEOSGeometry, MultiPolygon

    from commcare_connect.labs.admin_boundaries.models import AdminBoundary

    geom = GEOSGeometry(wkt, srid=4326)
    return AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=level,
        name=name,
        boundary_id=bid,
        geometry=MultiPolygon(geom, srid=4326),
        source=source,
        parent_boundary_id=parent_boundary_id,
        population=population,
    )


@pytest.mark.django_db
class TestLabsSource:
    def test_covers_and_list_and_geometry(self):
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        src = LabsAdminBoundarySource()
        assert src.covers("NGA", 1) is True
        assert src.covers("NG", 1) is True  # alpha-2 normalised
        assert src.covers("NGA", 3) is False
        areas = src.list_areas("NGA", 1)
        assert [a.name for a in areas] == ["Borno"]
        assert areas[0].ref["boundary_id"] == "ng-bo"
        geom = src.get_geometry(areas[0])
        assert geom["type"] in ("Polygon", "MultiPolygon")

    def test_name_contains_filter(self):
        _make_boundary("Maiduguri", 2, _INSIDE, "ng-bo-mai")
        _make_boundary("Jere", 2, _OUTSIDE, "ng-bo-jere")
        src = LabsAdminBoundarySource()
        areas = src.list_areas("NGA", 2, name_contains="jere")
        assert [a.name for a in areas] == ["Jere"]

    def test_spatial_narrowing_by_parent_geom(self):
        from django.contrib.gis.geos import GEOSGeometry

        _make_boundary("Inside-LGA", 2, _INSIDE, "in")
        _make_boundary("Outside-LGA", 2, _OUTSIDE, "out")
        src = LabsAdminBoundarySource()
        parent = GEOSGeometry(_SQUARE, srid=4326)
        areas = src.list_areas("NGA", 2, parent_geom=parent)
        assert [a.name for a in areas] == ["Inside-LGA"]

    def test_parent_boundary_id_narrowing_and_population(self):
        # Two LGAs; only one is a child of the chosen state by parent_boundary_id.
        _make_boundary("Borno", 1, _SQUARE, "ng-bo", population=5_000_000)
        _make_boundary("Jere", 2, _INSIDE, "ng-bo-jere", parent_boundary_id="ng-bo", population=120_000)
        _make_boundary("Other", 2, _OUTSIDE, "ng-ot-other", parent_boundary_id="ng-ot")
        src = LabsAdminBoundarySource()
        parent = AdminArea(name="Borno", level=1, source="labs", country="NGA", ref={"boundary_id": "ng-bo"})
        areas = src.list_areas("NGA", 2, parent=parent)
        assert [a.name for a in areas] == ["Jere"]  # exact, indexed — no spatial work
        assert areas[0].population == 120_000


@pytest.mark.django_db
class TestResolverIntegration:
    def test_labs_preferred_when_present_else_overture(self, monkeypatch):
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")  # labs has level 1 only

        def fake_list(a2, subtype=None, region=None, name_contains=None, limit=500):
            return [{"name": "OvertureWard", "subtype": "locality", "region": "NG-BO", "area_km2": 1.0}]

        monkeypatch.setattr(ab.boundaries, "list_admin_areas", fake_list)
        r = BoundaryResolver()
        assert r.source_for("NGA", 1).name == "labs"
        assert r.source_for("NGA", 3).name == "overture"  # labs has no level 3
        assert r.list_areas("NGA", 1)[0].source == "labs"
        assert r.list_areas("NGA", 3)[0].name == "OvertureWard"


@pytest.mark.django_db
class TestBoundaryEndpoints:
    def _login(self, client, django_user_model):
        import time

        user = django_user_model.objects.create(username="b", email="b@example.com")
        client.force_login(user)
        s = client.session
        s["labs_oauth"] = {"access_token": "t", "expires_at": time.time() + 3600}
        s.save()

    def test_countries_flags_bespoke(self, client, django_user_model):
        from django.urls import reverse

        # Two levels of the same country: bespoke must report NGA exactly once
        # (regression — Meta.ordering used to defeat .distinct(), echoing every row).
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        _make_boundary("Jere", 2, _INSIDE, "ng-bo-jere", parent_boundary_id="ng-bo")
        self._login(client, django_user_model)
        resp = client.get(reverse("microplans:countries"))
        data = resp.json()
        assert data["status"] == "ok"
        assert data["bespoke"] == ["NGA"]  # de-duplicated
        assert any(c["alpha3"] == "NGA" for c in data["countries"])

    def test_areas_endpoint_uses_labs(self, client, django_user_model):
        from django.urls import reverse

        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        self._login(client, django_user_model)
        resp = client.post(
            reverse("microplans:admin_areas", args=[1]),
            data=json.dumps({"country": "NGA", "level": 1}),
            content_type="application/json",
        )
        data = resp.json()
        assert data["status"] == "ok" and data["source"] == "labs"
        assert [a["name"] for a in data["areas"]] == ["Borno"]

    def test_geometry_endpoint(self, client, django_user_model):
        from django.urls import reverse

        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        self._login(client, django_user_model)
        area = {"name": "Borno", "level": 1, "source": "labs", "country": "NGA", "ref": {"boundary_id": "ng-bo"}}
        resp = client.post(
            reverse("microplans:admin_area_geometry", args=[1]),
            data=json.dumps({"area": area}),
            content_type="application/json",
        )
        data = resp.json()
        assert data["status"] == "ok"
        assert data["geometry"]["type"] in ("Polygon", "MultiPolygon")


# ---- viewport (bbox) listing: the Boundaries map layer ----


def _bbox(minx, miny, maxx, maxy):
    from django.contrib.gis.geos import Polygon

    p = Polygon.from_bbox((minx, miny, maxx, maxy))
    p.srid = 4326
    return p


_AROUND_SQUARE = (12.9, 10.9, 13.3, 11.3)  # contains _SQUARE and _INSIDE, excludes _OUTSIDE


@pytest.mark.django_db
class TestLabsViewport:
    def test_lists_only_boundaries_intersecting_bbox(self):
        from commcare_connect.microplans.core.admin_boundaries import BoundaryFeature

        _make_boundary("Borno", 1, _SQUARE, "ng-bo", population=5_000_000)
        _make_boundary("Far", 1, _OUTSIDE, "far")
        feats = LabsAdminBoundarySource().list_in_bbox(_bbox(*_AROUND_SQUARE))
        assert [f.name for f in feats] == ["Borno"]
        f = feats[0]
        assert isinstance(f, BoundaryFeature)
        assert f.source == "labs" and f.boundary_id == "ng-bo" and f.level == 1
        assert f.geometry["type"] in ("Polygon", "MultiPolygon")
        assert f.area_km2 and f.area_km2 > 0
        assert f.population == 5_000_000

    def test_level_filter(self):
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        _make_boundary("Jere", 2, _INSIDE, "ng-bo-jere", parent_boundary_id="ng-bo")
        feats = LabsAdminBoundarySource().list_in_bbox(_bbox(*_AROUND_SQUARE), levels=[2])
        assert [f.name for f in feats] == ["Jere"]

    def test_iso_filter_excludes_other_countries(self):
        from django.contrib.gis.geos import GEOSGeometry, MultiPolygon

        from commcare_connect.labs.admin_boundaries.models import AdminBoundary

        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        AdminBoundary.objects.create(
            iso_code="KEN",
            admin_level=1,
            name="Nairobi",
            boundary_id="ke-nbi",
            geometry=MultiPolygon(GEOSGeometry(_INSIDE, srid=4326), srid=4326),
            source="grid3",
        )
        feats = LabsAdminBoundarySource().list_in_bbox(_bbox(*_AROUND_SQUARE), iso="NGA")
        assert {f.name for f in feats} == {"Borno"}

    def test_simplify_tolerance_yields_valid_geometry(self):
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        feats = LabsAdminBoundarySource().list_in_bbox(_bbox(*_AROUND_SQUARE), tolerance=0.001)
        assert feats[0].geometry["type"] in ("Polygon", "MultiPolygon")


class TestOvertureViewport:
    def test_calls_bbox_query_and_normalises(self, monkeypatch):
        captured = {}

        def fake_bbox(a2, bbox_wkt, subtypes=None, simplify=None, limit=1500):
            captured.update(a2=a2, subtypes=subtypes, simplify=simplify, bbox_wkt=bbox_wkt)
            return [
                {
                    "name": "Jere",
                    "subtype": "county",
                    "region": "NG-BO",
                    "area_km2": 12.3,
                    "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
                }
            ]

        monkeypatch.setattr(ab.boundaries, "list_admin_areas_in_bbox", fake_bbox)
        feats = OvertureBoundarySource().list_in_bbox(_bbox(*_AROUND_SQUARE), iso="NGA", tolerance=0.002)
        assert captured["a2"] == "NG" and captured["simplify"] == 0.002
        assert len(feats) == 1
        f = feats[0]
        assert f.source == "overture" and f.level == ab.LEVEL_COUNTY and f.name == "Jere"
        assert f.geometry["type"] == "Polygon" and f.area_km2 == 12.3

    def test_requires_iso(self):
        # Overture can't prune the parquet without a country; no iso -> empty, no query.
        assert OvertureBoundarySource().list_in_bbox(_bbox(*_AROUND_SQUARE), iso=None) == []


@pytest.mark.django_db
class TestResolverViewport:
    def test_dispatches_to_labs_and_flags_truncation(self):
        for i in range(3):
            _make_boundary(f"Area{i}", 1, _SQUARE, f"a{i}")
        feats, truncated = BoundaryResolver().boundaries_in_bbox(
            _bbox(*_AROUND_SQUARE), source="labs", iso="NGA", limit=2
        )
        assert truncated is True
        assert len(feats) == 2

    def test_no_truncation_under_cap(self):
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        feats, truncated = BoundaryResolver().boundaries_in_bbox(_bbox(*_AROUND_SQUARE), source="labs", iso="NGA")
        assert truncated is False and [f.name for f in feats] == ["Borno"]

    def test_source_pick_overture(self, monkeypatch):
        _make_boundary("Borno", 1, _SQUARE, "ng-bo")  # labs present, but user picks overture

        def fake_bbox(a2, bbox_wkt, subtypes=None, simplify=None, limit=1500):
            return [
                {
                    "name": "Ovr",
                    "subtype": "region",
                    "region": "x",
                    "area_km2": 1.0,
                    "geometry": {"type": "Polygon", "coordinates": []},
                }
            ]

        monkeypatch.setattr(ab.boundaries, "list_admin_areas_in_bbox", fake_bbox)
        feats, _ = BoundaryResolver().boundaries_in_bbox(_bbox(*_AROUND_SQUARE), source="overture", iso="NGA")
        assert [f.name for f in feats] == ["Ovr"]


def _login_labs(client, django_user_model):
    import time

    user = django_user_model.objects.create(username="vp", email="vp@example.com")
    client.force_login(user)
    s = client.session
    s["labs_oauth"] = {"access_token": "t", "expires_at": time.time() + 3600}
    s.save()


@pytest.mark.django_db
class TestViewportEndpoint:
    def test_returns_featurecollection_for_bbox(self, client, django_user_model):
        from django.urls import reverse

        _make_boundary("Borno", 1, _SQUARE, "ng-bo", population=5_000_000)
        _make_boundary("Far", 1, _OUTSIDE, "far")
        _login_labs(client, django_user_model)
        resp = client.get(
            reverse("microplans:boundary_viewport"),
            {"bbox": "12.9,10.9,13.3,11.3", "iso": "NGA", "source": "labs", "zoom": "9"},
        )
        data = resp.json()
        assert data["status"] == "ok"
        assert data["type"] == "FeatureCollection"
        assert data["source"] == "labs"
        assert data["truncated"] is False
        assert [f["properties"]["name"] for f in data["features"]] == ["Borno"]
        props = data["features"][0]["properties"]
        assert props["admin_level"] == 1 and props["population"] == 5_000_000
        assert data["features"][0]["geometry"]["type"] in ("Polygon", "MultiPolygon")
        assert "labs" in data["available_sources"]
        assert data["source_labels"]["labs"]

    def test_level_filter_param(self, client, django_user_model):
        from django.urls import reverse

        _make_boundary("Borno", 1, _SQUARE, "ng-bo")
        _make_boundary("Jere", 2, _INSIDE, "ng-bo-jere", parent_boundary_id="ng-bo")
        _login_labs(client, django_user_model)
        resp = client.get(
            reverse("microplans:boundary_viewport"),
            {"bbox": "12.9,10.9,13.3,11.3", "iso": "NGA", "level": "2"},
        )
        assert [f["properties"]["name"] for f in resp.json()["features"]] == ["Jere"]

    def test_missing_bbox_is_400(self, client, django_user_model):
        from django.urls import reverse

        _login_labs(client, django_user_model)
        assert client.get(reverse("microplans:boundary_viewport")).status_code == 400

    def test_malformed_bbox_is_400(self, client, django_user_model):
        from django.urls import reverse

        _login_labs(client, django_user_model)
        assert client.get(reverse("microplans:boundary_viewport"), {"bbox": "nope"}).status_code == 400
