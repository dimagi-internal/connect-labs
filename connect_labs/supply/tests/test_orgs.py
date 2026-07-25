import json

import pytest

from connect_labs.supply.models import Certification

from . import factories as f

pytestmark = pytest.mark.django_db


def _post(client, url, payload):
    return client.post(url, data=json.dumps(payload), content_type="application/json")


def test_supplier_reads_and_edits_own_profile(supplier_client):
    client, member = supplier_client
    resp = client.get("/supply/api/org/profile/")
    assert resp.status_code == 200
    assert resp.json()["org"]["legal_name"] == member.org.legal_name

    resp = _post(
        client,
        "/supply/api/org/profile/",
        {"hq_city": "Kano", "description": "RUTF manufacturer", "contact_name": "Amina Bello", "gln": "6291234500008"},
    )
    assert resp.status_code == 200
    member.org.refresh_from_db()
    assert member.org.hq_city == "Kano"
    assert member.org.gln == "6291234500008"
    # legal_name is not editable through the profile endpoint
    assert member.org.legal_name != "Kano"


def test_certifications_add_and_delete(supplier_client):
    client, member = supplier_client
    resp = _post(
        client,
        "/supply/api/org/certifications/",
        {"cert_type": "UNICEF RUTF approval", "issuer": "UNICEF", "expiry_date": "2027-03-01"},
    )
    assert resp.status_code == 200
    cert_id = resp.json()["certification"]["id"]
    assert Certification.objects.filter(org=member.org).count() == 1

    assert _post(client, f"/supply/api/org/certifications/{cert_id}/delete/", {}).status_code == 200
    assert Certification.objects.filter(org=member.org).count() == 0


def test_cannot_touch_another_orgs_certification(supplier_client):
    client, _member = supplier_client
    rival_cert = f.CertificationFactory(org=f.SupplierOrgFactory(legal_name="Rival Foods"))
    resp = _post(client, f"/supply/api/org/certifications/{rival_cert.id}/delete/", {})
    assert resp.status_code == 404
    assert Certification.objects.filter(id=rival_cert.id).exists()


def test_staff_cannot_use_supplier_profile_endpoints(admin_client):
    client, _user = admin_client
    assert client.get("/supply/api/org/profile/").status_code == 403


def test_anonymous_gets_401(db):
    from django.test import Client

    assert Client().get("/supply/api/org/profile/").status_code == 401
