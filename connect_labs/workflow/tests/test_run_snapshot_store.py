"""A completed run's snapshot stored in a child labs record (workflow/run_snapshot_store.py).

What has to hold, end to end through a REAL `LabsRecordAPIClient` against a fake
`/export/labs_record/` that behaves like Connect's (filter by query params, POST
upserts by id, DELETE cascades children):

* a large snapshot lands in a `workflow_run_snapshot` child; the run holds a ref
  and a small summary; a small snapshot stays inline exactly as before;
* every existing read of `run.snapshot` / `run.data["snapshot"]` still works, and
  costs one child GET the first time and none after;
* LISTING runs fetches no child -- the whole point;
* re-writing a run (rename, state merge) does not re-upload an unchanged child;
  a changed snapshot does update it;
* deleting a run removes its child, and legacy inline runs read unchanged;
* the summary answers the trend readers (run_history_api's `project_state`,
  benchmark auto-publish's `run_history`) exactly as the full snapshot does.
"""

from __future__ import annotations

import copy
import json
import json as _json
import pickle
from unittest.mock import MagicMock, patch

import httpx
import pytest

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.models import LocalLabsRecord
from connect_labs.workflow.data_access import WorkflowDataAccess, WorkflowRunRecord
from connect_labs.workflow.run_codec import CODEC_ID, encode_snapshot
from connect_labs.workflow.run_snapshot_store import (
    EXTERNALIZE_MIN_BYTES,
    REF_KEY,
    SNAPSHOT_RECORD_TYPE,
    SUMMARY_KEY,
    LazyRunData,
    build_summary,
    expand_summary_state,
    snapshot_state,
    stored_body,
    summary_covers,
)
from connect_labs.workflow.snapshot_builders import wrap_for_runner
from connect_labs.workflow.snapshot_runtime import project_state
from connect_labs.workflow.tests.kmc_shaped_snapshot import kmc_shaped_snapshot

# The paths the trend renders request from /runs/history/ (each prefixed with the
# `snapshot` state key), copied from the render code -- see SUMMARY_PAYLOAD_KEYS.
KMC_TREND_KEYS = [
    "snapshot." + k
    for k in (
        "programInd",
        "byLLO",
        "byOpp",
        "pooledOverCredible",
        "meta",
        "series.N.programme",
        "series.N.byLLO",
        "series.N.byOpp",
    )
]
OTHER_HISTORY_KEYS = ["snapshot.meta.as_of", "snapshot.programInd", "snapshot.meta"]


@pytest.fixture(scope="module")
def big_snapshot() -> dict:
    # No case index: a programme run grades every case but stores none (embed_cases).
    # Through JSON once, as anything read back from storage is (int dict keys -> str).
    return json.loads(json.dumps(kmc_shaped_snapshot(embed_cases=False)))


def _small_snapshot() -> dict:
    return wrap_for_runner({"programInd": {"C01": {"value": 0.5}}, "meta": {"as_of": "2026-09-28"}}, "snapshot")


# ─── A fake Connect labs_record endpoint ───────────────────────────────────


