"""The data migration that carries existing partners into the registry.

The labs database holds real PulsePartner rows in production. Dropping the table
without carrying them would lose every join date on the network growth curve —
dates that are directory facts Connect has never held and cannot regenerate.
"""
import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.carry import carry_partners
from connect_labs.marketplace.models import OrgConnectSlug, OrgProfile


@pytest.mark.django_db
class TestCarryPartners:
    def test_carries_a_partner_into_an_org_and_profile(self):
        rows = [
            {
                "name": "Harbourside Health Initiative",
                "short": "HHI",
                "joined_at": "2025-03-04",
                "joined_basis": "EOI submission",
                "country_iso3": "NGA",
                "lat": 12.0,
                "lon": 8.5,
                "location_precision": "city",
                "location_label": "Kano",
            }
        ]
        carry_partners(rows, aliases=[])

        org = LabsOrg.objects.get(name="Harbourside Health Initiative")
        assert org.short_name == "HHI"
        profile = OrgProfile.objects.get(org=org)
        assert str(profile.joined_at) == "2025-03-04"
        assert profile.country_iso3 == "NGA"
        assert profile.location_precision == "city"

    def test_carries_an_alias_with_its_reason(self):
        rows = [{"name": "Harbourside Health Initiative"}]
        carry_partners(
            rows,
            aliases=[
                {
                    "slug": "harbourside-ng",
                    "partner_name": "Harbourside Health Initiative",
                    "why": "second workspace, confirmed by hand",
                }
            ],
        )
        mapping = OrgConnectSlug.objects.get(slug="harbourside-ng")
        assert mapping.org.name == "Harbourside Health Initiative"
        assert mapping.why == "second workspace, confirmed by hand"

    def test_is_idempotent(self):
        rows = [{"name": "Harbourside Health Initiative"}]
        carry_partners(rows, aliases=[])
        carry_partners(rows, aliases=[])
        assert LabsOrg.objects.filter(name="Harbourside Health Initiative").count() == 1

    def test_an_alias_whose_partner_vanished_is_dropped_not_guessed(self):
        """A wrong attribution files one organisation's history under another's
        name, so an unresolvable alias is dropped rather than approximated."""
        carry_partners(
            [{"name": "Harbourside Health Initiative"}],
            aliases=[{"slug": "ghost", "partner_name": "Ghost Org", "why": "a reason"}],
        )
        assert not OrgConnectSlug.objects.filter(slug="ghost").exists()

    def test_an_alias_with_no_reason_is_dropped(self):
        carry_partners(
            [{"name": "Harbourside Health Initiative"}],
            aliases=[{"slug": "unreasoned", "partner_name": "Harbourside Health Initiative", "why": ""}],
        )
        assert not OrgConnectSlug.objects.filter(slug="unreasoned").exists()
