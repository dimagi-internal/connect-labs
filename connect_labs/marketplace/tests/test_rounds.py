"""Parsing the restructured EOI/RFP tab. All values invented."""

from connect_labs.marketplace.directory import parse_rounds
from connect_labs.marketplace.testing import SHEET_ROUNDS_HEADER

HEADER = SHEET_ROUNDS_HEADER

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


ROUND_COL = HEADER + ["Round"]


def _form(slug, name, sheet_id, round_="chp-2026", over=None):
    row = _row({0: slug, 1: name, 3: "Published", 12: f"https://docs.google.com/spreadsheets/d/{sheet_id}"})
    row += [""] * (len(ROUND_COL) - len(row))
    row[ROUND_COL.index("Round")] = round_
    for k, v in (over or {}).items():
        row[k] = v
    return row


class TestRoundsWithSeveralForms:
    """An English and a French form for one call are one round."""

    def test_rows_sharing_a_round_become_one_round_with_both_forms(self):
        rows = [
            ROUND_COL,
            _form("chp-2026-en", "Community Health RFI 2026 — English", "sheetEN0123456789abcdefghij"),
            _form("chp-2026-fr", "Community Health RFI 2026 — French", "sheetFR0123456789abcdefghij"),
        ]
        [r], skipped = parse_rounds(rows)
        assert skipped == []
        assert r.slug == "chp-2026"
        assert r.title == "Community Health RFI 2026"
        assert [(f.key, f.label) for f in r.forms] == [("chp-2026-en", "English"), ("chp-2026-fr", "French")]
        assert [f.source_row for f in r.forms] == [2, 3]
        assert r.response_spreadsheet_id == "sheetEN0123456789abcdefghij"  # the first form's, as before
        assert r.forms[1].response_spreadsheet_id == "sheetFR0123456789abcdefghij"

    def test_a_blank_round_keeps_the_row_a_round_of_its_own(self):
        rows = [
            ROUND_COL,
            _form("a-2026", "A", "sheetA0123456789abcdefghij", round_=""),
            _form("b-2026", "B", "sheetB0123456789abcdefghij", round_=""),
        ]
        rounds, _ = parse_rounds(rows)
        assert [r.slug for r in rounds] == ["a-2026", "b-2026"]
        assert [len(r.forms) for r in rounds] == [1, 1]

    def test_the_tab_without_a_round_column_reads_as_before(self):
        [r], _ = parse_rounds([HEADER, _row()])
        assert r.slug == "demo-2026" and [f.key for f in r.forms] == ["demo-2026"]

    def test_forms_that_disagree_on_the_deadline_are_reported(self):
        rows = [
            ROUND_COL,
            _form("chp-2026-en", "CHP — English", "sheetEN0123456789abcdefghij"),
            _form("chp-2026-fr", "CHP — French", "sheetFR0123456789abcdefghij", over={5: "1 May 2026"}),
        ]
        [r], skipped = parse_rounds(rows)
        assert r.application_deadline == "1 April 2026"
        assert "Application Deadline differs" in skipped[0]

    def test_a_round_named_after_a_rows_own_slug_joins_that_row(self):
        rows = [
            ROUND_COL,
            _form("solo-2026", "Solo", "sheetS0123456789abcdefghij", round_=""),
            _form("other-en", "Other EN", "sheetO0123456789abcdefghij", round_="solo-2026"),
        ]
        rounds, skipped = parse_rounds(rows)
        # 'solo-2026' is a row's own slug; the second row joins it as a form of
        # that same round, which is allowed: it names the group it joins.
        assert [r.slug for r in rounds] == ["solo-2026"]
        assert [f.key for f in rounds[0].forms] == ["solo-2026", "other-en"]

    def test_names_with_no_shared_prefix_keep_their_whole_names(self):
        rows = [
            ROUND_COL,
            _form("x-en", "Alpha call", "sheetX0123456789abcdefghij"),
            _form("x-fr", "Appel beta", "sheetY0123456789abcdefghij"),
        ]
        [r], _ = parse_rounds(rows)
        assert r.title == "Alpha call"
        assert [f.label for f in r.forms] == ["Alpha call", "Appel beta"]

    def test_a_round_value_that_is_a_form_of_another_round_is_refused(self):
        rows = [
            ROUND_COL,
            _form("a-en", "A EN", "sheetA0123456789abcdefghij", round_="group-a"),
            _form("b-en", "B EN", "sheetB0123456789abcdefghij", round_="a-en"),
        ]
        rounds, skipped = parse_rounds(rows)
        assert [r.slug for r in rounds] == ["group-a"]
        assert "also a form of round 'group-a'" in skipped[0]
