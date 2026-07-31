"""The completed_works spine and the grid rollup.

Both exist to answer the same question: how do we show scale, money and
geography without holding beneficiary-level records? `completed_works` carries
money and payment status at ~53 B/row with no form data; the grid carries
geography as counts-per-cell after the visit rows expire.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.pulse import ingest
from connect_labs.pulse.models import PulseEvent, PulseGridCell, PulseOpportunity, PulseWork
from connect_labs.pulse.normalize import work_key_for, work_to_fields

# Shape taken from a real /export/opportunity/765/completed_works/ row.
# entity_id carries a beneficiary name and phone, as production does.
WORK = {
    "opportunity_id": 765,
    "username": "85e863299d1540774fed",
    "entity_id": "Amina Bello - 08031110001 - 1 month visit",
    "entity_name": "Amina Bello - 08031110001",
    "payment_unit_id": 994,
    "status": "approved",
    "date_created": "2026-07-28T13:32:52.369975Z",
    "status_modified_date": "2026-07-28T13:32:52.520841Z",
    "payment_date": None,
    "last_modified": "2026-07-28T13:32:52.394066Z",
    "saved_completed_count": 1,
    "saved_approved_count": 1,
    "saved_payment_accrued": 900,
    "saved_payment_accrued_usd": "0.65",
    "saved_org_payment_accrued_usd": "0.00",
    "reason": None,
}


class FakeWorksClient:
    """Mimics the endpoint's quirk: rows carry no `id`, but `next` does."""

    def __init__(self, rows, page_size=2):
        self.rows = rows
        self.page_size = page_size
        self.paths = []

    def _page(self, offset):
        return self.rows[offset : offset + self.page_size]


@pytest.fixture
def opp(db):
    return PulseOpportunity.objects.create(
        opportunity_id=765, name="Mother Baby Wellness (Nigeria)", org_slug="connect-nigeria", program_id=42
    )


@pytest.mark.django_db
class TestWorkKey:
    def test_key_is_stable_for_the_same_work(self):
        assert work_key_for(WORK) == work_key_for(dict(WORK))

    def test_key_differs_per_beneficiary(self):
        other = {**WORK, "entity_id": "Chidinma Okeke - 08031110002 - 1 month visit"}
        assert work_key_for(WORK) != work_key_for(other)

    def test_key_differs_per_payment_unit(self):
        assert work_key_for(WORK) != work_key_for({**WORK, "payment_unit_id": 995})

    def test_key_does_not_contain_its_inputs(self):
        """The tuple includes a beneficiary name and phone; the key must not."""
        key = work_key_for(WORK)
        assert "Amina" not in key
        assert "08031110001" not in key
        assert len(key) == 64  # sha256 hex


@pytest.mark.django_db
class TestWorkNormalisation:
    def test_maps_money_and_status(self, opp):
        fields = work_to_fields(WORK, opp)
        assert fields["status"] == "approved"
        assert str(fields["usd_to_worker"]) == "0.65"
        assert fields["approved_count"] == 1
        assert fields["org_slug"] == "connect-nigeria"

    def test_carries_no_beneficiary_identity(self, opp):
        fields = work_to_fields(WORK, opp)
        blob = " ".join(str(v) for v in fields.values())
        assert "Amina" not in blob
        assert "08031110001" not in blob

    def test_pulse_work_has_no_column_for_identity(self):
        actual = {f.name for f in PulseWork._meta.get_fields()}
        assert "entity_id" not in actual
        assert "entity_name" not in actual

    def test_rejects_row_without_a_timestamp(self, opp):
        assert work_to_fields({**WORK, "date_created": None}, opp) is None

    def test_null_payment_date_is_allowed(self, opp):
        """Unpaid work is the normal case; it must not be dropped."""
        assert work_to_fields(WORK, opp)["payment_date"] is None


