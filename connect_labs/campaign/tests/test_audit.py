"""Audit-logging tests.

Privileged writes persist an AuditLog row (real history, not seeded text), the
client IP is masked, and the rows surface in the bootstrap payload's AUDIT_LOG.
"""

from __future__ import annotations

import json

import pytest
from django.urls import reverse

from connect_labs.campaign.models import AuditLog
from connect_labs.campaign.services import audit


def _post(client, url, body):
    return client.post(url, data=json.dumps(body), content_type="application/json")


def test_mask_ip():
    assert audit.mask_ip("102.89.4.17") == "102.89.x.x"
    assert audit.mask_ip("") == ""
    assert audit.mask_ip("2001:db8:abcd::1") == "2001:db8::x"


@pytest.mark.django_db
def test_invite_writes_audit_row(client, login_as, seeded_campaign):
    login_as(client)
    before = AuditLog.objects.count()
    _post(
        client,
        reverse("campaign:user_invite"),
        {"name": "Zed", "email": "zed@partner.org", "role": "reporting"},
    )
    assert AuditLog.objects.count() == before + 1
    row = AuditLog.objects.order_by("-id").first()
    assert row.module == "User Management"
    assert "zed@partner.org" in row.action
    assert "Reporting User" in row.action  # role display name, not the key


@pytest.mark.django_db
def test_payment_approval_writes_audit_row(client, login_as, seeded_campaign):
    login_as(client)  # campaign_admin can approve payments
    worker = seeded_campaign.workers.filter(kyc="approved").first()
    before = AuditLog.objects.count()
    resp = _post(
        client,
        reverse("campaign:pay_set_status"),
        {"status": "approved", "worker_ids": [worker.worker_id]},
    )
    assert resp.status_code == 200
    assert AuditLog.objects.filter(module="Payments").count() >= 1
    assert AuditLog.objects.count() > before


@pytest.mark.django_db
def test_audit_rows_appear_in_bootstrap_payload(client, login_as, seeded_campaign):
    login_as(client)
    _post(
        client,
        reverse("campaign:user_invite"),
        {"name": "Yael", "email": "yael@partner.org", "role": "operations"},
    )
    resp = client.get(reverse("campaign:bootstrap"))
    log = resp.json()["campaign"]["AUDIT_LOG"]
    assert any("yael@partner.org" in e["action"] for e in log)
    # newest first, and each entry carries the masked-IP shape the UI renders
    assert {"at", "user", "action", "module", "ip"} <= set(log[0].keys())
