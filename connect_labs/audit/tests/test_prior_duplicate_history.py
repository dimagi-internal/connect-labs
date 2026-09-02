"""Per-FLW duplicate/fake history for the review screen's 60/40 side panel.

Every test here pins a rule whose failure produces a *plausible wrong number* on
a page that renders perfectly -- the failure mode prior_audit_projection exists
to prevent. Two are load-bearing beyond the obvious:

* an image judged in several sessions must count ONCE (the winner rule), and
* narrowing by username before picking winners must not let an older attributed
  row beat the newer unattributed one -- which over-counts, in the direction that
  accuses an FLW of duplicates they were not last judged to have.
"""

from datetime import date, datetime, timezone

import pytest

from connect_labs.audit.models import AuditSessionRecord
from connect_labs.audit.prior_audit_models import PriorAuditVerdict
from connect_labs.audit.prior_audit_projection import (
    _visit_date_of,
    duplicate_history_for_flws,
    has_flw_attribution,
    replace_session,
    rows_for_session,
)

OPP = 2154
UMAR = "umar.yari"
AISHA = "aisha.bello"


def _dt(day, hour=12):
    return datetime(2026, 8, day, hour, tzinfo=timezone.utc)


def _session(id, visit_results, visit_images, completed_at=None, status="completed"):
    data = {
        "status": status,
        "visit_results": visit_results,
        "visit_images": visit_images,
        "title": f"session {id}",
        "opportunity_id": OPP,
    }
    if completed_at:
        data["completed_at"] = completed_at.isoformat()
    return AuditSessionRecord(
        {"id": id, "experiment": "audit", "type": "AuditSession", "opportunity_id": OPP, "data": data}
    )


def _vr(**assessments):
    return {"assessments": {b: {"result": r, "question_id": "form/photo"} for b, r in assessments.items()}}


def _images(username, visit_day, *blob_ids):
    return [
        {"blob_id": b, "username": username, "visit_date": _dt(visit_day).isoformat(), "question_id": "form/photo"}
        for b in blob_ids
    ]


# --- the date convention -------------------------------------------------------


def test_a_naive_visit_timestamp_is_read_as_utc():
    """The bulk-data view assumes UTC for a naive timestamp. If this disagreed, a
    photo would sit in one day on its card and the next day in the table."""
    assert _visit_date_of("2026-08-21T23:30:00") == date(2026, 8, 21)


@pytest.mark.parametrize("raw", ["", None, "not-a-date"])
def test_an_unreadable_visit_timestamp_yields_no_date(raw):
    assert _visit_date_of(raw) is None


# --- recording ----------------------------------------------------------------