class _FakeConnect:
    """Stores what it is POSTed, filters GETs by their params, cascades DELETEs.

    Mirrors the Connect facts the design rests on: every query param is a filter
    (`id`, `type`, `experiment`, `labs_record_id`, scope ids), there is no field
    projection, POST is update_or_create by id with `data` replaced wholesale, and
    `labs_record` is a self-FK with ON DELETE CASCADE.
    """

    def __init__(self):
        self.stored: dict[int, dict] = {}
        self.next_id = 900
        self.gets: list[dict] = []
        self.posts: list[dict] = []

    def _resp(self, body):
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = body
        resp.text = ""
        return resp

    def post(self, url, json=None, **kwargs):
        (payload,) = json
        payload = _json.loads(_json.dumps(payload))  # the wire is JSON
        self.posts.append(payload)
        rid = payload.get("id")
        if rid is None:
            rid = self.next_id
            self.next_id += 1
        record = {
            "opportunity_id": None,
            "program_id": None,
            "organization_id": None,
            "labs_record_id": None,
            "username": None,
            **self.stored.get(rid, {}),
            **payload,
            "id": rid,
        }
        self.stored[rid] = record
        return self._resp([copy.deepcopy(record)])

    def get(self, url, params=None, **kwargs):
        params = dict(params or {})
        self.gets.append(params)
        out = []
        for rec in self.stored.values():
            ok = True
            for k, v in params.items():
                if k.startswith("data__"):
                    continue
                if str(rec.get(k)) != str(v):
                    ok = False
            if ok:
                out.append(copy.deepcopy(rec))
        return self._resp(out)

    def request(self, method, url, json=None, **kwargs):
        assert method == "DELETE"
        doomed = {item["id"] for item in json}
        while True:
            more = {rid for rid, rec in self.stored.items() if rec.get("labs_record_id") in doomed} - doomed
            if not more:
                break
            doomed |= more
        for rid in doomed:
            self.stored.pop(rid, None)
        return self._resp(None)

    def of_type(self, type_):
        return {rid: r for rid, r in self.stored.items() if r["type"] == type_}

    def child_gets(self):
        """Reads of a child's snapshot (by id) -- what a lazy load costs."""
        return [g for g in self.gets if g.get("type") == SNAPSHOT_RECORD_TYPE and "id" in g]

    def child_lookups(self):
        """Listings of a run's children -- what the write boundary does when the
        run carries no ref yet (to reuse a child a failed write left behind)."""
        return [g for g in self.gets if g.get("type") == SNAPSHOT_RECORD_TYPE and "id" not in g]


@pytest.fixture
def connect():
    server = _FakeConnect()
    wda = WorkflowDataAccess(opportunity_id=700, access_token="fake")
    assert isinstance(wda.labs_api, LabsRecordAPIClient)
    client = wda.labs_api.http_client
    with (
        patch.object(client, "post", side_effect=server.post),
        patch.object(client, "get", side_effect=server.get),
        patch.object(client, "request", side_effect=server.request),
    ):
        yield wda, server
    wda.close()


def _completed_run(wda, snapshot, **extra):
    run = wda.create_run(7, opportunity_id=700, period_start="2026-09-22", period_end="2026-09-28")
    if extra:
        run = wda.update_run_state(run.id, extra, run=run)
    return wda.complete_run(run.id, snapshot, run=run)


# ─── Write boundary ────────────────────────────────────────────────────────


