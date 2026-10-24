"""Labs' Overture snapshots: the manifest, the tile router, and the refresh.

The design these pin (overture.py, docs/superpowers/specs/2026-10-24-overture-mirror-design.md):
labs reads only Overture data it holds, a release becomes current only after
it is checked, and a failure anywhere leaves labs on what it already reads.
"""

from __future__ import annotations

import json
from io import StringIO

import pytest
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from shapely.geometry import box

from connect_labs.microplans.core import barriers, overture
from connect_labs.microplans.management.commands import overture_refresh

NIGERIA = (2.6, 4.2, 14.7, 13.9)
MADOBI = (8.282, 11.770, 8.288, 11.775)


@pytest.fixture
def s3_json(settings, monkeypatch):
    """Turn the manifest read on, against an in-memory bucket."""
    settings.OVERTURE_SNAPSHOTS_READ_MANIFEST = True
    cache.clear()
    store: dict[str, dict | None] = {}
    monkeypatch.setattr(overture, "_read_json", lambda key: store.get(key))
    yield store
    cache.clear()


# ---------------------------------------------------------------------------
# The manifest


def test_with_no_manifest_labs_reads_the_floor(s3_json):
    assert overture.active_release() == overture.OVERTURE_RELEASE


def test_the_manifest_names_the_release(s3_json):
    s3_json[overture.MANIFEST_KEY] = {"release": "2026-11-18.0", "previous": "2026-10-21.0"}
    assert overture.active_release() == "2026-11-18.0"
    assert overture.theme_path("buildings", "building") == (
        f"s3://{overture.EXTRACT_BUCKET}/overture/snapshots/2026-11-18.0/raw/theme=buildings/type=building/*"
    )


def test_an_s3_outage_keeps_the_last_release_rather_than_falling_to_the_floor(s3_json):
    """A failed manifest read must not change what labs reads. Falling back to the
    code floor here would quietly undo a switch during an S3 blip."""
    s3_json[overture.MANIFEST_KEY] = {"release": "2026-11-18.0"}
    assert overture.active_release() == "2026-11-18.0"

    cache.delete(overture._CACHE_KEY.format(overture.MANIFEST_KEY))  # the 10-minute entry expires
    s3_json[overture.MANIFEST_KEY] = None  # and S3 does not answer
    assert overture.active_release() == "2026-11-18.0"


def test_a_switch_reaches_a_reader_once_its_cache_expires(s3_json):
    s3_json[overture.MANIFEST_KEY] = {"release": "2026-10-21.0"}
    assert overture.active_release() == "2026-10-21.0"
    s3_json[overture.MANIFEST_KEY] = {"release": "2026-11-18.0"}
    assert overture.active_release() == "2026-10-21.0"  # still cached
    overture.forget_cached_manifest()
    assert overture.active_release() == "2026-11-18.0"


# ---------------------------------------------------------------------------
# The tile router


def _ready(**over):
    info = {
        "tiles": {"buildings": {"nigeria": NIGERIA}, "segments": {"nigeria": NIGERIA}},
        "filters": {"segments": barriers.segment_filters()},
    }
    info.update(over)
    return info


def test_an_area_inside_a_region_routes_to_its_tiles(monkeypatch):
    monkeypatch.setattr(overture, "ready", lambda release=None: _ready())
    assert overture.tile_route(MADOBI, "buildings") == "nigeria"


def test_an_area_straddling_a_region_edge_reads_raw_not_a_partial_tile_set(monkeypatch):
    monkeypatch.setattr(overture, "ready", lambda release=None: _ready())
    assert overture.tile_route((14.6, 10.0, 14.9, 10.2), "buildings") is None


def test_a_kind_with_no_tiles_reads_raw(monkeypatch):
    monkeypatch.setattr(overture, "ready", lambda release=None: _ready())
    assert overture.tile_route(MADOBI, "water") is None


def test_tiles_cut_under_other_road_classes_are_not_used(monkeypatch):
    """Changing ROAD_CLASSES must not keep serving tiles that left the new classes out."""
    monkeypatch.setattr(overture, "ready", lambda release=None: _ready())
    assert overture.tile_route(MADOBI, "segments", filters=barriers.segment_filters()) == "nigeria"
    other = {"road_classes": ["motorway"], "rail": True}
    assert overture.tile_route(MADOBI, "segments", filters=other) is None


