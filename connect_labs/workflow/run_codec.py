"""Compact storage format for a saved workflow run's snapshot.

A completed run's `data["snapshot"]` is mostly long lists of records that all
share one shape -- a programme snapshot's `cases` is ~9k dicts carrying the same
~14 keys, and the byFLW / monthly series repeat the pattern. Stored as plain JSON,
every element spells out every field name again, so most of the bytes are keys.

This module stores such a list COLUMNAR -- field names once, then rows of values:

    [{"a": 1, "b": 2}, {"a": 3, "b": 4}]  ->  {"$cols": ["a", "b"], "$rows": [[1, 2], [3, 4]]}

It is applied at exactly two places, so nothing above the storage layer ever sees
it: `LabsRecordAPIClient.create_record` / `update_record` encode a `workflow_run`'s
snapshot on the way out (`encode_record_data`), and `LocalLabsRecord.__init__`
decodes it on the way in (`decode_record_data`). Every reader -- the DAO, views,
MCP tools, hand-down, benchmarks, the browser -- keeps getting the exact shape the
snapshot builder produced. Putting the write side in the client rather than the
DAO is deliberate: several writers (synthetic seeders, the mbw session adapter)
call `labs_api.create_record` / `update_record` directly, and every DAO write
spreads `**run.data` back out of a decoded record -- any writer that forgot to
re-encode would silently re-inflate the run.

Only `snapshot` is encoded. The rest of `data` (definition_id, period_*, status,
state.generated_by, state.hand_down_key, ...) stays plain JSON because the labs
API filters on it server-side (`data__state__hand_down_key=...`, see
`hand_down.hand_down_key`), and a filter cannot see inside an encoded value.

The encoded snapshot is wrapped in a versioned envelope, `{"$codec": "columnar/1",
"value": ...}`. That makes decoding opt-in by marker rather than by guessing:
legacy runs (plain JSON) and already-decoded data pass through unchanged, and a
second decode of decoded data is a no-op. Inside the envelope the transform is
exact and lossless -- `decode(encode(x)) == x` for any JSON value -- including a
user dict that happens to look like a marker, which is escaped as `{"$esc": {...}}`.
"""

from __future__ import annotations

from typing import Any

CODEC_ID = "columnar/1"
RUN_RECORD_TYPE = "workflow_run"

_COLS = "$cols"
_ROWS = "$rows"
_ESC = "$esc"
_CODEC = "$codec"
_VALUE = "value"

# Key sets a decoder reads as a marker rather than as a user dict. A user dict
# with EXACTLY one of these key sets must be escaped by the encoder; any other
# dict -- including one with a "$cols" key among others -- is unambiguous.
_COLUMNAR_KEYS = frozenset((_COLS, _ROWS))
_ESC_KEYS = frozenset((_ESC,))


def encode(obj: Any) -> Any:
    """Columnar-encode every list of >= 2 same-keyed dicts inside `obj`.

    "Same-keyed" means the identical key SET, in any order; `$cols` takes the first
    element's order. A list that is shorter, mixed, or has differing keys stays a
    list (its elements are still encoded). Non-container values pass through.
    """
    if isinstance(obj, dict):
        encoded = {k: encode(v) for k, v in obj.items()}
        if obj.keys() == _COLUMNAR_KEYS or obj.keys() == _ESC_KEYS:
            return {_ESC: encoded}
        return encoded
    if isinstance(obj, list):
        if len(obj) >= 2 and isinstance(obj[0], dict):
            first_keys = obj[0].keys()
            if all(isinstance(item, dict) and item.keys() == first_keys for item in obj):
                cols = list(first_keys)
                return {_COLS: cols, _ROWS: [[encode(item[c]) for c in cols] for item in obj]}
        return [encode(item) for item in obj]
    return obj


def decode(obj: Any) -> Any:
    """Inverse of `encode`. Rows are rebuilt directly from `$cols`, so a decoded
    element is never itself re-read as a marker."""
    if isinstance(obj, dict):
        keys = obj.keys()
        if keys == _COLUMNAR_KEYS and isinstance(obj[_COLS], list) and isinstance(obj[_ROWS], list):
            cols = obj[_COLS]
            return [{c: decode(v) for c, v in zip(cols, row)} for row in obj[_ROWS]]
        if keys == _ESC_KEYS and isinstance(obj[_ESC], dict):
            return {k: decode(v) for k, v in obj[_ESC].items()}
        return {k: decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode(item) for item in obj]
    return obj


def _is_envelope(value: Any) -> bool:
    return isinstance(value, dict) and value.keys() == {_CODEC, _VALUE} and value[_CODEC] == CODEC_ID


def encode_snapshot(snapshot: Any) -> Any:
    """The stored form of a snapshot: the encoded value in a versioned envelope.

    Idempotent -- an already-encoded snapshot is returned as is -- and `None`
    (an in-progress run has no snapshot) stays `None`.
    """
    if snapshot is None or _is_envelope(snapshot):
        return snapshot
    return {_CODEC: CODEC_ID, _VALUE: encode(snapshot)}


def decode_snapshot(snapshot: Any) -> Any:
    """The snapshot as readers expect it. Anything without the envelope -- a legacy
    plain-JSON run, or data that is already decoded -- is returned unchanged."""
    if _is_envelope(snapshot):
        return decode(snapshot[_VALUE])
    return snapshot


def encode_record_data(record_type: str, data: Any) -> Any:
    """Write boundary: a labs record's `data` as it should be stored."""
    if record_type != RUN_RECORD_TYPE or not isinstance(data, dict) or data.get("snapshot") is None:
        return data
    return {**data, "snapshot": encode_snapshot(data["snapshot"])}


def decode_record_data(record_type: str, data: Any) -> Any:
    """Read boundary: a labs record's `data` as callers expect to see it."""
    if record_type != RUN_RECORD_TYPE or not isinstance(data, dict) or not _is_envelope(data.get("snapshot")):
        return data
    return {**data, "snapshot": decode_snapshot(data["snapshot"])}