class TestWrite:
    def test_large_snapshot_goes_to_a_child_and_the_run_holds_ref_and_summary(self, connect, big_snapshot):
        wda, server = connect
        run = _completed_run(wda, big_snapshot)

        stored_run = server.stored[run.id]
        child_id, child = next(iter(server.of_type(SNAPSHOT_RECORD_TYPE).items()))
        assert "snapshot" not in stored_run["data"]
        assert stored_run["data"]["status"] == "completed"
        ref = stored_run["data"][REF_KEY]
        assert ref["record_id"] == child_id
        assert ref["bytes"] == len(stored_body(child["data"]["snapshot"]))
        assert child["labs_record_id"] == run.id
        assert child["experiment"] == "workflow"
        assert child["opportunity_id"] == stored_run["opportunity_id"] == 700
        assert child["data"]["snapshot"]["$codec"] == CODEC_ID
        assert stored_run["data"][SUMMARY_KEY] == build_summary(big_snapshot)
        # The returned record carries the snapshot it was given: no fetch.
        assert run.snapshot == big_snapshot
        assert server.child_gets() == []

    def test_a_child_connect_refuses_leaves_the_run_saved_inline(self, connect, big_snapshot):
        """The child record is the optimisation, never the save: if Connect will not
        take it, the run stores its snapshot inline, exactly as before this module."""
        wda, server = connect
        real_post = server.post

        def refuse_children(url, json=None, **kwargs):
            if json and json[0].get("type") == SNAPSHOT_RECORD_TYPE:
                raise httpx.HTTPStatusError("400", request=MagicMock(), response=MagicMock(status_code=400))
            return real_post(url, json=json, **kwargs)

        wda.labs_api.http_client.post.side_effect = refuse_children
        run = _completed_run(wda, big_snapshot)
        stored = server.stored[run.id]["data"]
        assert stored["status"] == "completed"
        assert stored["snapshot"]["$codec"] == CODEC_ID
        assert REF_KEY not in stored and SUMMARY_KEY not in stored
        assert server.of_type(SNAPSHOT_RECORD_TYPE) == {}
        assert wda.get_run(run.id).snapshot == big_snapshot

    def test_small_snapshot_stays_inline(self, connect):
        wda, server = connect
        run = _completed_run(wda, _small_snapshot())

        stored = server.stored[run.id]["data"]
        assert stored["snapshot"]["$codec"] == CODEC_ID
        assert REF_KEY not in stored and SUMMARY_KEY not in stored
        assert server.of_type(SNAPSHOT_RECORD_TYPE) == {}
        assert run.snapshot == _small_snapshot()

    def test_threshold_is_measured_on_the_stored_bytes(self, big_snapshot):
        assert len(stored_body(encode_snapshot(_small_snapshot()))) < EXTERNALIZE_MIN_BYTES
        assert len(stored_body(encode_snapshot(big_snapshot))) >= EXTERNALIZE_MIN_BYTES

    def test_rename_and_state_update_do_not_reupload_or_refetch_an_unchanged_child(self, connect, big_snapshot):
        wda, server = connect
        run = _completed_run(wda, big_snapshot)
        child_id = server.stored[run.id]["data"][REF_KEY]["record_id"]
        fresh = wda.get_run(run.id)
        posts_before = len(server.posts)

        renamed = wda.rename_run(run.id, "Week 39", run=fresh)

        new_posts = server.posts[posts_before:]
        assert [p["type"] for p in new_posts] == ["workflow_run"]  # the run only, never the child
        assert server.stored[run.id]["data"]["name"] == "Week 39"
        assert server.stored[run.id]["data"][REF_KEY]["record_id"] == child_id
        assert server.child_gets() == []  # the rename did not download the snapshot either
        assert renamed.snapshot == big_snapshot  # ...and it is still there for readers
        assert len(server.child_gets()) == 1

    def test_rename_of_a_loaded_run_skips_the_child_by_hash(self, connect, big_snapshot):
        wda, server = connect
        run = _completed_run(wda, big_snapshot)
        fresh = wda.get_run(run.id)
        assert fresh.snapshot == big_snapshot  # loaded, so {**run.data} carries it
        posts_before = len(server.posts)

        server_data = {**fresh.data, "name": "spread after load"}
        wda.labs_api.update_record(run.id, "workflow", "workflow_run", server_data, current_record=fresh)

        assert [p["type"] for p in server.posts[posts_before:]] == ["workflow_run"]

    def test_a_changed_snapshot_updates_the_same_child(self, connect, big_snapshot):
        wda, server = connect
        run = _completed_run(wda, big_snapshot)
        child_id = server.stored[run.id]["data"][REF_KEY]["record_id"]
        changed = copy.deepcopy(big_snapshot)
        changed["state"]["snapshot"]["meta"]["as_of"] = "2026-10-05"

        updated = wda.labs_api.update_record(
            run.id, "workflow", "workflow_run", {**wda.get_run(run.id).data, "snapshot": changed}
        )

        assert list(server.of_type(SNAPSHOT_RECORD_TYPE)) == [child_id]
        assert server.stored[run.id]["data"][REF_KEY]["record_id"] == child_id
        assert server.stored[run.id]["data"][SUMMARY_KEY]["state"]["snapshot"]["meta"]["as_of"] == "2026-10-05"
        assert updated.data["snapshot"] == changed
        assert wda.get_run(run.id).snapshot == changed

    def test_a_run_created_with_a_large_snapshot_is_externalized(self, connect, big_snapshot):
        # Seeders and the mbw adapter create runs WITH a snapshot, through the client.
        wda, server = connect
        rec = wda.labs_api.create_record(
            experiment="workflow", type="workflow_run", data={"definition_id": 7, "snapshot": big_snapshot}
        )
        assert "snapshot" not in server.stored[rec.id]["data"]
        assert len(server.of_type(SNAPSHOT_RECORD_TYPE)) == 1
        assert rec.data["snapshot"] == big_snapshot
        # It knew the run was new, so it did not look for an existing child.
        assert server.child_gets() == [] and server.child_lookups() == []

    def test_a_retry_reuses_an_orphaned_child_instead_of_making_a_second(self, connect, big_snapshot):
        wda, server = connect
        run = wda.create_run(7, opportunity_id=700, period_start="2026-09-22", period_end="2026-09-28")
        orphan = wda.labs_api.create_child_record(
            parent=run, experiment="workflow", type=SNAPSHOT_RECORD_TYPE, data={"snapshot": {}}
        )

        wda.complete_run(run.id, big_snapshot, run=run)

        assert list(server.of_type(SNAPSHOT_RECORD_TYPE)) == [orphan.id]
        assert server.stored[run.id]["data"][REF_KEY]["record_id"] == orphan.id


