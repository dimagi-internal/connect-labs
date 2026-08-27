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
        # Public, matching AuditSessionRecord: callers addressing the API read
        # this rather than reaching through _storage_record for it.
        self.storage_opportunity_id = storage_opportunity_id if storage_opportunity_id is not None else opportunity_id


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
        m.side_effect = lambda s: MagicMock(opportunity_id=s.storage_opportunity_id)
        yield m


def _data_access(main_client, opportunity_id=1973, access_token="fake"):
    with patch("connect_labs.workflow.data_access.LabsRecordAPIClient") as MockAPI:
        MockAPI.return_value = main_client
        from connect_labs.audit.data_access import AuditDataAccess

        return AuditDataAccess(access_token=access_token, opportunity_id=opportunity_id)


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


class TestSweepDoesNotRepeatOnMiss:
    """The 2026-07-30 fan-out (#1060 comment): #1037 memoised only the HIT.

    Measured over the 24h to 2026-07-30, still on production: 23,445 scoped
    ``GET /export/labs_record/?id=...&opportunity_id=...`` calls, peaking around
    1,370 requests/minute, with session 7234 swept 447 times. Two defects, both
    of which make a single session re-run the whole fan-out indefinitely.
    """

    def _never_found(self):
        client = MagicMock()
        client.get_record_by_id.return_value = None
        return client

    def test_failed_sweep_is_not_repeated_for_the_same_caller(self):
        """Defect 1: a sweep that finds nothing memoises nothing, so it re-runs forever."""
        main_client = self._never_found()
        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": i} for i in range(100, 140)])

        assert da.get_audit_session(7234) is None
        assert main_client.get_record_by_id.call_count > 10, "precondition: the first call really does sweep"

        main_client.get_record_by_id.reset_mock()
        da.search_opportunities.reset_mock()

        assert da.get_audit_session(7234) is None
        da.search_opportunities.assert_not_called()
        assert main_client.get_record_by_id.call_count <= 2, (
            f"a caller that already failed to resolve this session re-swept "
            f"({main_client.get_record_by_id.call_count} requests) instead of "
            f"remembering the miss"
        )

    def test_miss_memo_is_per_caller_not_global(self):
        """One caller's miss must not blind a colleague who CAN see the session.

        The sweep enumerates *the caller's own* opportunities, so "not found" is
        a fact about that caller's access, not about the session. Caching it
        globally would hide a session from everyone the moment one unauthorized
        user looked for it.
        """
        da_blind = _data_access(self._never_found(), access_token="token-blind")
        da_blind.search_opportunities = MagicMock(return_value=[{"id": 1976}])
        assert da_blind.get_audit_session(9) is None

        found = FakeSession(id=9, opportunity_id=1976)
        seeing_client = MagicMock()
        seeing_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )
        da_seeing = _data_access(seeing_client, access_token="token-seeing")
        da_seeing.search_opportunities = MagicMock(return_value=[{"id": 1976}])

        assert da_seeing.get_audit_session(9) is found

    def test_unauthorized_caller_does_not_evict_the_shared_memo(self):
        """Defect 2: the memo is shared, but was evicted on a per-caller auth failure.

        The location memo is keyed on session id alone — deliberately, since a
        session's storage opportunity is the same fact for everybody. But a
        caller who cannot read that opportunity gets None and used to
        ``cache.delete`` the shared entry, so a user with no access could evict
        the memo for the users who do. Two people on the same session then
        thrash it: each re-sweep repopulates, the other evicts.
        """
        from django.core.cache import cache

        from connect_labs.audit.data_access import _session_opp_cache_key

        cache.set(_session_opp_cache_key(9), 1976, 300)

        da_blind = _data_access(self._never_found(), access_token="token-blind")
        da_blind.search_opportunities = MagicMock(return_value=[{"id": 1976}])

        assert da_blind.get_audit_session(9) is None
        assert cache.get(_session_opp_cache_key(9)) == 1976, (
            "a caller who cannot see the remembered opportunity evicted the memo " "for everyone else"
        )

    def test_a_real_relocation_still_self_heals(self):
        """Not evicting must not strand a genuinely moved session on a stale memo."""
        from django.core.cache import cache

        from connect_labs.audit.data_access import _session_opp_cache_key

        cache.set(_session_opp_cache_key(9), 4242, 300)  # where it used to live

        found = FakeSession(id=9, opportunity_id=1976)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )
        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1976}])

        assert da.get_audit_session(9) is found
        assert cache.get(_session_opp_cache_key(9)) == 1976, "the memo should re-point to the new home"


