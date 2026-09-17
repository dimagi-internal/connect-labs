"""What the importer could not read cleanly.

Every finding must carry a row number: "some numbers did not parse" is not
actionable, "row 14, Org Team Size, '50+'" is. All data invented.
"""

from connect_labs.marketplace.directory import parse_contacts, parse_organizations
from connect_labs.marketplace.quality import audit

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


def _audit(org_rows, contact_rows, skipped=()):
    orgs = parse_organizations(org_rows)
    contacts = parse_contacts(contact_rows)
    return audit(org_rows, contact_rows, orgs, contacts, list(skipped))


def _kinds(findings):
    return {f.kind for f in findings}


class TestNumberColumns:
    def test_reports_an_unparseable_count_with_its_row_and_value(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "50+", "", "Kenya"]]
        [finding] = [f for f in _audit(rows, [CONTACT_HEADER]) if f.kind == "unparsed_number"]
        assert finding.row == 2
        assert "50+" in finding.detail
        assert "Org Team Size" in finding.detail

    def test_an_empty_number_column_is_not_a_finding(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", "Kenya"]]
        assert "unparsed_number" not in _kinds(_audit(rows, [CONTACT_HEADER]))


class TestNearDuplicateOrganisations:
    def test_reports_two_rows_differing_only_by_case_or_spacing(self):
        rows = [
            ORG_HEADER,
            ["Fenwick Trust", "", "", "", "", "", "Kenya"],
            ["  fenwick   trust ", "", "", "", "", "", "Kenya"],
        ]
        [finding] = [f for f in _audit(rows, [CONTACT_HEADER]) if f.kind == "near_duplicate_org"]
        assert finding.row == 3
        assert "row 2" in finding.detail

    def test_genuinely_different_names_are_not_flagged(self):
        rows = [
            ORG_HEADER,
            ["Fenwick Trust", "", "", "", "", "", "Kenya"],
            ["Fenwick Trust International", "", "", "", "", "", "Kenya"],
        ]
        assert "near_duplicate_org" not in _kinds(_audit(rows, [CONTACT_HEADER]))


class TestCountries:
    def test_reports_an_organisation_with_no_country(self):
        rows = [ORG_HEADER, ["Fenwick Trust"]]
        assert "missing_country" in _kinds(_audit(rows, [CONTACT_HEADER]))


class TestContacts:
    def test_reports_a_contact_row_with_no_email(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", "Kenya"]]
        contacts = [CONTACT_HEADER, ["A Person", "Fenwick Trust", "Analyst", "", "", "", ""]]
        [finding] = [f for f in _audit(rows, contacts) if f.kind == "contact_without_email"]
        assert finding.row == 2

    def test_reports_an_address_that_is_not_an_email(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", "Kenya"]]
        contacts = [CONTACT_HEADER, ["A Person", "Fenwick Trust", "", "", "not-an-address", "", ""]]
        assert "malformed_email" in _kinds(_audit(rows, contacts))

    def test_reports_an_organisation_with_no_contact_at_all(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", "Kenya"]]
        assert "org_without_contact" in _kinds(_audit(rows, [CONTACT_HEADER]))

    def test_an_org_with_a_contact_is_not_flagged(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", "Kenya"]]
        contacts = [CONTACT_HEADER, ["A Person", "Fenwick Trust", "", "", "a@example.invalid", "", ""]]
        assert "org_without_contact" not in _kinds(_audit(rows, contacts))


class TestSkippedRowsAreCarriedThrough:
    def test_importer_refusals_appear_as_findings(self):
        rows = [ORG_HEADER, ["Fenwick Trust", "", "", "", "", "", "Kenya"]]
        findings = _audit(rows, [CONTACT_HEADER], skipped=["fenwick-ng → 'Ghost' (no reason given)"])
        assert "refused" in _kinds(findings)