# ─── Read boundary ─────────────────────────────────────────────────────────


class TestRead:
    def test_listing_runs_fetches_no_child(self, connect, big_snapshot):
        wda, server = connect
        for _ in range(3):
            _completed_run(wda, big_snapshot)
        server.gets.clear()

        runs = wda.list_runs(definition_id=7)

        assert len(runs) == 3
        assert all(r.is_completed and r.period_end == "2026-09-28" for r in runs)
        assert all("snapshot" in r.data for r in runs)  # membership does not load
        assert all(r.data[SUMMARY_KEY] for r in runs)
        assert server.child_gets() == []
        # And the listing carried no snapshot bytes at all.
        assert all("snapshot" not in server.stored[r.id]["data"] for r in runs)
        assert server.child_lookups() == []

    def test_reading_the_snapshot_loads_the_child_exactly_once(self, connect, big_snapshot):
        wda, server = connect
        run_id = _completed_run(wda, big_snapshot).id
        run = wda.get_run(run_id)
        assert server.child_gets() == []

        assert run.snapshot == big_snapshot
        assert run.data["snapshot"] == big_snapshot
        assert run.data.get("snapshot") == big_snapshot
        assert {**run.data}["snapshot"] == big_snapshot
        assert dict(run.data)["snapshot"] == big_snapshot
        assert json.loads(json.dumps(run.data))["snapshot"] == big_snapshot
        assert run.state == {}
        (get,) = server.child_gets()
        assert get["id"] == server.stored[run_id]["data"][REF_KEY]["record_id"]
        assert get["opportunity_id"] == 700

    def test_the_loader_survives_the_dao_being_closed(self, connect, big_snapshot):
        # run_history_api closes its DAO before iterating the runs it listed.
        wda, server = connect
        run_id = _completed_run(wda, big_snapshot).id
        run = wda.get_run(run_id)
        wda.labs_api.http_client.close()

        with patch("httpx.Client.get", side_effect=lambda url, params=None, **kw: server.get(url, params)):
            assert run.snapshot == big_snapshot
        assert len(server.child_gets()) == 1

    def test_legacy_inline_runs_read_unchanged(self, connect, big_snapshot):
        wda, server = connect
        # A run written before child records: the snapshot inline, encoded.
        legacy = server.post(
            "",
            json=[
                {
                    "experiment": "workflow",
                    "type": "workflow_run",
                    "opportunity_id": 700,
                    "data": {"definition_id": 7, "status": "completed", "snapshot": encode_snapshot(big_snapshot)},
                }
            ],
        ).json()[0]

        (run,) = wda.list_runs(definition_id=7)
        assert run.id == legacy["id"]
        assert not isinstance(run.data, LazyRunData)
        assert run.snapshot == big_snapshot
        assert server.child_gets() == []
        assert snapshot_state(run, KMC_TREND_KEYS) == big_snapshot["state"]

    def test_a_missing_child_reads_as_no_snapshot(self, connect, big_snapshot):
        wda, server = connect
        run_id = _completed_run(wda, big_snapshot).id
        server.stored.pop(server.stored[run_id]["data"][REF_KEY]["record_id"])
        assert wda.get_run(run_id).snapshot is None

    def test_a_failed_load_is_retried_on_the_next_access(self, big_snapshot):
        calls = []

        def loader():
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("transient")
            return big_snapshot

        data = LazyRunData({REF_KEY: {"record_id": 1}}, loader)
        with pytest.raises(RuntimeError):
            data.get("snapshot")
        assert data["snapshot"] == big_snapshot
        assert len(calls) == 2


