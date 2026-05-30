"""Tests for the bulk name-resolution service + endpoint.

Covers the ``name-match-and-confirm`` spine item of the microplans-10-wards
DDD spec: paste a list of ward names, get back a per-name resolution with
``matched_id`` or ``unresolved_reason``.
"""

from __future__ import annotations

import json

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.test import Client
from django.urls import reverse

from commcare_connect.labs.admin_boundaries.models import AdminBoundary
from commcare_connect.labs.admin_boundaries.services import (
    NameResolution,
    resolve_many_by_name,
)


def _box(x: float, y: float, size: float = 0.1) -> MultiPolygon:
    """Build a trivial square polygon centered roughly at (x, y)."""
    p = Polygon.from_bbox((x, y, x + size, y + size))
    return MultiPolygon(p)


@pytest.fixture
def kano_boundaries(db):
    """Seed two Kano LGAs + a few wards under each.

    Mirrors the shape of the real GeoPoDe data (ADM2 = LGA, ADM3 = ward, with
    parent_boundary_id pointing from the ward to its LGA).
    """
    # LGAs (admin_level=2)
    madobi_lga = AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=2,
        name="Madobi",
        boundary_id="NGA-LGA-madobi",
        geometry=_box(8.0, 11.7),
        source=AdminBoundary.Source.GEOPODE,
    )
    makoda_lga = AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=2,
        name="Makoda",
        boundary_id="NGA-LGA-makoda",
        geometry=_box(8.2, 12.1),
        source=AdminBoundary.Source.GEOPODE,
    )

    # Wards under Madobi
    AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=3,
        name="Madobi",
        boundary_id="NGA-W-madobi",
        geometry=_box(8.0, 11.7, 0.02),
        source=AdminBoundary.Source.GEOPODE,
        parent_boundary_id=madobi_lga.boundary_id,
        population=28_000.0,
    )
    AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=3,
        name="Gora",
        boundary_id="NGA-W-gora",
        geometry=_box(8.05, 11.72, 0.02),
        source=AdminBoundary.Source.GEOPODE,
        parent_boundary_id=madobi_lga.boundary_id,
        population=24_000.0,
    )

    # Wards under Makoda
    AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=3,
        name="Galinja",
        boundary_id="NGA-W-galinja",
        geometry=_box(8.2, 12.1, 0.02),
        source=AdminBoundary.Source.GEOPODE,
        parent_boundary_id=makoda_lga.boundary_id,
        population=22_000.0,
    )

    # An ambiguous case: "Tofa" exists as both a ward under Madobi LGA AND as
    # the name of another LGA's ward (real-world Kano has this: "Tofa" is an
    # LGA AND a ward inside it).
    AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=3,
        name="Tofa",
        boundary_id="NGA-W-tofa-1",
        geometry=_box(7.9, 11.9, 0.02),
        source=AdminBoundary.Source.GEOPODE,
        parent_boundary_id=madobi_lga.boundary_id,
        population=29_000.0,
    )
    AdminBoundary.objects.create(
        iso_code="NGA",
        admin_level=3,
        name="Tofa",
        boundary_id="NGA-W-tofa-2",
        geometry=_box(7.95, 11.85, 0.02),
        source=AdminBoundary.Source.GEOPODE,
        parent_boundary_id=makoda_lga.boundary_id,
        population=27_500.0,
    )


# ---------------------------------------------------------------------------
# Service tests
# ---------------------------------------------------------------------------


