"""Parsing the LLO Directory tabs.

Parsing is separated from fetching so it can be tested without a network or a
service account: every test here hands ``parse_*`` the rows a fetch would have
returned. All names and addresses are invented.
"""
from connect_labs.marketplace.directory import parse_contacts, parse_dates, parse_mapping, parse_organizations

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
    "Email addresses from Contacts sheet",
    "EOIs Applied for (Add Link to EOI)",
    "Organization Notes",
    "Latest MSA Link",
    "Latest Work Order Link",
]


class TestParseOrganizations:
    def test_reads_every_column_the_sheet_carries(self):
        rows = [
            ORG_HEADER,
            [
                "Harbourside Health Initiative",
                "HHI",
                "Yes",
                "2011",
                "40",
                "250",
                "Nigeria",
                "Kano, Jigawa",
                "Health; Nutrition",
                "https://example.invalid",
                "12 Example Road",
                "a@example.invalid",
                "Readers EOI",
                "a note",
                "https://msa.invalid",
                "https://wo.invalid",
            ],
        ]
        [org] = parse_organizations(rows)
        assert org.name == "Harbourside Health Initiative"
        assert org.short_name == "HHI"
        assert org.has_used_connect is True
        assert org.year_established == 2011
        assert org.team_size == 40
        assert org.flws_managed == 250
        assert org.countries == ["Nigeria"]
        assert org.regions == ["Kano", "Jigawa"]
        assert org.sectors == ["Health", "Nutrition"]
        assert org.website == "https://example.invalid"
        assert org.msa_link == "https://msa.invalid"
        assert org.source_row == 2

    def test_a_country_name_containing_a_comma_is_not_split(self):
        """ "Congo, the Democratic Republic of the" splits into "Congo", which
        resolves to the OTHER Congo. The quoted whole is one country."""
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", '"Congo, the Democratic Republic of the"']]
        [org] = parse_organizations(rows)
        assert org.countries == ["Congo, the Democratic Republic of the"]

    def test_skips_blank_and_duplicate_names(self):
        rows = [ORG_HEADER, ["Fenwick Trust"], [""], ["Fenwick Trust"], ["   "]]
        assert [o.name for o in parse_organizations(rows)] == ["Fenwick Trust"]

    def test_a_non_numeric_count_becomes_none_rather_than_raising(self):
        """People write "50+" and "approx 200" in number columns."""
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "circa 2010", "", "50+"]]
        [org] = parse_organizations(rows)
        assert org.year_established is None
        assert org.flws_managed is None

    def test_has_used_connect_is_tri_state(self):
        rows = [ORG_HEADER, ["A", "", "Yes"], ["B", "", "No"], ["C", "", ""]]
        assert [o.has_used_connect for o in parse_organizations(rows)] == [True, False, None]


class TestParseContacts:
    def test_reads_a_contact_and_its_organisation(self):
        rows = [
            [
                "Contact Full Name",
                "Organization Name",
                "Role / Title",
                "Main POC?",
                "Email Address",
                "Phone Number",
                "Contact Notes (e.g. best way to contact)",
            ],
            ["A Person", "Fenwick Trust", "Analyst", "Yes", "a@example.invalid", "+10000000000", "email only"],
        ]
        [contact] = parse_contacts(rows)
        assert contact.full_name == "A Person"
        assert contact.org_name == "Fenwick Trust"
        assert contact.is_main_poc is True
        assert contact.email == "a@example.invalid"

    def test_drops_rows_with_no_email(self):
        """The email is the key. A contact without one cannot be deduplicated
        and cannot be written to, so it is not a contact yet."""
        rows = [["Contact Full Name", "Organization Name"], ["A Person", "Fenwick Trust"]]
        assert parse_contacts(rows) == []

    def test_lowercases_the_email_so_case_drift_does_not_duplicate(self):
        rows = [
            ["Contact Full Name", "Organization Name", "Role / Title", "Main POC?", "Email Address"],
            ["A Person", "Fenwick Trust", "", "", "  A@Example.INVALID "],
        ]
        assert parse_contacts(rows)[0].email == "a@example.invalid"


class TestParseDates:
    def test_reads_an_iso_join_date_and_its_basis(self):
        rows = [["Organization Name", "Joined", "Basis"], ["Fenwick Trust", "2025-03-04", "EOI submission"]]
        assert parse_dates(rows) == {"Fenwick Trust": ("2025-03-04", "EOI submission")}

    def test_ignores_a_date_that_is_not_iso(self):
        """The tab holds free text like "Rolling" and "Q2 2025"."""
        rows = [["Organization Name", "Joined"], ["Fenwick Trust", "Q2 2025"]]
        assert parse_dates(rows) == {}


class TestParseMapping:
    def test_carries_a_verdict_with_its_reason(self):
        rows = [
            ["slug"] + [""] * 7 + ["org"],
            ["fenwick-ng", "", "", "", "", "second workspace", "", "", "Fenwick Trust"],
        ]
        mapped, skipped = parse_mapping(rows, {"Fenwick Trust"})
        assert mapped == {"fenwick-ng": ("Fenwick Trust", "second workspace")}
        assert skipped == []

    def test_refuses_a_verdict_with_no_reason(self):
        """An attribution without a stated reason is a guess someone will
        later trust, so it is skipped loudly rather than applied."""
        rows = [["slug"] + [""] * 7 + ["org"], ["fenwick-ng", "", "", "", "", "", "", "", "Fenwick Trust"]]
        mapped, skipped = parse_mapping(rows, {"Fenwick Trust"})
        assert mapped == {}
        assert "no reason given" in skipped[0]

    def test_refuses_a_verdict_pointing_at_an_unknown_organisation(self):
        rows = [["slug"] + [""] * 7 + ["org"], ["fenwick-ng", "", "", "", "", "a reason", "", "", "Ghost Org"]]
        mapped, skipped = parse_mapping(rows, {"Fenwick Trust"})
        assert mapped == {}
        assert "not on the Organizations tab" in skipped[0]