@pytest.mark.django_db
class TestWorkStorage:
    def test_stores_and_dedupes(self, opp):
        rows = [WORK, dict(WORK)]  # same work seen twice
        ingest._store_works(rows, opp)
        assert PulseWork.objects.count() == 1

    def test_restatement_updates_status_rather_than_being_ignored(self, opp):
        """Works mutate: pending -> approved, and payment_date arrives later.
        Discarding a re-seen row would freeze work at 'pending' forever."""
        ingest._store_works([{**WORK, "status": "pending", "saved_payment_accrued_usd": "0.00"}], opp)
        assert PulseWork.objects.get().status == "pending"

        ingest._store_works([{**WORK, "status": "approved", "payment_date": "2026-07-29T00:00:00Z"}], opp)
        row = PulseWork.objects.get()
        assert row.status == "approved"
        assert row.payment_date is not None
        assert PulseWork.objects.count() == 1

    def test_cursor_is_recovered_from_the_next_link(self):
        """completed_works omits `id` from rows but keysets on it internally,
        so the only place the cursor exists is the `next` URL."""
        assert ingest._last_id_from_next("https://x.com/e/?page_size=5&last_id=997551&cursor_order=forward") == 997551
        assert ingest._last_id_from_next("https://x.com/e/?last_id=42") == 42
        assert ingest._last_id_from_next(None) is None
        assert ingest._last_id_from_next("https://x.com/e/?page_size=5") is None


@pytest.mark.django_db
class TestGridFold:
    def _event(self, vid, lat, lon, days_old, country="NG"):
        ts = timezone.now() - timedelta(days=days_old)
        return PulseEvent.objects.create(
            connect_visit_id=vid,
            opportunity_id=765,
            field_ts=ts,
            sync_ts=ts,
            lat=lat,
            lon=lon,
            country=country,
            status="approved",
        )

    def test_folds_old_events_and_deletes_them(self):
        for i in range(5):
            self._event(i, 11.0330, 7.6380, days_old=60)
        self._event(99, 11.0330, 7.6380, days_old=1)  # inside retention

        result = ingest.fold_events_to_grid()

        assert result["folded"] == 5
        assert PulseEvent.objects.count() == 1  # recent one survives
        cell = PulseGridCell.objects.get()
        assert cell.n == 5

    def test_nearby_points_share_a_cell(self):
        """~1km binning: a village is one cell, not fifty households."""
        self._event(1, 11.0330, 7.6380, 60)
        self._event(2, 11.0334, 7.6382, 60)
        ingest.fold_events_to_grid()
        assert PulseGridCell.objects.count() == 1
        assert PulseGridCell.objects.get().n == 2

    def test_distant_points_do_not(self):
        self._event(1, 11.03, 7.63, 60)
        self._event(2, 12.00, 8.52, 60)
        ingest.fold_events_to_grid()
        assert PulseGridCell.objects.count() == 2

    def test_folding_is_idempotent(self):
        """A retry after partial failure must not double-count the map."""
        for i in range(4):
            self._event(i, 11.03, 7.63, 60)
        ingest.fold_events_to_grid()
        ingest.fold_events_to_grid()
        assert PulseGridCell.objects.get().n == 4

    def test_accumulates_across_folds(self):
        """The map gets denser over time even though rows keep expiring."""
        for i in range(3):
            self._event(i, 11.03, 7.63, 60)
        ingest.fold_events_to_grid()
        for i in range(10, 12):
            self._event(i, 11.03, 7.63, 60)
        ingest.fold_events_to_grid()
        assert PulseGridCell.objects.get().n == 5
        assert PulseEvent.objects.count() == 0

    def test_events_without_gps_are_still_expired(self):
        """No coordinate means nothing to fold, but the row must not linger."""
        ts = timezone.now() - timedelta(days=60)
        PulseEvent.objects.create(connect_visit_id=500, opportunity_id=765, field_ts=ts, sync_ts=ts, status="approved")
        ingest.fold_events_to_grid()
        assert PulseEvent.objects.count() == 0
        assert PulseGridCell.objects.count() == 0

    def test_cell_tracks_its_time_span(self):
        self._event(1, 11.03, 7.63, 90)
        self._event(2, 11.03, 7.63, 45)
        ingest.fold_events_to_grid()
        cell = PulseGridCell.objects.get()
        assert cell.first_ts < cell.last_ts

    def test_grid_holds_no_beneficiary_level_detail(self):
        """The whole point: a cell cannot be resolved back to a household.

        `program_id` was added deliberately so a filtered map narrows its
        density as well as its points. It does not weaken this: a programme
        spans dozens of opportunities and hundreds of thousands of services, so
        knowing "these 412 services in this ~1.1 km cell belonged to ECD
        Nigeria 2025" is coarser than the delivery type already stored, not
        finer. Anything that narrowed toward a household — an opportunity id, a
        worker, a timestamp per point — would not belong here, which is what
        this list exists to enforce.
        """
        self._event(1, 11.0330133, 7.6380900, 60)
        ingest.fold_events_to_grid()
        fields = {f.name for f in PulseGridCell._meta.get_fields()}
        assert fields == {
            "id",
            "lat_q",
            "lon_q",
            "country",
            "service_slug",
            "n",
            "approved_n",
            "flagged_n",
            "first_ts",
            "last_ts",
            "program_id",
        }
        cell = PulseGridCell.objects.get()
        # Binned to ~1.1km, so the original coordinate is not recoverable.
        assert cell.lat != pytest.approx(11.0330133)