class TestLazyRunData:
    def _data(self, loads):
        def loader():
            loads.append(1)
            return {"state": {"x": 1}}

        return LazyRunData({"status": "completed", REF_KEY: {"record_id": 5}, "state": {}}, loader)

    def test_metadata_reads_do_not_load(self):
        loads = []
        data = self._data(loads)
        assert data["status"] == "completed"
        assert data.get("state") == {}
        assert "snapshot" in data and "nope" not in data
        assert len(data) == 4 and bool(data)
        assert "not loaded" in repr(data)
        assert data.stored_data() == {"status": "completed", REF_KEY: {"record_id": 5}, "state": {}}
        assert loads == []

    @pytest.mark.parametrize(
        "use",
        [
            lambda d: {**d},
            dict,
            lambda d: list(d),
            lambda d: list(d.items()),
            lambda d: d.copy(),
            lambda d: copy.deepcopy(d),
            lambda d: pickle.loads(pickle.dumps(d)),
            lambda d: json.loads(json.dumps(d)),
            lambda d: d | {},
        ],
    )
    def test_every_whole_dict_view_carries_the_snapshot(self, use):
        loads = []
        out = use(self._data(loads))
        if isinstance(out, list):
            assert "snapshot" in (out if isinstance(out[0], str) else [k for k, _ in out])
        else:
            assert out["snapshot"] == {"state": {"x": 1}}
        assert loads == [1]

    def test_equality_compares_with_the_snapshot(self):
        loads = []
        data = self._data(loads)
        assert data == {"status": "completed", REF_KEY: {"record_id": 5}, "state": {}, "snapshot": {"state": {"x": 1}}}

    def test_assigning_a_snapshot_replaces_the_pending_load(self):
        loads = []
        data = self._data(loads)
        data["snapshot"] = {"new": True}
        assert data["snapshot"] == {"new": True}
        assert loads == []


# ─── Delete ────────────────────────────────────────────────────────────────


class TestDelete:
    def test_deleting_a_run_deletes_its_child_without_fetching_it(self, connect, big_snapshot):
        wda, server = connect
        run_id = _completed_run(wda, big_snapshot).id
        assert server.of_type(SNAPSHOT_RECORD_TYPE)
        server.gets.clear()

        with patch.object(wda, "_scoped_audit_session_ids", return_value=[]):
            counts = wda.delete_run(run_id)

        assert counts["run"] == 1
        assert run_id not in server.stored
        assert server.of_type(SNAPSHOT_RECORD_TYPE) == {}
        assert server.child_gets() == []  # delete_run's get_run no longer downloads it


# ─── The summary ───────────────────────────────────────────────────────────


