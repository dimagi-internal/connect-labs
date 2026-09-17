"""The fill must never hold the whole opportunity, whatever the read asks for.

`fetch_raw_visits` fills the cache and answers the query in one call, so the cost
of a read has been unrelated to its result size: `fetch_visits_for_ids` asks for a
handful of visits by id and, on a cache miss, materialised every visit in the
opportunity — with `form_json`, and with images — to return them.

The unit is in `AuditDataAccess.fetch_visits_slim`'s own docstring: ~350 MB per
10k visits with `form_json` (~20 MB without), so ~35 KB a visit. A ~30k-visit
opportunity is **~1.05 GB resident per walk** on a **4096 MB** task. Two
concurrent walks is half the container. 2026-09-16 produced 27 OOM kills with the
tier at two tasks; 1,211 `page_size=2500` fetches over 83 cursors, 63 of them
repeated across more than one log stream.

These tests assert a COST, not a behaviour. With the unbounded fill the returned
rows are correct, the filters apply, the counts are right — every behavioural
property holds. Only how much is resident at once differs, and that is the whole
defect, which is why the suite has stayed green through every one of these
incidents (see `test_sse_backpressure.py` for the same class in another
subsystem).

The proxy for "resident rows" is the largest list ever handed to the cache writer
in one call. It is exact for this code (the writer is the only thing the fetch
accumulates into) and it is deterministic, which a GC-liveness probe would not be.
"""

import contextlib
from unittest.mock import patch

import pytest

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend

PAGE_SIZE = 2500
PAGES = 12  # ~30k visits, the shape #1361 measured
TOTAL = PAGE_SIZE * PAGES


class _RecordingCache:
    """Stands in for SQLCacheManager, recording every write's SIZE.

    Deliberately not a MagicMock: the assertion is about the sizes this receives,
    so the sizes have to be recorded by something that cannot silently accept a
    call it does not implement.
    """

    def __init__(self, opportunity_id, pipeline_id=None):
        self.opportunity_id = opportunity_id
        self.pipeline_id = pipeline_id
        self.write_sizes: list[int] = []
        self.rows: list[dict] = []
        self.images_fetched = None
        self.finalized = None

    # --- the two write APIs, which is what this test is about ---------------
    def store_raw_visits(self, visit_dicts, visit_count, images_fetched=False):
        self.write_sizes.append(len(visit_dicts))
        self.rows = list(visit_dicts)
        self.images_fetched = images_fetched

    def store_raw_visits_start(self, visit_count, images_fetched=False):
        self.images_fetched = images_fetched

    def store_raw_visits_batch(self, visit_dicts):
        self.write_sizes.append(len(visit_dicts))
        self.rows.extend(visit_dicts)
        return len(visit_dicts)

    def store_raw_visits_finalize(self, actual_count):
        self.finalized = actual_count

    def store_raw_visits_abort(self):
        self.rows = []

    # --- everything the miss path touches on the way through ----------------
    def has_valid_raw_cache(self, expected_visit_count, tolerance_pct=100):
        return False  # force the miss

    def get_raw_visit_count_ignoring_ttl(self):
        return 0  # first-ever fetch: skips the shrink guard entirely

    def get_raw_visit_count(self):
        return len(self.rows)

    def get_raw_delta_anchor(self):
        return None  # no anchor: the delta top-up declines, full rebuild

    def slot_has_image_data(self):
        return False

    def clear_pending_raw_fetch_anomaly(self):
        pass

    def get_pending_raw_fetch_anomaly(self):
        return None

    def set_pending_raw_fetch_anomaly(self, anomaly, minutes):
        pass

    def extend_raw_cache_ttl(self, minutes):
        pass


class _FakeExportClient:
    """Paginates TOTAL records in pages of PAGE_SIZE, like the real export API."""

    def __init__(self):
        self.pages_served = 0
        self.params_seen = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def paginate(self, endpoint, params=None):
        self.params_seen.append(params)
        for p in range(PAGES):
            self.pages_served += 1
            yield [{"id": p * PAGE_SIZE + i, "form_json": {"filler": "x"}} for i in range(PAGE_SIZE)]


@contextlib.contextmanager
def _leader():
    yield True


@pytest.fixture
def harness():
    cache = {}

    def _make(opportunity_id, pipeline_id=None):
        cache["obj"] = _RecordingCache(opportunity_id, pipeline_id)
        return cache["obj"]

    client = _FakeExportClient()
    with (
        patch("connect_labs.labs.analysis.backends.sql.backend.SQLCacheManager", _make),
        patch("connect_labs.labs.analysis.backends.sql.backend.claim_raw_rebuild", lambda *a, **k: _leader()),
        patch(
            "connect_labs.labs.integrations.connect.factory.get_export_client",
            lambda **kw: client,
        ),
        patch(
            "connect_labs.labs.analysis.backends.visit_record.record_to_visit_dict",
            lambda record, opportunity_id: dict(record),
        ),
        patch.object(SQLBackend, "_load_from_cache", lambda self, cm, skip_form_json, filter_visit_ids: list(cm.rows)),
    ):
        yield SQLBackend(), cache, client


def test_a_cache_miss_never_hands_the_writer_the_whole_opportunity(harness):
    """The bound. ~1.05 GB resident becomes ~87 MB, and this is what pins it."""
    backend, cache, _client = harness
    backend.fetch_raw_visits(opportunity_id=2154, access_token="t")

    sizes = cache["obj"].write_sizes
    assert sizes, "the fill wrote nothing — the test is not exercising the miss path"
    biggest = max(sizes)
    assert biggest <= PAGE_SIZE, (
        f"a single cache write received {biggest} visits ({TOTAL} total). The fill is "
        f"accumulating the whole opportunity before writing it: at ~35 KB a visit with "
        f"form_json that is ~{biggest * 35 / 1_000_000:.2f} GB resident on a 4096 MB task. "
        f"The bounded filler writes one page ({PAGE_SIZE}) at a time."
    )


def test_asking_for_one_visit_by_id_does_not_materialise_the_opportunity(harness):
    """`fetch_visits_for_ids`' shape — the read that costs the most per row returned."""
    backend, cache, _client = harness
    backend.fetch_raw_visits(opportunity_id=2154, access_token="t", filter_visit_ids={7})

    biggest = max(cache["obj"].write_sizes)
    assert biggest <= PAGE_SIZE, (
        f"a request for ONE visit accumulated {biggest} of them before writing. "
        f"This is the audit bulk-data / image path (#1361)."
    )


def test_an_image_request_is_also_bounded_and_records_that_it_asked(harness):
    """include_images used to skip both the bound AND the single-flight lock."""
    backend, cache, client = harness
    backend.fetch_raw_visits(opportunity_id=2154, access_token="t", include_images=True)

    biggest = max(cache["obj"].write_sizes)
    assert biggest <= PAGE_SIZE, f"the image fill accumulated {biggest} visits"
    assert cache["obj"].images_fetched is True, (
        "the slot must record that this fetch ASKED for images, or slot_has_image_data() "
        "reads False and every image request re-downloads the whole opportunity"
    )
    assert client.params_seen and client.params_seen[0] == {
        "images": "true"
    }, f"the fill did not request images from the API: params={client.params_seen}"


def test_every_row_still_reaches_the_cache(harness):
    """The bound must not lose data — otherwise the fix for it is to remove it."""
    backend, cache, client = harness
    backend.fetch_raw_visits(opportunity_id=2154, access_token="t")

    assert sum(cache["obj"].write_sizes) == TOTAL
    assert client.pages_served == PAGES
