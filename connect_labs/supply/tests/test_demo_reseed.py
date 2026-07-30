"""The HTTP reseed, and the guards that make it acceptable on a public host.

`/supply/` has open registration on the open internet, and this endpoint empties
the site's tables. Its refusals are the feature, so they are asserted here rather
than assumed from a settings file — the same reasoning as `test_dev_auth.py`.
"""
import pytest

from connect_labs.supply.api.demo import TOKEN_ENV
from connect_labs.supply.models.procurement import RFP, Award

pytestmark = pytest.mark.django_db

URL = "/supply/api/demo/reseed/"


def test_it_404s_when_no_token_is_configured(client, monkeypatch):
    """Unconfigured must be indistinguishable from "no such route" — an endpoint
    that empties a public site should not advertise itself."""
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    assert client.post(URL).status_code == 404


def test_it_403s_without_the_token(client, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    assert client.post(URL).status_code == 403


def test_it_403s_with_the_wrong_token(client, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    response = client.post(URL, HTTP_AUTHORIZATION="Bearer not-the-secret")
    assert response.status_code == 403


def test_it_405s_on_get_even_with_the_token(client, monkeypatch):
    """A destructive action must not be reachable by navigating to a URL."""
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    response = client.get(URL, HTTP_AUTHORIZATION="Bearer the-secret")
    assert response.status_code == 405


def test_an_empty_configured_token_does_not_authorise_an_empty_header(client, monkeypatch):
    """Whitespace/empty must never satisfy the compare."""
    monkeypatch.setenv(TOKEN_ENV, "   ")
    assert client.post(URL, HTTP_AUTHORIZATION="Bearer ").status_code == 404


def test_it_reseeds_with_a_valid_bearer_token(client, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    response = client.post(URL, HTTP_AUTHORIZATION="Bearer the-secret")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and body["reseeded"] is True
    # The seeder's own summary comes back, so a caller can assert the world it is
    # about to film rather than trusting a 200.
    assert body["summary"]["suppliers"] > 0
    assert body["summary"]["solicitations"] > 0
    assert RFP.objects.exists()


def test_the_x_reseed_token_header_also_works(client, monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    response = client.post(URL, HTTP_X_RESEED_TOKEN="the-secret")
    assert response.status_code == 200


def test_reseeding_restores_a_world_a_walkthrough_consumed(client, monkeypatch):
    """The reason this exists.

    A render awards lots; `award_lot` then refuses those lots, so take two does
    not merely look different — it fails. Reseeding has to give back an
    un-awarded tender.
    """
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    client.post(URL, HTTP_AUTHORIZATION="Bearer the-secret")

    live = RFP.objects.get(title__contains="Northeast Nigeria Q3 2026")
    awarded_before = Award.objects.filter(lot__rfp=live).count()

    # Simulate what a walkthrough does to the world it films. Award.lot is a
    # OneToOne, so pick a lot the seed left un-awarded — which is precisely the
    # lot the narrative's award scene clicks.
    from connect_labs.supply.tests import factories as f

    lot = next(lot for lot in live.lots.all() if not hasattr(lot, "award"))
    f.AwardFactory(lot=lot, lot_bid=lot.lot_bids.first())
    assert Award.objects.filter(lot__rfp=live).count() == awarded_before + 1

    # Put it back.
    response = client.post(URL, HTTP_AUTHORIZATION="Bearer the-secret")
    assert response.status_code == 200

    live = RFP.objects.get(title__contains="Northeast Nigeria Q3 2026")
    assert Award.objects.filter(lot__rfp=live).count() == awarded_before


def test_a_concurrent_reseed_gets_409_not_a_half_built_world(client, monkeypatch):
    """Two interleaved `--reset` runs leave a world that reads as a product bug,
    and a per-take render loop is exactly the caller that would race."""
    monkeypatch.setenv(TOKEN_ENV, "the-secret")
    from connect_labs.supply.api import demo as demo_api

    demo_api._LOCK.acquire()
    try:
        response = client.post(URL, HTTP_AUTHORIZATION="Bearer the-secret")
        assert response.status_code == 409
        assert response.json()["retry"] is True
    finally:
        demo_api._LOCK.release()
