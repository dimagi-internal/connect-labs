"""#1197: a repoint must not leave the old fixture's visit count on the row.

`visit_count` is a denormalized column the labs-context picker renders directly
(`labs/context.py:96`). Only `synthetic_generate_from_manifest` ever refreshed
it, so every other way of pointing an opp at a new fixture folder left the old
number in place — and the chrome printed it next to the new data.
"""

from unittest.mock import patch

import pytest

from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.labs.synthetic.visit_count import refresh_visit_count, resync_visit_count


@pytest.fixture
def opp(db):
    return SyntheticOpportunity.objects.create(
        opportunity_id=10101,
        gdrive_folder_id="folder-old",
        visit_count=940,
        enabled=True,
    )


def _store_returning(visits):
    """Patch the fixture store so load_endpoint yields `visits`."""
    store = patch("connect_labs.labs.integrations.connect.factory._get_fixture_store")
    m = store.start()
    m.return_value.load_endpoint.return_value = visits
    return store, m


def _store_raising(exc):
    store = patch("connect_labs.labs.integrations.connect.factory._get_fixture_store")
    m = store.start()
    m.return_value.load_endpoint.side_effect = exc
    return store, m


class TestResyncAfterRepoint:
    def test_a_new_folder_replaces_the_stale_count(self, opp):
        opp.gdrive_folder_id = "folder-new"
        patcher, _ = _store_returning([{"id": i} for i in range(12)])
        try:
            assert resync_visit_count(opp, previous_folder_id="folder-old") == 12
        finally:
            patcher.stop()
        opp.refresh_from_db()
        assert opp.visit_count == 12

    def test_an_unreadable_new_folder_nulls_rather_than_keeping_the_old_number(self, opp):
        """The distinction this function exists for.

        After a repoint the stored count is known wrong, so keeping it prints a
        confidently incorrect number. Null is the model's own "not yet computed"
        state, which the picker renders as 0.
        """
        opp.gdrive_folder_id = "folder-new"
        patcher, _ = _store_raising(RuntimeError("drive is down"))
        try:
            assert resync_visit_count(opp, previous_folder_id="folder-old") is None
        finally:
            patcher.stop()
        opp.refresh_from_db()
        assert opp.visit_count is None, "a stale 940 is worse than an honest unknown"

    def test_re_registering_the_same_folder_does_not_refetch(self, opp):
        """Register is idempotent; the same bytes still have the same count, and
        Drive is expensive enough that re-reading it for nothing is the reason
        the count is denormalized in the first place."""
        patcher, mock_store = _store_returning([{"id": 1}])
        try:
            assert resync_visit_count(opp, previous_folder_id="folder-old") == 940
        finally:
            patcher.stop()
        mock_store.return_value.load_endpoint.assert_not_called()
        opp.refresh_from_db()
        assert opp.visit_count == 940

    def test_a_brand_new_row_has_no_previous_folder_and_is_computed(self, db):
        row = SyntheticOpportunity.objects.create(opportunity_id=10102, gdrive_folder_id="f1")
        patcher, _ = _store_returning([{"id": 1}, {"id": 2}])
        try:
            assert resync_visit_count(row, previous_folder_id=None) == 2
        finally:
            patcher.stop()
        row.refresh_from_db()
        assert row.visit_count == 2

    def test_a_non_list_payload_counts_as_zero_not_as_a_failure(self, opp):
        opp.gdrive_folder_id = "folder-new"
        patcher, _ = _store_returning({"unexpected": "shape"})
        try:
            assert resync_visit_count(opp, previous_folder_id="folder-old") == 0
        finally:
            patcher.stop()


class TestRefreshKeepsItsOwnSemantics:
    """The management command re-derives a count that is still believed correct,
    so a transient Drive error must not zero it. That is the opposite of the
    repoint case and both behaviours have to survive together."""

    def test_failure_leaves_a_still_valid_count_alone(self, opp):
        patcher, _ = _store_raising(RuntimeError("drive is down"))
        try:
            assert refresh_visit_count(opp) is None
        finally:
            patcher.stop()
        opp.refresh_from_db()
        assert opp.visit_count == 940

    def test_success_updates(self, opp):
        patcher, _ = _store_returning([{"id": i} for i in range(3)])
        try:
            assert refresh_visit_count(opp) == 3
        finally:
            patcher.stop()
        opp.refresh_from_db()
        assert opp.visit_count == 3
