"""Parsing the restructured EOI/RFP tab. All values invented."""
from connect_labs.marketplace.directory import parse_rounds

HEADER = [
    "Slug",
    "Program / Initiative Name",
    "Announcement Type (EOI/RFP)",
    "Status",
    "Published Date",
    "Application Deadline",
    "Selection Decision Date",
    "Program Start Date",
    "Program End Date",
    "Target Countries / Regions",
    "Announcement Link",
    "Form Link",
    "Response Sheet Link",
    "Response Tab",
    "Column Map (JSON, blank = auto-detect)",
    "Labs Access",
    "Labs Access Checked",
    "Notes",
]

SHEET = "https://docs.google.com/spreadsheets/d/1abcDEFghiJKLmnoPQRstuVWxyz0123456789"


def _row(over=None):
    """A valid round row, with per-column overrides keyed by column index."""
    row = [
        "demo-2026",
        "Demo round",
        "EOI",
        "Closed",
        "1 March 2026",
        "1 April 2026",
        "",
        "",
        "",
        "Nigeria",
        "https://doc.invalid",
        "https://form.invalid",
        SHEET,
        "Form Responses 1",
        "",
        "",
        "",
        "",
    ]
    for k, v in (over or {}).items():
        row[k] = v
    return row


class TestParseRounds:
    def test_reads_a_round_and_extracts_the_response_sheet_id(self):
        [r], skipped = parse_rounds([HEADER, _row()])
        assert r.slug == "demo-2026"
        assert r.solicitation_type == "eoi"
        assert r.status == "closed"
        assert r.response_spreadsheet_id == "1abcDEFghiJKLmnoPQRstuVWxyz0123456789"
        assert r.response_tab == "Form Responses 1"
        assert skipped == []

    def test_published_becomes_active(self):
        [r], _ = parse_rounds([HEADER, _row({3: "Published"})])
        assert r.status == "active"

    def test_an_rfp_is_typed_as_one(self):
        [r], _ = parse_rounds([HEADER, _row({2: "RFP"})])
        assert r.solicitation_type == "rfp"

    def test_a_row_with_no_slug_is_refused(self):
        rounds, skipped = parse_rounds([HEADER, _row({0: ""})])
        assert rounds == []
        assert "no slug" in skipped[0]

    def test_a_duplicate_slug_is_refused_rather_than_overwriting(self):
        rounds, skipped = parse_rounds([HEADER, _row(), _row()])
        assert len(rounds) == 1
        assert "already used" in skipped[0]

    def test_a_round_with_no_response_sheet_still_imports(self):
        """The Interviews round has no discoverable sheet. It must still appear,
        so the directory can say 'not ingested' rather than 'no applicants'."""
        [r], _ = parse_rounds([HEADER, _row({12: ""})])
        assert r.response_spreadsheet_id == ""

    def test_a_malformed_column_map_is_reported_not_fatal(self):
        rounds, skipped = parse_rounds([HEADER, _row({14: "{not json"})])
        assert rounds[0].column_map == {}
        assert "not valid JSON" in skipped[0]

    def test_a_column_map_override_is_read(self):
        [r], _ = parse_rounds([HEADER, _row({14: '{"org_name": "Organisation"}'})])
        assert r.column_map == {"org_name": "Organisation"}