class TestSummary:
    def test_project_state_on_the_summary_equals_the_full_snapshot(self, big_snapshot):
        state = big_snapshot["state"]
        summary_state = expand_summary_state(build_summary(big_snapshot))
        for keys in (KMC_TREND_KEYS, OTHER_HISTORY_KEYS):
            assert all(summary_covers(k) for k in keys)
            assert project_state(summary_state, keys) == project_state(state, keys)

    def test_it_is_a_small_fraction_of_the_snapshot(self, big_snapshot):
        stored = len(stored_body(encode_snapshot(big_snapshot)))
        summary = len(json.dumps(build_summary(big_snapshot)))
        # Measured: 46 KB of an 856 KB stored programme snapshot (5.4%); 3.2% with a case index.
        assert summary < stored * 0.10, (summary, stored)

    def test_duplicated_llo_opps_are_dropped_and_restored(self, big_snapshot):
        summary = build_summary(big_snapshot)
        assert all("opps" not in e for e in summary["state"]["snapshot"]["byLLO"])
        assert expand_summary_state(summary)["snapshot"]["byLLO"] == big_snapshot["state"]["snapshot"]["byLLO"]

    def test_llo_opps_that_are_not_a_byopp_filter_are_kept(self):
        payload = {"byOpp": [{"opp": 1, "llo": "A"}], "byLLO": [{"llo": "A", "opps": [{"opp": 99}]}]}
        summary = build_summary({"state": {"snapshot": payload}})
        assert expand_summary_state(summary)["snapshot"]["byLLO"] == payload["byLLO"]

    def test_uncovered_paths_fall_back_to_the_full_snapshot(self, connect, big_snapshot):
        wda, server = connect
        run = wda.get_run(_completed_run(wda, big_snapshot).id)
        for path in ("snapshot.byFLW", "snapshot.series.N.byFLW", "snapshot.monthly", "snapshot", "snapshot.series"):
            assert not summary_covers(path)
        assert server.child_gets() == []
        assert (
            snapshot_state(run, ["snapshot.programInd"])["snapshot"].keys()
            == build_summary(big_snapshot)["state"]["snapshot"].keys()
        )
        assert server.child_gets() == []
        assert snapshot_state(run, ["snapshot.programInd", "snapshot.byFLW"]) == big_snapshot["state"]
        assert len(server.child_gets()) == 1

    def test_benchmark_run_history_reads_the_summary(self, connect, big_snapshot):
        from connect_labs.benchmarks.auto_publish import run_history

        wda, server = connect
        _completed_run(wda, big_snapshot)
        server.gets.clear()

        history = run_history(wda, 7, "snapshot")

        assert server.child_gets() == []
        # ...and it is the history the full snapshot gives.
        full = WorkflowRunRecord(
            {
                "id": 1,
                "experiment": "workflow",
                "type": "workflow_run",
                "opportunity_id": 700,
                "data": {
                    "definition_id": 7,
                    "status": "completed",
                    "period_end": "2026-09-28",
                    "snapshot": big_snapshot,
                },
            }
        )
        with patch.object(wda, "list_runs", return_value=[full]):
            assert history == run_history(wda, 7, "snapshot")
        assert history and set(history[0]["byOpp"]) == {"C", "N"}


# ─── run_history_api, the trend endpoint ───────────────────────────────────


class TestRunHistoryApi:
    def test_trend_keys_are_served_without_a_child_fetch(self, connect, big_snapshot, rf):
        from connect_labs.workflow import views

        wda, server = connect
        run = _completed_run(wda, big_snapshot)
        server.gets.clear()
        request = rf.get("/x", {"keys": ",".join(KMC_TREND_KEYS)})
        request.labs_context = {"opportunity_id": 700}

        with (
            patch.object(views, "WorkflowDataAccess", return_value=wda),
            patch.object(wda, "close"),
            patch("connect_labs.workflow.history_cache.get", return_value=None),
            patch("connect_labs.workflow.history_cache.store"),
        ):
            resp = views.run_history_api.__wrapped__.__wrapped__(request, 7)

        body = json.loads(resp.content)
        assert [r["id"] for r in body["runs"]] == [run.id]
        expected = json.loads(json.dumps(project_state(big_snapshot["state"], KMC_TREND_KEYS)))
        assert body["runs"][0]["state"] == expected
        assert server.child_gets() == []


# ─── The labs-local backend (synthetic opportunities) ──────────────────────


