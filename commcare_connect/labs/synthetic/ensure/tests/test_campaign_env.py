"""The campaign-utility-tool env realizes a national synthetic campaign via the
standard ensure engine (synthetic_env_ensure path)."""
from __future__ import annotations

import pytest

from commcare_connect.campaign.models import Campaign, WorkerCase
from commcare_connect.campaign.services import dev_boundaries
from commcare_connect.labs.synthetic.ensure.engine import ensure_synthetic_data
from commcare_connect.labs.synthetic.ensure.registry import get_env_path, list_envs

pytestmark = pytest.mark.django_db


def test_campaign_env_is_discovered():
    keys = {e["env"] for e in list_envs()}
    assert "campaign-utility-tool" in keys


def test_ensure_realizes_the_campaign():
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    path = str(get_env_path("campaign-utility-tool"))
    realized = ensure_synthetic_data(path)
    # the campaign ensurer's marker is present and the campaign is built
    marker = realized["campaign_MR-NAT-2026"]
    assert marker["code"] == "MR-NAT-2026"
    assert marker["commcare_domain"] == "campaign-synthetic-mr-nat-2026"
    assert marker["workers"] == 5000
    assert Campaign.objects.filter(code="MR-NAT-2026").exists()
    assert WorkerCase.objects.filter(campaign__code="MR-NAT-2026").count() == 5000


def test_ensure_is_idempotent():
    dev_boundaries.seed_demo_boundaries(lgas_per_state=1, wards_per_lga=1)
    path = str(get_env_path("campaign-utility-tool"))
    ensure_synthetic_data(path)
    ensure_synthetic_data(path)  # re-run — rebuilds in place, no duplicate
    assert Campaign.objects.filter(code="MR-NAT-2026").count() == 1
    assert WorkerCase.objects.filter(campaign__code="MR-NAT-2026").count() == 5000
