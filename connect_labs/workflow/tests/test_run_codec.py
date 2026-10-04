"""The columnar storage format for a saved run's snapshot (workflow/run_codec.py).

Two halves: the codec itself must be exact (every JSON value round-trips, marker
look-alikes included, legacy runs read unchanged), and the boundaries must hold --
every write of a workflow_run through the labs client stores the encoded form,
every record built from storage exposes the decoded form, and a read-modify-write
(rename, state merge) never silently re-inflates a run.
"""

from __future__ import annotations

import json
import random
from unittest.mock import MagicMock, patch

import httpx
import pytest

from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
from connect_labs.labs.models import LocalLabsRecord
from connect_labs.workflow.data_access import WorkflowDataAccess, WorkflowRunRecord
from connect_labs.workflow.run_codec import (
    CODEC_ID,
    decode,
    decode_record_data,
    decode_snapshot,
    encode,
    encode_record_data,
    encode_snapshot,
)
from connect_labs.workflow.snapshot_builders import wrap_for_runner


def _realistic_snapshot(n_cases: int = 9000) -> dict:
    """A programme-snapshot-shaped payload: a long same-keyed case list (~14 keys),
    a per-FLW roll-up and a monthly series, wrapped exactly as a saved run stores it."""
    rng = random.Random(7)
    cases = [
        {
            "case_id": f"c-{i:06d}",
            "flw_id": f"flw-{i % 120:03d}",
            "opportunity_id": 1000 + i % 4,
            "registered_on": f"2026-0{1 + i % 9}-{1 + i % 28:02d}",
            "birth_weight_g": rng.randint(900, 2500),
            "gestation_weeks": rng.randint(28, 40),
            "kmc_hours_day1": round(rng.uniform(0, 24), 1),
            "discharged": rng.random() < 0.8,
            "discharge_weight_g": rng.randint(1200, 3000) if rng.random() < 0.8 else None,
            "visits": rng.randint(0, 8),
            "last_visit": None if i % 11 == 0 else "2026-09-30",
            "district": rng.choice(["North", "South", "East", "West"]),
            "status": rng.choice(["active", "closed", "lost"]),
            "flag": "",
        }
        for i in range(n_cases)
    ]
    by_flw = [{"flw_id": f"flw-{i:03d}", "cases": 75, "visits": 300, "pct_on_time": 0.81} for i in range(120)]
    monthly = [{"month": f"2026-{m:02d}", "registered": 1000, "discharged": 800} for m in range(1, 10)]
    return wrap_for_runner({"cases": cases, "byFLW": by_flw, "monthly": monthly}, "snapshot")


# ─── The codec ─────────────────────────────────────────────────────────────


