"""All api modules must select the SAME campaign as the bootstrap/list views.

Regression guard for the bug where ``workers``/``activities``/``microplans``/
``users`` resolved the campaign with a naive ``Campaign.objects.order_by("id")
.first()`` while the read paths used ``_select_campaign`` (which prefers the
national CommCare-domain campaign). With both campaigns present, a mutation
would land on — or fail to find a worker in — the wrong campaign.
"""

from __future__ import annotations

import pytest
from django.test import RequestFactory

from connect_labs.campaign.api import activities, microplans, users, workers
from connect_labs.campaign.api.bootstrap import _select_campaign
from connect_labs.campaign.models import Campaign, Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def demo_and_national():
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    demo = Campaign.objects.create(workspace=ws, name="Demo", code="DEMO")  # lower id, no domain
    national = Campaign.objects.create(
        workspace=ws, name="National", code="NAT", commcare_domain="campaign-synthetic-nat"
    )
    return demo, national


@pytest.mark.parametrize("module", [workers, activities, microplans, users])
def test_module_campaign_matches_bootstrap_selection(demo_and_national, module):
    demo, national = demo_and_national
    req = RequestFactory().get("/campaign/api/whatever/")
    # Every module's _campaign(request) must agree with the canonical selector,
    # i.e. prefer the national domain campaign — not the first-by-id demo.
    assert module._campaign(req) == national == _select_campaign(req)
