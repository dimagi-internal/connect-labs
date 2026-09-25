"""The importer, exercised over rows rather than over a network.

``import_directory`` takes the four tabs' rows as lists, so every behaviour
below is tested without a service account. All data is invented.
"""

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.management.commands.marketplace_import import import_directory
from connect_labs.marketplace.models import OrgConnectSlug, OrgContact, OrgProfile

ORG_HEADER = [
    "Organization Name",
    "Short Name",
    "Has Used Connect",
    "Year of Establishment",
    "Org Team Size",
    "No. of FLWs Managed",
    "Countries of Operation",
    "Regions/States of Operation",
    "Primary Sector(s)",
    "Website",
    "Office Address",
    "Emails",
    "EOIs",
    "Organization Notes",
    "MSA",
    "Work Order",
]
CONTACT_HEADER = [
    "Contact Full Name",
    "Organization Name",
    "Role / Title",
    "Main POC?",
    "Email Address",
    "Phone Number",
    "Notes",
]
DATE_HEADER = ["Organization Name", "Joined", "Basis"]
MAP_HEADER = ["slug"] + [""] * 7 + ["org"]

ORGS = [
    ORG_HEADER,
    [
        "Harbourside Health Initiative",
        "HHI",
        "Yes",
        "2011",
        "40",
        "250",
        "Nigeria",
        "Kano",
        "Health",
        "https://example.invalid",
        "12 Example Road",
        "",
        "",
        "a note",
        "",
        "",
    ],
    ["Fenwick Trust", "FT", "No", "", "", "80", "Kenya", "", "Health", "", "", "", "", "", "", ""],
]
CONTACTS = [
    CONTACT_HEADER,
    ["A Person", "Harbourside Health Initiative", "Director", "Yes", "a@example.invalid", "", ""],
    ["B Person", "Harbourside Health Initiative", "Analyst", "", "b@example.invalid", "", ""],
]
DATES = [DATE_HEADER, ["Harbourside Health Initiative", "2025-03-04", "EOI submission"]]
MAPPING = [MAP_HEADER, ["harbourside-ng", "", "", "", "", "second workspace", "", "", "Harbourside Health Initiative"]]


