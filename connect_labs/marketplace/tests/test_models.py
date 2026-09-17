"""The satellite tables that hang off LabsOrg.

Every fact here is invented. The rule these tests defend is that LabsOrg stays
identity-only, so that when Connect grows an organisation model the migration is
a repointed foreign key rather than a redesign.
"""

import pytest
from django.db import IntegrityError

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgConnectSlug, OrgContact, OrgProfile


@pytest.fixture
def org(db):
    return LabsOrg.objects.create(slug="harbourside-health", name="Harbourside Health Initiative")


class TestOrgProfile:
    def test_holds_directory_facts_without_touching_labsorg(self, org):
        profile = OrgProfile.objects.create(
            org=org,
            has_used_connect=True,
            year_established=2011,
            countries=["NG", "KE"],
            website="https://example.invalid",
        )
        assert profile.org_id == org.pk
        assert org.marketplace_profile == profile
        # The constraint that makes this whole design migratable: directory
        # facts hang off the profile, never off LabsOrg itself.
        assert not hasattr(org, "countries")

    def test_is_one_to_one(self, org):
        OrgProfile.objects.create(org=org)
        with pytest.raises(IntegrityError):
            OrgProfile.objects.create(org=org)


class TestOrgContact:
    def test_one_row_per_email_per_org(self, org):
        OrgContact.objects.create(org=org, full_name="A Person", email="a@example.invalid")
        with pytest.raises(IntegrityError):
            OrgContact.objects.create(org=org, full_name="Same Person Again", email="a@example.invalid")

    def test_same_email_may_appear_at_a_different_org(self, org, db):
        other = LabsOrg.objects.create(slug="fenwick-trust", name="Fenwick Trust")
        OrgContact.objects.create(org=org, full_name="A Person", email="a@example.invalid")
        OrgContact.objects.create(org=other, full_name="A Person", email="a@example.invalid")
        assert OrgContact.objects.filter(email="a@example.invalid").count() == 2


class TestOrgConnectSlug:
    def test_requires_a_reason(self, org):
        """A slug attributed to an organisation without a stated reason is a
        guess someone will later trust. Pulse learned this; it is carried here."""
        mapping = OrgConnectSlug(org=org, slug="harbourside-ng", why="")
        with pytest.raises(ValueError):
            mapping.save()

    def test_slug_is_unique_across_labs(self, org, db):
        other = LabsOrg.objects.create(slug="fenwick-trust", name="Fenwick Trust")
        OrgConnectSlug.objects.create(org=org, slug="shared-slug", why="confirmed by hand")
        with pytest.raises(IntegrityError):
            OrgConnectSlug.objects.create(org=other, slug="shared-slug", why="confirmed by hand")
