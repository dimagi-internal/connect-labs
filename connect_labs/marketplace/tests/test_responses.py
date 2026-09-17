"""Reading a response sheet whose shape is not known in advance.

All headers and values are invented, but each case reproduces a shape that
actually occurs in the directory's response sheets.
"""

import datetime as dt

from connect_labs.marketplace.responses import build_questions, detect_columns, parse_submissions, parse_timestamp


class TestBuildQuestions:
    def test_the_header_row_is_the_question_list_kept_verbatim(self):
        qs = build_questions(["Timestamp", "Organization name"])
        assert [q.text for q in qs] == ["Timestamp", "Organization name"]

    def test_a_blank_header_stays_addressable(self):
        """The 2024 CHC sheet has two blank headers. Dropping them would lose
        whatever those columns hold, which cannot be re-collected."""
        qs = build_questions(["Timestamp", "", ""])
        assert len({q.id for q in qs}) == 3
        assert "unlabelled" in qs[1].text

    def test_duplicate_headers_do_not_collide(self):
        """One round asks for "Email Address" twice."""
        qs = build_questions(["Email Address", "Email Address"])
        assert qs[0].id != qs[1].id


class TestDetectColumns:
    def test_finds_the_organisation_name_however_it_is_worded(self):
        for header in [
            "Organization name",
            "Please provide the name of your organization",
            "What is the full, registered name of your organization?",
        ]:
            qs = build_questions(["Timestamp", header])
            assert "org_name" in detect_columns(qs), header

    def test_the_sheets_column_map_overrides_detection(self):
        qs = build_questions(["Timestamp", "Organization name", "Legal entity"])
        got = detect_columns(qs, overrides={"org_name": "Legal entity"})
        assert got["org_name"] == qs[2].id


class TestParseTimestamp:
    def test_reads_an_unambiguous_us_order_stamp(self):
        assert parse_timestamp("3/4/2026 10:30:00") == dt.datetime(2026, 3, 4, 10, 30)

    def test_falls_back_to_day_first_when_month_first_is_impossible(self):
        assert parse_timestamp("25/11/2025") == dt.datetime(2025, 11, 25)

    def test_an_unreadable_stamp_is_none_rather_than_a_guess(self):
        assert parse_timestamp("last Tuesday") is None


class TestParseSubmissions:
    HEADER = ["Timestamp", "Organization name", "Valid email address", "Country", "Website details"]

    def test_keeps_every_answer_verbatim(self):
        rows = [self.HEADER, ["3/4/2026 09:00:00", "Fenwick Trust", "a@example.invalid", "Kenya", "x.invalid"]]
        questions, [sub] = parse_submissions(rows)
        assert len(sub.answers) == len(questions)
        assert sub.org_name == "Fenwick Trust"
        assert sub.country == "Kenya"
        assert sub.source_row == 2

    def test_pulls_both_addresses_out_of_one_cell(self):
        """Four cells in the real sheets hold two addresses at once."""
        rows = [self.HEADER, ["3/4/2026", "Fenwick Trust", "a@example.invalid, b@example.invalid", "", ""]]
        _, [sub] = parse_submissions(rows)
        assert sub.emails == ["a@example.invalid", "b@example.invalid"]

    def test_handles_a_newline_separated_pair(self):
        rows = [self.HEADER, ["3/4/2026", "Fenwick Trust", "a@example.invalid\nb@example.invalid", "", ""]]
        _, [sub] = parse_submissions(rows)
        assert len(sub.emails) == 2

    def test_skips_wholly_blank_rows(self):
        rows = [self.HEADER, ["", "", "", "", ""], ["3/4/2026", "Fenwick Trust", "", "", ""]]
        _, subs = parse_submissions(rows)
        assert len(subs) == 1

    def test_a_short_row_does_not_raise(self):
        """Trailing empty cells are simply absent from the API's response."""
        rows = [self.HEADER, ["3/4/2026", "Fenwick Trust"]]
        _, [sub] = parse_submissions(rows)
        assert sub.website == ""


class TestFrenchForms:
    """The 2026 Readers round ran an English and a French form over the same
    window. The French one matched nothing until its wording was recognised."""

    HEADER = [
        "Timestamp",
        "Email Address",
        "Nom de l'organisation",
        "Site web",
        "Nom du contact principal",
        "Titre du contact principal",
        "Courriel du contact principal",
        "Pays et zone(s) où vous exercez",
    ]

    def test_reads_a_french_organisation_name_contact_and_country(self):
        rows = [
            self.HEADER,
            [
                "3/4/2026",
                "a@example.invalid",
                "Fondation Exemple",
                "https://x.invalid",
                "Une Personne",
                "Directrice",
                "b@example.invalid",
                "Côte d'Ivoire",
            ],
        ]
        _, [sub] = parse_submissions(rows)
        assert sub.org_name == "Fondation Exemple"
        assert sub.contact_name == "Une Personne"
        assert sub.country == "Côte d'Ivoire"
        assert sub.website == "https://x.invalid"
