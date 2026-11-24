"""The service: stored inputs in, the same world as the browser engine out, and writes the rules allow."""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from connect_labs.audit_reviews import service
from connect_labs.audit_reviews.models import AuditSource, Decision, Review, SourceRun, TeamEvent

CASES = {c["name"]: c for c in json.loads((Path(__file__).parent / "parity" / "cases.json").read_text("utf-8"))}

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fake KC Pulse runs, shaped as the run-history projection gives them
# ---------------------------------------------------------------------------


def _measures(metrics):
    return [
        {
            "id": mid,
            "title": m["title"],
            "unit": m["unit"],
            "direction": m["direction"],
            "bands": m["bands"],
            "min_denominator": m["min_den"],
        }
        for mid, m in metrics.items()
    ]


def kc_state(run, metrics):
    return {
        "snapshot.meta": {"as_of": run["period_end"]},
        "snapshot.series.WINDOW.programme": run["programme"],
        "snapshot.series.WINDOW.byLLO": [{"llo": k, "ind": v} for k, v in run["llo"].items()],
        "snapshot.series.WINDOW.byOpp": [
            {"opp": int(k), "llo": _llo_of(k), "ind": v} for k, v in run["opportunity"].items()
        ],
        "snapshot.series.WINDOW.byFLW": [
            {
                "key": w["key"],
                "flw": w["username"],
                "name": w["name"],
                "opp": int(w["opp"]),
                "llo": w["llo"],
                "ind": w["cells"],
            }
            for w in run["worker"]
        ],
        "snapshot.series.WINDOW.measures": _measures(metrics),
    }


_OPP_LLO = {}


def _llo_of(opp):
    return _OPP_LLO[str(opp)]


class FakeDao:
    def __init__(self, case, runs=None, legacy_state=None):
        for o in case["D"]["source"]["opportunities"]:
            _OPP_LLO[str(o["id"])] = o["llo"]
        self.case = case
        self.runs = case["D"]["runs"] if runs is None else runs
        self.legacy_state = legacy_state

    def list_runs(self, definition_id):
        return [
            SimpleNamespace(
                id=r["run_id"],
                is_completed=True,
                period_start=r["period_start"],
                period_end=r["period_end"],
                completed_at=f"{r['period_end']}T09:00:00Z",
                kc=r,
            )
            for r in self.runs
        ]

    def get_run(self, run_id):
        return SimpleNamespace(id=run_id, state=self.legacy_state or {})


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    def read(run):
        case_metrics = next(iter(CASES.values()))["D"]["metrics"]
        return kc_state(run.kc, case_metrics)

    monkeypatch.setattr(service, "read_run_state", read)


def definition(workflow_id=900, **config):
    cfg = {"audit_source_workflow_id": 800, "read_workflows": [800], "audit_program_name": "Generated program"}
    cfg.update(config)
    return SimpleNamespace(id=workflow_id, data={"config": cfg})


def setup_for(case):
    D = case["D"]
    return {"pool": D["pool"], "parents": D["parents"], "supporting": D["supporting"], "known": D["knownMetrics"]}


def source_for(case, **config):
    go_live = case["opts"]["goLiveRunId"]
    d = definition(audit_go_live_run_id=None if go_live == "none" else go_live, audit_setup=setup_for(case), **config)
    source = service.open_source(d, opportunity_id=10301)
    if case["saved"]:
        source.settings = case["saved"]
        source.save()
    return source, d


def store_decisions(source, decisions):
    for kind, key in (("open", "opens"), ("close", "closes"), ("dismiss", "dismissed")):
        for rid, ents in decisions[key].items():
            for ent, v in ents.items():
                Decision.objects.create(
                    source=source,
                    kind=kind,
                    run_id=int(rid),
                    entity=ent,
                    review_number=v.get("num"),
                    status=v.get("status", ""),
                    decided_by=v["by"],
                    decided_at=service.from_ms(v["at"]),
                )


def _norm(x):
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items() if v is not None}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    return x


