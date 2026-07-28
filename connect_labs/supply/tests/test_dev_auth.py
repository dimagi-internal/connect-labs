"""The dev-login persona switch, and the guard that makes it acceptable.

Supply is deployed on a public host with open registration, so an endpoint that
logs a caller in as a procurement admin without a password is only defensible
because it refuses outside DEBUG. That refusal is asserted here rather than
assumed from a settings file.
"""
import pytest
from django.core.management import call_command

pytestmark = pytest.mark.django_db


def test_it_refuses_outside_debug(client, settings):
    settings.DEBUG = False
    response = client.get("/supply/dev-login/?persona=ada")
    assert response.status_code == 403


def test_it_logs_in_a_seeded_persona(client, settings):
    settings.DEBUG = True
    call_command("seed_supply_demo")

    response = client.get("/supply/dev-login/?persona=zara")
    assert response.status_code == 302

    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "partner"
    assert body["org"]["legal_name"] == "Komadugu Health Initiative"


def test_every_persona_resolves_to_a_seeded_user(client, settings):
    """A persona that stops being seeded must stop being reachable."""
    settings.DEBUG = True
    call_command("seed_supply_demo")

    from connect_labs.supply.views_dev_auth import _personas

    expected = {
        "ada": "procurement_admin",
        "tomas": "reviewer",
        "hauwa": "gov_observer",
        "dale": "funder",
        "amina": "supplier",
        "zara": "partner",
    }
    assert set(_personas()) == set(expected)
    for persona, role in expected.items():
        assert client.get(f"/supply/dev-login/?persona={persona}").status_code == 302
        assert client.get("/supply/api/bootstrap/").json()["role"] == role, persona


def test_an_unknown_persona_is_rejected(client, settings):
    settings.DEBUG = True
    response = client.get("/supply/dev-login/?persona=nobody")
    assert response.status_code == 400


def test_it_never_creates_a_user(client, settings):
    """No seed, no login — this endpoint is a switch, not a factory."""
    settings.DEBUG = True
    from django.contrib.auth import get_user_model

    before = get_user_model().objects.count()
    assert client.get("/supply/dev-login/?persona=ada").status_code == 404
    assert get_user_model().objects.count() == before
