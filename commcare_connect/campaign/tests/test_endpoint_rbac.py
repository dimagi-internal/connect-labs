"""Table-driven RBAC enforcement: every mutating endpoint × every role.

For each (endpoint, role): the request must 403 iff the role lacks the endpoint's
required permission, and must NOT 403 when it has it. This is the server-side gate —
the real security boundary — exercised exhaustively, extending the previous
single-role `test_rbac_reporting_user_cannot_write`.

Uses the plain (CSRF-disabled) client deliberately: this isolates the RBAC decision.
CSRF transport is covered separately in `test_workers_api.test_csrf_round_trip_*`.
"""
from __future__ import annotations

import json

import pytest
from django.urls import reverse

from commcare_connect.campaign.services import rbac
from commcare_connect.campaign.tests.factories import CampaignFactory, WorkerFactory

# name -> (required module, verb, url(worker_id), body(worker_id))
ENDPOINTS = {
    "pay_set_status": (
        "payments",
        "approve",
        lambda wid: reverse("campaign:pay_set_status"),
        lambda wid: {"worker_ids": [wid], "status": "approved"},
    ),
    "pay_queue": (
        "payments",
        "approve",
        lambda wid: reverse("campaign:pay_queue", args=[wid]),
        lambda wid: {"approved_count": 1},
    ),
    "kyc_status": (
        "kyc",
        "approve",
        lambda wid: reverse("campaign:kyc_status", args=[wid]),
        lambda wid: {"status": "review"},
    ),
    "kyc_resolve_dupe": (
        "kyc",
        "approve",
        lambda wid: reverse("campaign:kyc_resolve_dupe", args=[wid]),
        lambda wid: {"keep": True},
    ),
    "kyc_investigation": (
        "kyc",
        "approve",
        lambda wid: reverse("campaign:kyc_investigation", args=[wid]),
        lambda wid: {"status": "Open", "note": "x"},
    ),
}


@pytest.fixture
def clean_worker(db):
    """A single campaign + a clean (unflagged, non-rejected) worker on it."""
    campaign = CampaignFactory()
    worker = WorkerFactory(campaign=campaign, kyc="pending", pay="pending", fraud_rules=[])
    return worker


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", sorted(ENDPOINTS))
@pytest.mark.parametrize("role", rbac.ROLES)
def test_mutating_endpoint_enforces_rbac(client, login_as, clean_worker, role, endpoint):
    module, verb, url_for, body_for = ENDPOINTS[endpoint]
    login_as(client, role)
    resp = client.post(
        url_for(clean_worker.worker_id),
        data=json.dumps(body_for(clean_worker.worker_id)),
        content_type="application/json",
    )
    if rbac.can(role, module, verb):
        assert resp.status_code != 403, f"{role} should be allowed {endpoint}, got {resp.status_code}"
    else:
        assert resp.status_code == 403, f"{role} should be denied {endpoint}, got {resp.status_code}"


@pytest.mark.django_db
@pytest.mark.parametrize("role", rbac.ROLES)
def test_every_role_can_read_bootstrap(client, login_as, seeded_campaign, role):
    """overview:view is granted to all five roles — the read endpoint must not 403 anyone."""
    login_as(client, role)
    resp = client.get(reverse("campaign:bootstrap"))
    assert resp.status_code == 200