class _FakeLocalBackend:
    """In-memory stand-in for local_records_backend's CRUD, to drive the client's
    local dispatch without a database (the real backend is covered below, in CI)."""

    def __init__(self):
        self.rows: dict[int, dict] = {}
        self.next_id = 1
        self.reads: list[dict] = []

    def _record(self, row, model_class=None):
        return (model_class or LocalLabsRecord)(copy.deepcopy(row))

    def create_record(self, *, opportunity_id, experiment, type, data, username=None, program_id=None,
                      organization_id=None, labs_record_id=None, public=False):  # fmt: skip
        rid = self.next_id
        self.next_id += 1
        self.rows[rid] = {
            "id": rid,
            "opportunity_id": opportunity_id,
            "experiment": experiment,
            "type": type,
            "data": copy.deepcopy(data),
            "username": username,
            "program_id": program_id,
            "organization_id": organization_id,
            "labs_record_id": labs_record_id,
            "public": public,
        }
        return self._record(self.rows[rid])

    def update_record(self, *, record_id, opportunity_id, experiment, type, data, **kw):
        row = self.rows[record_id]
        assert row["opportunity_id"] == opportunity_id
        row["data"] = copy.deepcopy(data)
        if kw.get("labs_record_id") is not None:
            row["labs_record_id"] = kw["labs_record_id"]
        return self._record(row)

    def get_records(self, *, model_class=None, **filters):
        self.reads.append(filters)
        out = []

        def matches(row, k, v):
            if v is None or k in ("program_id", "organization_id"):
                return True
            if k.endswith("__iexact"):  # a JSON-data lookup, e.g. definition_id__iexact
                return str(row["data"].get(k[: -len("__iexact")])).lower() == str(v).lower()
            return row.get(k) == v

        for row in self.rows.values():
            if all(matches(row, k, v) for k, v in filters.items()):
                out.append(self._record(row, model_class))
        return out

    def get_record_by_id(self, *, record_id, model_class=None, **filters):
        self.reads.append({"id": record_id, **filters})
        row = self.rows.get(record_id)
        return self._record(row, model_class) if row else None


class TestLocalBackendPath:
    def test_child_storage_and_lazy_read_route_to_the_local_backend(self, big_snapshot):
        fake = _FakeLocalBackend()
        from connect_labs.labs.integrations.connect import api_client

        with (
            patch.object(api_client._local_backend, "is_labs_only_opportunity_id", return_value=True),
            patch.object(api_client._local_backend, "is_labs_only_program_id", return_value=False),
            patch.multiple(
                api_client._local_backend,
                create_record=fake.create_record,
                update_record=fake.update_record,
                get_records=fake.get_records,
                get_record_by_id=fake.get_record_by_id,
            ),
        ):
            wda = WorkflowDataAccess(opportunity_id=10_050, access_token="fake")
            with patch.object(wda.labs_api.http_client, "send", side_effect=AssertionError("no HTTP")):
                run = _completed_run(wda, big_snapshot)
                children = [r for r in fake.rows.values() if r["type"] == SNAPSHOT_RECORD_TYPE]
                assert len(children) == 1 and children[0]["labs_record_id"] == run.id
                assert children[0]["opportunity_id"] == 10_050
                assert "snapshot" not in fake.rows[run.id]["data"]

                fake.reads.clear()
                (listed,) = wda.list_runs(definition_id=7)
                assert not [r for r in fake.reads if r.get("type") == SNAPSHOT_RECORD_TYPE]
                assert listed.snapshot == big_snapshot
                assert len([r for r in fake.reads if r.get("type") == SNAPSHOT_RECORD_TYPE]) == 1
            wda.close()


@pytest.mark.django_db
class TestRealLocalBackend:
    def test_store_read_and_cascade_delete(self, big_snapshot):
        from connect_labs.labs.synthetic.models import LabsLocalRecord, SyntheticOpportunity

        SyntheticOpportunity.objects.create(
            opportunity_id=10_050, gdrive_folder_id="f", labs_only=True, allowed_domains=["@dimagi.com"]
        )
        wda = WorkflowDataAccess(opportunity_id=10_050, access_token="fake")
        try:
            run = _completed_run(wda, big_snapshot)
            child = LabsLocalRecord.objects.get(type=SNAPSHOT_RECORD_TYPE)
            assert child.labs_record_id == run.id and child.opportunity_id == 10_050
            assert "snapshot" not in LabsLocalRecord.objects.get(id=run.id).data

            (listed,) = wda.list_runs(definition_id=7)
            assert isinstance(listed.data, LazyRunData)
            assert listed.snapshot == big_snapshot

            wda.labs_api.delete_records([run.id])
            assert not LabsLocalRecord.objects.filter(id__in=[run.id, child.id]).exists()
        finally:
            wda.close()