class TestResolveManyByName:
    def test_single_match_returns_full_resolution(self, kano_boundaries):
        results = resolve_many_by_name(
            ["Galinja"], iso_code="NGA", admin_level=3
        )
        assert len(results) == 1
        r = results[0]
        assert r.matched_id == "NGA-W-galinja"
        assert r.matched_name == "Galinja"
        assert r.lga == "Makoda"
        assert r.population == 22_000.0
        assert r.unresolved_reason == ""

    def test_unknown_name_returns_not_found(self, kano_boundaries):
        results = resolve_many_by_name(
            ["FakeWardName"], iso_code="NGA", admin_level=3
        )
        assert len(results) == 1
        r = results[0]
        assert r.matched_id == ""
        assert r.unresolved_reason == "not found"

    def test_ambiguous_name_returns_ambiguous(self, kano_boundaries):
        results = resolve_many_by_name(["Tofa"], iso_code="NGA", admin_level=3)
        assert len(results) == 1
        assert results[0].matched_id == ""
        assert "ambiguous" in results[0].unresolved_reason
        assert "2 candidates" in results[0].unresolved_reason

    def test_mixed_batch_preserves_order(self, kano_boundaries):
        """The verify on the spec's resolve-many-endpoint feature:
        POST with names=[Galinja, FakeWardName] returns a JSON list with
        one matched_id non-null and one unresolved_reason non-empty."""
        results = resolve_many_by_name(
            ["Galinja", "FakeWardName"], iso_code="NGA", admin_level=3
        )
        assert len(results) == 2
        assert results[0].matched_id == "NGA-W-galinja"
        assert results[0].unresolved_reason == ""
        assert results[1].matched_id == ""
        assert results[1].unresolved_reason == "not found"

    def test_case_insensitive_match(self, kano_boundaries):
        results = resolve_many_by_name(
            ["galinja", "MADOBI", "GoRa"], iso_code="NGA", admin_level=3
        )
        assert all(r.unresolved_reason == "" for r in results)
        assert {r.matched_name for r in results} == {"Galinja", "Madobi", "Gora"}

    def test_empty_name_in_batch(self, kano_boundaries):
        results = resolve_many_by_name(
            ["Galinja", "", "  "], iso_code="NGA", admin_level=3
        )
        assert results[0].matched_id == "NGA-W-galinja"
        assert results[1].unresolved_reason == "empty name"
        assert results[2].unresolved_reason == "empty name"

    def test_wrong_admin_level_returns_not_found(self, kano_boundaries):
        # "Galinja" is admin_level=3 only. Searching level 2 returns no match.
        results = resolve_many_by_name(
            ["Galinja"], iso_code="NGA", admin_level=2
        )
        assert results[0].unresolved_reason == "not found"

    def test_iso_filter_isolates_country(self, db, kano_boundaries):
        # A same-named boundary in a different country must not collide.
        AdminBoundary.objects.create(
            iso_code="KEN",
            admin_level=3,
            name="Galinja",
            boundary_id="KEN-W-galinja",
            geometry=_box(36.8, -1.3, 0.02),
            source=AdminBoundary.Source.GEOPODE,
        )
        results = resolve_many_by_name(
            ["Galinja"], iso_code="NGA", admin_level=3
        )
        assert results[0].matched_id == "NGA-W-galinja"

    def test_source_filter_when_provided(self, db, kano_boundaries):
        # An OSM-sourced "Galinja" should not collide with the GeoPoDe one
        # when the caller asks for source="geopode".
        AdminBoundary.objects.create(
            iso_code="NGA",
            admin_level=3,
            name="Galinja",
            boundary_id="NGA-W-galinja-osm",
            geometry=_box(8.21, 12.11, 0.02),
            source=AdminBoundary.Source.OSM,
        )
        results = resolve_many_by_name(
            ["Galinja"], iso_code="NGA", admin_level=3, source="geopode"
        )
        # With source filter, only the geopode-sourced row matches.
        assert results[0].matched_id == "NGA-W-galinja"
        # Without the filter, the same call sees both and goes ambiguous.
        results_all = resolve_many_by_name(
            ["Galinja"], iso_code="NGA", admin_level=3
        )
        assert "ambiguous" in results_all[0].unresolved_reason


# ---------------------------------------------------------------------------
# View / endpoint tests
# ---------------------------------------------------------------------------


@pytest.fixture
def authed_client(db, django_user_model):
    user = django_user_model.objects.create_user(username="tester", password="t")
    c = Client()
    c.force_login(user)
    return c


class TestResolveManyByNameEndpoint:
    URL = "/labs/explorer/boundaries/resolve_many/"

    def test_resolves_mixed_batch(self, kano_boundaries, authed_client):
        resp = authed_client.post(
            self.URL,
            data=json.dumps(
                {"names": ["Galinja", "FakeWardName"], "iso_code": "NGA", "admin_level": 3}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 200
        payload = resp.json()
        assert payload["success"] is True
        res = payload["resolutions"]
        assert len(res) == 2
        assert res[0]["matched_id"] == "NGA-W-galinja"
        assert res[0]["unresolved_reason"] == ""
        assert res[1]["matched_id"] == ""
        assert res[1]["unresolved_reason"] == "not found"

    def test_invalid_iso_code(self, db, authed_client):
        resp = authed_client.post(
            self.URL,
            data=json.dumps(
                {"names": ["X"], "iso_code": "XX", "admin_level": 3}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "iso_code" in resp.json()["error"].lower()

    def test_invalid_admin_level(self, db, authed_client):
        resp = authed_client.post(
            self.URL,
            data=json.dumps(
                {"names": ["X"], "iso_code": "NGA", "admin_level": "bad"}
            ),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "admin_level" in resp.json()["error"].lower()

    def test_names_must_be_list(self, db, authed_client):
        resp = authed_client.post(
            self.URL,
            data=json.dumps({"names": "Galinja", "iso_code": "NGA", "admin_level": 3}),
            content_type="application/json",
        )
        assert resp.status_code == 400
        assert "names" in resp.json()["error"].lower()

    def test_url_reverses(self):
        # Confirms the URL name is registered as expected.
        # admin_boundaries is included under the `explorer` namespace, so the
        # fully-qualified reverse name is `explorer:admin_boundaries:...`.
        url = reverse("explorer:admin_boundaries:resolve_many")
        assert url == self.URL

    def test_unauthenticated_redirects(self, db):
        c = Client()
        resp = c.post(self.URL, content_type="application/json")
        # LoginRequiredMixin redirects to login on POST too.
        assert resp.status_code in (302, 401, 403)