def test_every_region_has_a_sane_bbox_and_a_sample_inside_it():
    """A bad bbox silently cuts the wrong ground; a sample outside it checks nothing."""
    for name, meta in overture.REGIONS.items():
        x0, y0, x1, y1 = meta["bbox"]
        assert x0 < x1 and y0 < y1 and -180 <= x0 and x1 <= 180 and -90 <= y0 and y1 <= 90, name
        sx0, sy0, sx1, sy1 = meta["sample"]
        assert x0 <= sx0 < sx1 <= x1 and y0 <= sy0 < sy1 <= y1, f"{name} sample is outside its bbox"
        assert len(meta["iso2"]) == 2, name


def test_every_sample_is_a_small_box_in_a_covered_country():
    """The check samples each country; one the plan does not cover would fail
    every refresh, and a typo'd code would check nothing."""
    assert set(overture.REGIONS) <= set(overture.SAMPLES)
    for name, meta in overture.SAMPLES.items():
        x0, y0, x1, y1 = meta["sample"]
        assert x0 < x1 <= x0 + 1 and y0 < y1 <= y0 + 1, name
        assert meta["iso2"] in overture.COVERAGE_COUNTRIES, name
    assert len(set(overture.COVERAGE_COUNTRIES)) == len(overture.COVERAGE_COUNTRIES)


# ---------------------------------------------------------------------------
# Barriers


class _Con:
    def __init__(self, fail=False):
        self.sql, self.fail = [], fail

    def execute(self, sql, params=None):
        if self.fail:
            raise RuntimeError("No files found that match the pattern")
        self.sql.append(sql)
        return self

    def fetchall(self):
        return [('{"type":"LineString","coordinates":[[8.28,11.77],[8.29,11.78]]}',)]

    def close(self):
        pass


def test_barriers_read_tiles_inside_a_region_and_raw_outside(monkeypatch):
    monkeypatch.setattr(
        overture,
        "ready",
        lambda release=None: _ready(
            tiles={"segments": {"nigeria": NIGERIA}, "water": {"nigeria": NIGERIA}},
            filters={"segments": barriers.segment_filters(), "water": barriers.water_filters()},
        ),
    )
    con = _Con()
    monkeypatch.setattr(overture, "connect", lambda: con)
    assert barriers._query_barriers(box(*MADOBI)) is not None
    assert all("/tiles/" in sql for sql in con.sql) and len(con.sql) == 2

    con.sql.clear()
    barriers._query_barriers(box(77.20, 28.60, 77.21, 28.61))  # Delhi
    assert all("/raw/" in sql for sql in con.sql) and len(con.sql) == 2
    assert not any(overture.SOURCE_BUCKET in sql for sql in con.sql)


@pytest.mark.django_db
def test_a_failed_barrier_read_raises_and_caches_nothing(monkeypatch):
    """October's bug: the read failed, was logged, and "no barriers" was cached as
    the area's answer. Now it raises, so nothing poisons the cache."""
    from connect_labs.microplans.models import BarrierArea

    monkeypatch.setattr(overture, "ready", lambda release=None: None)
    monkeypatch.setattr(overture, "connect", lambda: _Con(fail=True))
    with pytest.raises(RuntimeError):
        barriers.fetch_barriers(box(*MADOBI))
    assert BarrierArea.objects.count() == 0


# ---------------------------------------------------------------------------
# overture_refresh


class _FakeS3:
    """Just enough of an S3 client for the manifest, markers and prune."""

    class exceptions:
        class NoSuchKey(Exception):
            pass

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.objects = dict(objects or {})

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        if Key not in self.objects:
            raise self.exceptions.NoSuchKey(Key)
        return {"Body": StringIO(self.objects[Key].decode())}

    def delete_objects(self, Bucket, Delete):
        for o in Delete["Objects"]:
            self.objects.pop(o["Key"], None)

    def get_paginator(self, name):
        fake = self

        class P:
            def paginate(self, Bucket, Prefix, Delimiter=None):
                keys = [k for k in fake.objects if k.startswith(Prefix)]
                if Delimiter:
                    prefixes = sorted({Prefix + k[len(Prefix) :].split(Delimiter)[0] + Delimiter for k in keys})
                    yield {"CommonPrefixes": [{"Prefix": p} for p in prefixes]}
                else:
                    yield {"Contents": [{"Key": k, "Size": len(fake.objects[k])} for k in keys]}

        return P()