def _random_json(rng: random.Random, depth: int = 0):
    """Arbitrary JSON, biased toward the shapes the codec rewrites: lists of dicts
    that share a key set, nearly share one, or look like markers."""
    leaves = [None, True, False, 0, -3, 2.5, "", "x", "$cols", "$rows"]
    if depth > 3:
        return rng.choice(leaves)
    kind = rng.choice(["leaf", "dict", "list", "records", "marker"])
    if kind == "leaf":
        return rng.choice(leaves)
    if kind == "dict":
        return {rng.choice(["a", "b", "$cols", "$rows", "$esc", "c"]): _random_json(rng, depth + 1) for _ in range(3)}
    if kind == "list":
        return [_random_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    if kind == "records":
        keys = rng.sample(["a", "b", "c", "$cols", "$rows"], rng.randint(0, 3))
        rows = [
            {k: _random_json(rng, depth + 1) for k in rng.sample(keys, len(keys))} for _ in range(rng.randint(0, 4))
        ]
        if rows and rng.random() < 0.3:
            rows[-1]["extra"] = 1  # one differing key set: must stay a plain list
        return rows
    return rng.choice(
        [
            {"$cols": ["a"], "$rows": [[1]]},
            {"$esc": {"a": 1}},
            {"$esc": 5},
            {"$cols": "not-a-list", "$rows": []},
            {"$codec": CODEC_ID, "value": [1]},
        ]
    )


class TestCodec:
    def test_round_trip_is_exact_over_arbitrary_json(self):
        rng = random.Random(1234)
        for _ in range(3000):
            value = _random_json(rng)
            assert decode(encode(value)) == value
            # And it survives the actual storage medium.
            assert decode(json.loads(json.dumps(encode(value)))) == value

    def test_same_keyed_list_becomes_columnar(self):
        value = [{"a": 1, "b": None}, {"b": 2, "a": 3}]
        assert encode(value) == {"$cols": ["a", "b"], "$rows": [[1, None], [3, 2]]}
        assert decode(encode(value)) == value

    @pytest.mark.parametrize(
        "value",
        [
            [],
            [{"a": 1}],  # a single dict is not worth a header
            [{"a": 1}, {"b": 1}],  # differing key sets
            [{"a": 1}, {"a": 1, "b": 2}],
            [{"a": 1}, 5],  # mixed
            [1, 2, 3],
        ],
    )
    def test_ineligible_lists_stay_lists(self, value):
        assert isinstance(encode(value), list)
        assert decode(encode(value)) == value

    def test_nested_record_lists_are_encoded_recursively(self):
        value = [{"id": 1, "kids": [{"x": 1}, {"x": 2}]}, {"id": 2, "kids": []}]
        encoded = encode(value)
        assert encoded["$rows"][0][1] == {"$cols": ["x"], "$rows": [[1], [2]]}
        assert decode(encoded) == value

    @pytest.mark.parametrize(
        "user_dict",
        [{"$cols": ["a"], "$rows": [[1]]}, {"$esc": {"a": 1}}, {"$esc": 3}],
    )
    def test_user_dict_shaped_like_a_marker_is_escaped(self, user_dict):
        encoded = encode({"payload": user_dict})
        assert encoded["payload"] == {"$esc": user_dict}
        assert decode(encoded) == {"payload": user_dict}

    def test_dict_with_marker_key_among_others_needs_no_escape(self):
        value = {"$cols": 1, "other": 2}
        assert encode(value) == value

    def test_list_of_marker_shaped_dicts_round_trips(self):
        # Rows are rebuilt from $cols directly, so a decoded element is never
        # re-read as a marker.
        value = [{"$cols": [], "$rows": []}, {"$cols": ["z"], "$rows": [[1]]}]
        assert decode(encode(value)) == value


class TestSnapshotEnvelope:
    def test_legacy_plain_snapshot_reads_unchanged(self):
        legacy = _realistic_snapshot(50)
        assert decode_snapshot(legacy) is legacy

    def test_decode_is_idempotent_on_decoded_data(self):
        snap = _realistic_snapshot(50)
        once = decode_snapshot(encode_snapshot(snap))
        assert decode_snapshot(once) == snap

    def test_encode_is_idempotent(self):
        stored = encode_snapshot(_realistic_snapshot(50))
        assert encode_snapshot(stored) is stored

    def test_none_snapshot_stays_none(self):
        assert encode_snapshot(None) is None
        assert encode_record_data("workflow_run", {"state": {}}) == {"state": {}}

    def test_only_workflow_runs_are_touched(self):
        data = {"snapshot": [{"a": 1}, {"a": 2}]}
        assert encode_record_data("workflow_definition", data) is data
        assert decode_record_data("workflow_definition", {"snapshot": encode_snapshot([1])})["snapshot"] != [1]

    def test_run_metadata_stays_plain_for_server_side_filters(self):
        data = {
            "definition_id": 7,
            "status": "completed",
            "period_end": "2026-09-30",
            "state": {"generated_by": "hand_down", "hand_down_key": "7|2026-09-30", "rows": [{"a": 1}, {"a": 2}]},
            "snapshot": _realistic_snapshot(10),
        }
        stored = encode_record_data("workflow_run", data)
        assert {k: v for k, v in stored.items() if k != "snapshot"} == {
            k: v for k, v in data.items() if k != "snapshot"
        }
        assert stored["snapshot"]["$codec"] == CODEC_ID
        assert decode_record_data("workflow_run", stored) == data

    def test_realistic_case_list_shrinks(self):
        snap = _realistic_snapshot()
        plain = len(json.dumps(snap).encode("utf-8"))
        stored = len(json.dumps(encode_snapshot(snap)).encode("utf-8"))
        assert decode_snapshot(json.loads(json.dumps(encode_snapshot(snap)))) == snap
        # Measured: 2.82 MB -> 1.00 MB (64% smaller) for 9k cases x 14 keys.
        assert stored < plain * 0.5, (plain, stored)


# ─── The size cap measures what is stored ──────────────────────────────────


class TestSizeCapMeasuresStoredBytes:
    def test_snapshot_over_cap_as_plain_json_but_under_it_stored_passes(self):
        from connect_labs.workflow.templates import _SNAPSHOT_SIZE_HARD_BYTES, _check_snapshot_size

        row = {f"a_rather_long_field_name_{i:02d}": i for i in range(12)}
        snap = {"state": {"rows": [dict(row) for _ in range(20000)]}}
        assert len(json.dumps(snap)) >= _SNAPSHOT_SIZE_HARD_BYTES
        assert len(json.dumps(encode_snapshot(snap))) < _SNAPSHOT_SIZE_HARD_BYTES
        _check_snapshot_size("columnar", snap)  # no raise

    def test_incompressible_snapshot_still_rejected(self):
        from connect_labs.workflow.templates import SnapshotTooLargeError, _check_snapshot_size

        with pytest.raises(SnapshotTooLargeError):
            _check_snapshot_size("huge", {"blob": "x" * (6 * 1024 * 1024)})


# ─── The boundaries: client writes encoded, records read decoded ───────────


class _FakeLabsServer:
    """Stands in for POST /export/labs_record/: stores what it is sent, echoes it back."""

    def __init__(self):
        self.stored: dict[int, dict] = {}

    def post(self, url, json=None, **kwargs):
        (payload,) = json
        record = {
            "id": payload.get("id", 900 + len(self.stored)),
            "opportunity_id": payload.get("opportunity_id"),
            **payload,
        }
        self.stored[record["id"]] = record
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = 200
        resp.raise_for_status.return_value = None
        resp.json.return_value = [record]
        return resp


@pytest.fixture
def wda_and_server():
    server = _FakeLabsServer()
    wda = WorkflowDataAccess(opportunity_id=700, access_token="fake")
    assert isinstance(wda.labs_api, LabsRecordAPIClient)
    with patch.object(wda.labs_api.http_client, "post", side_effect=server.post):
        yield wda, server
    wda.close()


def _stored_run(server, wda, **data) -> WorkflowRunRecord:
    """A run as a read would return it: created through the client, rebuilt from storage."""
    created = wda.labs_api.create_record(experiment="workflow", type="workflow_run", data=data)
    return WorkflowRunRecord(server.stored[created.id])


class TestWriteAndReadBoundaries:
    def test_complete_run_stores_encoded_and_exposes_decoded(self, wda_and_server):
        wda, server = wda_and_server
        run = _stored_run(server, wda, definition_id=7, status="in_progress", state={})
        snap = _realistic_snapshot(200)

        completed = wda.complete_run(run.id, snap, run=run)

        stored = server.stored[run.id]["data"]
        assert stored["snapshot"]["$codec"] == CODEC_ID
        assert stored["status"] == "completed"
        assert completed.snapshot == snap
        assert WorkflowRunRecord(server.stored[run.id]).snapshot == snap

    def test_rename_does_not_reinflate_a_completed_run(self, wda_and_server):
        wda, server = wda_and_server
        snap = _realistic_snapshot(200)
        run = _stored_run(server, wda, definition_id=7, status="completed", state={}, snapshot=snap)
        stored_before = server.stored[run.id]["data"]["snapshot"]
        assert run.snapshot == snap  # decoded on read

        renamed = wda.rename_run(run.id, "Week 39", run=run)

        assert server.stored[run.id]["data"]["snapshot"] == stored_before
        assert server.stored[run.id]["data"]["name"] == "Week 39"
        assert renamed.snapshot == snap

    def test_update_run_state_keeps_snapshot_encoded_and_state_plain(self, wda_and_server):
        wda, server = wda_and_server
        snap = _realistic_snapshot(50)
        run = _stored_run(server, wda, definition_id=7, status="in_progress", state={"a": 1}, snapshot=snap)

        updated = wda.update_run_state(run.id, {"rows": [{"x": 1}, {"x": 2}]}, run=run)

        stored = server.stored[run.id]["data"]
        assert stored["snapshot"]["$codec"] == CODEC_ID
        assert stored["state"] == {"a": 1, "rows": [{"x": 1}, {"x": 2}]}
        assert updated.snapshot == snap

    def test_direct_client_writers_are_encoded_too(self, wda_and_server):
        # Synthetic seeders and the mbw adapter bypass the DAO and call the client.
        wda, server = wda_and_server
        snap = _realistic_snapshot(20)
        rec = wda.labs_api.create_record(experiment="workflow", type="workflow_run", data={"snapshot": snap})
        assert server.stored[rec.id]["data"]["snapshot"]["$codec"] == CODEC_ID
        assert rec.data["snapshot"] == snap

    def test_legacy_plain_run_reads_unchanged(self):
        snap = _realistic_snapshot(20)
        legacy = {"id": 1, "experiment": "workflow", "type": "workflow_run", "opportunity_id": 1, "data": {}}
        legacy["data"] = {"definition_id": 7, "snapshot": snap}
        assert WorkflowRunRecord(legacy).snapshot == snap
        assert LocalLabsRecord(legacy).data["snapshot"] == snap
