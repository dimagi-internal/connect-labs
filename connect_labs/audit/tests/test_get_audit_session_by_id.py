"""Regression coverage for get_audit_session()'s by-id lookup (#905, and the
2026-07-29 fan-out incident).

Two shapes are pinned here:

* **#905** — the read is the server-side by-id filter (``get_record_by_id``),
  never a fetch-all-and-scan (``get_records``).
* **2026-07-29** — the cross-opportunity sweep is memoised and runs on ONE
  pooled client. Uncached and unpooled, it was issuing ~700 req/min at
  production Connect and pinned the single web task at 100% CPU for 54 minutes.
  A repeat lookup must cost exactly one request, and the sweep must never
  construct a client per candidate opportunity.

``get_audit_session`` takes no ``try_multiple_opportunities`` flag: all eleven
call sites passed ``True``, so the efficient path is now the only path.
"""

from unittest.mock import MagicMock, patch

import pytest

from connect_labs.audit.models import AuditSessionRecord


class FakeSession:
    """Stands in for AuditSessionRecord.

    ``storage_opportunity_id`` is what ``_storage_record`` reads — the scope the
    API filters by — as distinct from ``opportunity_id``, the opportunity being
    audited.
    """

    def __init__(self, id, opportunity_id, storage_opportunity_id=None):
        self.id = id
        self.opportunity_id = opportunity_id
        self.data = {"opportunity_id": opportunity_id}
        self._storage_opportunity_id = storage_opportunity_id if storage_opportunity_id is not None else opportunity_id


@pytest.fixture(autouse=True)
def _clear_cache():
    """The session -> opportunity memo is process-wide; isolate every test."""
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _storage_record_reads_the_fake():
    """Point _storage_record at FakeSession's storage id."""
    with patch("connect_labs.audit.data_access._storage_record") as m:
        m.side_effect = lambda s: MagicMock(opportunity_id=s._storage_opportunity_id)
        yield m


def _data_access(main_client, opportunity_id=1973):
    with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
        MockAPI.return_value = main_client
        from connect_labs.audit.data_access import AuditDataAccess

        return AuditDataAccess(access_token="fake", opportunity_id=opportunity_id)


class TestGetAuditSessionById:
    def test_current_scope_uses_by_id_lookup_not_fetch_all(self):
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = FakeSession(id=7, opportunity_id=1973)

        da = _data_access(main_client)
        session = da.get_audit_session(7)

        assert session.id == 7
        main_client.get_record_by_id.assert_called_once_with(
            7,
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
            opportunity_id=None,
        )
        main_client.get_records.assert_not_called()

    def test_miss_without_other_opportunities_returns_none(self):
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = None

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1973}])

        assert da.get_audit_session(404) is None
        main_client.get_records.assert_not_called()

    def test_sweep_finds_session_in_another_opportunity(self):
        found = FakeSession(id=9, opportunity_id=1976)
        main_client = MagicMock()
        # Ambient scope misses; the 1976 probe hits.
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1973}, {"id": 1976}])

        session = da.get_audit_session(9)

        assert session is found
        main_client.get_records.assert_not_called()
        # Ambient opportunity (1973) is never re-probed inside the sweep.
        swept = [c.kwargs.get("opportunity_id") for c in main_client.get_record_by_id.call_args_list]
        assert 1976 in swept
        assert swept.count(1973) == 0

    def test_sweep_reuses_one_client_never_constructs_per_opportunity(self):
        """The incident's proximate cost: a fresh client (and TLS handshake) per opportunity."""
        found = FakeSession(id=9, opportunity_id=1976)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": i} for i in range(1974, 1990)] + [{"id": 1976}])

        with patch("connect_labs.audit.data_access.LabsRecordAPIClient") as MockPerOpp:
            assert da.get_audit_session(9) is found

        MockPerOpp.assert_not_called()

    def test_repeat_lookup_is_memoised_to_a_single_request(self):
        """The fix that matters: the sweep runs once, not once per page open."""
        found = FakeSession(id=9, opportunity_id=1976)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": i} for i in range(1974, 1990)])

        assert da.get_audit_session(9) is found
        assert main_client.get_record_by_id.call_count > 1, "sanity: the first lookup should have swept"

        main_client.get_record_by_id.reset_mock()
        da.search_opportunities.reset_mock()

        assert da.get_audit_session(9) is found

        # Straight to the remembered opportunity: one request, no sweep.
        main_client.get_record_by_id.assert_called_once_with(
            9,
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
            opportunity_id=1976,
        )
        da.search_opportunities.assert_not_called()

    def test_stale_memo_is_dropped_and_reresolved(self):
        """A remembered location that no longer serves the record must self-heal."""
        from django.core.cache import cache

        from connect_labs.audit.data_access import _session_opp_cache_key

        cache.set(_session_opp_cache_key(9), 4242, 300)

        found = FakeSession(id=9, opportunity_id=1976)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1976}])

        assert da.get_audit_session(9) is found
        assert cache.get(_session_opp_cache_key(9)) == 1976