@pytest.mark.django_db
class TestBatchDedup:
    """A duplicate key inside one batch must not fail the whole batch.

    Postgres rejects ON CONFLICT DO UPDATE when a single statement proposes the
    same key twice, so an unlucky page boundary would otherwise lose every row
    in that batch, not just the duplicate.
    """

    def test_duplicate_within_one_batch_is_collapsed(self, opp):
        ingest._store_works([WORK, dict(WORK), dict(WORK)], opp)
        assert PulseWork.objects.count() == 1

    def test_last_occurrence_wins(self, opp):
        """Rows arrive in ascending id order, so the last is the freshest."""
        ingest._store_works([{**WORK, "status": "pending"}, {**WORK, "status": "approved"}], opp)
        assert PulseWork.objects.get().status == "approved"

    def test_distinct_works_in_one_batch_all_survive(self, opp):
        rows = [{**WORK, "entity_id": f"Person {i} - 0803111000{i}"} for i in range(5)]
        ingest._store_works(rows, opp)
        assert PulseWork.objects.count() == 5


@pytest.mark.django_db
class TestOpportunityCountry:
    """PulseOpportunity.country is derived from visit GPS.

    Nothing in the export states an opportunity's country, and the field being
    present but never populated is worse than absent — every country-scoped
    card would render empty without erroring.
    """

    def _visit(self, vid, opp_id, country):
        ts = timezone.now()
        return PulseEvent.objects.create(
            connect_visit_id=vid,
            opportunity_id=opp_id,
            field_ts=ts,
            sync_ts=ts,
            lat=11.0,
            lon=7.6,
            country=country,
            status="approved",
        )

    def test_sets_country_from_modal_visit_country(self, opp):
        for i in range(5):
            self._visit(i, 765, "NG")
        assert ingest.refresh_opportunity_countries() == 1
        opp.refresh_from_db()
        assert opp.country == "NG"

    def test_minority_gps_noise_does_not_win(self, opp):
        for i in range(10):
            self._visit(i, 765, "NG")
        self._visit(99, 765, "KE")
        ingest.refresh_opportunity_countries()
        opp.refresh_from_db()
        assert opp.country == "NG"

    def test_is_idempotent(self, opp):
        for i in range(3):
            self._visit(i, 765, "NG")
        assert ingest.refresh_opportunity_countries() == 1
        assert ingest.refresh_opportunity_countries() == 0

    def test_works_inherit_country_from_their_opportunity(self, opp):
        for i in range(3):
            self._visit(i, 765, "NG")
        ingest.refresh_opportunity_countries()
        opp.refresh_from_db()
        ingest._store_works([WORK], opp)
        assert PulseWork.objects.get().country == "NG"
