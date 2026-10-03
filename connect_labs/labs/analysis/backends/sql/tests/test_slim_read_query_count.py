"""A slim cache read is ONE query, not one per visit.

`_load_from_cache(skip_form_json=True)` defers `form_json`, and `_model_to_visit_dict`
then read `row.form_json` unconditionally to derive `xform_id` (#1909). On a deferred
field that is `refresh_from_db` -- a SELECT per row fetching the blob the defer
existed to skip. It returned the right answer, so every behavioural test passed.

Measured 2026-10-03 on opp 765 (138,748 visits): `workflow_ensure_visit_cache` spent
5-8 minutes per pipeline on a raw-cache HIT, the MCP client timed out, and a row
deleted by a concurrent rebuild mid-iteration raised `RawVisitCache.DoesNotExist`.

These assert the QUERY COUNT, because that is the property that broke.
"""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT

OPP = 990765
N = 30


def _seed(n: int) -> None:
    future = timezone.now() + timezone.timedelta(days=1)
    RawVisitCache.objects.bulk_create(
        RawVisitCache(
            opportunity_id=OPP,
            pipeline_id=USER_VISITS_RAW_SLOT,
            visit_count=n,
            expires_at=future,
            visit_id=str(80000 + i),
            username=f"flw{i}",
            form_json={"id": f"xform-{i}", "form": {"x": i}},
            visit_date="2024-01-15",
            status="approved",
        )
        for i in range(n)
    )


@pytest.mark.django_db
class TestSlimReadQueryCount:
    def test_slim_read_does_not_query_per_row(self):
        _seed(N)
        with CaptureQueriesContext(connection) as ctx:
            rows = SQLBackend()._load_from_cache(
                SQLCacheManager(OPP, config=None), skip_form_json=True, filter_visit_ids=None
            )
        assert len(rows) == N
        assert len(ctx.captured_queries) < N, (
            f"a slim read of {N} visits ran {len(ctx.captured_queries)} queries -- the deferred "
            "form_json is being refreshed once per row"
        )
        assert all(r["form_json"] == {} and r["xform_id"] is None for r in rows)

    def test_slim_read_survives_rows_deleted_mid_iteration(self):
        """A concurrent rebuild deleting the slot must not crash a reader already iterating."""
        _seed(N)
        manager = SQLCacheManager(OPP, config=None)
        qs = manager.get_raw_visits_queryset().defer("form_json")
        rows = list(qs)
        RawVisitCache.objects.filter(opportunity_id=OPP).delete()
        from connect_labs.labs.analysis.backends.sql.backend import _model_to_visit_dict

        out = [_model_to_visit_dict(r, skip_form_json=True) for r in rows]
        assert len(out) == N

    def test_full_read_still_derives_xform_id(self):
        """Guards against over-correcting: non-slim reads keep form_json and xform_id."""
        _seed(N)
        rows = SQLBackend()._load_from_cache(
            SQLCacheManager(OPP, config=None), skip_form_json=False, filter_visit_ids=None
        )
        assert {r["xform_id"] for r in rows} == {f"xform-{i}" for i in range(N)}
