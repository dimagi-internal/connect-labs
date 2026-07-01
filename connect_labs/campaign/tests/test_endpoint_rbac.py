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

from connect_labs.campaign.services import rbac
from connect_labs.campaign.tests.factories import CampaignFactory, CampaignUserFactory, WorkerFactory

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


# --- Plan 4: Activity + Microplanning endpoints --------------------------------
# Plan 4's own api tests check only a single role; this covers every role. Note the
# asymmetry the matrix encodes: operations_manager can create activities (manage) but
# can only VIEW planning — so it is denied microplan create/edit.
PLAN4_ENDPOINTS = {
    "activity_create": (
        "activities",
        "create",
        lambda ids: reverse("campaign:activity_create"),
        lambda ids: {"name": "RBAC probe", "donor": "Gavi", "region": "Kano", "target": 1000},
    ),
    "activity_sync": (
        "activities",
        "create",
        lambda ids: reverse("campaign:activity_sync", args=[ids["activity"]]),
        lambda ids: {},
    ),
    "microplan_create": (
        "planning",
        "create",
        lambda ids: reverse("campaign:microplan_create"),
        lambda ids: {
            "region": "Kano",
            "regionId": "kano",
            "lga": "Dala",
            "target": 100000,
            "goalPct": 95,
            "roles": [],
        },
    ),
    "microplan_update": (
        "planning",
        "edit",
        lambda ids: reverse("campaign:microplan_update", args=[ids["microplan"]]),
        lambda ids: {
            "region": "Kano",
            "regionId": "kano",
            "lga": "Dala",
            "target": 123456,
            "goalPct": 95,
            "roles": [],
        },
    ),
    "microplan_target": (
        "planning",
        "edit",
        lambda ids: reverse("campaign:microplan_target", args=[ids["microplan"]]),
        lambda ids: {"target": 200000, "goalPct": 90},
    ),
    "microplan_budget": (
        "planning",
        "edit",
        lambda ids: reverse("campaign:microplan_budget", args=[ids["microplan"]]),
        lambda ids: {"budget": 999000},
    ),
}


@pytest.fixture
def seeded_ids(seeded_campaign):
    activity = seeded_campaign.activities.filter(synced=False).first() or seeded_campaign.activities.first()
    microplan = seeded_campaign.microplans.first()
    return {"activity": activity.activity_id, "microplan": microplan.microplan_id}


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", sorted(PLAN4_ENDPOINTS))
@pytest.mark.parametrize("role", rbac.ROLES)
def test_plan4_endpoint_enforces_rbac(client, login_as, seeded_ids, role, endpoint):
    module, verb, url_for, body_for = PLAN4_ENDPOINTS[endpoint]
    login_as(client, role)
    resp = client.post(url_for(seeded_ids), data=json.dumps(body_for(seeded_ids)), content_type="application/json")
    if rbac.can(role, module, verb):
        assert resp.status_code != 403, f"{role} should be allowed {endpoint}, got {resp.status_code}"
    else:
        assert resp.status_code == 403, f"{role} should be denied {endpoint}, got {resp.status_code}"


# --- User Management endpoints ------------------------------------------------
# All three require ("users", "manage") → campaign_admin only. set_role/set_status
# target a DIFFERENT user so the no-self-modify 400 guard can't fire before the
# @require_perm gate. The assertion checks only 403-iff-denied; 200/400 is not
# asserted (matching the convention of the tables above).
TARGET_USERNAME = "target@x.org"

USER_ENDPOINTS = {
    "user_invite": (
        "users",
        "manage",
        lambda _u: reverse("campaign:user_invite"),
        lambda _u: {"name": "P", "email": "probe@x.org", "role": "reporting", "scope": "All regions"},
    ),
    "user_set_role": (
        "users",
        "manage",
        lambda u: reverse("campaign:user_set_role", args=[u]),
        lambda u: {"role": "reporting"},
    ),
    "user_set_status": (
        "users",
        "manage",
        lambda u: reverse("campaign:user_set_status", args=[u]),
        lambda u: {"status": "deactivated"},
    ),
}


@pytest.mark.django_db
@pytest.mark.parametrize("endpoint", sorted(USER_ENDPOINTS))
@pytest.mark.parametrize("role", rbac.ROLES)
def test_user_endpoint_enforces_rbac(client, login_as, db, role, endpoint):
    module, verb, url_for, body_for = USER_ENDPOINTS[endpoint]
    # Target must differ from the logged-in user to avoid triggering self-modify guard.
    CampaignUserFactory(commcare_username=TARGET_USERNAME, email=TARGET_USERNAME, name="T", role="reporting_user")
    login_as(client, role)
    resp = client.post(
        url_for(TARGET_USERNAME),
        data=json.dumps(body_for(TARGET_USERNAME)),
        content_type="application/json",
    )
    if rbac.can(role, module, verb):
        assert resp.status_code != 403, f"{role} should be allowed {endpoint}, got {resp.status_code}"
    else:
        assert resp.status_code == 403, f"{role} should be denied {endpoint}, got {resp.status_code}"
