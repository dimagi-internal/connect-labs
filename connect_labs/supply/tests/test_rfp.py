import json
from datetime import date, timedelta

import pytest

from connect_labs.supply.models import RFP, Award, Bid

from . import factories as f

pytestmark = pytest.mark.django_db

TODAY = date.today()


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def _qualify(org, category="rutf", days=400):
    return f.QualificationFactory(
        org=org, category=category, granted_at=TODAY, expires_at=TODAY + timedelta(days=days)
    )


def test_unqualified_org_cannot_see_or_bid(supplier_client):
    client, member = supplier_client
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)

    assert client.get("/supply/api/rfps/").json()["rfps"] == []
    resp = _post(client, f"/supply/api/rfps/{rfp.id}/bid/", {"lot_bids": [{"lot_id": lot.id, "unit_price": 40}]})
    # 404 rather than 403: a solicitation the org cannot see is reported as
    # absent, so the response does not disclose that it exists.
    assert resp.status_code == 404
    assert Bid.objects.count() == 0


def test_expired_qualification_does_not_grant_access(supplier_client):
    client, member = supplier_client
    f.QualificationFactory(
        org=member.org, category="rutf", granted_at=TODAY - timedelta(days=600), expires_at=TODAY - timedelta(days=1)
    )
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    f.LotFactory(rfp=rfp)
    assert client.get("/supply/api/rfps/").json()["rfps"] == []


def test_qualified_org_bids_per_lot(supplier_client):
    client, member = supplier_client
    _qualify(member.org)
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED, bid_deadline=TODAY + timedelta(days=10))
    lot1 = f.LotFactory(rfp=rfp, delivery_place="Maiduguri")
    lot2 = f.LotFactory(rfp=rfp, delivery_place="Damaturu")

    listed = client.get("/supply/api/rfps/").json()["rfps"]
    assert [r["id"] for r in listed] == [rfp.id]

    resp = _post(
        client,
        f"/supply/api/rfps/{rfp.id}/bid/",
        {
            "lot_bids": [
                {"lot_id": lot1.id, "unit_price": 41.5, "lead_time_days": 21, "notes": "FCA Kano"},
                {"lot_id": lot2.id, "unit_price": 43.0, "lead_time_days": 25},
            ]
        },
    )
    assert resp.status_code == 200
    bid = Bid.objects.get(org=member.org, rfp=rfp)
    assert bid.status == Bid.Status.DRAFT
    assert bid.lot_bids.count() == 2

    # saving again replaces the lot bids rather than duplicating them
    _post(client, f"/supply/api/rfps/{rfp.id}/bid/", {"lot_bids": [{"lot_id": lot1.id, "unit_price": 39.0}]})
    assert bid.lot_bids.count() == 1
    assert float(bid.lot_bids.get().unit_price) == 39.0

    assert _post(client, f"/supply/api/rfps/{rfp.id}/bid/submit/", {}).status_code == 200
    bid.refresh_from_db()
    assert bid.status == Bid.Status.SUBMITTED
    assert bid.submitted_at is not None


def test_cannot_bid_on_lot_from_another_rfp(supplier_client):
    client, member = supplier_client
    _qualify(member.org)
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    f.LotFactory(rfp=rfp)
    foreign_lot = f.LotFactory()
    resp = _post(
        client, f"/supply/api/rfps/{rfp.id}/bid/", {"lot_bids": [{"lot_id": foreign_lot.id, "unit_price": 40}]}
    )
    assert resp.status_code == 400


def test_cannot_bid_after_deadline_or_when_closed(supplier_client):
    client, member = supplier_client
    _qualify(member.org)
    past = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED, bid_deadline=TODAY - timedelta(days=1))
    lot = f.LotFactory(rfp=past)
    assert (
        _post(
            client, f"/supply/api/rfps/{past.id}/bid/", {"lot_bids": [{"lot_id": lot.id, "unit_price": 40}]}
        ).status_code
        == 400
    )

    closed = f.RFPFactory(categories=["rutf"], status=RFP.Status.CLOSED)
    closed_lot = f.LotFactory(rfp=closed)
    assert (
        _post(
            client, f"/supply/api/rfps/{closed.id}/bid/", {"lot_bids": [{"lot_id": closed_lot.id, "unit_price": 40}]}
        ).status_code
        == 400
    )


