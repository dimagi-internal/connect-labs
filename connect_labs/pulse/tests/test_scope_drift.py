"""Pulse must say when it is tracking opportunities it can no longer read.

Cursors accumulate the union of everything any poller identity has ever seen.
When the poller changes -- or its Connect org membership does -- whatever falls
outside the new scope just 404s forever, and upstream a 404 means "not found OR
not yours" indistinguishably. Nothing named the cause.

Proven expensive: naming the poller explicitly (#1043, deployed 2026-07-29
15:58 UTC) moved Pulse onto an account with different memberships, and 110
opportunities -- 16 still active -- fell out of scope at 16:05 UTC and were
never ingested again. It went unnoticed for a month, and the backoff fix in
#1277 would have made it permanently quieter still.

These tests pin the signal that makes it visible.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from connect_labs.pulse import ingest
from connect_labs.pulse.api import _ingest_state
from connect_labs.pulse.models import TIER_COLD, PulseCursor, PulseScalar


def _cursors(*opportunity_ids):
    for oid in opportunity_ids:
        for endpoint in (ingest.VISITS_ENDPOINT, ingest.WORKS_ENDPOINT):
            PulseCursor.objects.create(opportunity_id=oid, endpoint=endpoint, tier=TIER_COLD)


def _drift_row():
    return PulseScalar.objects.get(key=ingest.SCALAR_SCOPE_DRIFT).value


@pytest.mark.django_db
class TestDetection:
    def test_opportunities_outside_the_entitled_set_are_counted(self):
        _cursors(1, 2, 3)

        value = ingest.record_scope_drift([1])

        assert value["count"] == 2
        assert value["opportunity_ids"] == [2, 3]
        assert value["since"]
        assert _drift_row()["count"] == 2

    def test_full_scope_reports_zero(self):
        _cursors(1, 2)

        value = ingest.record_scope_drift([1, 2])

        assert value["count"] == 0
        assert value["opportunity_ids"] == []
        assert value["since"] is None

    def test_two_endpoints_on_one_opportunity_count_once(self):
        """Cursors are per (opportunity, endpoint); the signal is opportunities."""
        _cursors(7)

        assert ingest.record_scope_drift([])["count"] == 1

    def test_an_entitled_opportunity_with_no_cursor_is_not_drift(self):
        """Drift is what we track and cannot read -- not what we could read and don't."""
        _cursors(1)

        assert ingest.record_scope_drift([1, 2, 3])["count"] == 0

    def test_none_ids_in_the_payload_are_ignored(self):
        _cursors(1)

        assert ingest.record_scope_drift([1, None])["count"] == 0


@pytest.mark.django_db
class TestSinceSurvivesRedetection:
    def test_since_is_held_across_polls(self):
        """The age is the whole point: "blind since when" must not reset every 5 min."""
        _cursors(1, 2)
        first = ingest.record_scope_drift([1])["since"]

        assert ingest.record_scope_drift([1])["since"] == first

    def test_since_is_held_even_as_the_drifted_set_grows(self):
        _cursors(1, 2, 3)
        first = ingest.record_scope_drift([1, 2])["since"]

        later = ingest.record_scope_drift([1])

        assert later["count"] == 2
        assert later["since"] == first

    def test_since_resets_once_scope_is_whole_again(self):
        _cursors(1, 2)
        ingest.record_scope_drift([1])
        assert ingest.record_scope_drift([1, 2])["since"] is None

        again = ingest.record_scope_drift([1])

        assert again["since"] is not None


@pytest.mark.django_db
class TestWriteDiscipline:
    def test_an_unchanged_verdict_does_not_rewrite_the_row(self):
        """PulseScalar carries auto_now and this runs every 5 minutes."""
        _cursors(1, 2)
        ingest.record_scope_drift([1])
        stamp = PulseScalar.objects.get(key=ingest.SCALAR_SCOPE_DRIFT).updated_at

        ingest.record_scope_drift([1])

        assert PulseScalar.objects.get(key=ingest.SCALAR_SCOPE_DRIFT).updated_at == stamp

    def test_a_changed_verdict_does_rewrite(self):
        _cursors(1, 2, 3)
        ingest.record_scope_drift([1, 2])
        stamp = PulseScalar.objects.get(key=ingest.SCALAR_SCOPE_DRIFT).updated_at

        ingest.record_scope_drift([1])

        assert PulseScalar.objects.get(key=ingest.SCALAR_SCOPE_DRIFT).updated_at > stamp

    def test_the_id_list_is_capped_but_the_count_is_not(self):
        _cursors(*range(1, 260))

        value = ingest.record_scope_drift([])

        assert value["count"] == 259
        assert len(value["opportunity_ids"]) == ingest._DRIFT_ID_SAMPLE
        assert value["truncated"] is True


@pytest.mark.django_db
class TestSurfacedToOperators:
    def test_health_payload_carries_the_drift(self):
        _cursors(1, 2, 3)
        ingest.record_scope_drift([1])

        state = _ingest_state()

        assert state["scope_drift_count"] == 2
        assert state["scope_drift_ids"] == [2, 3]
        assert state["scope_drift_since"]

    def test_drift_does_not_clear_the_live_badge(self, monkeypatch):
        """Ingest is healthy for everything in scope; this is configuration, not an outage.

        The poller is stubbed because ``_ingest_state`` independently forfeits
        the badge when it cannot resolve one, and there is no configured poller
        in a test database -- without this the assertion passes or fails for a
        reason that has nothing to do with drift.
        """
        monkeypatch.setattr("connect_labs.pulse.api.get_poller_user", lambda: SimpleNamespace(username="stub-poller"))
        _cursors(1, 2)
        ingest.record_scope_drift([1])
        ingest.record_success("tail")

        state = _ingest_state()

        assert state["scope_drift_count"] == 1
        assert state["live_ok"] is True

    def test_health_payload_is_safe_before_the_scalar_exists(self):
        state = _ingest_state()

        assert state["scope_drift_count"] == 0
        assert state["scope_drift_since"] == ""
        assert state["scope_drift_ids"] == []


@pytest.mark.django_db
def test_refresh_opportunities_records_drift(monkeypatch):
    """The check rides the poll that already holds the entitled list -- no extra call."""
    _cursors(1, 2)
    monkeypatch.setattr(
        ingest,
        "fetch_json",
        lambda *a, **k: {"organizations": [], "programs": [], "opportunities": [{"id": 1, "name": "kept"}]},
        raising=False,
    )
    monkeypatch.setattr(
        "connect_labs.pulse.client.fetch_json",
        lambda *a, **k: {
            "organizations": [],
            "programs": [],
            "opportunities": [{"id": 1, "name": "kept"}],
        },
    )

    ingest.refresh_opportunities(client=None)

    assert _drift_row()["count"] == 1
    assert _drift_row()["opportunity_ids"] == [2]