# ---------------------------------------------------------------------------
# The same world as the browser
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name", ["go_live_mid_history", "go_live_first_run", "stricter_settings", "saved_empty_lists"]
)
def test_recompute_matches_the_browser_engine(name):
    case = CASES[name]
    source, d = source_for(case)
    service.ingest_runs(source, FakeDao(case))
    store_decisions(source, case["decisions"])
    payload = service.recompute(source).payload

    want = case["world"]
    got_reviews = [{k: v for k, v in r.items() if k != "key"} for r in payload["reviews"]]
    assert _norm(got_reviews) == _norm(want["reviews"])
    assert _norm(payload["latestProposals"]) == _norm(want["proposalsByRun"][want["latestRi"]])
    assert _norm(payload["history"]) == _norm(want["history"])
    assert payload["goLiveIdx"] == want["goLiveIdx"]
    # Derived rows agree with the payload.
    rows = {r.number: r for r in Review.objects.filter(source=source)}
    assert set(rows) == {r["id"] for r in want["reviews"]}
    assert {n: r.status for n, r in rows.items()} == {r["id"]: r["status"] for r in want["reviews"]}


def test_no_go_live_is_a_preview_of_the_latest_run():
    case = CASES["preview_no_go_live"]
    source, d = source_for(case)
    service.sync(source, FakeDao(case), d)
    payload = service.world(source)
    assert payload["preview"] is True
    assert payload["goLiveIdx"] == len(payload["runs"]) - 1
    with pytest.raises(service.Refused):
        service.record_decisions(source, [{"kind": "open", "entity": "program"}], user=None, by="Staff")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


def test_sync_freezes_new_runs_once_and_follows_deletions():
    case = CASES["go_live_mid_history"]
    source, d = source_for(case)
    runs = case["D"]["runs"]
    assert service.sync(source, FakeDao(case, runs=runs[:8]), d) is True
    assert SourceRun.objects.filter(source=source).count() == 8
    assert service.sync(source, FakeDao(case, runs=runs[:8]), d) is False  # nothing new, world current
    service.sync(source, FakeDao(case, runs=runs[:5] + runs[6:10]), d)  # one dropped, two added
    assert sorted(SourceRun.objects.filter(source=source).values_list("run_id", flat=True)) == sorted(
        r["run_id"] for r in runs[:5] + runs[6:10]
    )


def test_one_run_per_week_newest_copy_wins():
    case = CASES["go_live_first_run"]
    source, d = source_for(case)
    service.ingest_runs(source, FakeDao(case, runs=case["D"]["runs"][:3]))
    first = case["D"]["runs"][1]
    SourceRun.objects.create(
        source=source,
        run_id=99999,
        period_start=first["period_start"],
        period_end=first["period_end"],
        completed_at=f"{first['period_end']}T23:00:00Z",
        data={**SourceRun.objects.get(run_id=first["run_id"]).data},
    )
    SourceRun.objects.create(  # a monthly run is not a week
        source=source, run_id=99998, period_start="2026-06-01", period_end="2026-06-30", completed_at="x", data={}
    )
    assert [sr.run_id for sr in service.weekly_runs(source)] == [
        case["D"]["runs"][0]["run_id"],
        99999,
        case["D"]["runs"][2]["run_id"],
    ]


def test_legacy_run_state_is_imported_once_with_the_pages_numbers():
    case = CASES["go_live_mid_history"]
    runs = case["D"]["runs"]
    r6, r7 = str(runs[6]["run_id"]), str(runs[7]["run_id"])
    at = int(datetime(2026, 11, 17, 9, tzinfo=timezone.utc).timestamp() * 1000)
    legacy = {
        # saved out of order: numbers follow run, then time, then entity
        f"open|{r7}|flw:worker03": {"by": "A", "at": at},
        f"open|{r6}|llo:BRAVO": {"by": "B", "at": at + 5},
        f"open|{r6}|flw:worker01": {"by": "C", "at": at + 5},
        f"dismiss|{r6}|flw:worker02": {"by": "A", "at": at},
        f"close|{r7}|llo:BRAVO": {"status": "closed_other", "note": "moved", "by": "B", "at": at + 9},
        "ev|llo:BRAVO@" + r6 + "|e1": {"type": "comment", "text": "called them", "by": "B", "at": at + 7},
        "cfg": {"closeAfter": 3},
        "unrelated": 5,
    }
    source, d = source_for(case, audit_decisions_run_id=777)
    dao = FakeDao(case, legacy_state=legacy)
    service.sync(source, dao, d)
    nums = dict(Decision.objects.filter(source=source, kind="open").values_list("entity", "review_number"))
    assert nums == {"flw:worker01": 1001, "llo:BRAVO": 1002, "flw:worker03": 1003}
    close = Decision.objects.get(source=source, kind="close")
    assert (close.status, close.note) == ("closed_other", "moved")
    ev = TeamEvent.objects.get(source=source)
    assert (ev.review_key, ev.payload, ev.created_by) == (f"llo:BRAVO@{r6}", {"text": "called them"}, "B")
    source.refresh_from_db()
    assert source.imported_from_run_id == 777 and source.settings == {"closeAfter": 3}
    # once only
    dao.legacy_state = {**legacy, f"open|{r7}|flw:worker09": {"by": "A", "at": at}}
    service.sync(source, dao, d)
    assert Decision.objects.filter(source=source, kind="open").count() == 3


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