def test_cannot_edit_bid_after_submitting(supplier_client):
    client, member = supplier_client
    _qualify(member.org)
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    _post(client, f"/supply/api/rfps/{rfp.id}/bid/", {"lot_bids": [{"lot_id": lot.id, "unit_price": 40}]})
    _post(client, f"/supply/api/rfps/{rfp.id}/bid/submit/", {})
    resp = _post(client, f"/supply/api/rfps/{rfp.id}/bid/", {"lot_bids": [{"lot_id": lot.id, "unit_price": 1}]})
    assert resp.status_code == 400


def test_supplier_cannot_see_other_bids(supplier_client):
    client, member = supplier_client
    _qualify(member.org)
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    rival_bid = f.BidFactory(org=f.SupplierOrgFactory(legal_name="Rival Foods"), rfp=rfp, status=Bid.Status.SUBMITTED)
    f.LotBidFactory(bid=rival_bid, lot=lot, unit_price=10)

    body = client.get(f"/supply/api/rfps/{rfp.id}/").json()
    assert body["my_bid"] is None
    assert "bids" not in body
    assert client.get(f"/supply/api/rfps/{rfp.id}/comparison/").status_code == 403


def test_admin_publishes_rfp_requires_lot(admin_client):
    client, _user = admin_client
    resp = _post(
        client,
        "/supply/api/rfps/",
        {"title": "RUTF Northeast Nigeria Q3", "categories": ["rutf"], "countries": ["NG"]},
    )
    assert resp.status_code == 200
    rfp_id = resp.json()["rfp"]["id"]
    assert _post(client, f"/supply/api/rfps/{rfp_id}/transition/", {"status": "published"}).status_code == 400

    _post(
        client,
        f"/supply/api/rfps/{rfp_id}/lots/",
        {
            "category": "rutf",
            "description": "60,000 cartons RUTF",
            "quantity": 60000,
            "unit": "cartons",
            "delivery_country": "NG",
            "delivery_place": "Maiduguri",
        },
    )
    assert _post(client, f"/supply/api/rfps/{rfp_id}/transition/", {"status": "published"}).status_code == 200
    assert RFP.objects.get(id=rfp_id).status == RFP.Status.PUBLISHED


