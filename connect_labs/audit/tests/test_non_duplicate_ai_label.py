"""Tests for _non_duplicate_ai_label (connect_labs.audit.views).

Server-side split of ai_notes into "everything except the duplicate-detector's
own label" -- lets the review UI show a real classifier verdict (e.g. "MUAC
Mismatch") on an image that's ALSO a confirmed duplicate, without the client
re-implementing this split against hardcoded copies of AI_NOTES_JOIN_SEP and
DUPLICATE_FLAG_LABEL.
"""
from connect_labs.audit.views import _non_duplicate_ai_label


def test_strips_the_duplicate_label_alone():
    assert _non_duplicate_ai_label("Potential Duplicate") == ""


def test_keeps_a_real_classifier_label_alongside_the_duplicate_label():
    assert _non_duplicate_ai_label("MUAC Mismatch (strict tolerance); Potential Duplicate") == (
        "MUAC Mismatch (strict tolerance)"
    )


def test_keeps_a_real_classifier_label_with_no_duplicate_flag():
    assert _non_duplicate_ai_label("Hyperzoomed") == "Hyperzoomed"


def test_keeps_multiple_real_labels_joined():
    assert _non_duplicate_ai_label("Hyperzoomed; MUAC Mismatch (strict tolerance)") == (
        "Hyperzoomed; MUAC Mismatch (strict tolerance)"
    )


def test_empty_or_none_ai_notes_returns_empty_string():
    assert _non_duplicate_ai_label("") == ""
    assert _non_duplicate_ai_label(None) == ""
