"""The workspace page: the scenario Market size reads, laid out around a map."""

import json
import re

import pytest
from django.contrib.gis.geos import MultiPolygon, Polygon
from django.urls import reverse

from connect_labs.labs.admin_boundaries.models import AdminBoundary


@pytest.fixture
def client_in(client, django_user_model):
    client.force_login(django_user_model.objects.create_user(username="workspace", password="x"))
    return client


def test_the_workspace_has_its_own_address():
    assert reverse("cea:workspace") == "/labs/cea/workspace/"
    assert reverse("cea:workspace_shapes") == "/labs/cea/workspace/shapes/"


@pytest.mark.django_db
class TestPage:
    def test_it_opens_with_the_tabs_and_the_same_embedded_scenario_as_market_size(self, client_in):
        body = client_in.get(
            reverse("cea:workspace"), {"models": "kc", "kc_reach": "30", "grain": "country"}
        ).content.decode()
        assert 'data-testid="cea-tabs"' in body and 'id="map"' in body
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        market = client_in.get(
            reverse("cea:market"), {"models": "kc", "kc_reach": "30", "grain": "country"}
        ).content.decode()
        same = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', market, re.S).group(1))
        assert data["shapes_url"] == "/labs/cea/workspace/shapes/"
        # the workspace adds the outlines' address; a CSRF token is masked afresh on every response
        ignore = {"shapes_url", "csrf"}
        assert {k: v for k, v in data.items() if k not in ignore} == {k: v for k, v in same.items() if k not in ignore}

    def test_the_workspace_starts_at_territories_and_market_size_at_countries(self, client_in):
        def state(page, **q):
            body = client_in.get(reverse(page), q).content.decode()
            return json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))["state"]

        assert state("cea:workspace")["grain"] == "territory" and state("cea:market")["grain"] == "country"
        assert state("cea:workspace", grain="country")["grain"] == "country"  # a link that says so wins

    def test_a_saved_scenario_keeps_its_grain_on_the_workspace(self, client_in, django_user_model):
        from connect_labs.effectiveness import saved

        user = django_user_model.objects.get(username="workspace")
        record, _ = saved.save(user, "Countries", {"models": "kc"})  # a country grain is the default, so not written
        body = client_in.get(reverse("cea:workspace"), {"scenario": record.pk}).content.decode()
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        assert data["state"]["grain"] == "country"

    def test_the_numbers_come_from_the_market_api_so_the_two_pages_cannot_differ(self, client_in):
        body = client_in.get(reverse("cea:workspace")).content.decode()
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        assert data["api_url"] == reverse("cea:market_data")


@pytest.mark.django_db
class TestShapes:
    def test_a_country_outline_is_keyed_by_the_code_a_market_row_carries(self, client_in):
        ring = Polygon(((0, 0), (0, 5), (5, 5), (5, 0), (0, 0)))
        AdminBoundary.objects.create(
            name="Niger",
            iso_code="NER",
            admin_level=0,
            boundary_id="NER-ADM0-test",
            source=AdminBoundary.Source.GEOBOUNDARIES,
            geometry=MultiPolygon(ring),
        )
        got = client_in.get(reverse("cea:workspace_shapes")).json()
        assert got["type"] == "FeatureCollection"
        ner = next(f for f in got["features"] if f["properties"]["iso"] == "NER")
        assert ner["id"] == "NER" and ner["properties"]["name"] == "Niger" and ner["properties"]["key"] == "NER"

    def test_the_outlines_do_not_depend_on_the_scenario(self, client_in):
        a = client_in.get(reverse("cea:workspace_shapes")).content
        b = client_in.get(reverse("cea:workspace_shapes"), {"models": "kc", "indicator": "u5mr"}).content
        assert a == b

    def test_a_territory_outline_is_keyed_by_the_id_a_territory_market_row_carries(self, client_in):
        ring = Polygon(((0, 0), (0, 2), (2, 2), (2, 0), (0, 0)))
        kano = AdminBoundary.objects.create(
            name="Kano",
            iso_code="NGA",
            admin_level=1,
            boundary_id="NGA-KN-test",
            source=AdminBoundary.Source.GEOBOUNDARIES,
            geometry=MultiPolygon(ring),
        )
        got = client_in.get(reverse("cea:workspace_shapes"), {"grain": "territory"}).json()
        feature = next(f for f in got["features"] if f["properties"]["name"] == "Kano")
        assert feature["id"] == str(kano.pk) == feature["properties"]["key"]
        assert feature["properties"]["iso"] == "NGA" and feature["properties"]["country"] == "Nigeria"
        # country outlines carry no territories
        countries = client_in.get(reverse("cea:workspace_shapes")).json()
        assert all(f["properties"]["name"] != "Kano" for f in countries["features"])
