"""#1193: a 404 from the export API must not read as "this URL is wrong".

Connect answers "not authorized" and "not found" with the same 404, because
`_get_opportunity_or_404` filters by org membership and raises NotFound on a
miss. #1285 fixed that misreading in the pulse, audit and workflow clients and
did not cover this one — so an export-client 404 still said only "returned 404",
and #1193 spent an investigation on a URL that was never the problem.
"""

from connect_labs.labs.integrations.connect.export_client import _status_error_message


def test_404_names_membership_as_the_likely_cause():
    msg = _status_error_message(404, "https://connect.dimagi.com/export/opportunity/1790/user_visits/")

    assert "404" in msg
    assert "membership" in msg.lower()
    assert "may not read this" in msg.lower(), "the reader has to be told the two cases are indistinguishable"
    assert "https://connect.dimagi.com/export/opportunity/1790/user_visits/" in msg


def test_401_and_403_point_at_the_token_not_the_url():
    for code in (401, 403):
        msg = _status_error_message(code, "https://x/export/y/")
        assert str(code) in msg
        assert "scope" in msg or "expired" in msg


def test_other_statuses_are_left_plain():
    """A 500 or a 400 means what it says; only the ambiguous ones get a gloss."""
    assert _status_error_message(500, "https://x/y/") == "Export API returned 500 for https://x/y/"
    assert _status_error_message(400, "https://x/y/") == "Export API returned 400 for https://x/y/"
