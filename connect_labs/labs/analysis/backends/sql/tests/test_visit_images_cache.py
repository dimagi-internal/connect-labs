"""Reading a case's photos must not re-download its whole opportunity.

The image reader asks "does this visit have a photo". Only a slot fetched WITH
images can answer that, and the backend used to decide by looking at the rows it
was asked about: no images on them meant "the cache cannot answer", so it
re-fetched the entire opportunity from Connect. For a case whose visits simply
have no photo -- most of them -- that happened on EVERY open. Measured on
2026-09-11: 40s for one case on opp 524, 23s of it outbound, on a web tier that
was already queueing.

The slot now records whether its fetch asked for images, so "no photo" is an
answer the cache can give.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

OPP = 970001


def _visit(vid: int, images: list | None = None) -> dict:
    return {
        "id": vid,
        "username": "flw",
        "visit_date": "2026-09-01",
        "status": "approved",
        "form_json": {"form": {"@name": "Record Visit Details"}},
        "images": images or [],
    }


@pytest.mark.django_db
class TestTheSlotRemembersHowItWasFilled:
    def test_a_fetch_without_images_marks_the_rows_unfetched(self):
        SQLCacheManager(OPP).store_raw_visits([_visit(1)], 1)
        assert RawVisitCache.objects.filter(opportunity_id=OPP, images_fetched=True).count() == 0
        assert SQLCacheManager(OPP).slot_has_image_data() is False

    def test_a_fetch_with_images_marks_them_even_when_a_visit_has_no_photo(self):
        SQLCacheManager(OPP).store_raw_visits([_visit(1), _visit(2, [{"url": "x"}])], 2, images_fetched=True)
        assert SQLCacheManager(OPP).slot_has_image_data() is True

    def test_the_batched_writer_carries_the_flag_too(self):
        cm = SQLCacheManager(OPP)
        cm.store_raw_visits_start(2, images_fetched=True)
        cm.store_raw_visits_batch([_visit(1), _visit(2)])
        cm.store_raw_visits_finalize(actual_count=2)
        assert SQLCacheManager(OPP).slot_has_image_data() is True

    def test_slots_are_per_pipeline(self):
        SQLCacheManager(OPP, pipeline_id=7).store_raw_visits([_visit(1)], 1, images_fetched=True)
        assert SQLCacheManager(OPP, pipeline_id=7).slot_has_image_data() is True
        assert SQLCacheManager(OPP, pipeline_id=8).slot_has_image_data() is False


@pytest.mark.django_db
class TestReadingPhotosFromAWarmSlot:
    def _warm(self, *, images_fetched: bool, with_photo: bool):
        SQLCacheManager(OPP).store_raw_visits(
            [_visit(1, [{"url": "photo"}] if with_photo else []), _visit(2)],
            2,
            images_fetched=images_fetched,
        )

    def _fetch(self, filter_ids):
        backend = SQLBackend()
        # A real list, not a bare MagicMock: the shrink guard inspects what came
        # back, and a mock makes it retry -- which would read as "called twice".
        fresh = [_visit(1, [{"url": "photo"}]), _visit(2)]
        with patch.object(SQLBackend, "_fetch_from_api", return_value=fresh) as from_api:
            rows = backend.fetch_raw_visits(
                opportunity_id=OPP,
                access_token="t",
                expected_visit_count=2,
                include_images=True,
                filter_visit_ids=filter_ids,
                accept_low_count=True,
            )
        return rows, from_api

    def test_a_case_with_no_photo_is_answered_from_cache(self):
        """The bug: this re-downloaded the opportunity every time."""
        self._warm(images_fetched=True, with_photo=True)
        rows, from_api = self._fetch({2})
        from_api.assert_not_called()
        assert [r["id"] for r in rows] == ["2"] or [str(r["id"]) for r in rows] == ["2"]

    def test_no_photos_anywhere_in_the_opportunity_is_still_an_answer(self):
        self._warm(images_fetched=True, with_photo=False)
        _rows, from_api = self._fetch({1, 2})
        from_api.assert_not_called()

    def test_a_slot_fetched_without_images_is_refetched_once(self):
        self._warm(images_fetched=False, with_photo=False)
        _rows, from_api = self._fetch({1})
        assert from_api.call_count == 1
        assert from_api.call_args.kwargs.get("include_images") is True

    def test_an_expired_slot_is_refetched(self):
        self._warm(images_fetched=True, with_photo=True)
        RawVisitCache.objects.filter(opportunity_id=OPP).update(
            expires_at=timezone.now() - timezone.timedelta(minutes=1)
        )
        _rows, from_api = self._fetch({1})
        assert from_api.call_count == 1
