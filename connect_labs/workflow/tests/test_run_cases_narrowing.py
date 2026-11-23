"""``workflow_run_cases`` reads only what is asked, once: a drill-down evaluates the drilled
opportunity alone, the opportunities not yet kept are evaluated together in ONE pass,
and a repeat read evaluates nothing (``case_briefing.run_cases``).

Measured on the KMC phone report (12 synthetic opportunities, about 13,000 babies):
every call evaluated all twelve, one at a time, and answered in 70-150s with up to
330k characters, even for a person looking at one partner.
"""

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from connect_labs.workflow import case_briefing as cb
from connect_labs.workflow.tests.test_case_coaching_action import DANGER, PROPS, THRIVING, _danger_row

OPPS = [10122, 10123, 10131]
CONTEXT = {"props_doc": PROPS, "full_registry": {"display": {"entity": {"label_field": "case_name"}}}}


@pytest.fixture(autouse=True)
def clean_cache():
    cache.clear()
    yield
    cache.clear()


def _run():
    return SimpleNamespace(id=13296, period_end="2026-11-21", is_completed=False, snapshot=None, program_id=10121)


def _definition(opps=OPPS):
    return SimpleNamespace(id=13242, version=3, opportunity_ids=list(opps), data={}, name="KMC Programme")


def _cases(opp, workers=3, per=4):
    out = []
    for w in range(workers):
        for c in range(per):
            danger = c == 0
            out.append(
                _danger_row(
                    entity_id=f"{opp}-{w}-{c}",
                    opportunity_id=opp,
                    username=f"flw_{w:03d}",
                    case_name=f"Baby {opp}-{w}-{c}",
                    **{DANGER: danger, THRIVING: not danger},
                )
            )
    return out


class _Evaluation:
    """Stands in for the database: counts what is evaluated, and for which opportunities."""

    def __init__(self, cases_of=_cases):
        self.cases_of = cases_of
        self.many: list[list[int]] = []
        self.one: list[tuple[int, str | None]] = []
        self.roster: list[list[int]] = []

    def rows_of(self, r, *, opportunity_ids, as_of=None):
        self.many.append(list(opportunity_ids))
        return [c for o in opportunity_ids for c in self.cases_of(o)]

    def rows(self, r, *, opportunity_id, username=None, entity_ids=None, as_of=None):
        self.one.append((opportunity_id, username))
        return [c for c in self.cases_of(opportunity_id) if not username or c["username"] == username]

    def run_roster(self, wda, run, definition, opportunity_ids=None):
        self.roster.append(list(opportunity_ids))
        return {
            f"{o}::flw_{w:03d}": {"name": f"Worker {w}", "username": f"flw_{w:03d}", "opportunity_id": o}
            for o in opportunity_ids
            for w in range(3)
        }

    def read(self, user="manager", definition=None, **kw):
        with (
            patch("connect_labs.workflow.snapshot_builders.case_context", return_value=CONTEXT),
            patch("connect_labs.workflow.snapshot_builders.case_rows_of", side_effect=self.rows_of),
            patch("connect_labs.workflow.snapshot_builders.case_rows", side_effect=self.rows),
            patch("connect_labs.workflow.actions.run_roster", side_effect=self.run_roster),
        ):
            return cb.run_cases(
                SimpleNamespace(pk=user, username=user), MagicMock(), _run(), definition or _definition(), **kw
            )


def test_a_drill_down_evaluates_only_the_drilled_opportunity():
    ev = _Evaluation()
    out = ev.read(opportunity_ids=[10131], detail="summary")
    assert ev.many == [[10131]] and ev.roster == [[10131]]
    assert out["opportunity_ids"] == [10131]
    assert [o["opportunity_id"] for o in out["opportunities"]] == [10131]


def test_an_opportunity_outside_the_run_is_refused_before_any_evaluation():
    ev = _Evaluation()
    with pytest.raises(cb.CaseBriefingError) as e:
        ev.read(opportunity_ids=[10131, 999])
    assert e.value.code == "outside_run" and "999" in e.value.public_message
    assert ev.many == []


def test_the_programme_is_evaluated_in_one_pass_and_a_repeat_evaluates_nothing():
    ev = _Evaluation()
    first = ev.read(detail="summary")
    assert ev.many == [OPPS]
    second = ev.read(detail="summary")
    assert ev.many == [OPPS] and ev.roster == [OPPS]
    # Warm, nothing is resolved, evaluated or fetched: the answer is the same.
    assert set(second.pop("timing")) == {"total"}
    first.pop("timing")
    assert second == first
    # A drill-down after the programme read is answered from what that read kept.
    ev.read(opportunity_ids=[10123], detail="summary")
    assert ev.many == [OPPS] and ev.one == []


def test_only_the_opportunities_not_kept_are_evaluated():
    ev = _Evaluation()
    ev.read(opportunity_ids=[10131])
    ev.read()
    assert ev.many == [[10131], [10122, 10123]]


