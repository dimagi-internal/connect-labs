"""Single-visit and batch lookups must filter in SQL, not in Python.

`get_visit_data` used to fetch EVERY visit in the opportunity — with form_json, the
heaviest column — and linear-scan the resulting list for one id. `get_visits_batch`
did the same and then filtered. `fetch_raw_visits` has always accepted
`filter_visit_ids`; these two just didn't pass it.

That is the request shape that OOM-killed gunicorn workers on 2026-08-24 (453 of
them, from ~62k-visit materialisations). These tests pin the COST — that the id
filter reaches the query — because the return value looks identical either way and
a value-only assertion passes with the full scan restored.
"""

import contextlib
from unittest import mock

from connect_labs.audit.data_access import AuditDataAccess


@contextlib.contextmanager
def _dao(returned_visits):
    """An AuditDataAccess with no __init__ (no network) and a stub pipeline.

    `pipeline` is a read-only property, so it is patched on the CLASS for the
    duration of the test rather than assigned on the instance.
    """
    dao = AuditDataAccess.__new__(AuditDataAccess)
    dao.opportunity_id = 2155
    pipeline = mock.Mock()
    pipeline.fetch_raw_visits.return_value = returned_visits
    with mock.patch.object(AuditDataAccess, "pipeline", pipeline):
        yield dao, pipeline


def test_get_visit_data_asks_the_query_for_one_visit():
    with _dao([{"id": "77", "form_json": {}}]) as (dao, pipeline):
        got = dao.get_visit_data(77, opportunity_id=2155)

    assert got == {"id": "77", "form_json": {}}
    kwargs = pipeline.fetch_raw_visits.call_args.kwargs
    assert kwargs["filter_visit_ids"] == {77}, "the id filter must reach the query"
    assert kwargs["opportunity_id"] == 2155


def test_get_visits_batch_asks_the_query_for_just_those_visits():
    with _dao([{"id": "1"}, {"id": "2"}]) as (dao, pipeline):
        got = dao.get_visits_batch([1, 2], opportunity_id=2155)

    assert [v["id"] for v in got] == ["1", "2"]
    kwargs = pipeline.fetch_raw_visits.call_args.kwargs
    assert kwargs["filter_visit_ids"] == {1, 2}


def test_visit_cache_still_short_circuits_without_touching_the_pipeline():
    """The cheap path must stay cheap — a cache hit does no query at all."""
    with _dao([]) as (dao, pipeline):
        got = dao.get_visit_data(5, opportunity_id=2155, visit_cache={5: {"id": 5}})

    assert got == {"id": 5}
    pipeline.fetch_raw_visits.assert_not_called()


def test_str_and_int_ids_still_match():
    """RawVisitCache.visit_id is a CharField, so a cache hit returns str ids while
    callers pass ints. The lookup must still find the row."""
    with _dao([{"id": 77}]) as (dao, _):
        assert dao.get_visit_data(77, opportunity_id=2155) == {"id": 77}