def _refresh(fake, *args):
    out = StringIO()
    cmd = overture_refresh.Command(stdout=out)
    cmd.src = cmd.dst = fake
    cmd.release = args[0]
    cmd.dry_run = False
    return cmd, out


def _manifest(fake):
    return json.loads(fake.objects[overture.MANIFEST_KEY])


def test_switch_refuses_a_snapshot_that_is_not_ready():
    fake = _FakeS3({overture.MANIFEST_KEY: json.dumps({"release": "2026-10-21.0"}).encode()})
    cmd, _ = _refresh(fake, "2026-11-18.0")
    with pytest.raises(CommandError, match="no READY.json"):
        cmd._switch([], [])
    assert _manifest(fake)["release"] == "2026-10-21.0"


def test_switch_keeps_the_release_it_replaces_as_the_rollback():
    fake = _FakeS3(
        {
            overture.MANIFEST_KEY: json.dumps({"release": "2026-10-21.0"}).encode(),
            "overture/snapshots/2026-11-18.0/READY.json": b"{}",
        }
    )
    cmd, _ = _refresh(fake, "2026-11-18.0")
    cmd._switch([], [])
    assert _manifest(fake)["release"] == "2026-11-18.0"
    assert _manifest(fake)["previous"] == "2026-10-21.0"


def test_prune_deletes_only_snapshots_that_are_neither_current_nor_previous():
    fake = _FakeS3(
        {
            overture.MANIFEST_KEY: json.dumps({"release": "C", "previous": "B"}).encode(),
            "overture/snapshots/A/raw/x.parquet": b"a",
            "overture/snapshots/B/raw/x.parquet": b"b",
            "overture/snapshots/C/raw/x.parquet": b"c",
        }
    )
    cmd, _ = _refresh(fake, "C")
    cmd._prune([], [])
    assert sorted(k.split("/")[2] for k in fake.objects if k.startswith("overture/snapshots/")) == ["B", "C"]


def _planned(fake, release, cells):
    fake.objects[f"overture/snapshots/{release}/tiles/cells.json"] = json.dumps(
        {"cells": cells, "block_degrees": overture.BLOCK_DEGREES}
    ).encode()


def _block(fake, release, kind, block, rows=5, filters="current"):
    filters = overture_refresh._filters(kind) if filters == "current" else filters
    fake.objects[f"overture/snapshots/{release}/tiles/{kind}/blocks/{block}.json"] = json.dumps(
        {"rows": rows, "filters": filters}
    ).encode()


def test_ready_lists_a_kind_only_when_every_strip_was_cut_under_the_current_filters():
    """One strip of segments cut with other road classes must keep the whole kind
    off the grid, or the reader would serve roads the grouper no longer asks for
    (or "no roads" for a strip that was never cut)."""
    fake = _FakeS3({"overture/snapshots/R/raw/theme=buildings/x.parquet": b"x"})
    _planned(fake, "R", [[8, 11], [8, 12], [10, 11]])
    for block in (8, 10):
        _block(fake, "R", "buildings", block)
        _block(fake, "R", "water", block)
    _block(fake, "R", "segments", 8)
    _block(fake, "R", "segments", 10, filters={"road_classes": ["motorway"], "rail": True})
    cmd, _ = _refresh(fake, "R")
    cmd.checks = {}
    cmd._ready([], [])
    grid = json.loads(fake.objects["overture/snapshots/R/READY.json"])["grid"]
    assert grid["cells"] == [[8, 11], [8, 12], [10, 11]]
    assert grid["kinds"]["buildings"] == {"rows": 10, "filters": None}
    assert sorted(grid["kinds"]) == ["buildings", "water"]