def test_one_worker_reads_only_their_opportunity():
    """From the whole opportunity's evaluation, as the coaching preview reads a case:
    a worker read cold and warm give the same answer."""
    ev = _Evaluation()
    out = ev.read(worker_keys=["10131::flw_001"])
    assert ev.many == [[10131]] and ev.one == [] and ev.roster == [[10131]]
    assert [w["key"] for w in out["workers"]] == ["10131::flw_001"]
    assert out["detail"] == "cases" and out["workers"][0]["cases"][0]["case_state"] == DANGER


def test_one_worker_of_a_kept_opportunity_costs_no_evaluation():
    ev = _Evaluation()
    ev.read(opportunity_ids=[10131])
    ev.read(worker_keys=["10131::flw_002"])
    assert ev.one == [] and ev.many == [[10131]]


def test_an_opportunity_with_no_cases_is_not_kept():
    """Its raw visits may be cold (they expire): a kept empty list would hide them."""
    ev = _Evaluation(cases_of=lambda o: [])
    ev.read(opportunity_ids=[10131])
    ev.read(opportunity_ids=[10131])
    assert ev.many == [[10131], [10131]]


def test_another_person_gets_their_own_evaluation():
    ev = _Evaluation()
    ev.read(user="a", opportunity_ids=[10131])
    ev.read(user="b", opportunity_ids=[10131])
    assert ev.many == [[10131], [10131]]


def test_an_edited_workflow_is_read_afresh():
    ev = _Evaluation()
    ev.read(opportunity_ids=[10131])
    edited = _definition()
    edited.version = 4
    ev.read(opportunity_ids=[10131], definition=edited)
    assert ev.many == [[10131], [10131]]


def test_the_summary_shape():
    out = _Evaluation().read(opportunity_ids=[10131], detail="summary")
    assert out["detail"] == "summary" and out["source"] == "live" and out["as_of"] == "2026-11-21"
    assert {s["name"] for s in out["case_states"]} >= {DANGER, THRIVING}
    assert "coach" not in out["case_states"][0]  # the guidance comes with the case lists
    (opp,) = out["opportunities"]
    assert opp["counts"] == {DANGER: 3, THRIVING: 9}
    assert opp["workers_with_cases"] == 3 and opp["more_workers"] == 0
    worker = opp["most_urgent_workers"][0]
    assert worker["name"] == "Worker 0" and worker["counts"] == {DANGER: 1, THRIVING: 3}
    top = worker["most_urgent_case"]
    assert top["case_state"] == DANGER and top["entity_id"] == "10131-0-0" and top["facts"]
    assert set(top) == {"entity_id", "name", "case_state", "date", "facts"}


def test_a_programme_summary_stays_small_and_fast():
    """The KMC phone report's shape -- 12 opportunities, ~40 workers each, ~13,000 cases --
    folds to a few KB (3 workers per opportunity) once the cases are evaluated."""
    opps = list(range(10122, 10134))
    big = {o: _cases(o, workers=40, per=27) for o in opps}
    ev = _Evaluation(cases_of=lambda o: big[o])
    ev.read(definition=_definition(opps), detail="summary")  # evaluates and keeps
    started = time.perf_counter()
    out = ev.read(definition=_definition(opps), detail="summary")
    elapsed = time.perf_counter() - started
    assert len(ev.many) == 1
    assert sum(len(v) for v in big.values()) > 12_000
    assert len(json.dumps(out)) < 20_000
    assert all(len(o["most_urgent_workers"]) == 3 and o["more_workers"] == 37 for o in out["opportunities"])
    assert elapsed < 2.0, f"a warm programme summary took {elapsed:.2f}s"


def test_an_overlapping_read_waits_for_the_one_running():
    """A second read of the same opportunities while the first is evaluating takes the
    first one's answer instead of evaluating them again."""
    ev = _Evaluation()
    source = cb.CaseSource(SimpleNamespace(pk="manager", username="manager"), MagicMock(), _run(), _definition())
    cache.add(source._key("fill", (10131,)), 1, 60)

    def the_first_read_finishes(_seconds):
        cache.set(source.cases_key(10131), _cases(10131), 60)
        cache.delete(source._key("fill", (10131,)))

    with patch.object(cb.time, "sleep", side_effect=the_first_read_finishes):
        out = ev.read(opportunity_ids=[10131], detail="summary")
    assert ev.many == [] and out["opportunities"][0]["counts"]


def test_a_wait_that_ends_evaluates_only_what_is_still_not_kept():
    """A fill that died (its claim outlives it) or kept only some opportunities: the
    waiter takes what was kept and evaluates the rest."""
    ev = _Evaluation()
    source = cb.CaseSource(SimpleNamespace(pk="manager", username="manager"), MagicMock(), _run(), _definition())
    cache.add(source._key("fill", tuple(OPPS)), 1, 60)
    cache.set(source.cases_key(10122), _cases(10122), 60)

    def the_claim_lapses(_seconds):
        cache.delete(source._key("fill", tuple(OPPS)))

    with patch.object(cb.time, "sleep", side_effect=the_claim_lapses):
        out = ev.read(detail="summary")
    assert ev.many == [[10123, 10131]]
    assert all(o["counts"] for o in out["opportunities"])
