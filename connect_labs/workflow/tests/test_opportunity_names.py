"""What a run's opportunities are called, for the agent on its page."""

import pytest
from django.contrib.auth import get_user_model

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.workflow import opportunity_names
from connect_labs.workflow.opportunity_names import run_opportunities

pytestmark = pytest.mark.django_db


@pytest.fixture
def viewer():
    return get_user_model().objects.create_user(
        username="viewer", password="p", email="v@dimagi.com", view_synthetic_opps=True
    )


def _labs_only(opp_id, label, org_name):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opp_id, label=label, org_name=org_name, labs_only=True, enabled=True, gdrive_folder_id="g"
    )


def test_a_labs_only_opportunity_is_named_from_its_registry_row(viewer, monkeypatch):
    _labs_only(10131, "[Synthetic] KMC - UG - NAMA - P1 - Apr 26", "Dimagi-KMC (Synthetic)")
    monkeypatch.setattr(
        "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data",
        lambda *a, **k: pytest.fail("no Connect read when every id is named"),
    )
    [opp] = run_opportunities(viewer, [10131], access_token="tok")
    assert opp["id"] == 10131 and opp["synthetic"] is True
    assert opp["name"] == "[Synthetic] KMC - UG - NAMA - P1 - Apr 26"
    assert opp["organization"]  # the registry organisation it is filed under


def test_a_labs_only_opportunity_the_caller_cannot_see_is_not_named():
    _labs_only(10131, "[Synthetic] NAMA", "Dimagi-KMC (Synthetic)")
    stranger = get_user_model().objects.create_user(username="s", password="p", view_synthetic_opps=False)
    [opp] = run_opportunities(stranger, [10131])
    assert opp == {"id": 10131, "name": None, "organization": None, "synthetic": True}


def test_a_real_opportunity_is_named_from_the_callers_connect_org_data(viewer, monkeypatch):
    seen = {}

    def fake(token, owner=None, **kw):
        seen.update(token=token, owner=owner)
        return {
            "organizations": [{"slug": "nama", "name": "NAMA Wellness"}],
            "opportunities": [{"id": 812, "name": "KMC Uganda", "organization": "nama"}, {"id": 9, "name": "Other"}],
        }

    monkeypatch.setattr("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", fake)
    out = run_opportunities(viewer, [812, 813], access_token="tok")
    assert out == [
        {"id": 812, "name": "KMC Uganda", "organization": "NAMA Wellness", "synthetic": False},
        {"id": 813, "name": None, "organization": None, "synthetic": False},
    ]
    assert seen == {"token": "tok", "owner": "viewer"}


def test_a_failed_lookup_leaves_the_ids_unnamed(viewer, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data", boom)
    assert run_opportunities(viewer, [5], access_token="tok") == [
        {"id": 5, "name": None, "organization": None, "synthetic": False}
    ]
    assert opportunity_names.run_opportunities(viewer, []) == []
