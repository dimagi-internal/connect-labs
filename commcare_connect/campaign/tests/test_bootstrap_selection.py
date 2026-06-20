"""Bootstrap campaign-selection tests (which campaign the tool shows)."""
from __future__ import annotations

import pytest
from django.test import RequestFactory

from commcare_connect.campaign.api import bootstrap
from commcare_connect.campaign.models import Campaign, Workspace

pytestmark = pytest.mark.django_db


@pytest.fixture
def two_campaigns():
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    demo = Campaign.objects.create(workspace=ws, name="Demo", code="DEMO")  # no domain, lower id
    national = Campaign.objects.create(
        workspace=ws, name="National", code="NAT", commcare_domain="campaign-synthetic-nat"
    )
    return demo, national


def test_prefers_the_commcare_domain_campaign(two_campaigns):
    demo, national = two_campaigns
    req = RequestFactory().get("/campaign/api/bootstrap/")
    assert bootstrap._select_campaign(req) == national


def test_explicit_campaign_code_wins(two_campaigns):
    demo, national = two_campaigns
    req = RequestFactory().get("/campaign/api/bootstrap/?campaign=DEMO")
    assert bootstrap._select_campaign(req) == demo


def test_falls_back_to_first_when_no_domain_campaign():
    ws = Workspace.objects.create(slug="nigeria", country="Nigeria", name="Nigeria")
    demo = Campaign.objects.create(workspace=ws, name="Demo", code="DEMO")
    req = RequestFactory().get("/campaign/api/bootstrap/")
    assert bootstrap._select_campaign(req) == demo


def test_unknown_code_falls_through(two_campaigns):
    demo, national = two_campaigns
    req = RequestFactory().get("/campaign/api/bootstrap/?campaign=NOPE")
    assert bootstrap._select_campaign(req) == national  # falls back to domain preference