def test_ready_still_lists_the_per_country_tiles_for_readers_not_yet_on_the_grid():
    fake = _FakeS3({"overture/snapshots/R/raw/theme=buildings/x.parquet": b"x"})
    _planned(fake, "R", [[8, 11]])
    fake.objects["overture/snapshots/R/tiles/buildings/nigeria/_DONE.json"] = json.dumps(
        {"rows": 5, "filters": None, "bbox": list(NIGERIA)}
    ).encode()
    cmd, _ = _refresh(fake, "R")
    cmd.checks = {}
    cmd._ready([], [])
    assert json.loads(fake.objects["overture/snapshots/R/READY.json"])["tiles"]["buildings"] == {
        "nigeria": list(NIGERIA)
    }


def test_the_plan_and_cut_read_labs_raw_copy_not_overtures_bucket():
    out = StringIO()
    call_command("overture_refresh", "2099-01-01.0", "--steps", "plan,cut", "--blocks", "8", "--dry-run", stdout=out)
    plan = out.getvalue()
    assert "overture/snapshots/2099-01-01.0/raw/theme=divisions/type=division_area" in plan
    assert "overture/snapshots/2099-01-01.0/raw/theme=buildings/type=building" in plan
    assert "overture/snapshots/2099-01-01.0/raw/theme=transportation/type=segment" in plan
    assert "overture/snapshots/2099-01-01.0/tiles/water/grid" in plan
    assert overture.SOURCE_BUCKET not in plan


def test_a_strip_that_does_not_start_on_the_grid_is_refused():
    with pytest.raises(CommandError, match="multiple of"):
        call_command("overture_refresh", "2099-01-01.0", "--steps", "cut", "--blocks", "9", "--dry-run")


def test_strips_group_the_planned_cells_by_their_west_edge():
    assert overture_refresh._blocks([(8, 11), (9, 12), (10, 11), (-1, 5)]) == {
        -2: [(-1, 5)],
        8: [(8, 11), (9, 12)],
        10: [(10, 11)],
    }
    assert overture.cells_touching((8.9, 11.5, 9.1, 11.6)) == [(8, 11), (9, 11)]


def test_a_cut_needs_a_plan():
    cmd, _ = _refresh(_FakeS3(), "R")
    with pytest.raises(CommandError, match="run --steps plan first"):
        cmd._cut(["segments"], [])


def test_check_fails_when_a_planned_strip_was_never_cut(monkeypatch):
    fake = _FakeS3()
    _planned(fake, "R", [[8, 11], [10, 11]])
    for kind in overture.TILE_KINDS:
        _block(fake, "R", kind, 8)
    monkeypatch.setattr(overture, "SAMPLES", {})
    monkeypatch.setattr(overture, "connect", lambda: _Con())
    cmd, _ = _refresh(fake, "R")
    with pytest.raises(CommandError, match="strip 10: no buildings tiles"):
        cmd._check([], [])


def test_unknown_steps_are_refused_before_anything_runs():
    with pytest.raises(CommandError, match="unknown step"):
        call_command("overture_refresh", "2099-01-01.0", "--steps", "copy,bump", "--dry-run")


def test_a_cut_clears_a_half_written_strip_before_cutting_it_again(monkeypatch):
    """A killed cut leaves files and no marker. DuckDB's OVERWRITE_OR_IGNORE keeps
    files it does not rewrite, so those would be read alongside the new cut. Only
    the strip's own columns are cleared: other strips may be cutting in parallel."""
    grid = "overture/snapshots/R/tiles/segments/grid/"
    fake = _FakeS3({grid + "tx=9/ty=11/data_9.parquet": b"partial", grid + "tx=10/ty=11/data_0.parquet": b"other"})
    _planned(fake, "R", [[8, 11], [9, 11], [10, 11]])

    class Con:
        def execute(self, sql, params=None):
            return self

        def fetchone(self):
            return (42,)

        def close(self):
            pass

    monkeypatch.setattr(overture, "connect", lambda: Con())
    cmd, _ = _refresh(fake, "R")
    cmd._cut(["segments"], [8])
    assert grid + "tx=9/ty=11/data_9.parquet" not in fake.objects
    assert grid + "tx=10/ty=11/data_0.parquet" in fake.objects
    marker = json.loads(fake.objects["overture/snapshots/R/tiles/segments/blocks/8.json"])
    assert marker["rows"] == 42 and marker["cells"] == 2
