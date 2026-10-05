"""A completed run's snapshot, stored in a CHILD labs record.

A saved `workflow_run` used to carry its whole snapshot inline in `data`, so
anything that LISTED runs -- the trend endpoint, the workflow list page, the
hand-down ledger, benchmark auto-publish, the history rebuild -- downloaded every
run's snapshot to read a handful of fields. The labs-record API has no field
projection: a listed record always arrives with its full `data`. A KMC programme
report stores ~0.5-1.5 MB per run and has ~70 weekly runs.

So a large snapshot moves into a child record, and the run keeps a pointer plus a
small projection:

    run   (type workflow_run)          data = {definition_id, period_*, status,
                                               state, name, ...,
                                               snapshot_ref:     {record_id, bytes, sha256},
                                               snapshot_summary: {v, state: {...}}}
    child (type workflow_run_snapshot) data = {snapshot: <run_codec envelope>}
          labs_record_id = run id, same experiment and scope as the run

Like `run_codec`, this is applied at one write boundary and one read boundary,
both inside `LabsRecordAPIClient`, so readers keep seeing `run.data["snapshot"]`:

* WRITE -- `create_record` / `update_record` of a `workflow_run` whose snapshot
  encodes to `EXTERNALIZE_MIN_BYTES` or more upsert the child, then write the run
  with `snapshot` replaced by `snapshot_ref` + `snapshot_summary`. A snapshot whose
  content hash matches the ref is NOT re-uploaded: `rename_run` and
  `update_run_state` re-write the whole `data` dict on every call.
  Smaller snapshots stay inline exactly as before (fewer records, and most
  templates' snapshots are small).
* READ -- a run record carrying a `snapshot_ref` gets a `LazyRunData` as its
  `data`: the snapshot is fetched (one GET by id) the first time something asks
  for it, and never if nothing does. That is what makes LISTING cheap without
  changing a single reader.

Readers that only need the trend fields -- `run_history_api` and benchmark
auto-publish's `run_history` -- read `snapshot_summary` through `snapshot_state`
instead, which falls back to the full snapshot whenever a requested path is not
in the summary (or the run is a legacy inline one, which has no summary).

Legacy runs (inline snapshot, no ref) read exactly as before; no migration is
needed. A legacy run that is rewritten with a large snapshot (renamed, say) is
externalized by that write -- the boundary does not care where the snapshot
came from.

Deleting a run deletes its child: Connect's `LabsRecord.labs_record` is a
nullable self-FK with ON DELETE CASCADE, and the labs-local backend (whose
`labs_record_id` is a plain integer) cascades this type explicitly in
`local_records_backend.delete_records`.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable, Mapping
from typing import Any

SNAPSHOT_RECORD_TYPE = "workflow_run_snapshot"
SNAPSHOT_KEY = "snapshot"
REF_KEY = "snapshot_ref"
SUMMARY_KEY = "snapshot_summary"

# Below this the snapshot stays inline. 64 KB keeps the small template snapshots
# (audit weeks, daily summaries, demo seeds) as single records, and moves the
# programme-report class (hundreds of KB to MBs) out of every listing.
EXTERNALIZE_MIN_BYTES = 64 * 1024

SUMMARY_VERSION = 1

# What the summary keeps of each graded payload (`snapshot.state.<state_key>`),
# and WHY each key is there. These are the only multi-run readers:
#
#   run_history_api (`?keys=` from the render code, paths under `state`):
#     kmc_programme_metrics_render.js -- programInd, byLLO, byOpp,
#         pooledOverCredible, meta, series.N.{programme, byLLO, byOpp}
#     kmc_opp_report_render.js        -- programInd, meta
#     indicator_report_render.js      -- programInd, byLLO, byOpp, meta
#     kmc_flw_review_render.js,
#     indicator_worker_review_render.js -- meta.as_of
#   benchmarks.auto_publish.run_history:
#     cMeasures (to name the primary series), byOpp[].{opp, ind},
#     series.<name>.byOpp[].{opp, ind}
#
# Everything else -- the case index, byFLW, monthly / monthlyByScope, weekly,
# the series' byFLW and monthly -- is per-run detail that only a single-run read
# (the run page, preview, hand-down, publish_run) uses, and those get the full
# snapshot from the child.
SUMMARY_PAYLOAD_KEYS = ("programInd", "byOpp", "byLLO", "pooledOverCredible", "cMeasures", "meta")
SUMMARY_SERIES_KEYS = ("programme", "byLLO", "byOpp")
_SERIES = "series"
# Each `byLLO[]` entry carries `opps`: a copy of the `byOpp` entries of that LLO,
# which in JSON is a second copy of byOpp (~17 KB on a KMC programme run). The
# summary drops it when it is EXACTLY that filter of byOpp and puts it back on
# read, so a projection through the summary equals one through the snapshot.
_LLO_OPPS = "opps"
_DERIVED_OPPS = "byLLOOppsFromByOpp"


# ─── Hashing / sizing ───────────────────────────────────────────────────────


def stored_body(encoded_snapshot: Any) -> bytes:
    """The bytes a snapshot is sized and hashed by. Canonical (sorted keys,
    compact separators) so the same snapshot always hashes the same."""
    return json.dumps(encoded_snapshot, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# ─── The summary ────────────────────────────────────────────────────────────


def _opps_derivable(payload: dict) -> bool:
    by_llo, by_opp = payload.get("byLLO"), payload.get("byOpp")
    if not isinstance(by_llo, list) or not isinstance(by_opp, list) or not by_llo:
        return False
    for entry in by_llo:
        if not isinstance(entry, dict) or _LLO_OPPS not in entry:
            return False
        expected = [o for o in by_opp if isinstance(o, dict) and o.get("llo") == entry.get("llo")]
        if entry[_LLO_OPPS] != expected:
            return False
    return True


def build_summary(snapshot: Any) -> dict:
    """The small projection of `snapshot` that multi-run readers use. See
    SUMMARY_PAYLOAD_KEYS for what is kept and why."""
    state = snapshot.get("state") if isinstance(snapshot, dict) else None
    out_state: dict[str, dict] = {}
    derived: list[str] = []
    for state_key, payload in (state or {}).items() if isinstance(state, dict) else ():
        if not isinstance(payload, dict):
            continue
        kept = {k: payload[k] for k in SUMMARY_PAYLOAD_KEYS if k in payload}
        if _SERIES in payload:
            series = payload[_SERIES]
            if isinstance(series, dict):
                kept[_SERIES] = {
                    name: (
                        {k: block[k] for k in SUMMARY_SERIES_KEYS if k in block} if isinstance(block, dict) else block
                    )
                    for name, block in series.items()
                }
            else:
                kept[_SERIES] = series
        if _opps_derivable(payload):
            kept["byLLO"] = [{k: v for k, v in e.items() if k != _LLO_OPPS} for e in payload["byLLO"]]
            derived.append(state_key)
        out_state[state_key] = kept
    return {"v": SUMMARY_VERSION, "state": out_state, _DERIVED_OPPS: derived}


def expand_summary_state(summary: Mapping) -> dict:
    """The summary's `state`, with every dropped `byLLO[].opps` put back."""
    state = dict(summary.get("state") or {})
    for state_key in summary.get(_DERIVED_OPPS) or []:
        payload = state.get(state_key)
        if not isinstance(payload, dict):
            continue
        by_opp = payload.get("byOpp") or []
        state[state_key] = {
            **payload,
            "byLLO": [
                {**e, _LLO_OPPS: [o for o in by_opp if isinstance(o, dict) and o.get("llo") == e.get("llo")]}
                for e in payload.get("byLLO") or []
            ],
        }
    return state


