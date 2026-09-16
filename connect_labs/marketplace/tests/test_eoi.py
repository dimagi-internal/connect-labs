"""Importing rounds and their submissions. All data invented."""
import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.directory import parse_rounds
from connect_labs.marketplace.eoi import check_access, ingest_round, upsert_rounds
from connect_labs.marketplace.models import OrgContact
from connect_labs.solicitations.local_models import ACCESS_DENIED, ACCESS_MISSING, ACCESS_OK, Solicitation

HEADER = [
    "Slug",
    "Name",
    "Type",
    "Status",
    "Published Date",
    "Application Deadline",
    "Decision",
    "Start",
    "End",
    "Countries",
    "Announcement",
    "Form",
    "Response Sheet",
    "Response Tab",
    "Column Map",
    "Labs Access",
    "Checked",
    "Notes",
]
SHEET = "https://docs.google.com/spreadsheets/d/1abcDEFghiJKLmnoPQRstuVWxyz0123456789"

ROUND_ROWS = [
    HEADER,
    [
        "demo-2026",
        "Demo round",
        "EOI",
        "Closed",
        "1 March 2026",
        "1 April 2026",
        "",
        "",
        "",
        "Kenya",
        "",
        "",
        SHEET,
        "Form Responses 1",
        "",
        "",
        "",
        "",
    ],
]

RESPONSES = [
    ["Timestamp", "Organization name", "Valid email address", "Country"],
    ["3/4/2026 09:00:00", "Fenwick Trust", "a@example.invalid", "Kenya"],
    ["3/5/2026 09:00:00", "Unknown Body", "z@example.invalid", "Kenya"],
]


@pytest.fixture
def round_(db):
    rounds, _ = parse_rounds(ROUND_ROWS)
    upsert_rounds(rounds)
    return Solicitation.objects.get(slug="demo-2026")


@pytest.mark.django_db
class TestUpsertRounds:
    def test_creates_a_round_with_its_dates_and_sheet(self, round_):
        assert round_.title == "Demo round"
        assert round_.solicitation_type == "eoi"
        assert round_.published_on.isoformat() == "2026-03-01"
        assert round_.response_spreadsheet_id == "1abcDEFghiJKLmnoPQRstuVWxyz0123456789"

    def test_is_idempotent(self, round_):
        rounds, _ = parse_rounds(ROUND_ROWS)
        upsert_rounds(rounds)
        assert Solicitation.objects.count() == 1

    def test_a_free_text_date_becomes_none_rather_than_raising(self, db):
        rows = [
            HEADER,
            [
                "rolling-2026",
                "Rolling",
                "EOI",
                "Closed",
                "Rolling",
                "Open ended",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ],
        ]
        rounds, _ = parse_rounds(rows)
        upsert_rounds(rounds)
        assert Solicitation.objects.get(slug="rolling-2026").published_on is None


@pytest.mark.django_db
class TestCheckAccess:
    def test_records_a_readable_sheet(self, round_):
        [result] = check_access(lambda sid, tab: [["Timestamp"]])
        assert result["state"] == ACCESS_OK
        round_.refresh_from_db()
        assert round_.sa_access_state == ACCESS_OK
        assert round_.sa_access_checked_at is not None

    def test_records_a_refusal_with_its_reason(self, round_):
        def denied(sid, tab):
            raise PermissionError("403 caller does not have permission")

        [result] = check_access(denied)
        assert result["state"] == ACCESS_DENIED
        assert "403" in result["detail"]

    def test_a_round_with_no_sheet_is_missing_not_denied(self, db):
        rows = [
            HEADER,
            ["nosheet", "No sheet", "EOI", "Closed", "", "", "", "", "", "", "", "", "", "", "", "", "", ""],
        ]
        rounds, _ = parse_rounds(rows)
        upsert_rounds(rounds)
        results = {r["slug"]: r["state"] for r in check_access(lambda sid, tab: [])}
        assert results["nosheet"] == ACCESS_MISSING


@pytest.mark.django_db
class TestIngestRound:
    def test_stores_submissions_and_matches_a_known_contact(self, round_):
        org = LabsOrg.objects.create(slug="fenwick", name="Fenwick Trust")
        OrgContact.objects.create(org=org, email="a@example.invalid", full_name="A Person")

        stats = ingest_round(round_, RESPONSES)
        assert stats["submissions"] == 2
        assert stats["matched"] == 1
        assert stats["unmatched"] == 1
        assert round_.responses.get(source_row=2).llo_entity == org

    def test_an_unknown_applicant_is_kept_and_left_unmatched(self, round_):
        ingest_round(round_, RESPONSES)
        unmatched = round_.responses.get(source_row=3)
        assert unmatched.llo_entity is None
        assert unmatched.org_name == "Unknown Body"
        assert unmatched.responses  # its answers are kept regardless

    def test_records_the_question_list_on_the_round(self, round_):
        ingest_round(round_, RESPONSES)
        round_.refresh_from_db()
        assert [q["text"] for q in round_.questions][:2] == ["Timestamp", "Organization name"]

    def test_is_idempotent(self, round_):
        ingest_round(round_, RESPONSES)
        ingest_round(round_, RESPONSES)
        assert round_.responses.count() == 2

    def test_marks_the_round_ingested_so_empty_is_distinguishable(self, round_):
        """ "No applicants" and "never read" must not look the same."""
        assert round_.was_ingested is False
        ingest_round(round_, RESPONSES)
        round_.refresh_from_db()
        assert round_.was_ingested is True

    def test_two_organisations_normalising_alike_match_neither(self, round_):
        """Resolving an ambiguous name to whichever row was created first would
        file one organisation's application under another's."""
        LabsOrg.objects.create(slug="fenwick-a", name="Fenwick Trust")
        LabsOrg.objects.create(slug="fenwick-b", name="Fenwick (Trust)")
        ingest_round(round_, RESPONSES)
        assert round_.responses.get(source_row=2).llo_entity is None
