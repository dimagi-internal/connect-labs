"""The streaming path must not hold an opportunity's visits in memory.

These pin the COST, not the behaviour. The behaviour (right counts, right cache
rows) is covered by test_stream_raw_visits.py and test_raw_fetch_shrink_guard.py,
and all of it kept passing while the tier was OOM-killing gunicorn workers three
times in eight hours on 2026-09-08 — because the defect was never a wrong answer,
it was an allocation proportional to the dataset.

Two shapes, both of which returned the whole opportunity to compute one integer
(dimagi-internal/connect-labs#1575):

  * cache MISS -> ``slim_dicts.extend(batch)`` accumulated every page.
  * cache HIT  -> ``_load_from_cache()`` SELECTed every row back out of Postgres.

Nothing downstream ever read an element: ``process_and_cache`` documents that
``visit_dicts are only used for len()`` once the rows are already stored, and on
this path they always are. So the assertions below are deliberately about TYPE and
about which methods get called, not about values.
"""

import pytest
from django.test import override_settings

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

OPP_ID = 77
PIPELINE_ID = 2002


def _page(n_rows, start_id=1, next_url=None):
    return {
        "next": next_url,
        "results": [
            {
                "id": start_id + i,
                "opportunity_id": OPP_ID,
                "username": f"flw{start_id + i}",
                "form_json": {"id": f"xform-{start_id + i}"},
                "images": [],
            }
            for i in range(n_rows)
        ],
    }


@pytest.mark.django_db
@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_download_path_yields_a_count_and_retains_no_rows(httpx_mock):
    """A cache MISS must report a number, whatever the dataset size."""
    httpx_mock.add_response(
        url=f"https://connect.example.com/export/opportunity/{OPP_ID}/user_visits/?page_size=2500",
        json=_page(50, start_id=1),
    )

    backend = SQLBackend()
    events = list(backend.stream_raw_visits(opportunity_id=OPP_ID, access_token="t", expected_visit_count=50))

    complete = [e for e in events if e[0] == "complete"]
    assert len(complete) == 1
    payload = complete[0][1]

    # An int, not a container. `len(payload) == 50` would pass for a list of 50
    # visit dicts, which is exactly the regression this guards, so assert the type.
    assert payload == 50
    assert isinstance(payload, int)
    assert not hasattr(payload, "__len__")

    # The rows still had to land in Postgres -- leaner must not mean lossy.
    assert RawVisitCache.objects.filter(opportunity_id=OPP_ID).count() == 50


@pytest.mark.django_db
@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
def test_cache_hit_counts_in_sql_instead_of_materialising_every_row(monkeypatch):
    """A cache HIT must answer from a COUNT, never by loading the rows.

    This is the hotter of the two paths: a burst of concurrent streams on one
    opportunity mostly hits cache, and each hit was pulling the entire opportunity
    into Python to be counted and thrown away.
    """
    manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
    manager.store_raw_visits(
        visit_dicts=[{"id": i, "username": f"flw{i}", "form_json": {}} for i in range(1, 31)],
        visit_count=30,
    )

    called = []
    monkeypatch.setattr(
        SQLBackend,
        "_load_from_cache",
        lambda self, *a, **k: called.append(1) or [],
    )

    backend = SQLBackend()
    events = list(
        backend.stream_raw_visits(
            opportunity_id=OPP_ID,
            access_token="t",
            expected_visit_count=30,
            pipeline_id=PIPELINE_ID,
        )
    )

    assert events[-1] == ("cached", 30)
    assert isinstance(events[-1][1], int)
    assert called == [], "_load_from_cache materialises every row; a cache hit only needs COUNT(*)"


@pytest.mark.django_db
def test_peer_rebuild_lend_can_answer_with_a_count_only(monkeypatch):
    """The single-flight lend path is where concurrent streams pile up.

    ``count_only=True`` must return the number without loading the rows; the
    list-returning form is still used by fetch_raw_visits and stays intact.
    """
    manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
    manager.store_raw_visits(
        visit_dicts=[{"id": i, "username": f"flw{i}", "form_json": {}} for i in range(1, 13)],
        visit_count=12,
    )

    called = []
    monkeypatch.setattr(
        SQLBackend,
        "_load_from_cache",
        lambda self, *a, **k: called.append(1) or [],
    )

    backend = SQLBackend()
    lent = backend._lend_cache_during_peer_rebuild(manager, OPP_ID, PIPELINE_ID, skip_form_json=True, count_only=True)

    assert lent == 12
    assert isinstance(lent, int)
    assert called == [], "count_only must not load rows"