def summary_covers(path: str) -> bool:
    """Whether the summary answers a dotted path under the snapshot's `state`
    exactly as the full snapshot would. `<state_key>.<kept key>[...]` and
    `<state_key>.series.<any name>.<kept series key>[...]` are covered; a path
    through a key the summary drops is not, and neither is a bare state key."""
    parts = path.split(".")
    if len(parts) < 2:
        return False
    if parts[1] in SUMMARY_PAYLOAD_KEYS:
        return True
    return parts[1] == _SERIES and len(parts) >= 4 and parts[3] in SUMMARY_SERIES_KEYS


def snapshot_state(run: Any, paths: Iterable[str]) -> dict:
    """`run.snapshot["state"]` for a reader that needs only `paths` of it.

    From `snapshot_summary` when the run has one and it covers every path --
    no child fetch -- otherwise from the full snapshot (lazily loaded for an
    externalized run, already in hand for a legacy inline one). `paths` are
    dotted, under `state`, as `run_history_api`'s `?keys=` are; a series name
    may be `*`.
    """
    paths = list(paths)
    data = getattr(run, "data", None)
    summary = data.get(SUMMARY_KEY) if isinstance(data, Mapping) else None
    if isinstance(summary, Mapping) and paths and all(summary_covers(p) for p in paths):
        return expand_summary_state(summary)
    snap = getattr(run, "snapshot", None) if hasattr(run, "snapshot") else (data or {}).get(SNAPSHOT_KEY)
    return ((snap or {}).get("state") if isinstance(snap, Mapping) else None) or {}


# ─── The lazy read side ─────────────────────────────────────────────────────


