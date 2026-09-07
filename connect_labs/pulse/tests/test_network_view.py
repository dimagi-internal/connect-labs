"""Locating partners, and drawing the network's growth.

Two things are easy to get wrong here and both are silent. A location resolver
that guesses produces a map nobody can challenge -- a dot is a dot, whether it
came from a street address or the word "Nigeria" -- so precision is asserted as
hard as position. And the growth series is dated from a spine with two known
traps in it, tested here so a later refactor cannot quietly reintroduce them.
"""

from __future__ import annotations

import datetime as dt

import pytest
from django.contrib.auth import get_user_model

from connect_labs.pulse import hq_location
from connect_labs.pulse.models import PulseEvent, PulsePartner, PulseWork
from connect_labs.pulse.network_api import WORKS_FLOOR, build_payload, first_service_by_partner
from connect_labs.pulse.partner_names import invalidate

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _fresh_partner_cache():
    """partner_names caches the directory for a minute so resolve() is not a
    query per partner per request. A test that seeds partners and then resolves
    them inside that window reads an empty cache and passes for the wrong
    reason -- which one of these tests did before this fixture existed."""
    invalidate()
    yield
    invalidate()


class TestCountryNames:
    def test_the_directory_dropdown_names_resolve(self):
        assert hq_location.country_to_iso3("Nigeria") == "NGA"
        assert hq_location.country_to_iso3("Malawi") == "MWI"

    def test_a_comma_inside_a_country_name_does_not_split_it(self):
        """The ISO name is "Congo, the Democratic Republic of the". Splitting on
        the comma leaves "Congo", which resolves to the OTHER Congo -- and put
        ten partners in the wrong country before this was caught."""
        assert hq_location.country_to_iso3("Congo, the Democratic Republic of the") == "COD"
        assert hq_location.country_to_iso3("Tanzania, United Republic of") == "TZA"

    def test_an_unknown_country_is_not_guessed(self):
        assert hq_location.country_to_iso3("Freedonia") is None
        assert hq_location.country_to_iso3("") is None


class TestTownMatching:
    def test_a_town_in_the_address_is_found(self):
        got = hq_location._city_point("NGA", "12 Ahmadu Bello Way, Kaduna")
        assert got is not None
        assert got[2] == "Kaduna"

    def test_address_furniture_is_not_a_town(self):
        """ "Nothing", an email, a bare PO box: the address column contains all
        three, and a resolver that matched on any word would scatter partners
        across the map with full confidence."""
        for junk in ("Nothing", "usmantech45@gmail.com", "P.O. Box 41"):
            assert hq_location._city_point("NGA", junk) is None

    def test_a_country_with_no_gazetteer_yields_nothing_rather_than_a_wrong_town(self):
        assert hq_location._city_point("MWI", "Lilongwe") is None


class TestFirstService:
    def _work(self, slug, when, key):
        return PulseWork.objects.create(
            work_key=key,
            opportunity_id=1,
            org_slug=slug,
            status="approved",
            created_ts=when,
            usd_to_worker="1.00",
            usd_to_org="1.00",
        )

    def test_the_bulk_import_instant_cannot_date_a_partner(self):
        """Connect bulk-created its completed_works rows at one instant on
        2025-01-14, so reading that date as a first delivery collapses every
        partner already active onto one day."""
        PulsePartner.objects.create(name="Foreland Rural Health Trust", short="FRHT")
        invalidate()
        self._work("frht", WORKS_FLOOR - dt.timedelta(days=1), "a" * 64)
        assert first_service_by_partner() == {}

    def test_a_real_later_work_does_date_a_partner(self):
        PulsePartner.objects.create(name="Foreland Rural Health Trust", short="FRHT")
        invalidate()
        when = WORKS_FLOOR + dt.timedelta(days=30)
        self._work("frht", when, "b" * 64)
        assert first_service_by_partner() == {"Foreland Rural Health Trust": when.date()}

    def test_a_device_clock_from_before_connect_existed_is_ignored(self):
        """One partner's earliest visit claims 2010. The server-assigned sync
        timestamp is what is trusted, and a sanity floor sits under it."""
        PulsePartner.objects.create(name="Foreland Rural Health Trust", short="FRHT")
        invalidate()
        PulseEvent.objects.create(
            connect_visit_id=1,
            opportunity_id=1,
            org_slug="frht",
            field_ts=dt.datetime(2010, 1, 1, tzinfo=dt.timezone.utc),
            sync_ts=dt.datetime(2010, 1, 1, tzinfo=dt.timezone.utc),
            status="approved",
        )
        assert first_service_by_partner() == {}


class TestPayload:
    def test_the_series_is_cumulative_and_never_falls(self):
        PulsePartner.objects.create(name="A", joined_at=dt.date(2024, 7, 2), lat=9.0, lon=8.0, country_iso3="NGA")
        PulsePartner.objects.create(name="B", joined_at=dt.date(2025, 2, 1), lat=1.0, lon=32.0, country_iso3="UGA")
        series = build_payload()["series"]
        assert [s["network"] for s in series] == sorted(s["network"] for s in series)
        assert series[-1]["network"] == 2

    def test_precision_travels_with_every_point(self):
        """The map draws a town differently from a country, so a point that
        arrives without its precision would be drawn as more than it is."""
        PulsePartner.objects.create(
            name="A", lat=9.0, lon=8.0, country_iso3="NGA", location_precision="country", location_label="Nigeria"
        )
        point = build_payload()["points"][0]
        assert point["precision"] == "country"
        assert point["place"] == "Nigeria"

    def test_a_partner_with_no_location_is_omitted_rather_than_placed_at_zero(self):
        """(0, 0) is in the Gulf of Guinea, which is where an unplaced partner
        lands if the view is careless."""
        PulsePartner.objects.create(name="Nowhere", joined_at=dt.date(2025, 1, 1))
        payload = build_payload()
        assert payload["points"] == []
        assert payload["totals"]["partners"] == 1


class TestEntitlement:
    def test_the_endpoint_refuses_an_anonymous_caller(self, client):
        """It names partners and says where they are. api.py already decided
        partner identity needs positive authorisation and fails closed."""
        assert client.get("/labs/pulse/api/network/").status_code == 403

    def test_a_labs_session_is_entitled(self, client):
        user = get_user_model().objects.create_user(username="analyst", password="x")  # noqa: S106
        client.force_login(user)
        response = client.get("/labs/pulse/api/network/")
        assert response.status_code == 200
        assert "series" in response.json()

    def test_an_unimported_directory_says_so_rather_than_drawing_a_dead_network(self, client):
        user = get_user_model().objects.create_user(username="analyst2", password="x")  # noqa: S106
        client.force_login(user)
        assert "empty_reason" in client.get("/labs/pulse/api/network/").json()


def test_the_page_requires_a_session(client):
    assert client.get("/labs/pulse/network/").status_code in (302, 301)
