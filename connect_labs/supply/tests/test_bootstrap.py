import json
from datetime import date, timedelta

import pytest
from django.test import Client

from connect_labs.supply.models import Bid, EOIRound, EOISubmission, RFP

from . import factories as f

pytestmark = pytest.mark.django_db
TODAY = date.today()


def test_bootstrap_401_for_anonymous():
    assert Client().get("/supply/api/bootstrap/").status_code == 401


def test_bootstrap_401_for_user_without_supply_role(db):
    user = f.UserFactory(username="stranger")
    c = Client()
    c.force_login(user)
    assert c.get("/supply/api/bootstrap/").status_code == 401


def test_supplier_bootstrap_shape(supplier_client):
    client, member = supplier_client
    f.QualificationFactory(
        org=member.org, category="rutf", granted_at=TODAY, expires_at=TODAY + timedelta(days=300)
    )
    rnd = f.EOIRoundFactory(status=EOIRound.Status.OPEN, categories=["rutf"])
    f.EOISubmissionFactory(org=member.org, round=rnd)
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    f.LotFactory(rfp=rfp)

    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "supplier"
    assert body["org"]["legal_name"] == member.org.legal_name
    assert body["perms"]["org"] == ["view", "edit"]
    assert [r["id"] for r in body["open_rounds"]] == [rnd.id]
    assert body["open_rounds"][0]["applied"] is True
    assert len(body["my_submissions"]) == 1
    assert [r["id"] for r in body["eligible_rfps"]] == [rfp.id]
    assert body["eligible_rfps"][0]["my_bid"] is None
    # supplier never receives staff surfaces
    assert "review_queue" not in body
    assert "registry" not in body


def test_supplier_bootstrap_embeds_own_bid(supplier_client):
    client, member = supplier_client
    f.QualificationFactory(
        org=member.org, category="rutf", granted_at=TODAY, expires_at=TODAY + timedelta(days=300)
    )
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    client.post(
        f"/supply/api/rfps/{rfp.id}/bid/",
        data=json.dumps({"lot_bids": [{"lot_id": lot.id, "unit_price": 44}]}),
        content_type="application/json",
    )
    body = client.get("/supply/api/bootstrap/").json()
    my_bid = body["eligible_rfps"][0]["my_bid"]
    assert my_bid["status"] == Bid.Status.DRAFT
    assert my_bid["lot_bids"][0]["unit_price"] == 44.0


def test_admin_bootstrap_shape(admin_client):
    client, _user = admin_client
    f.EOISubmissionFactory(status=EOISubmission.Status.SUBMITTED)
    f.QualificationFactory(granted_at=TODAY, expires_at=TODAY + timedelta(days=100))
    f.RFPFactory()

    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "procurement_admin"
    assert body["org"] is None
    assert len(body["review_queue"]) == 1
    assert len(body["registry"]) == 1
    assert len(body["rounds"]) >= 1
    assert len(body["rfps"]) == 1


def test_reviewer_bootstrap_omits_round_and_rfp_management(reviewer_client):
    client, _user = reviewer_client
    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "reviewer"
    assert "review_queue" in body
    assert "registry" in body
    assert "rounds" not in body
    assert "rfps" not in body


def test_app_shell_redirects_anonymous_and_renders_for_members(supplier_client):
    assert Client().get("/supply/").url == "/supply/login/"
    client, _member = supplier_client
    resp = client.get("/supply/")
    assert resp.status_code == 200
    assert b"supply-bootstrap" in resp.content
    assert b"supply-bundle.js" in resp.content
    # satellite convention: no labs template inheritance or analytics leakage
    assert b"labs-analytics" not in resp.content
    assert b"{#" not in resp.content