class LazyRunData(dict):
    """A run's `data` whose `snapshot` lives in a child record, fetched on first use.

    A dict subclass, so every existing reader keeps working unchanged:
    `data["snapshot"]`, `data.get("snapshot")`, `run.snapshot`, `{**data}`,
    `dict(data)`, `json.dumps(data)` and `==` all see the snapshot, loading it
    (once) if they need its VALUE. `"snapshot" in data`, `len(data)`, truthiness,
    and every other key's read do NOT load it, so listing runs and reading their
    metadata costs no child fetch.

    The mechanism: until loaded, `snapshot` is simply absent from the underlying
    dict storage, and every method that could expose it (item access, `get`,
    iteration, views, copies, equality, pickling) is overridden to load first.
    Overriding `__iter__` also matters for a non-obvious reason: CPython's dict
    merge -- behind `{**d}`, `dict(d)` and `d2.update(d)` -- reads the raw storage
    of a dict subclass directly UNLESS its `__iter__` is overridden, in which case
    it goes through `keys()` and `__getitem__`.

    `stored_data()` returns the storage as is -- `snapshot_ref` and
    `snapshot_summary` included, `snapshot` only if already loaded -- for writers
    that re-write a run without touching its snapshot.
    """

    __slots__ = ("_loader",)

    def __init__(self, stored: Mapping, loader: Callable[[], Any] | None):
        super().__init__(stored)
        has_ref = isinstance(dict.get(self, REF_KEY), Mapping)
        self._loader = loader if (loader is not None and has_ref and SNAPSHOT_KEY not in stored) else None

    # -- loading -------------------------------------------------------------

    @property
    def snapshot_loaded(self) -> bool:
        return self._loader is None

    def _materialize(self) -> None:
        loader = self._loader
        if loader is None:
            return
        value = loader()  # raises -> stays unloaded, so the next access retries
        dict.__setitem__(self, SNAPSHOT_KEY, value)
        self._loader = None

    def _raw(self) -> dict:
        # NOT dict.copy(self): for a subclass that overrides __iter__, CPython's
        # copy goes through keys() -- which would load the snapshot. Iterating
        # the base items view reads the storage directly.
        return {k: v for k, v in dict.items(self)}

    def stored_data(self) -> dict:
        """The raw stored fields, without loading the snapshot."""
        return self._raw()

    # -- reads that need the snapshot's value --------------------------------

    def __getitem__(self, key):
        if key == SNAPSHOT_KEY:
            self._materialize()
        return dict.__getitem__(self, key)

    def get(self, key, default=None):
        if key == SNAPSHOT_KEY:
            self._materialize()
        return dict.get(self, key, default)

    def __iter__(self):
        self._materialize()
        return dict.__iter__(self)

    def keys(self):
        self._materialize()
        return dict.keys(self)

    def values(self):
        self._materialize()
        return dict.values(self)

    def items(self):
        self._materialize()
        return dict.items(self)

    def __reversed__(self):
        self._materialize()
        return dict.__reversed__(self)

    def __eq__(self, other):
        self._materialize()
        if isinstance(other, LazyRunData):
            other._materialize()
        return dict.__eq__(self, other)

    def __ne__(self, other):
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq

    __hash__ = None

    def copy(self):
        self._materialize()
        return self._raw()

    __copy__ = copy

    def __deepcopy__(self, memo):
        import copy as _copy

        return _copy.deepcopy(self.copy(), memo)

    def __reduce_ex__(self, protocol):
        # Pickles as a plain dict: the loader is a closure over a live client.
        return (dict, (self.copy(),))

    def __or__(self, other):
        return self.copy() | other

    def __ror__(self, other):
        return dict(other) | self.copy()

    # -- reads that do not -------------------------------------------------------

    def __contains__(self, key):
        if key == SNAPSHOT_KEY and self._loader is not None:
            return True
        return dict.__contains__(self, key)

    def __len__(self):
        return dict.__len__(self) + (1 if self._loader is not None else 0)

    def __repr__(self):
        if self._loader is None:
            return dict.__repr__(self)
        ref = dict.get(self, REF_KEY) or {}
        return f"LazyRunData({dict.__repr__(self)}, snapshot=<not loaded: record {ref.get('record_id')}>)"

    # -- writes ----------------------------------------------------------------

    def __setitem__(self, key, value):
        if key == SNAPSHOT_KEY:
            self._loader = None
        dict.__setitem__(self, key, value)

    def __delitem__(self, key):
        if key == SNAPSHOT_KEY and self._loader is not None:
            self._loader = None
            if not dict.__contains__(self, key):
                return
        dict.__delitem__(self, key)

    def update(self, *args, **kwargs):
        incoming = dict(*args, **kwargs)
        if SNAPSHOT_KEY in incoming:
            self._loader = None
        dict.update(self, incoming)

    def __ior__(self, other):
        self.update(other)
        return self

    def setdefault(self, key, default=None):
        if key == SNAPSHOT_KEY:
            self._materialize()
        return dict.setdefault(self, key, default)

    def pop(self, key, *default):
        if key == SNAPSHOT_KEY:
            self._materialize()
        return dict.pop(self, key, *default)

    def popitem(self):
        self._materialize()
        return dict.popitem(self)

    def clear(self):
        self._loader = None
        dict.clear(self)


def stored_form(data: Any) -> Any:
    """`data` as it should be re-written: a `LazyRunData`'s raw storage (its
    snapshot NOT loaded), anything else unchanged. Writers that spread a run's
    data to change one field use this instead of `{**run.data}`, which would
    fetch the snapshot only to write the same one back."""
    return data.stored_data() if isinstance(data, LazyRunData) else data
