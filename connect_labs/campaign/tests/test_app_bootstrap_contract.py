"""Contract tests for the inline page bootstrap (`AppView`).

The app page ships a `json_script` bootstrap blob and a `<meta name="csrf-token">`
tag. Two things must hold for every role:
  1. the emitted `perms_matrix` exactly equals `rbac.can(...)` for the logged-in
     role (so the server-computed matrix can become the single source of truth);
  2. the CSRF token is rendered into the page (the only transport, since this
     project uses CSRF_USE_SESSIONS and has no csrftoken cookie).
"""
from __future__ import annotations

import json
import re

import pytest
from django.urls import reverse

from connect_labs.campaign.services import rbac

pytestmark = pytest.mark.contract


def _bootstrap_blob(html: str) -> dict:
    m = re.search(r'<script id="campaign-bootstrap" type="application/json">(.*?)</script>', html, re.DOTALL)
    assert m, "app page must render a #campaign-bootstrap json_script blob"
    return json.loads(m.group(1))


@pytest.mark.django_db
@pytest.mark.parametrize("role", rbac.ROLES)
def test_app_view_perms_matrix_matches_rbac(client, login_as, role):
    login_as(client, role)
    html = client.get(reverse("campaign:app")).content.decode()
    blob = _bootstrap_blob(html)

    assert blob["user"]["role"] == role
    matrix = blob["perms_matrix"]
    for module in rbac.MODULES:
        for verb in rbac.VERBS:
            assert matrix[module][verb] is rbac.can(role, module, verb), f"perms_matrix wrong at {module}:{verb}"


@pytest.mark.django_db
def test_app_page_renders_csrf_meta_token(client, login_as):
    login_as(client, "campaign_admin")
    html = client.get(reverse("campaign:app")).content.decode()
    m = re.search(r'<meta name="csrf-token" content="([^"]+)"', html)
    assert m and len(m.group(1)) >= 30