def test_rows_carry_the_flw_and_the_visit_day():
    session = _session(1, {"v1": _vr(b1="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22))
    (row,) = rows_for_session(session)
    assert (row.username, row.visit_date) == (UMAR, date(2026, 8, 21))


def test_a_verdict_with_no_matching_image_is_recorded_unattributed_not_guessed():
    """visit_images is the only source of attribution. A verdict whose image is
    missing from it gets none -- inventing one would put a duplicate on an FLW's
    record on the strength of nothing."""
    session = _session(1, {"v1": _vr(b_ghost="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22))
    (row,) = rows_for_session(session)
    assert row.username == "" and row.visit_date is None


def test_attribution_falls_back_to_the_visits_first_image():
    """username/visit_date are properties of the VISIT; the bulk-data view reads
    them off the first image and applies them to all of it. An image carrying
    neither inherits rather than losing its attribution."""
    images = _images(UMAR, 21, "b1") + [{"blob_id": "b2", "question_id": "form/photo"}]
    session = _session(1, {"v1": _vr(b1="pass", b2="fake")}, {"v1": images}, _dt(22))
    by_blob = {r.blob_id: r for r in rows_for_session(session)}
    assert by_blob["b2"].username == UMAR
    assert by_blob["b2"].visit_date == date(2026, 8, 21)


# --- the history read ---------------------------------------------------------


@pytest.mark.django_db
def test_days_run_newest_first_with_dd_mm_labels():
    """Left to right is most recent first: the strip scrolls, so the newest days have
    to be the ones visible before anyone touches it."""
    replace_session(
        _session(
            1,
            {
                "v1": _vr(b1="duplicate_fake", b2="fake", b3="duplicate"),
                "v2": _vr(b4="duplicate_fake", b5="duplicate_fake", b6="fake", b7="fake"),
            },
            {"v1": _images(UMAR, 21, "b1", "b2", "b3"), "v2": _images(UMAR, 25, "b4", "b5", "b6", "b7")},
            _dt(26),
        )
    )
    history = duplicate_history_for_flws(OPP, [UMAR])[UMAR]
    assert [(d["label"], d["count"]) for d in history["days"]] == [("25/08", 4), ("21/08", 3)]
    assert history["days"][0]["iso"] == "2026-08-25"


@pytest.mark.django_db
def test_a_day_below_the_threshold_gets_no_column():
    """Two on a day is ordinary noise; the panel is for the days that stand out."""
    replace_session(
        _session(
            1,
            {"v1": _vr(b1="duplicate_fake", b2="fake")},
            {"v1": _images(UMAR, 21, "b1", "b2")},
            _dt(22),
        )
    )
    assert duplicate_history_for_flws(OPP, [UMAR])[UMAR]["days"] == []


@pytest.mark.django_db
def test_a_day_exactly_at_the_threshold_gets_a_column():
    """Pins the boundary as inclusive -- the spec is "3 or more", and an off-by-one
    here silently drops every three-duplicate day in the system."""
    replace_session(
        _session(
            1,
            {"v1": _vr(b1="duplicate_fake", b2="fake", b3="duplicate")},
            {"v1": _images(UMAR, 21, "b1", "b2", "b3")},
            _dt(22),
        )
    )
    assert [d["count"] for d in duplicate_history_for_flws(OPP, [UMAR])[UMAR]["days"]] == [3]


@pytest.mark.django_db
def test_images_below_the_threshold_are_reported_not_discarded():
    """They have no column, so they are counted separately. Dropping them would leave
    the headline total disagreeing with the columns under it, which is the one thing
    this panel must never do."""
    replace_session(
        _session(
            1,
            {
                "v1": _vr(b1="duplicate_fake", b2="fake", b3="duplicate"),
                "v2": _vr(b4="duplicate_fake"),
                "v3": _vr(b5="fake", b6="fake"),
            },
            {
                "v1": _images(UMAR, 25, "b1", "b2", "b3"),
                "v2": _images(UMAR, 22, "b4"),
                "v3": _images(UMAR, 21, "b5", "b6"),
            },
            _dt(26),
        )
    )
    history = duplicate_history_for_flws(OPP, [UMAR])[UMAR]
    assert [(d["label"], d["count"]) for d in history["days"]] == [("25/08", 3)]
    assert history["below_threshold"] == 3
    assert history["total"] == 6


@pytest.mark.django_db
def test_the_total_equals_everything_the_panel_can_account_for():
    """columns + below-threshold + undated == total, exactly. The panel states all
    three, so a reader can always reconcile the headline against what they see."""
    images = {
        "v1": _images(UMAR, 25, "b1", "b2", "b3"),
        "v2": _images(UMAR, 21, "b4"),
        "v3": [{"blob_id": "b5", "username": UMAR, "visit_date": "nonsense"}],
    }
    replace_session(
        _session(
            1,
            {
                "v1": _vr(b1="duplicate_fake", b2="fake", b3="duplicate"),
                "v2": _vr(b4="duplicate_fake"),
                "v3": _vr(b5="duplicate_fake"),
            },
            images,
            _dt(26),
        )
    )
    h = duplicate_history_for_flws(OPP, [UMAR])[UMAR]
    assert sum(d["count"] for d in h["days"]) + h["below_threshold"] + h["undated"] == h["total"]
    assert (h["below_threshold"], h["undated"], h["total"]) == (1, 1, 5)


@pytest.mark.django_db
def test_only_duplicate_and_fake_count_not_pass_or_fail():
    replace_session(
        _session(
            1,
            {"v1": _vr(b1="pass", b2="fail", b3="duplicate_fake", b4="fake", b5="duplicate")},
            {"v1": _images(UMAR, 21, "b1", "b2", "b3", "b4", "b5")},
            _dt(22),
        )
    )
    history = duplicate_history_for_flws(OPP, [UMAR])[UMAR]
    assert [d["count"] for d in history["days"]] == [3]
    assert history["total"] == 3


@pytest.mark.django_db
def test_an_image_judged_by_several_sessions_counts_once():
    """The winner rule. Without it, an image re-judged in five audits reads as five
    separate duplicates and the FLW's record inflates every time anyone reviews."""
    images = {"v1": _images(UMAR, 21, "b1")}
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake")}, images, _dt(22)))
    replace_session(_session(2, {"v1": _vr(b1="duplicate_fake")}, images, _dt(23)))
    replace_session(_session(3, {"v1": _vr(b1="duplicate_fake")}, images, _dt(24)))
    assert duplicate_history_for_flws(OPP, [UMAR])[UMAR]["total"] == 1


@pytest.mark.django_db
def test_the_most_recent_verdict_wins_so_a_retracted_duplicate_stops_counting():
    images = {"v1": _images(UMAR, 21, "b1")}
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake")}, images, _dt(22)))
    replace_session(_session(2, {"v1": _vr(b1="pass")}, images, _dt(23)))
    assert duplicate_history_for_flws(OPP, [UMAR])[UMAR]["total"] == 0


@pytest.mark.django_db
def test_a_newer_unattributed_verdict_still_beats_an_older_attributed_one():
    """THE reason the username filter is not applied to the winner scan.

    Session 2 is the most recent word on b1 and says "pass", but its row carries no
    username (its blob is absent from visit_images). Filtering the scan by username
    would drop that row, leave session 1's older "duplicate_fake" as the apparent
    winner, and report a duplicate that has since been overturned.
    """
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22)))
    replace_session(_session(2, {"v1": _vr(b1="pass")}, {"v1": _images(UMAR, 21, "b_other")}, _dt(23)))

    rows = {(r.session_id, r.username) for r in PriorAuditVerdict.objects.all()}
    assert (2, "") in rows, "precondition: session 2's row is unattributed"
    assert duplicate_history_for_flws(OPP, [UMAR])[UMAR]["total"] == 0


@pytest.mark.django_db
def test_an_unattributed_duplicate_is_attributed_to_the_visits_known_owner():
    """The mirror of the case above. b2's row has no username, but v1 is known to be
    UMAR's from b1's row, so his history must not silently lose the flag."""
    replace_session(_session(1, {"v1": _vr(b1="pass")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22)))
    replace_session(_session(2, {"v1": _vr(b2="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(23)))
    assert duplicate_history_for_flws(OPP, [UMAR])[UMAR]["total"] == 1


@pytest.mark.django_db
def test_the_current_session_is_excluded_so_the_panel_shows_only_earlier_audits():
    images = {
        "v1": _images(UMAR, 21, "b1", "b2", "b3"),
        "v2": _images(UMAR, 22, "b4", "b5", "b6"),
    }
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake", b2="fake", b3="duplicate")}, images, _dt(23)))
    replace_session(_session(9, {"v2": _vr(b4="duplicate_fake", b5="fake", b6="duplicate")}, images, _dt(24)))
    history = duplicate_history_for_flws(OPP, [UMAR], exclude_session_id=9)[UMAR]
    assert [d["label"] for d in history["days"]] == ["21/08"]
    assert history["total"] == 3, "the session being reviewed must not count against its own FLW"


@pytest.mark.django_db
def test_each_flw_sees_only_their_own_flags():
    replace_session(
        _session(
            1,
            {"v1": _vr(b1="duplicate_fake"), "v2": _vr(b2="duplicate_fake", b3="fake")},
            {"v1": _images(UMAR, 21, "b1"), "v2": _images(AISHA, 21, "b2", "b3")},
            _dt(22),
        )
    )
    history = duplicate_history_for_flws(OPP, [UMAR, AISHA])
    assert history[UMAR]["total"] == 1
    assert history[AISHA]["total"] == 2


@pytest.mark.django_db
def test_another_opportunitys_flags_do_not_leak_in():
    session = _session(1, {"v1": _vr(b1="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22))
    session.data["opportunity_id"] = 9999
    replace_session(session)
    assert duplicate_history_for_flws(OPP, [UMAR])[UMAR]["total"] == 0


@pytest.mark.django_db
def test_a_flagged_image_with_no_readable_date_is_reported_not_dropped():
    """It belongs to no column, so it is stated separately -- otherwise the headline
    total silently disagrees with the sum of the columns under it."""
    images = {"v1": [{"blob_id": "b1", "username": UMAR, "visit_date": "nonsense"}]}
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake")}, images, _dt(22)))
    history = duplicate_history_for_flws(OPP, [UMAR])[UMAR]
    assert history["days"] == []
    assert history["undated"] == 1
    assert history["total"] == 1


@pytest.mark.django_db
def test_an_flw_with_a_clean_record_gets_an_empty_history_not_a_missing_key():
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22)))
    history = duplicate_history_for_flws(OPP, [UMAR, AISHA])
    assert history[AISHA] == {
        "days": [],
        "total": 0,
        "below_threshold": 0,
        "undated": 0,
        "threshold": 3,
    }


@pytest.mark.django_db
def test_no_usernames_asked_for_returns_nothing_without_querying():
    assert duplicate_history_for_flws(OPP, []) == {}
    assert duplicate_history_for_flws(OPP, ["", None]) == {}


# --- the attribution guard ----------------------------------------------------


@pytest.mark.django_db
def test_an_opportunity_with_no_rows_at_all_counts_as_attributed():
    """No history is an honest empty answer; only a MISSING one has to be flagged."""
    assert has_flw_attribution(OPP) is True


@pytest.mark.django_db
def test_rows_that_predate_the_username_column_are_not_treated_as_a_clean_record():
    """The trap this guard exists for: the projection's state row says "built and
    current" while every verdict in it is unattributable, so the history reads empty
    for an FLW with a long record and nothing anywhere reports a problem."""
    PriorAuditVerdict.objects.create(
        opportunity_id=OPP, session_id=1, visit_id="v1", blob_id="b1", result="duplicate_fake", completed_at=_dt(22)
    )
    assert has_flw_attribution(OPP) is False


@pytest.mark.django_db
def test_one_attributed_row_is_enough_to_trust_the_index():
    replace_session(_session(1, {"v1": _vr(b1="duplicate_fake")}, {"v1": _images(UMAR, 21, "b1")}, _dt(22)))
    assert has_flw_attribution(OPP) is True


# --- the panel's markup -------------------------------------------------------
#
# Read from the file rather than rendered, because these are structural facts about
# the layout that no context value can change. The failure they guard is silent: a
# 40% column that cannot shrink stretches the whole card sideways instead of
# scrolling, and a missing empty-state renders a blank box that reads as "clean".

from pathlib import Path  # noqa: E402

TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "connect_labs" / "templates" / "audit" / "bulk_assessment.html"


def _filter_card() -> str:
    """Just the white filter/controls card, so these assertions cannot be satisfied
    by some unrelated part of a 2400-line template."""
    import re

    lines = TEMPLATE_PATH.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if "bg-white rounded-lg shadow-sm p-4 mb-4" in line)
    depth = 0
    for i in range(start, len(lines)):
        depth += len(re.findall(r"<div\b", lines[i])) + len(re.findall(r"<template\b", lines[i]))
        depth -= lines[i].count("</div>") + lines[i].count("</template>")
        if depth == 0:
            return "\n".join(lines[start : i + 1])
    raise AssertionError("the filter card's markup does not balance")


def test_the_card_splits_sixty_forty():
    card = _filter_card()
    assert "lg:w-3/5" in card, "the controls column is not 60%"
    assert "lg:w-2/5" in card, "the history column is not 40%"


def test_both_columns_can_shrink_so_the_history_scrolls_instead_of_stretching_the_card():
    """`min-width: auto` is the flex default. Without min-w-0 the 40% column refuses
    to shrink below its content, overflow-x-auto never engages, and a wide strip
    widens the whole page -- which is the one thing the scroll was asked for."""
    card = _filter_card()
    assert card.count("min-w-0") >= 2
    assert "overflow-x-auto" in card


def test_the_history_panel_states_every_outcome_it_can_have():
    """Four distinct cases, and three of them are easy to leave as a blank box that
    a reviewer reads as "this FLW is clean"."""
    card = _filter_card()
    assert "History unavailable" in card, "an unattributed index must say so"
    assert "Pick an FLW above" in card, "several FLWs and no filter has no single answer"
    assert "No duplicate or fake images in earlier audits" in card, "a genuinely clean record"
    assert "priorDuplicateHistory.days" in card, "the columns themselves"


def test_the_table_is_two_rows_dates_then_counts():
    card = _filter_card()
    assert "day.label" in card and "day.count" in card
    assert card.index("day.label") < card.index("day.count"), "dates must be the top row"


def test_the_page_defaults_to_unattributed_rather_than_clean():
    """`=== true` and not a truthy check: an older server that does not send the key,
    or a history read that failed, must show "unavailable" and not an empty strip."""
    html = TEMPLATE_PATH.read_text()
    assert "data.prior_duplicates_attributed === true" in html


def test_the_panel_distinguishes_a_clean_record_from_one_below_the_threshold():
    """Both render as "no columns", and treating them the same would tell you an FLW
    flagged every week has never been flagged at all."""
    card = _filter_card()
    assert "No duplicate or fake images in earlier audits" in card
    assert "no single day reached" in card


def test_the_panel_states_both_buckets_no_column_can_show():
    card = _filter_card()
    assert "priorDuplicateHistory.below_threshold" in card, "days under the threshold"
    assert "priorDuplicateHistory.undated" in card, "flags with no readable date"


def test_the_threshold_is_never_written_as_a_literal_in_the_panel_text():
    """The number decides which columns appear AND appears in three sentences of the
    panel's own wording. Hardcoding it here would leave the text describing the old
    rule the first time the constant changes."""
    card = _filter_card()
    # Three places inside the card read it: the heading, the below-threshold message
    # and the note under the strip. State and hydration sit outside the card.
    assert card.count("priorDuplicatesThreshold") == 3
    from connect_labs.audit.prior_audit_projection import DUPLICATE_DAY_THRESHOLD

    assert f"with {DUPLICATE_DAY_THRESHOLD}" not in card and f"fewer than {DUPLICATE_DAY_THRESHOLD}" not in card


def test_the_heading_says_the_strip_is_filtered():
    """A strip with gaps in it must not read as a complete history."""
    assert "days with" in _filter_card()
