"""The one organisation registry.

Labs had three — `supply_chain.Party`, `supply_chain.Supplier`, and
`pulse.PulsePartner` — all meaning "an organisation in real life", each with
its own way of joining to Connect, none aware of the others. This is the
table they collapse into, and the tests here are about the matching rule,
because that is the part of a reconciliation design that decides whether it
works.
"""

import pytest

from connect_labs.labs.models import LabsOrg

pytestmark = pytest.mark.django_db


def _org(**overrides):
    fields = {"slug": "dabs", "name": "Dabs Nutritional Products"}
    fields.update(overrides)
    return LabsOrg.objects.create(**fields)


class TestLinking:
    def test_an_organisation_needs_no_connect_row_to_exist(self):
        """The whole point. A manufacturer in Norway has no Connect account
        and no reason to get one, and labs must still be able to name it
        without waiting for production."""
        org = _org(slug="nutriset", name="Nutriset", country="FR")
        assert org.pk and org.is_linked is False

    def test_linking_is_an_update_rather_than_a_migration(self):
        org = _org()
        org.connect_organization_id = 42
        org.save()
        assert org.is_linked is True


class TestMatching:
    def test_the_id_is_the_identity(self):
        org = _org(connect_organization_id=42, connect_organization_slug="dabs-ng")
        assert org.matches(organization_id=42)
        assert not org.matches(organization_id=43)

    def test_a_stale_slug_does_not_outvote_the_id(self):
        """A rename changes the slug and not the id, so a row that is already
        linked is matched on the id -- otherwise a rename would look like a
        different organisation."""
        org = _org(connect_organization_id=42, connect_organization_slug="old-name")
        assert org.matches(organization_id=42, slug="new-name")

    def test_the_slug_matches_only_a_row_with_no_id_yet(self):
        """Its one job: linking a local row to the Connect org it turns out
        to be."""
        unlinked = _org(slug="eha", name="EHA Clinics", connect_organization_slug="eha-clinics")
        assert unlinked.matches(slug="eha-clinics")

    def test_a_curated_alias_matches_too(self):
        """Pulse needed these because a handful of real partners fall through
        any strict matcher, and a wrong parent name is worse than a visible
        slug."""
        org = _org(connect_organization_slug="eha-clinics", aliases=["eha-pharma", "eha-ng"])
        assert org.matches(slug="eha-pharma")
        assert not org.matches(slug="someone-else")

    def test_nothing_matches_on_nothing(self):
        """An empty slug must not match a row whose own slug is empty, or
        every unlinked organisation becomes the same organisation."""
        org = _org(connect_organization_slug="")
        assert not org.matches()
        assert not org.matches(slug="")


class TestOneRegistry:
    def test_a_slug_is_unique_across_labs(self):
        """Not per programme. An organisation is the same organisation in
        every programme it appears in -- scoping it per programme is what
        produced three registries of the same thing."""
        from django.db import IntegrityError

        _org(slug="dabs")
        with pytest.raises(IntegrityError):
            _org(slug="dabs", name="A different Dabs")

    def test_one_connect_org_cannot_be_claimed_twice(self):
        """Two local rows resolving to one Connect id is the conflict the
        matching rule says to report rather than merge. The database refuses
        it outright, which is a better place to find out."""
        from django.db import IntegrityError

        _org(slug="a", connect_organization_id=42)
        with pytest.raises(IntegrityError):
            _org(slug="b", connect_organization_id=42)
