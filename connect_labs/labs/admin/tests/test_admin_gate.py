"""Access-control tests for Labs Admin (formerly Explorer).

The Admin tooling performs destructive/operational actions and must be gated to
Dimagi staff via ``AdminRequiredMixin``. The boundary *management* views are
gated too, but the read-only boundary APIs consumed by microplans
(``countries_api``, ``coverage_api``, ``resolve_many``) must stay reachable by
any logged-in user so those cross-app flows don't break.
"""

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from connect_labs.labs.tests.test_settings import LABS_SETTINGS


@pytest.fixture
def dimagi_user(db):
    return get_user_model().objects.create_user(username="staff", email="staff@dimagi.com", password="pw")


@pytest.fixture
def external_user(db):
    return get_user_model().objects.create_user(username="ext", email="partner@external.com", password="pw")


@override_settings(**LABS_SETTINGS)
@pytest.mark.parametrize(
    "url_name",
    [
        "labs_admin:index",
        "labs_admin:list",
        "labs_admin:visit_inspector",
        "labs_admin:cache_manager",
        "labs_admin:task_manager",
        "labs_admin:app_downloader",
        "labs_admin:admin_boundaries:index",
        "labs_admin:schedules",
    ],
)
def test_admin_tools_forbidden_for_external_user(client, external_user, url_name):
    client.force_login(external_user)
    resp = client.get(reverse(url_name))
    assert resp.status_code == 403, f"{url_name} should 403 for non-Dimagi user"


@override_settings(**LABS_SETTINGS)
def test_admin_index_allowed_for_dimagi_user(client, dimagi_user):
    client.force_login(dimagi_user)
    resp = client.get(reverse("labs_admin:index"))
    assert resp.status_code == 200


@override_settings(**LABS_SETTINGS)
def test_admin_index_redirects_anonymous(client, db):
    resp = client.get(reverse("labs_admin:index"))
    # LoginRequiredMixin redirects unauthenticated users to the login page.
    assert resp.status_code == 302
    assert "/labs/login/" in resp.url


@override_settings(**LABS_SETTINGS)
def test_shared_boundary_api_still_reachable_for_external_user(client, external_user):
    """countries_api backs the microplans bulk-create flow — must not be gated."""
    client.force_login(external_user)
    resp = client.get(reverse("labs_admin:admin_boundaries:countries_api"))
    assert resp.status_code != 403