@pytest.mark.django_db
class TestImportDirectory:
    def test_creates_orgs_profiles_contacts_and_attributions(self):
        stats = import_directory(ORGS, CONTACTS, DATES, MAPPING)
        assert LabsOrg.objects.count() == 2
        assert OrgProfile.objects.count() == 2
        assert OrgContact.objects.count() == 2
        assert OrgConnectSlug.objects.count() == 1
        assert stats["organisations"] == 2

        org = LabsOrg.objects.get(name="Harbourside Health Initiative")
        assert org.marketplace_profile.joined_at.isoformat() == "2025-03-04"
        assert org.marketplace_profile.joined_basis == "EOI submission"
        assert org.contacts.get(is_main_poc=True).email == "a@example.invalid"

    def test_is_idempotent(self):
        """The command runs daily on a beat. A second run must change nothing."""
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        assert LabsOrg.objects.count() == 2
        assert OrgProfile.objects.count() == 2
        assert OrgContact.objects.count() == 2
        assert OrgConnectSlug.objects.count() == 1

    def test_does_not_delete_by_default(self):
        """A half-read sheet must never look like a deletion."""
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        import_directory([ORG_HEADER, ORGS[1]], [CONTACT_HEADER], [DATE_HEADER], [MAP_HEADER])
        assert LabsOrg.objects.count() == 2
        assert OrgContact.objects.count() == 2

    def test_prune_deletes_what_the_sheet_no_longer_carries(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        import_directory([ORG_HEADER, ORGS[1]], [CONTACT_HEADER], [DATE_HEADER], [MAP_HEADER], prune=True)
        assert [o.name for o in LabsOrg.objects.all()] == ["Harbourside Health Initiative"]

    def test_prune_keeps_an_organisation_that_is_also_a_supplier(self):
        """Dropping out of the directory ends its directory profile, not the company.

        A supplier is a company, and quotes, orders and payments hang off it.
        The prune used to delete the organisation outright, which a supplier
        link now protects -- so it crashed the whole import. It keeps the
        organisation and drops only what the directory put there.
        """
        from connect_labs.supply_chain.models import Supplier

        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        dropped = LabsOrg.objects.exclude(name="Harbourside Health Initiative").get()
        Supplier.objects.enrol("prog:10501", org=dropped)

        stats = import_directory([ORG_HEADER, ORGS[1]], [CONTACT_HEADER], [DATE_HEADER], [MAP_HEADER], prune=True)

        assert LabsOrg.objects.filter(pk=dropped.pk).exists()
        assert not OrgProfile.objects.filter(org=dropped).exists()
        assert stats["pruned_organisations"] == 1
        assert stats["kept_in_use"] == 1

    def test_refuses_an_empty_roster(self):
        """An empty read is a failed read, not an empty directory."""
        with pytest.raises(ValueError, match="refusing"):
            import_directory([ORG_HEADER], [CONTACT_HEADER], [DATE_HEADER], [MAP_HEADER])

    def test_a_contact_for_an_unknown_organisation_is_skipped_not_invented(self):
        """Creating an org from a contact row would let a typo in the Contacts
        tab mint an organisation that does not exist."""
        contacts = [CONTACT_HEADER, ["C Person", "Ghost Org", "", "", "c@example.invalid", "", ""]]
        stats = import_directory(ORGS, contacts, DATES, MAPPING)
        assert LabsOrg.objects.count() == 2
        assert OrgContact.objects.count() == 0
        assert any("Ghost Org" in line for line in stats["skipped"])

    def test_an_attribution_without_a_reason_is_refused(self):
        mapping = [MAP_HEADER, ["harbourside-ng", "", "", "", "", "", "", "", "Harbourside Health Initiative"]]
        stats = import_directory(ORGS, CONTACTS, DATES, mapping)
        assert OrgConnectSlug.objects.count() == 0
        assert any("no reason given" in line for line in stats["skipped"])

    def test_a_contact_that_moved_organisations_does_not_linger_at_the_old_one(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        moved = [
            CONTACT_HEADER,
            ["A Person", "Fenwick Trust", "Director", "Yes", "a@example.invalid", "", ""],
            ["B Person", "Harbourside Health Initiative", "Analyst", "", "b@example.invalid", "", ""],
        ]
        import_directory(ORGS, moved, DATES, MAPPING, prune=True)
        assert OrgContact.objects.get(email="a@example.invalid").org.name == "Fenwick Trust"
        assert OrgContact.objects.count() == 2


@pytest.mark.django_db
class TestJoinDateStamping:
    """An organisation the directory never dated still belongs on the network
    growth curve — a behaviour carried over from pulse_partner_import."""

    def test_an_undated_organisation_is_stamped_with_today(self):
        from django.utils import timezone

        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        fenwick = LabsOrg.objects.get(name="Fenwick Trust")
        assert fenwick.marketplace_profile.joined_at == timezone.localdate()

    def test_a_stamped_date_never_moves_on_a_later_run(self):
        """Recomputing it each run would slide the whole undated cohort forward
        every day the beat fires."""
        import datetime as dt

        from django.utils import timezone

        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        profile = LabsOrg.objects.get(name="Fenwick Trust").marketplace_profile
        backdated = timezone.localdate() - dt.timedelta(days=30)
        OrgProfile.objects.filter(pk=profile.pk).update(joined_at=backdated)

        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        profile.refresh_from_db()
        assert profile.joined_at == backdated

    def test_a_dated_organisation_keeps_the_directory_date(self):
        import_directory(ORGS, CONTACTS, DATES, MAPPING)
        harbourside = LabsOrg.objects.get(name="Harbourside Health Initiative")
        assert harbourside.marketplace_profile.joined_at.isoformat() == "2025-03-04"


class TestTheBeatPullsEverythingTheSheetOwns:
    def test_the_daily_import_includes_the_rounds(self, monkeypatch):
        """The rounds tab carries decisions only the directory team can make —
        a round's Connect program, its response sheet link. Pulling
        organisations daily but leaving rounds behind a flag meant either
        correction could sit unread in the sheet indefinitely.
        """
        from connect_labs.pulse import tasks

        called = {}
        monkeypatch.setattr(
            "django.core.management.call_command",
            lambda name, **kwargs: called.update({"name": name} | kwargs),
        )
        tasks.import_partner_directory()

        assert called == {"name": "marketplace_import", "eoi": True}
