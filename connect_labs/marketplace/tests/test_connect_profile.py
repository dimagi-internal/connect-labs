"""Connect's profile wins over the directory's, for a linked organisation.

All data is invented.
"""

import pytest
from django.utils import timezone

from connect_labs.labs.models import ConnectOrganization, LabsOrg
from connect_labs.marketplace import connect_profile
from connect_labs.marketplace.directory import team_size_bracket
from connect_labs.marketplace.management.commands.marketplace_import import import_directory
from connect_labs.marketplace.models import OrgProfile
from connect_labs.marketplace.tests.test_import import CONTACTS, DATES, MAPPING, ORGS

pytestmark = pytest.mark.django_db

HARBOURSIDE = "Harbourside Health Initiative"


def _connect_profile(connect_id=501, **fields):
    defaults = {
        "slug": "harbourside-ng",
        "name": HARBOURSIDE,
        "last_seen_at": timezone.now(),
        "has_profile": True,
        "year_of_establishment": 2009,
        "team_size": "51-200",
        "countries": ["KEN"],
        "website": "https://harbourside.invalid",
    }
    defaults.update(fields)
    return ConnectOrganization.objects.create(connect_id=connect_id, **defaults)


def _link(name=HARBOURSIDE, connect_id=501):
    LabsOrg.objects.filter(name=name).update(connect_organization_id=connect_id)


class TestConnectWins:
    def test_a_linked_organisation_reads_connects_profile(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        _connect_profile()
        _link()

        assert connect_profile.apply_all() == 1

        profile = OrgProfile.objects.get(org__name=HARBOURSIDE)
        assert profile.year_established == 2009
        assert profile.team_size == "51-200"
        assert profile.countries == ["Kenya"], "ISO codes read as the names the directory uses"
        assert profile.website == "https://harbourside.invalid"
        assert profile.notes == "a note", "what Connect has no column for stays the directory's"

    def test_a_blank_on_connects_side_keeps_the_sheets_value(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        _connect_profile(year_of_establishment=None, team_size="", countries=[], website="")
        _link()

        connect_profile.apply_all()

        profile = OrgProfile.objects.get(org__name=HARBOURSIDE)
        assert (profile.year_established, profile.team_size, profile.countries) == (2011, "11-50", ["Nigeria"])

    def test_connects_default_false_does_not_overwrite_a_yes(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        _connect_profile(has_used_connect=False)
        _link()

        connect_profile.apply_all()

        assert OrgProfile.objects.get(org__name=HARBOURSIDE).has_used_connect is True

    def test_the_next_directory_import_does_not_revert_it(self):
        """The import runs daily and writes the sheet's row first; Connect must
        still win when it is done, whichever of the two ran last."""
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        _connect_profile()
        _link()
        connect_profile.apply_all()

        import_directory(ORGS, CONTACTS, DATES, MAPPING)

        assert OrgProfile.objects.get(org__name=HARBOURSIDE).year_established == 2009

    def test_an_unlinked_or_merged_away_organisation_keeps_the_sheet(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        _connect_profile(gone_at=timezone.now())
        _link()

        assert connect_profile.apply_all() == 0
        assert OrgProfile.objects.get(org__name="Fenwick Trust").countries == ["Kenya"]
        assert OrgProfile.objects.get(org__name=HARBOURSIDE).year_established == 2011


class TestTeamSizeBrackets:
    @pytest.mark.parametrize(
        "raw, bracket",
        [("1", "1-10"), ("10", "1-10"), ("11", "11-50"), ("200", "51-200"), ("500", "201-500"), ("501", "500+")],
    )
    def test_a_headcount_falls_in_connects_bracket(self, raw, bracket):
        assert team_size_bracket(raw) == bracket

    @pytest.mark.parametrize("raw", ["", "0", "50+", "about forty", None])
    def test_anything_else_is_blank(self, raw):
        assert team_size_bracket(raw) == ""

    def test_a_bracket_typed_into_the_sheet_is_kept(self):
        assert team_size_bracket("51-200") == "51-200"
