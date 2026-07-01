"""Tests for the labs-only synthetic opportunity feature."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from connect_labs.labs.context import _merge_labs_only_opps, get_org_data
from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.forms import LabsOnlySyntheticOpportunityForm
from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR, SyntheticOpportunity
from connect_labs.users.models import User


@pytest.fixture(autouse=True)
def clear_cache():
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


@pytest.fixture
def dimagi_user(db):
    return User.objects.create_user(
        username="alice",
        email="alice@dimagi.com",
        view_synthetic_opps=True,
    )


@pytest.fixture
def external_user(db):
    return User.objects.create_user(
        username="bob",
        email="bob@external.com",
        view_synthetic_opps=True,
    )


@pytest.fixture
def dimagi_user_toggle_off(db):
    return User.objects.create_user(
        username="carol",
        email="carol@dimagi.com",
        view_synthetic_opps=False,
    )


# ─── opp_id allocation ─────────────────────────────────────────────────────


@pytest.mark.django_db
def test_next_labs_only_opp_id_starts_at_floor():
    assert SyntheticOpportunity.next_labs_only_opp_id() == LABS_ONLY_OPP_ID_FLOOR


@pytest.mark.django_db
def test_next_labs_only_opp_id_increments():
    SyntheticOpportunity.objects.create(opportunity_id=10_000, gdrive_folder_id="f", labs_only=True)
    assert SyntheticOpportunity.next_labs_only_opp_id() == 10_001
    SyntheticOpportunity.objects.create(opportunity_id=10_500, gdrive_folder_id="g", labs_only=True)
    assert SyntheticOpportunity.next_labs_only_opp_id() == 10_501


@pytest.mark.django_db
def test_next_labs_only_opp_id_ignores_real_opps():
    # Real (non-labs-only) opp at 9_999_999 should not affect allocation.
    SyntheticOpportunity.objects.create(opportunity_id=9_999_999, gdrive_folder_id="f", labs_only=False)
    assert SyntheticOpportunity.next_labs_only_opp_id() == LABS_ONLY_OPP_ID_FLOOR


# ─── visibility (domain + toggle) ──────────────────────────────────────────


@pytest.mark.django_db
def test_is_visible_to_requires_labs_only_and_enabled(dimagi_user):
    real_opp = SyntheticOpportunity.objects.create(
        opportunity_id=42, gdrive_folder_id="f", labs_only=False, allowed_domains=["@dimagi.com"]
    )
    assert real_opp.is_visible_to(dimagi_user) is False

    disabled = SyntheticOpportunity.objects.create(
        opportunity_id=10_001,
        gdrive_folder_id="f",
        labs_only=True,
        enabled=False,
        allowed_domains=["@dimagi.com"],
    )
    assert disabled.is_visible_to(dimagi_user) is False


@pytest.mark.django_db
def test_is_visible_to_requires_toggle_on(dimagi_user_toggle_off):
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=10_000, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi.com"]
    )
    assert opp.is_visible_to(dimagi_user_toggle_off) is False


@pytest.mark.django_db
def test_is_visible_to_filters_by_domain(dimagi_user, external_user):
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=10_000, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi.com"]
    )
    assert opp.is_visible_to(dimagi_user) is True
    assert opp.is_visible_to(external_user) is False


@pytest.mark.django_db
def test_is_visible_to_empty_allowlist_means_any_domain(external_user):
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=10_000, gdrive_folder_id="f", labs_only=True, allowed_domains=[]
    )
    assert opp.is_visible_to(external_user) is True


@pytest.mark.django_db
def test_is_visible_to_multiple_domains(external_user):
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        gdrive_folder_id="f",
        labs_only=True,
        allowed_domains=["@dimagi.com", "@external.com"],
    )
    assert opp.is_visible_to(external_user) is True


@pytest.mark.django_db
def test_is_visible_to_dimagi_internal_domains_are_equivalent(dimagi_user, external_user):
    """An @dimagi-ai.com user sees opps allow-listed for @dimagi.com and vice versa;
    a non-Dimagi user is unaffected by the equivalence."""
    ai_user = User.objects.create_user(username="ace", email="ace@dimagi-ai.com", view_synthetic_opps=True)
    # Opp registered for @dimagi.com → visible to the @dimagi-ai.com user (cross-domain).
    opp_core = SyntheticOpportunity.objects.create(
        opportunity_id=10_000, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi.com"]
    )
    assert opp_core.is_visible_to(ai_user) is True
    assert opp_core.is_visible_to(dimagi_user) is True  # exact match still works
    assert opp_core.is_visible_to(external_user) is False  # equivalence is Dimagi-only

    # Reverse: opp registered for @dimagi-ai.com → visible to the @dimagi.com user.
    opp_ai = SyntheticOpportunity.objects.create(
        opportunity_id=10_001, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi-ai.com"]
    )
    assert opp_ai.is_visible_to(dimagi_user) is True
    assert opp_ai.is_visible_to(ai_user) is True
    assert opp_ai.is_visible_to(external_user) is False


# ─── context injection ─────────────────────────────────────────────────────


def _make_request(user, oauth_org_data=None):
    """Build a minimal mock request with session.labs_oauth + user.

    Real User instances already report is_authenticated=True (it's a property
    on AbstractUser), so we just attach the user directly.
    """
    request = MagicMock()
    request.session = {"labs_oauth": {"organization_data": oauth_org_data or {}}}
    request.user = user
    return request


@pytest.mark.django_db
def test_get_org_data_merges_visible_labs_only_opp(dimagi_user):
    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        label="CHC demo",
        org_name="Acme Health",
        program_name="Acme Demo Program",
        gdrive_folder_id="folder-xyz",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
    )
    request = _make_request(
        dimagi_user,
        oauth_org_data={"opportunities": [{"id": 5, "name": "Real Opp"}], "organizations": [], "programs": []},
    )

    org_data = get_org_data(request)

    opp_ids = {o["id"] for o in org_data["opportunities"]}
    assert opp_ids == {5, 10_000}
    labs_opp = next(o for o in org_data["opportunities"] if o["id"] == 10_000)
    assert labs_opp["labs_only"] is True
    assert labs_opp["name"] == "CHC demo"
    # Unset visit_count → picker falls back to 0 (not None).
    assert labs_opp["visit_count"] == 0
    # `organization` carries the org slug (Connect serializer shape), not the name.
    assert labs_opp["organization"] == "labs-synthetic-acme-health"

    org_names = {o["name"] for o in org_data["organizations"]}
    assert "Acme Health" in org_names

    program_names = {p["name"] for p in org_data["programs"]}
    assert "Acme Demo Program" in program_names


@pytest.mark.django_db
def test_get_org_data_surfaces_cached_visit_count(dimagi_user):
    SyntheticOpportunity.objects.create(
        opportunity_id=10_010,
        label="Attakar SD",
        gdrive_folder_id="folder-att",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
        visit_count=435,
    )
    request = _make_request(
        dimagi_user,
        oauth_org_data={"opportunities": [], "organizations": [], "programs": []},
    )

    org_data = get_org_data(request)

    labs_opp = next(o for o in org_data["opportunities"] if o["id"] == 10_010)
    assert labs_opp["visit_count"] == 435


@pytest.mark.django_db
def test_get_org_data_does_not_merge_when_toggle_off(dimagi_user_toggle_off):
    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        gdrive_folder_id="f",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
    )
    request = _make_request(dimagi_user_toggle_off)
    org_data = get_org_data(request)
    assert org_data.get("opportunities", []) == []


@pytest.mark.django_db
def test_get_org_data_does_not_merge_when_domain_mismatch(external_user):
    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        gdrive_folder_id="f",
        labs_only=True,
        allowed_domains=["@dimagi.com"],
    )
    request = _make_request(external_user)
    org_data = get_org_data(request)
    assert org_data.get("opportunities", []) == []


@pytest.mark.django_db
def test_get_org_data_ignores_disabled_labs_only(dimagi_user):
    SyntheticOpportunity.objects.create(
        opportunity_id=10_000,
        gdrive_folder_id="f",
        labs_only=True,
        enabled=False,
        allowed_domains=["@dimagi.com"],
    )
    request = _make_request(dimagi_user)
    assert get_org_data(request).get("opportunities", []) == []


@pytest.mark.django_db
def test_get_org_data_anonymous_user_returns_raw():
    request = MagicMock()
    request.session = {"labs_oauth": {"organization_data": {"opportunities": [{"id": 5}]}}}
    request.user = MagicMock()
    request.user.is_authenticated = False
    org_data = get_org_data(request)
    assert org_data == {"opportunities": [{"id": 5}]}


@pytest.mark.django_db
def test_merge_dedupes_when_opp_id_already_in_org_data(dimagi_user):
    """If the same opp_id is already present (shouldn't normally happen), don't duplicate."""
    SyntheticOpportunity.objects.create(
        opportunity_id=10_000, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi.com"]
    )
    merged = _merge_labs_only_opps(
        {"opportunities": [{"id": 10_000, "name": "Existing"}], "organizations": [], "programs": []},
        dimagi_user,
    )
    ids = [o["id"] for o in merged["opportunities"]]
    assert ids == [10_000]


# ─── registry.accessible_opp_ids ───────────────────────────────────────────


@pytest.mark.django_db
def test_accessible_opp_ids_includes_labs_only(dimagi_user):
    SyntheticOpportunity.objects.create(
        opportunity_id=10_000, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi.com"]
    )
    request = _make_request(dimagi_user)
    assert 10_000 in registry.accessible_opp_ids(request)


# ─── form ──────────────────────────────────────────────────────────────────


@pytest.mark.django_db
def test_labs_only_form_auto_allocates_opp_id():
    form = LabsOnlySyntheticOpportunityForm(
        data={
            "label": "My demo",
            "org_name": "Acme",
            "program_name": "AcmeProg",
            "gdrive_folder_id": "folder-xyz",
            "allowed_domains_input": "@dimagi.com",
            "enabled": "on",
            "notes": "",
        }
    )
    assert form.is_valid(), form.errors
    opp = form.save()
    assert opp.opportunity_id == LABS_ONLY_OPP_ID_FLOOR
    assert opp.labs_only is True
    assert opp.allowed_domains == ["@dimagi.com"]


@pytest.mark.django_db
def test_labs_only_form_validates_domain_format():
    form = LabsOnlySyntheticOpportunityForm(
        data={
            "label": "x",
            "org_name": "x",
            "program_name": "x",
            "gdrive_folder_id": "x",
            "allowed_domains_input": "dimagi.com",  # missing @
        }
    )
    assert not form.is_valid()
    assert "allowed_domains_input" in form.errors


@pytest.mark.django_db
def test_labs_only_form_empty_domains_means_empty_list():
    form = LabsOnlySyntheticOpportunityForm(
        data={
            "label": "x",
            "org_name": "x",
            "program_name": "x",
            "gdrive_folder_id": "x",
            "allowed_domains_input": "",
            "enabled": "on",
        }
    )
    assert form.is_valid(), form.errors
    opp = form.save()
    assert opp.allowed_domains == []