@pytest.fixture
def live_source():
    """go_live_first_run with no decisions yet: the latest run proposes reviews for everything flagged."""
    case = CASES["go_live_first_run"]
    last = case["D"]["runs"][-1]["run_id"]
    source, d = source_for({**case, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(case), d)
    return source


def _pending(source, cls):
    return [p["ent"] for p in service.world(source)["latestProposals"] if p.get("cls") == cls and p.get("pending")]


def test_confirming_opens_numbered_reviews(live_source):
    ents = _pending(live_source, "open")[:2]
    assert len(ents) == 2
    done = service.record_decisions(
        live_source, [{"kind": "open", "entity": e} for e in ents], user=None, by="Staff One"
    )
    assert [d["review_number"] for d in done] == [1001, 1002]
    payload = service.world(live_source)
    opened = {r["ent"]: r for r in payload["reviews"]}
    assert set(ents) <= set(opened)
    assert all(opened[e]["status"] == "open" and opened[e]["openedBy"] == "Staff One" for e in ents)
    assert set(ents).isdisjoint(_pending(live_source, "open"))
    with pytest.raises(service.Refused):  # already decided
        service.record_decisions(live_source, [{"kind": "open", "entity": ents[0]}], user=None, by="x")


def test_decisions_are_all_or_nothing(live_source):
    ent = _pending(live_source, "open")[0]
    with pytest.raises(service.Refused):
        service.record_decisions(
            live_source, [{"kind": "open", "entity": ent}, {"kind": "open", "entity": "flw:nobody"}], user=None, by="x"
        )
    assert not Decision.objects.filter(source=live_source).exists()


def test_not_this_week_sets_a_proposal_aside(live_source):
    ent = _pending(live_source, "open")[0]
    service.record_decisions(live_source, [{"kind": "dismiss", "entity": ent}], user=None, by="x")
    prop = next(p for p in service.world(live_source)["latestProposals"] if p["ent"] == ent)
    assert prop.get("decided") == "dismissed" and not prop.get("pending")


def test_closing_rules(live_source):
    ent = _pending(live_source, "open")[0]
    service.record_decisions(live_source, [{"kind": "open", "entity": ent}], user=None, by="x")
    with pytest.raises(service.Refused, match="needs a note"):
        service.record_decisions(
            live_source, [{"kind": "close", "entity": ent, "status": "closed_other"}], user=None, by="x"
        )
    with pytest.raises(service.Refused, match="green"):
        service.record_decisions(live_source, [{"kind": "close", "entity": ent}], user=None, by="x")
    with pytest.raises(service.Refused, match="no open review"):
        service.record_decisions(
            live_source, [{"kind": "close", "entity": "flw:nobody", "status": "closed_suspended"}], user=None, by="x"
        )
    service.record_decisions(
        live_source, [{"kind": "close", "entity": ent, "status": "closed_suspended"}], user=None, by="x"
    )
    review = next(r for r in service.world(live_source)["reviews"] if r["ent"] == ent)
    assert review["status"] == "closed_suspended"
    assert Review.objects.get(source=live_source, entity=ent).status == "closed_suspended"


def test_events_are_saved_once_and_shown_on_the_review(live_source):
    ent = _pending(live_source, "open")[0]
    service.record_decisions(live_source, [{"kind": "open", "entity": ent}], user=None, by="x")
    key = next(r["key"] for r in service.world(live_source)["reviews"] if r["ent"] == ent)
    args = dict(
        review_key=key,
        event_id="abc1",
        type="action",
        payload={"action": "investigate", "at": 1},
        user=None,
        by="Staff",
    )
    assert service.record_event(live_source, **args) is True
    assert service.record_event(live_source, **args) is False
    review = next(r for r in service.world(live_source)["reviews"] if r["ent"] == ent)
    team = [e for e in review["events"] if e.get("team")]
    assert len(team) == 1 and team[0]["payload"]["action"] == "investigate" and team[0]["actor"] == "Staff"
    row = Review.objects.get(source=live_source, entity=ent)
    assert (row.team_event_count, row.last_action_type) == (1, "investigate")
    with pytest.raises(service.Refused):
        service.record_event(live_source, **{**args, "event_id": "abc2", "review_key": "flw:nobody@1"})
    with pytest.raises(service.Refused):
        service.record_event(live_source, **{**args, "event_id": "abc3", "type": "delete_everything"})


def test_open_source_follows_the_definition():
    d = definition(audit_go_live_run_id=5, audit_mode="test")
    s = service.open_source(d, opportunity_id=1)
    assert (s.source_workflow_id, s.go_live_run_id, s.mode) == (800, 5, "test")
    s2 = service.open_source(definition(audit_go_live_run_id=6), opportunity_id=1)
    assert s2.pk == s.pk and s2.go_live_run_id == 6 and s2.mode == AuditSource.MODE_LIVE
    with pytest.raises(service.Refused):
        service.open_source(SimpleNamespace(id=901, data={"config": {}}))


def test_a_decision_on_an_older_run_is_refused(live_source):
    ent = _pending(live_source, "open")[0]
    latest = service.world(live_source)["runs"][-1]["run_id"]
    with pytest.raises(service.Refused, match="newer KC Pulse run"):
        service.record_decisions(live_source, [{"kind": "open", "entity": ent}], user=None, by="x", run_id=latest - 1)
    service.record_decisions(live_source, [{"kind": "open", "entity": ent}], user=None, by="x", run_id=latest)


def test_one_batch_cannot_decide_the_same_proposal_twice(live_source):
    ent = _pending(live_source, "open")[0]
    with pytest.raises(service.Refused, match="only once"):
        service.record_decisions(
            live_source, [{"kind": "open", "entity": ent}, {"kind": "dismiss", "entity": ent}], user=None, by="x"
        )
    assert not Decision.objects.filter(source=live_source).exists()


def test_nothing_new_is_taken_until_the_old_pages_decisions_are_in():
    case = CASES["go_live_first_run"]
    last = case["D"]["runs"][-1]["run_id"]
    source, d = source_for({**case, "opts": {"goLiveRunId": last}}, audit_decisions_run_id=777)
    dao = FakeDao(case)
    dao.get_run = lambda run_id: None  # the old run cannot be read right now
    service.sync(source, dao, d)
    source.refresh_from_db()
    ent = _pending(source, "open")[0]
    with pytest.raises(service.Refused, match="previous version"):
        service.record_decisions(source, [{"kind": "open", "entity": ent}], user=None, by="x")
    service.sync(source, FakeDao(case, legacy_state={}), d)  # readable now: imported (nothing in it)
    source.refresh_from_db()
    assert service.record_decisions(source, [{"kind": "open", "entity": ent}], user=None, by="x")


def test_an_unknown_action_label_is_refused(live_source):
    ent = _pending(live_source, "open")[0]
    service.record_decisions(live_source, [{"kind": "open", "entity": ent}], user=None, by="x")
    key = next(r["key"] for r in service.world(live_source)["reviews"] if r["ent"] == ent)
    with pytest.raises(service.Refused, match="Unknown action"):
        service.record_event(
            live_source, review_key=key, event_id="e9", type="action", payload={"action": "x" * 41}, user=None, by="x"
        )


def test_a_close_shows_its_reason_on_the_timeline(live_source):
    ent = _pending(live_source, "open")[0]
    service.record_decisions(live_source, [{"kind": "open", "entity": ent}], user=None, by="x")
    service.record_decisions(
        live_source,
        [{"kind": "close", "entity": ent, "status": "closed_other", "note": "Left the programme"}],
        user=None,
        by="x",
    )
    review = next(r for r in service.world(live_source)["reviews"] if r["ent"] == ent)
    closed = [e for e in review["events"] if e["type"] == "status_change"]
    assert closed and closed[-1]["payload"] == {"to": "closed_other", "reason": "Left the programme"}
