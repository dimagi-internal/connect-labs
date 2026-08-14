"""Donor report derivation: windows, verification levels and deliverable maths."""

from __future__ import annotations

from datetime import date, datetime
from datetime import timezone as dt_timezone

import pytest

from connect_labs.pulse import reports
from connect_labs.pulse.api import _program_scope
from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulseReport, PulseRollup, PulseWork


def _req(params: dict):
    class _R:
        GET = params
        user = None

    return _R()


def _ts(y, m, d, h=12):
    return datetime(y, m, d, h, tzinfo=dt_timezone.utc)


@pytest.fixture
def opp(db):
    return PulseOpportunity.objects.create(
        opportunity_id=901, name="Cholera response", org_slug="pride", program_id=77, country="NG"
    )


def _work(opp, *, status="approved", created, approved_count=1, usd="1.00", usd_org="2.20", key=None):
    return PulseWork.objects.create(
        work_key=key or f"k{PulseWork.objects.count()}",
        opportunity_id=opp.opportunity_id,
        program_id=opp.program_id,
        org_slug=opp.org_slug,
        worker_hash="w1",
        country="NG",
        status=status,
        created_ts=created,
        approved_count=approved_count,
        usd_to_worker=usd,
        usd_to_org=usd_org,
    )


class TestWindow:
    def test_window_excludes_work_outside_it(self, opp):
        _work(opp, created=_ts(2026, 6, 1), key="before")
        _work(opp, created=_ts(2026, 7, 10), key="inside")
        _work(opp, created=_ts(2026, 9, 1), key="after")

        sc = _program_scope(_req({"from": "2026-06-25", "to": "2026-07-31"}))
        assert list(sc["works"].values_list("work_key", flat=True)) == ["inside"]

    def test_end_date_includes_its_own_day(self, opp):
        """A report titled "…- 31 July" must contain 31 July's deliveries."""
        _work(opp, created=_ts(2026, 7, 31, 23), key="last-day")

        sc = _program_scope(_req({"from": "2026-06-25", "to": "2026-07-31"}))
        assert sc["works"].count() == 1

    def test_unparseable_date_widens_rather_than_raising(self, opp):
        _work(opp, created=_ts(2026, 6, 1), key="w")
        sc = _program_scope(_req({"from": "last tuesday"}))
        assert sc["works"].count() == 1
        assert sc["window_from"] is None


class TestVerification:
    def test_work_level_rate_counts_every_status(self, opp):
        for i in range(9):
            _work(opp, status="approved", created=_ts(2026, 7, 2), key=f"a{i}")
        _work(opp, status="rejected", created=_ts(2026, 7, 2), key="r")

        sc = _program_scope(_req({}))
        v = reports._verification(sc)
        assert v.submitted == 10
        assert v.approved == 9
        assert v.rate == pytest.approx(0.9)

    def test_over_limit_is_reported_separately_from_rejected(self, opp):
        _work(opp, status="approved", created=_ts(2026, 7, 2), key="a")
        _work(opp, status="over_limit", created=_ts(2026, 7, 2), key="o")

        rows = {r["status"]: r for r in reports._verification(_program_scope(_req({}))).rows}
        assert rows["over_limit"]["kind"] == "over"
        assert rows["approved"]["kind"] == "verified"

    def test_unknown_status_still_counted(self, opp):
        """A status Connect adds later must not vanish from a 100% breakdown."""
        _work(opp, status="approved", created=_ts(2026, 7, 2), key="a")
        _work(opp, status="quarantined", created=_ts(2026, 7, 2), key="q")

        v = reports._verification(_program_scope(_req({})))
        assert v.submitted == 2
        assert sum(r["n"] for r in v.rows) == 2

    def test_visit_level_preferred_when_rollups_exist(self, opp):
        _work(opp, status="approved", created=_ts(2026, 7, 2), approved_count=1, key="a")
        PulseRollup.objects.create(
            bucket_hour=_ts(2026, 7, 2), opportunity_id=opp.opportunity_id, status="approved", n=40
        )
        PulseRollup.objects.create(
            bucket_hour=_ts(2026, 7, 2), opportunity_id=opp.opportunity_id, status="rejected", n=10
        )

        m = reports._metrics(_program_scope(_req({})))
        assert m.verification.visits_available
        assert m.verification.visit_rate == pytest.approx(0.8)
        # The headline is the visit count, not the single payment unit — this is
        # the KMC undercount the design exists to avoid.
        assert m.services == 40
        assert m.services_are_visits
        assert m.episodes == 1

    def test_falls_back_to_payment_units_without_rollups(self, opp):
        _work(opp, status="approved", created=_ts(2026, 7, 2), approved_count=3, key="a")

        m = reports._metrics(_program_scope(_req({})))
        assert not m.verification.visits_available
        assert m.services == 3
        assert not m.services_are_visits


