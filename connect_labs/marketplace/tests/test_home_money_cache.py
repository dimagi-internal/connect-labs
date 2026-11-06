"""The program page's money figures are shared across workers, so no visitor pays the ~2s SUMs.

Measured on production 2026-11-06: committed_by_program ~1.2s and fixed_costs_by_program ~1.0s,
the second recomputed on every load of /labs/marketplace/.
"""

import pytest

from connect_labs.marketplace import programs, queries, tasks


@pytest.fixture(autouse=True)
def _clean():
    queries.invalidate()
    yield
    queries.invalidate()


@pytest.mark.django_db
@pytest.mark.parametrize(
    "public, private",
    [("committed_by_program", "_committed_by_program"), ("fixed_costs_by_program", "_fixed_costs_by_program")],
)
def test_a_second_worker_reads_the_shared_copy_instead_of_recomputing(monkeypatch, public, private):
    calls = []
    monkeypatch.setattr(queries, private, lambda: calls.append(1) or {"chc": 1})
    assert getattr(queries, public)() == {"chc": 1}
    # A different web worker: its own process cache is empty, Redis is not.
    queries._cache.update(dict.fromkeys(queries._CACHED))
    queries._cache["loaded_at"] = 0.0
    assert getattr(queries, public)() == {"chc": 1}
    assert calls == [1]


@pytest.mark.django_db
def test_the_warm_task_refreshes_the_money_figures(monkeypatch):
    seen = []
    monkeypatch.setattr(queries, "_committed_by_program", lambda: seen.append("committed") or {})
    monkeypatch.setattr(queries, "_fixed_costs_by_program", lambda: seen.append("fixed") or {})
    monkeypatch.setattr(queries, "spine_first_service", lambda refresh=False: {})
    tasks.warm_network()
    assert sorted(seen) == ["committed", "fixed"]


def test_lead_acid_battery_recycling_has_a_picture():
    img = programs.image("ulab")
    assert img and img["src"] == "images/programs/ulab.svg"


def test_the_design_section_carries_no_subtitle():
    assert {k: why for k, _, why in queries.STATES}["design"] == ""
