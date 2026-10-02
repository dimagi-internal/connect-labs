"""`entity.worker`: which worker an entity counts for when several visited it.

An entity is one row, so at the worker scopes it counts for exactly one worker. The
engine used to pick the visitor whose username sorts first (`MIN(username)`), with no
way to change it. MBW's audit credits a mother to the worker of her LATEST visit, so a
mother handed from one FLW to another landed on a different FLW than the audit shows,
and a registry could not reproduce its per-worker numbers.

The fixture's mother m1 is visited by ada (1 and 5 Sep) and then bola (10 Sep); m2
only by bola. Everything here executes the compiled SQL against Postgres.
"""

from __future__ import annotations

import copy

import pytest

from connect_labs.semantic.compiler import model_problems
from connect_labs.semantic.tests.test_layer1_lookups_windows import (  # noqa: F401 -- mbw_cache is a fixture
    OPP,
    PROPS,
    _lookups,
    _visits_config,
    mbw_cache,
)

# The MBW-shaped fixture of the lookups/windows suite, imported so both run on one cache.
pytestmark = pytest.mark.usefixtures("mbw_cache")

INDICATORS = {
    "version": 1,
    "cube": "mbw_mother",
    "defaults": {"min_denominator": 1},
    "series": ["M"],
    "measures": [
        {
            "name": "m01",
            "type": "number",
            "sql": "1.0 * {m01_mothers}",
            "meta": {"indicator": "M01", "unit": "mothers", "direction": "higher"},
        },
        {"name": "m01_mothers", "type": "count"},
    ],
}


def _props(worker=None):
    props = copy.deepcopy(PROPS)
    if worker is not None:
        props["entity"]["worker"] = worker
    props["properties"] = [{"name": "is_mother", "type": "bool", "sql": "TRUE"}]
    return props


def _evaluate(worker=None, *, scope="flw", as_of="DATE '2026-09-30'", visit_filter=None):
    from django.db import connection

    from connect_labs.semantic.runtime import evaluate

    return evaluate(
        _visits_config(),
        [OPP],
        extra_fields=_lookups(),
        registry_documents=(_props(worker), INDICATORS),
        scope=scope,
        as_of=as_of,
        visit_filter=visit_filter,
        connection=connection,
    )


def _mothers_by_worker(rows):
    return {r["username"]: int(r["m01_mothers"]) for r in rows}


def test_the_default_still_credits_the_alphabetically_first_visitor():
    """No registry that leaves `worker` out may see its per-worker numbers move."""
    assert _mothers_by_worker(_evaluate()) == {"ada": 1, "bola": 1}
    assert _mothers_by_worker(_evaluate("alphabetical")) == {"ada": 1, "bola": 1}


def test_last_visit_credits_the_worker_who_has_the_mother_now():
    assert _mothers_by_worker(_evaluate("last_visit")) == {"bola": 2}


def test_first_visit_credits_the_worker_who_found_her():
    assert _mothers_by_worker(_evaluate("first_visit")) == {"ada": 1, "bola": 1}


def test_last_visit_is_as_of_the_report_date():
    """On 7 Sep bola had not yet visited m1, so she was still ada's."""
    rows = _evaluate("last_visit", as_of="DATE '2026-09-07'")
    assert _mothers_by_worker(rows) == {"ada": 1, "bola": 1}


@pytest.mark.parametrize("worker, owns", [("ada", set()), ("bola", {"m1", "m2"})])
def test_a_one_worker_read_returns_the_entities_that_worker_owns(worker, owns):
    """With `last_visit`, ada's case table must not show m1: dropping bola's visit
    before grouping would make m1 look like ada's. The worker's read is the same rows
    the cohort-wide `flw` scope gives that worker."""
    rows = _evaluate("last_visit", scope="case", visit_filter={"opportunity_id": OPP, "username": worker})
    assert {r["case_id"].split("|", 1)[1] for r in rows} == owns
    assert all(r["username"] == worker for r in rows)


def test_a_one_worker_read_keeps_the_alphabetical_rule_unchanged():
    """The default keeps filtering the scan, as it always has: ada's own visit set."""
    rows = _evaluate(scope="case", visit_filter={"opportunity_id": OPP, "username": "ada"})
    assert {r["case_id"].split("|", 1)[1] for r in rows} == {"m1"}


def test_the_worker_rule_validates():
    for worker in ("alphabetical", "first_visit", "last_visit"):
        assert model_problems(_props(worker)) == []
    problems = model_problems(_props("most_visits"))
    assert any("entity.worker" in p for p in problems), problems