def test_scoring_and_comparison_ranked_by_price(admin_client):
    client, user = admin_client
    rfp = f.RFPFactory(categories=["rutf"], status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    cheap = f.LotBidFactory(
        bid=f.BidFactory(org=f.SupplierOrgFactory(legal_name="Cheap Co"), rfp=rfp, status=Bid.Status.SUBMITTED),
        lot=lot,
        unit_price=38,
    )
    dear = f.LotBidFactory(
        bid=f.BidFactory(org=f.SupplierOrgFactory(legal_name="Dear Co"), rfp=rfp, status=Bid.Status.SUBMITTED),
        lot=lot,
        unit_price=52,
    )
    draft_bid = f.LotBidFactory(
        bid=f.BidFactory(org=f.SupplierOrgFactory(legal_name="Draft Co"), rfp=rfp, status=Bid.Status.DRAFT),
        lot=lot,
        unit_price=1,
    )

    assert (
        _post(client, f"/supply/api/lot-bids/{cheap.id}/score/", {"technical_score": 80, "notes": "ok"}).status_code
        == 200
    )
    # re-scoring by the same reviewer updates rather than duplicating
    _post(client, f"/supply/api/lot-bids/{cheap.id}/score/", {"technical_score": 90})
    _post(client, f"/supply/api/lot-bids/{dear.id}/score/", {"technical_score": 70})

    comparison = client.get(f"/supply/api/rfps/{rfp.id}/comparison/").json()["lots"]
    assert len(comparison) == 1
    rows = comparison[0]["lot_bids"]
    # unsubmitted bids never reach the comparison table
    assert [r["id"] for r in rows] == [cheap.id, dear.id]
    assert draft_bid.id not in [r["id"] for r in rows]
    assert rows[0]["avg_technical_score"] == 90.0
    assert rows[0]["price_rank"] == 1 and rows[1]["price_rank"] == 2


def test_score_out_of_range_rejected(admin_client):
    client, _user = admin_client
    lb = f.LotBidFactory()
    assert _post(client, f"/supply/api/lot-bids/{lb.id}/score/", {"technical_score": 140}).status_code == 400


def test_cannot_award_unsubmitted_bid(admin_client):
    client, _user = admin_client
    rfp = f.RFPFactory(status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    draft = f.LotBidFactory(bid=f.BidFactory(rfp=rfp, status=Bid.Status.DRAFT), lot=lot)
    resp = _post(client, f"/supply/api/lots/{lot.id}/award/", {"lot_bid_id": draft.id})
    assert resp.status_code == 400
    assert Award.objects.count() == 0


def test_award_sets_rfp_awarded_when_all_lots_done(admin_client):
    client, _user = admin_client
    rfp = f.RFPFactory(status=RFP.Status.PUBLISHED)
    lot1, lot2 = f.LotFactory(rfp=rfp), f.LotFactory(rfp=rfp)
    bid = f.BidFactory(rfp=rfp, status=Bid.Status.SUBMITTED)
    lb1 = f.LotBidFactory(bid=bid, lot=lot1, unit_price=40)
    lb2 = f.LotBidFactory(bid=bid, lot=lot2, unit_price=41)

    assert _post(client, f"/supply/api/lots/{lot1.id}/award/", {"lot_bid_id": lb1.id}).status_code == 200
    rfp.refresh_from_db()
    assert rfp.status == RFP.Status.PUBLISHED  # one lot still open

    assert _post(client, f"/supply/api/lots/{lot2.id}/award/", {"lot_bid_id": lb2.id}).status_code == 200
    rfp.refresh_from_db()
    assert rfp.status == RFP.Status.AWARDED


def test_cannot_award_same_lot_twice(admin_client):
    client, _user = admin_client
    rfp = f.RFPFactory(status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    bid = f.BidFactory(rfp=rfp, status=Bid.Status.SUBMITTED)
    lb = f.LotBidFactory(bid=bid, lot=lot)
    other = f.LotBidFactory(bid=f.BidFactory(rfp=rfp, status=Bid.Status.SUBMITTED), lot=lot)
    assert _post(client, f"/supply/api/lots/{lot.id}/award/", {"lot_bid_id": lb.id}).status_code == 200
    assert _post(client, f"/supply/api/lots/{lot.id}/award/", {"lot_bid_id": other.id}).status_code == 400
    assert Award.objects.count() == 1


def test_reviewer_can_score_but_not_award(reviewer_client):
    client, _user = reviewer_client
    rfp = f.RFPFactory(status=RFP.Status.PUBLISHED)
    lot = f.LotFactory(rfp=rfp)
    lb = f.LotBidFactory(bid=f.BidFactory(rfp=rfp, status=Bid.Status.SUBMITTED), lot=lot)
    assert _post(client, f"/supply/api/lot-bids/{lb.id}/score/", {"technical_score": 65}).status_code == 200
    assert _post(client, f"/supply/api/lots/{lot.id}/award/", {"lot_bid_id": lb.id}).status_code == 403
    assert _post(client, "/supply/api/rfps/", {"title": "x", "categories": ["rutf"]}).status_code == 403


def test_supplier_cannot_score(supplier_client):
    client, member = supplier_client
    _qualify(member.org)
    lb = f.LotBidFactory()
    assert _post(client, f"/supply/api/lot-bids/{lb.id}/score/", {"technical_score": 99}).status_code == 403