class TestUpstreamErrorsDoNotAbortTheLadder:
    """The 2026-08-08 shape: 1807 consecutive 500s over 26 hours on one session.

    Every rung of this ladder exists so the next one gets a turn, but only the
    sweep was wrapped -- an upstream error on the remembered or ambient rung
    escaped as a 500 and the sweep that could have located the session never
    ran. A rung that errors must mean "not here", exactly like a rung that
    misses.
    """

    def test_error_on_ambient_scope_still_falls_through_to_the_sweep(self):
        found = FakeSession(id=9, opportunity_id=1976)
        main_client = MagicMock()

        def flaky(sid, **kw):
            if kw.get("opportunity_id") is None:
                raise RuntimeError("connect returned 502")
            return found if kw.get("opportunity_id") == 1976 else None

        main_client.get_record_by_id.side_effect = flaky
        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1976}])

        # Previously this raised, and the user got a 500.
        assert da.get_audit_session(9).id == 9

    def test_error_on_the_remembered_scope_still_falls_through(self):
        from django.core.cache import cache

        from connect_labs.audit.data_access import _session_opp_cache_key

        found = FakeSession(id=11, opportunity_id=1973)
        cache.set(_session_opp_cache_key(11), 4242)

        main_client = MagicMock()

        def flaky(sid, **kw):
            if kw.get("opportunity_id") == 4242:
                raise RuntimeError("connect returned 502")
            return found

        main_client.get_record_by_id.side_effect = flaky
        da = _data_access(main_client)

        assert da.get_audit_session(11).id == 11

    def test_a_total_upstream_outage_is_not_memoised_as_not_found(self):
        """The dangerous half of this fix.

        Treating an error as a miss is only safe if the miss is not remembered:
        caching one would turn a transient blip into a sticky "session not
        found" for a session that exists, which is worse than the 500.
        """
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = RuntimeError("connect is down")

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": 1976}])

        assert da.get_audit_session(12) is None

        # Upstream recovers; the very next call must go back to the network
        # rather than serve a memoised miss.
        recovered = FakeSession(id=12, opportunity_id=1973)
        main_client.get_record_by_id.side_effect = None
        main_client.get_record_by_id.return_value = recovered

        assert da.get_audit_session(12).id == 12

    def test_a_genuine_miss_is_still_memoised(self):
        """The optimisation #1060 added must survive the error handling."""
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = None

        da = _data_access(main_client)
        da.search_opportunities = MagicMock(return_value=[{"id": i} for i in range(100, 140)])

        assert da.get_audit_session(13) is None
        da.search_opportunities.reset_mock()

        assert da.get_audit_session(13) is None
        da.search_opportunities.assert_not_called()


class TestCallerSuppliedOpportunityHint:
    """#1169: rung 0 — the caller says where the session lives.

    A program-scoped DAO has no cheap rung at all. The ambient-scope probe is an
    *opportunity* scope, so a DAO scoped to a program misses it by construction
    and falls to the sweep on every single request — which is what let one
    auditor's save path stay expensive long after the 500s were fixed. The bulk
    page has already resolved the storage opportunity in order to render, so it
    hands it back.
    """

    def test_hint_resolves_a_program_scoped_lookup_without_a_sweep(self):
        found = FakeSession(id=21, opportunity_id=1978)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1978 else None
        )

        # Program-scoped: opportunity_id is None, so the ambient rung can never hit.
        da = _data_access(main_client, opportunity_id=None)
        da.search_opportunities = MagicMock(return_value=[{"id": i} for i in range(1970, 2000)])

        session = da.get_audit_session(21, opportunity_id=1978)

        assert session is found
        # The whole point: one request, and the sweep never ran.
        assert main_client.get_record_by_id.call_count == 1
        da.search_opportunities.assert_not_called()

    def test_a_wrong_hint_costs_one_request_and_the_ladder_still_resolves(self):
        """A hint is a shortcut, never an authorization — being wrong must not
        turn a resolvable session into a 404."""
        found = FakeSession(id=22, opportunity_id=1976)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1976 else None
        )

        da = _data_access(main_client, opportunity_id=1973)
        da.search_opportunities = MagicMock(return_value=[{"id": 1973}, {"id": 1976}])

        session = da.get_audit_session(22, opportunity_id=9999)

        assert session is found
        probed = [c.kwargs.get("opportunity_id") for c in main_client.get_record_by_id.call_args_list]
        assert probed[0] == 9999, "the hint is tried first"
        assert probed.count(9999) == 1, "and only once"

    def test_the_hint_still_goes_through_the_callers_own_token(self):
        """A forged hint buys nothing: the fetch is a normal scoped read, so the
        server runs its per-user authorization exactly as it would otherwise."""
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = None

        da = _data_access(main_client, opportunity_id=1973, access_token="caller-token")
        da.search_opportunities = MagicMock(return_value=[])

        assert da.get_audit_session(23, opportunity_id=4242) is None
        # No privileged path, no unscoped read — just the ordinary by-id call.
        main_client.get_record_by_id.assert_any_call(
            23,
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
            opportunity_id=4242,
        )
        main_client.get_records.assert_not_called()

    def test_a_correct_hint_is_remembered_for_callers_that_have_none(self):
        found = FakeSession(id=24, opportunity_id=1978)
        main_client = MagicMock()
        main_client.get_record_by_id.side_effect = lambda sid, **kw: (
            found if kw.get("opportunity_id") == 1978 else None
        )

        da = _data_access(main_client, opportunity_id=None)
        da.search_opportunities = MagicMock(return_value=[{"id": i} for i in range(1970, 2000)])

        assert da.get_audit_session(24, opportunity_id=1978) is found
        main_client.get_record_by_id.reset_mock()
        da.search_opportunities.reset_mock()

        # A later caller with no hint rides the memo the hinted call populated.
        assert da.get_audit_session(24) is found
        assert main_client.get_record_by_id.call_count == 1
        da.search_opportunities.assert_not_called()

    def test_no_hint_behaves_exactly_as_before(self):
        found = FakeSession(id=25, opportunity_id=1973)
        main_client = MagicMock()
        main_client.get_record_by_id.return_value = found

        da = _data_access(main_client)

        assert da.get_audit_session(25) is found
        main_client.get_record_by_id.assert_called_once_with(
            25,
            experiment="audit",
            type="AuditSession",
            model_class=AuditSessionRecord,
            opportunity_id=None,
        )
