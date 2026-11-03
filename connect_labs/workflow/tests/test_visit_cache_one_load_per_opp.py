"""Warming a workflow loads an opportunity's visit export once, not once per pipeline.

`fetch_raw_visits` does not use `pipeline_id` to choose the slot, so a workflow with six
pipelines re-read one export six times, materialising every row to take a count. On
2026-11-03 that was 146,400 visits six times in one web request, and it pinned a task (#2810).

These pin the COST, so they count the expensive call rather than checking the answer: the
answer was right the whole time.
"""

from __future__ import annotations

import pytest

from connect_labs.workflow import visit_cache


class _Backend:
    def __init__(self):
        self.calls = []

    def fetch_raw_visits(self, opportunity_id, **kwargs):
        self.calls.append(opportunity_id)
        return [{}] * 3


@pytest.fixture
def backend(monkeypatch):
    import connect_labs.labs.analysis.pipeline as pipeline_module

    fake = _Backend()

    class FakeAnalysis:
        def __init__(self, request=None, access_token=None, cchq_access_token=None):
            self.backend = fake

        def expected_visits_for(self, opp):
            return 3

    monkeypatch.setattr(pipeline_module, "AnalysisPipeline", FakeAnalysis)
    return fake


def _fetch_raw():
    return visit_cache._default_slot("token", {"opportunity_id": 765}, None)[1]


def test_every_pipeline_after_the_first_costs_no_load(backend):
    fetch_raw = _fetch_raw()

    counts = [fetch_raw(765, pipeline_id) for pipeline_id in (1, 2, 3, 4, 5, 6)]

    assert backend.calls == [765]
    assert counts == [3] * 6, "every pipeline still reports the opportunity's count"


def test_each_opportunity_is_still_loaded(backend):
    fetch_raw = _fetch_raw()

    fetch_raw(765, 1)
    fetch_raw(874, 1)
    fetch_raw(765, 2)

    assert backend.calls == [765, 874]


def test_a_failed_load_is_not_remembered(backend, monkeypatch):
    fetch_raw = _fetch_raw()
    healthy = backend.fetch_raw_visits

    def boom(opportunity_id, **kwargs):
        raise RuntimeError("export failed")

    monkeypatch.setattr(backend, "fetch_raw_visits", boom)
    with pytest.raises(RuntimeError):
        fetch_raw(765, 1)

    monkeypatch.setattr(backend, "fetch_raw_visits", healthy)
    assert fetch_raw(765, 2) == 3


def test_a_new_warm_loads_again(backend):
    """The memo lives for one warm: expiry and top-ups must still be seen next time."""
    _fetch_raw()(765, 1)
    _fetch_raw()(765, 1)

    assert backend.calls == [765, 765]
