"""Programmatic reseed, and the guards that make it acceptable on a public host.

`/supply/` has open registration on the open internet and this empties its
tables, so the refusals are the feature and are asserted here rather than assumed
from a settings file — the same reasoning as `test_dev_auth.py`.

Authenticated with a labs MCP PAT: already self-service, already how every other
programmatic labs operation authenticates, and — unlike supply's own ApiToken —
living outside the tables this deletes, so it survives its own reseed.
"""
import pytest

from connect_labs.mcp.models import MCPAccessToken
from connect_labs.supply.models.procurement import RFP, Award

pytestmark = pytest.mark.django_db

URL = "/supply/api/demo/reseed/"


@pytest.fixture()
def pat(django_user_model):
    """A live PAT's raw value (only available at creation time)."""
    user = django_user_model.objects.create(username="reseeder", email="reseeder@example.com")
    _token, raw = MCPAccessToken.create_token(user=user, name="reseed test")
    return raw


def test_it_401s_without_a_token(client):
    assert client.post(URL).status_code == 401


def test_it_401s_with_a_bogus_token(client):
    assert client.post(URL, HTTP_AUTHORIZATION="Bearer not-a-real-token").status_code == 401


def test_it_405s_on_get_even_with_a_valid_token(client, pat):
    """A destructive action must not be reachable by navigating to a URL."""
    assert client.get(URL, HTTP_AUTHORIZATION=f"Bearer {pat}").status_code == 405


def test_it_reseeds_with_a_valid_pat(client, pat):
    response = client.post(URL, HTTP_AUTHORIZATION=f"Bearer {pat}")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["reseeded"] is True
    # The seeder's own summary comes back, so a caller can assert the world it is
    # about to film rather than trusting a 200.
    assert body["summary"]["suppliers"] > 0
    assert body["summary"]["solicitations"] > 0
    assert RFP.objects.exists()


def test_reseeding_restores_a_world_a_walkthrough_consumed(client, pat):
    """The reason this exists.

    A render awards lots; `award_lot` then refuses those lots, so take two does
    not merely look different — it fails. Reseeding has to give back an
    un-awarded tender.
    """
    client.post(URL, HTTP_AUTHORIZATION=f"Bearer {pat}")

    live = RFP.objects.get(title__contains="Northeast Nigeria Q3 2026")
    awarded_before = Award.objects.filter(lot__rfp=live).count()

    from connect_labs.supply.tests import factories as f

    lot = next(lot for lot in live.lots.all() if not hasattr(lot, "award"))
    f.AwardFactory(lot=lot, lot_bid=lot.lot_bids.first())
    assert Award.objects.filter(lot__rfp=live).count() == awarded_before + 1

    assert client.post(URL, HTTP_AUTHORIZATION=f"Bearer {pat}").status_code == 200

    live = RFP.objects.get(title__contains="Northeast Nigeria Q3 2026")
    assert Award.objects.filter(lot__rfp=live).count() == awarded_before


def test_a_concurrent_reseed_gets_409_not_a_half_built_world(client, pat):
    """Two interleaved `--reset` runs leave a world that reads as a product bug,
    and a per-take render loop is exactly the caller that would race."""
    from connect_labs.supply.api import demo as demo_api

    demo_api._LOCK.acquire()
    try:
        response = client.post(URL, HTTP_AUTHORIZATION=f"Bearer {pat}")
        assert response.status_code == 409
        assert response.json()["retry"] is True
    finally:
        demo_api._LOCK.release()


# ---------------------------------------------------------------------------
# Setting the password: how a render gets a known credential with no
# pre-shared secret at all.
# ---------------------------------------------------------------------------


def test_it_can_set_the_demo_password_and_that_login_works(client, pat, django_user_model):
    response = client.post(
        URL,
        data='{"password": "render-known-2026"}',
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {pat}",
    )

    assert response.status_code == 200
    assert response.json()["password_set"] is True
    ada = django_user_model.objects.get(username="oes-lead@oes.example")
    assert ada.check_password("render-known-2026")


def test_it_restores_the_environment_afterwards(client, pat, monkeypatch):
    """The override is scoped: a caller choosing a password for one reseed must
    not silently repoint the instance's configured credential."""
    monkeypatch.setenv("SUPPLY_DEMO_PASSWORD", "instance-configured")

    client.post(
        URL,
        data='{"password": "just-this-once"}',
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {pat}",
    )

    import os

    assert os.environ["SUPPLY_DEMO_PASSWORD"] == "instance-configured"


def test_omitting_the_password_keeps_the_configured_one(client, pat, django_user_model, monkeypatch):
    monkeypatch.setenv("SUPPLY_DEMO_PASSWORD", "instance-configured")

    response = client.post(URL, HTTP_AUTHORIZATION=f"Bearer {pat}")

    assert response.json()["password_set"] is False
    ada = django_user_model.objects.get(username="oes-lead@oes.example")
    assert ada.check_password("instance-configured")


def test_the_mcp_tool_shares_one_implementation_with_the_route(db):
    """Two entry points, one reseed — they cannot drift."""
    from connect_labs.mcp.tools.supply_demo import supply_demo_reseed

    out = supply_demo_reseed(password="mcp-set-password-2026")

    assert out["ok"] is True and out["password_set"] is True
    assert out["summary"]["suppliers"] > 0


def test_the_mcp_tool_refuses_a_trivially_short_password(db):
    """This sets a login on a publicly reachable host."""
    from connect_labs.mcp.tools.supply_demo import supply_demo_reseed

    with pytest.raises(ValueError, match="at least 8 characters"):
        supply_demo_reseed(password="short")
