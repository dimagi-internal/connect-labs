"""Human verdicts on submissions labs refused to attribute. All data invented."""
from connect_labs.marketplace.directory import (
    RESPONSE_MAPPING_HEADER,
    VERDICT_LINK,
    VERDICT_NOT_LLO,
    parse_response_mapping,
)

KNOWN = {"Fenwick Trust", "Harbourside Health Initiative"}


def _rows(*rows):
    return [RESPONSE_MAPPING_HEADER, *rows]


class TestParseResponseMapping:
    def test_carries_a_link_verdict_with_its_reason(self):
        mapped, skipped = parse_response_mapping(
            _rows(["chc-2025", "73", "Fenwick Trust", "link", "trading name of the same body", "a reviewer"]), KNOWN
        )
        assert mapped == {"chc-2025:73": ("Fenwick Trust", VERDICT_LINK, "trading name of the same body")}
        assert skipped == []

    def test_not_an_llo_is_a_first_class_verdict(self):
        """Without it, a submission that is not an organisation sits in the
        queue for ever, indistinguishable from work not yet done."""
        mapped, _ = parse_response_mapping(
            _rows(["chc-2025", "80", "", "not an LLO", "an individual, not an organisation", ""]), KNOWN
        )
        assert mapped == {"chc-2025:80": (None, VERDICT_NOT_LLO, "an individual, not an organisation")}

    def test_refuses_a_verdict_with_no_reason(self):
        mapped, skipped = parse_response_mapping(_rows(["chc-2025", "73", "Fenwick Trust", "link", "", ""]), KNOWN)
        assert mapped == {}
        assert "no stated reason" in skipped[0]

    def test_refuses_an_organisation_that_is_not_on_the_directory(self):
        mapped, skipped = parse_response_mapping(_rows(["chc-2025", "73", "Ghost Org", "link", "a reason", ""]), KNOWN)
        assert mapped == {}
        assert "not on the Organizations tab" in skipped[0]

    def test_refuses_a_link_verdict_naming_no_organisation(self):
        mapped, skipped = parse_response_mapping(_rows(["chc-2025", "73", "", "link", "a reason", ""]), KNOWN)
        assert mapped == {}
        assert "names no organisation" in skipped[0]

    def test_reports_a_row_number_that_is_not_a_number(self):
        mapped, skipped = parse_response_mapping(
            _rows(["chc-2025", "seventy-three", "Fenwick Trust", "link", "x", ""]), KNOWN
        )
        assert mapped == {}
        assert "not a response row number" in skipped[0]

    def test_ignores_wholly_blank_rows(self):
        mapped, skipped = parse_response_mapping(_rows(["", "", "", "", "", ""]), KNOWN)
        assert mapped == {} and skipped == []


class TestGuidanceRows:
    def test_a_hash_comment_row_is_not_a_refused_verdict(self):
        """The tab carries instructions for the people using it. Warning about
        those every run would train them to ignore the real warnings."""
        mapped, skipped = parse_response_mapping(
            _rows(["# Find the slug and row on the unmatched queue.", "", "", "", "", ""]), KNOWN
        )
        assert mapped == {}
        assert skipped == []
