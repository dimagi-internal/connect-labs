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
        body = client_in.get(reverse("cea:workspace"), {"models": "kc", "kc_reach": "50"}).content.decode()
        assert 'data-testid="cea-tabs"' in body and 'id="map"' in body
        data = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', body, re.S).group(1))
        market = client_in.get(reverse("cea:market"), {"models": "kc", "kc_reach": "50"}).content.decode()
        same = json.loads(re.search(r'id="scenario-data"[^>]*>(.*?)</script>', market, re.S).group(1))
        assert data["shapes_url"] == "/labs/cea/workspace/shapes/"
        assert {k: v for k, v in data.items() if k != "shapes_url"} == same

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
        assert ner["id"] == "NER" and ner["properties"]["name"] == "Niger"

    def test_the_outlines_do_not_depend_on_the_scenario(self, client_in):
        a = client_in.get(reverse("cea:workspace_shapes")).content
        b = client_in.get(reverse("cea:workspace_shapes"), {"models": "kc", "indicator": "u5mr"}).content
        assert a == b