class TestMoney:
    def test_total_paid_sums_worker_and_org_shares(self, opp):
        _work(opp, created=_ts(2026, 7, 2), usd="1.00", usd_org="2.20", key="a")

        m = reports._metrics(_program_scope(_req({})))
        assert m.total_paid == pytest.approx(3.20)
        # The reference report's "cost per verified visit" tile.
        assert m.cost_per_service == pytest.approx(3.20)

    def test_unapproved_work_is_never_counted_as_delivery(self, opp):
        _work(opp, status="rejected", created=_ts(2026, 7, 2), usd="9.00", usd_org="9.00", key="r")

        m = reports._metrics(_program_scope(_req({})))
        assert m.services == 0
        assert m.total_paid == 0


class TestDeliverables:
    def _metrics(self, services=100, episodes=40, workers=16):
        m = reports.Metrics()
        m.services, m.episodes, m.workers = services, episodes, workers
        return m

    def test_multiplier_against_verified_services(self):
        rows = [{"label": "ORS co-packs", "basis": PulseReport.BASIS_SERVICES, "multiplier": 2}]
        assert reports.resolve_deliverables(rows, self._metrics())[0].value == 200

    def test_one_to_one_is_the_default(self):
        rows = [{"label": "Aqua Tabs", "basis": PulseReport.BASIS_SERVICES}]
        assert reports.resolve_deliverables(rows, self._metrics())[0].value == 100

    def test_episode_basis_uses_payment_units(self):
        rows = [{"label": "Kits", "basis": PulseReport.BASIS_WORKS, "multiplier": 1}]
        assert reports.resolve_deliverables(rows, self._metrics())[0].value == 40

    def test_override_wins_and_is_flagged_manual(self):
        rows = [{"label": "Referrals", "basis": PulseReport.BASIS_SERVICES, "multiplier": 2, "override": "1777"}]
        out = reports.resolve_deliverables(rows, self._metrics())[0]
        assert out.value == 1777
        assert out.is_manual

    def test_manual_basis_has_no_derived_value(self):
        rows = [{"label": "Camps served", "basis": PulseReport.BASIS_MANUAL}]
        out = reports.resolve_deliverables(rows, self._metrics())[0]
        assert out.value is None
        assert out.is_manual

    def test_unlabelled_rows_are_dropped(self):
        rows = [{"label": "  ", "basis": PulseReport.BASIS_SERVICES}]
        assert reports.resolve_deliverables(rows, self._metrics()) == []

    def test_garbage_multiplier_falls_back_to_one(self):
        rows = [{"label": "X", "basis": PulseReport.BASIS_SERVICES, "multiplier": "two"}]
        assert reports.resolve_deliverables(rows, self._metrics())[0].value == 100


class TestScopeParams:
    def test_report_scope_round_trips_through_the_shared_resolver(self, db):
        report = PulseReport(
            slug="s", program_id=77, org_slug="pride", window_start=date(2026, 6, 25), window_end=date(2026, 7, 31)
        )
        params = report.scope_params()
        assert params == {"program": "77", "org": "pride", "from": "2026-06-25", "to": "2026-07-31"}


class TestBackfillOrdering:
    """Rollups must be built before the fold deletes the events they read.

    Guards the ordering bug in ``pulse_backfill``: folding first left days
    31..N with no rollup rows at all, permanently, while the command reported
    success.
    """

    def test_rollups_are_rebuilt_before_events_are_folded(self, opp, monkeypatch, settings):
        settings.PULSE_EVENT_RETENTION_DAYS = 30
        from connect_labs.pulse import ingest

        old = _ts(2026, 1, 5)
        PulseEvent.objects.create(
            connect_visit_id=1,
            opportunity_id=opp.opportunity_id,
            program_id=opp.program_id,
            field_ts=old,
            sync_ts=old,
            status="approved",
            lat=11.8,
            lon=13.1,
            country="NG",
        )

        calls = []
        real_rollups, real_fold = ingest.rebuild_rollups, ingest.fold_events_to_grid
        monkeypatch.setattr(
            ingest, "rebuild_rollups", lambda *a, **k: (calls.append("rollup"), real_rollups(*a, **k))[1]
        )
        monkeypatch.setattr(
            ingest, "fold_events_to_grid", lambda *a, **k: (calls.append("fold"), real_fold(*a, **k))[1]
        )

        from connect_labs.pulse.management.commands.pulse_backfill import Command

        cmd = Command()
        monkeypatch.setattr(cmd, "_report", lambda: None)
        cmd.handle(works=False, visits=False, countries=False, all=False, days=365, fold=True)

        assert calls == ["rollup", "fold"], "the fold must not run before the rollup pass"
        # And the historical event survived into a rollup rather than being lost.
        assert PulseRollup.objects.filter(status="approved").exists()
