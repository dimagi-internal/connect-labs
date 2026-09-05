"""Tests for the raster path: reading a GeoTIFF, and reading a polygon out of it.

The MAP loader is the only source that computes its own aggregate rather than
receiving one, so the arithmetic is ours to get wrong. These tests build rasters
whose right answer is known by construction, which is the only way to tell a
plausible number from a correct one.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np
import pytest
import shapely

from connect_labs.labs.indicators.sources import geotiff, malaria_atlas


def _tiff(
    values: np.ndarray,
    *,
    origin=(0.0, 10.0),
    pixel=(1.0, -1.0),
    nodata=-9999.0,
    tiled=False,
    big=False,
    deflate=False,
    predictor=1,
) -> bytes:
    """A minimal big-endian float32 GeoTIFF, written to be read back.

    Round-tripping through a file we produced would only prove the reader agrees
    with itself, so this writer is deliberately independent of it: it lays out
    the bytes from the TIFF spec rather than from ``geotiff.py``.
    """
    height, width = values.shape
    raw = values.astype(">f4")
    if predictor == 2:
        # Horizontal differencing, as libtiff does it for 32-bit samples: on the
        # raw words, with wraparound, not in float arithmetic.
        words = raw.view(">u4").copy()
        words[:, 1:] = (words[:, 1:] - words[:, :-1].astype(">u4")).astype(">u4")
        body = words.tobytes()
    else:
        body = raw.tobytes()
    if deflate:
        body = zlib.compress(body)
    # A BigTIFF states offsets as LONG8, which is the whole point of it; writing
    # them as 32-bit LONG inside a 64-bit slot leaves the reader the high half.
    off_typ = 16 if big else 4
    if tiled:
        tags = [(322, 3, 1, width), (323, 3, 1, height), (324, off_typ, 1, None), (325, off_typ, 1, len(body))]
    else:
        tags = [(273, off_typ, 1, None), (278, 3, 1, height), (279, off_typ, 1, len(body))]

    nodata_bytes = f"{nodata}\0".encode()
    transform = struct.pack(">16d", pixel[0], 0, 0, origin[0], 0, pixel[1], 0, origin[1], 0, 0, 0, 0, 0, 0, 0, 1)

    entries = [
        (256, 3, 1, width),
        (257, 3, 1, height),
        (258, 3, 1, 32),
        (259, 3, 1, 8 if deflate else 1),
        (317, 3, 1, predictor),
        (277, 3, 1, 1),
        (284, 3, 1, 1),
        (339, 3, 1, 3),
        *tags,
        (34264, 12, 16, transform),
        (42113, 2, len(nodata_bytes), nodata_bytes),
    ]
    entries.sort(key=lambda e: e[0])

    # Classic TIFF and BigTIFF differ in three independently-sized fields, which
    # is precisely the thing worth writing out longhand in a test writer.
    if big:
        header = b"MM" + struct.pack(">HHHQ", 43, 8, 0, 16)
        entry_size, inline = 20, 8
        header_len = len(header) + 8 + len(entries) * entry_size + 8
    else:
        header = b"MM" + struct.pack(">HI", 42, 8)
        entry_size, inline = 12, 4
        header_len = len(header) + 2 + len(entries) * entry_size + 4

    heap = b""
    offsets = {}
    for tag, typ, count, value in entries:
        if isinstance(value, bytes):
            offsets[tag] = header_len + len(heap)
            heap += value
    data_offset = header_len + len(heap)

    def as_offset(v: int) -> bytes:
        return struct.pack(">Q", v) if big else struct.pack(">I", v)

    ifd = struct.pack(">Q", len(entries)) if big else struct.pack(">H", len(entries))
    for tag, typ, count, value in entries:
        if isinstance(value, bytes):
            payload = as_offset(offsets[tag])
        elif value is None:  # strip/tile offset — points at the pixel data
            payload = as_offset(data_offset)
        elif typ == 3:
            payload = struct.pack(">H", value) + b"\0" * (inline - 2)
        else:
            payload = as_offset(value)
        count_field = struct.pack(">Q", count) if big else struct.pack(">I", count)
        ifd += struct.pack(">HH", tag, typ) + count_field + payload
    ifd += as_offset(0)

    return header + ifd + heap + body


@pytest.mark.parametrize("tiled", [False, True])
def test_reads_back_what_was_written(tiled):
    values = np.arange(12, dtype="f4").reshape(3, 4)
    r = geotiff.read(_tiff(values, tiled=tiled))
    assert r.values.shape == (3, 4)
    np.testing.assert_allclose(r.masked(), values)
    assert (r.origin_x, r.origin_y) == (0.0, 10.0)
    assert (r.pixel_w, r.pixel_h) == (1.0, -1.0)


def test_nodata_becomes_nan_not_a_very_negative_number():
    """The failure this guards against is silent: -9999 summed as data."""
    values = np.array([[1.0, -9999.0], [3.0, 4.0]], dtype="f4")
    r = geotiff.read(_tiff(values))
    masked = r.masked()
    assert np.isnan(masked[0, 1])
    assert np.nansum(masked) == pytest.approx(8.0)


def test_cell_centres_sit_half_a_cell_in_from_the_corner():
    r = geotiff.read(_tiff(np.zeros((2, 2), dtype="f4")))
    xs, ys = r.cell_centres()
    np.testing.assert_allclose(xs, [0.5, 1.5])
    np.testing.assert_allclose(ys, [9.5, 8.5])


def test_rejects_a_compression_it_cannot_do_rather_than_guessing():
    raw = bytearray(_tiff(np.zeros((2, 2), dtype="f4")))
    i = raw.find(struct.pack(">HHI", 259, 3, 1))
    raw[i + 8 : i + 10] = struct.pack(">H", 7)  # JPEG
    with pytest.raises(geotiff.UnsupportedGeoTIFF, match="compression 7"):
        geotiff.read(bytes(raw))


def test_reads_a_deflated_raster():
    values = np.arange(12, dtype="f4").reshape(3, 4)
    np.testing.assert_allclose(geotiff.read(_tiff(values, deflate=True)).masked(), values)


def test_undoes_horizontal_differencing():
    """Predictor 2 stores deltas; read as-is the numbers are wrong but plausible."""
    values = np.array([[1.5, 2.5, 3.5], [10.0, 20.0, 30.0]], dtype="f4")
    np.testing.assert_allclose(geotiff.read(_tiff(values, predictor=2)).masked(), values)


def test_reads_a_deflated_and_predicted_raster():
    """How a real published raster arrives — both at once."""
    values = np.arange(20, dtype="f4").reshape(4, 5) * 1.25
    got = geotiff.read(_tiff(values, deflate=True, predictor=2)).masked()
    np.testing.assert_allclose(got, values)


@pytest.mark.parametrize("tiled", [False, True])
def test_reads_bigtiff(tiled):
    """Its counts and offsets are wider, and they are not all the same width.

    Reusing the directory's entry-count width for an entry's value-count reads
    the high half of a big-endian field, which is zero — so every tag comes back
    empty and the file looks corrupt rather than misparsed. That shipped once.
    """
    values = np.arange(12, dtype="f4").reshape(3, 4)
    r = geotiff.read(_tiff(values, big=True, tiled=tiled))
    np.testing.assert_allclose(r.masked(), values)
    assert (r.origin_x, r.origin_y) == (0.0, 10.0)


def test_rejects_an_unknown_magic():
    raw = bytearray(_tiff(np.zeros((2, 2), dtype="f4")))
    raw[2:4] = struct.pack(">H", 99)
    with pytest.raises(geotiff.UnsupportedGeoTIFF, match="unknown TIFF magic 99"):
        geotiff.read(bytes(raw))


def test_an_empty_tag_does_not_crash_the_reader():
    """GeoServer emits ExtraSamples with count 0; indexing it blindly raises."""
    raw = bytearray(_tiff(np.zeros((2, 2), dtype="f4")))
    i = raw.find(struct.pack(">HHI", 277, 3, 1))
    raw[i + 4 : i + 8] = struct.pack(">I", 0)  # count -> 0
    geotiff.read(bytes(raw))  # falls back to the default of one sample


# --------------------------------------------------------------------------
# Zonal statistics
# --------------------------------------------------------------------------


def _grid(values, origin=(0.0, 10.0)):
    return geotiff.read(_tiff(np.asarray(values, dtype="f4"), origin=origin))


def test_count_sums_only_the_cells_inside_the_polygon():
    r = _grid([[1, 2, 4, 8], [16, 32, 64, 128], [256, 512, 1024, 2048]])
    # Cell centres are at x=0.5,1.5,2.5,3.5 and y=9.5,8.5,7.5. This box takes
    # the first two columns of the first two rows: 1 + 2 + 16 + 32.
    box = shapely.box(0, 8, 2, 10)
    assert malaria_atlas.zonal(r, box, is_count=True) == pytest.approx(51.0)


def test_rate_without_weights_is_a_plain_mean():
    r = _grid([[10, 20, 0, 0], [30, 40, 0, 0], [0, 0, 0, 0]])
    box = shapely.box(0, 8, 2, 10)
    assert malaria_atlas.zonal(r, box, is_count=False) == pytest.approx(25.0)


def test_rate_is_pulled_toward_where_the_people_are():
    """The whole reason the weight grid exists.

    Two cells, one holding nine tenths of the people. An area mean calls the
    unit 55% because it lets the near-empty cell vote as loudly as the full
    one; the weighted mean gives 19%, near where the people actually are.
    """
    r = _grid([[10.0, 100.0], [0.0, 0.0]])
    weights = _grid([[9000.0, 1000.0], [0.0, 0.0]])
    box = shapely.box(0, 9, 2, 10)
    assert malaria_atlas.zonal(r, box, is_count=False) == pytest.approx(55.0)
    assert malaria_atlas.zonal(r, box, is_count=False, weights=weights) == pytest.approx(19.0)


def test_a_unit_smaller_than_a_cell_still_gets_an_answer():
    """Hundreds of Africa's ADM2 units are smaller than 5 km across."""
    r = _grid([[7.0, 8.0], [9.0, 10.0]])
    speck = shapely.box(0.40, 9.40, 0.44, 9.44)  # holds no cell centre
    assert malaria_atlas.zonal(r, speck, is_count=False) == pytest.approx(7.0)


def test_a_sub_cell_count_takes_only_its_share_of_the_cell():
    """Otherwise a district smaller than a cell inherits its neighbours' cases."""
    r = _grid([[1000.0, 0.0], [0.0, 0.0]])
    speck = shapely.box(0.4, 9.4, 0.5, 9.5)  # a hundredth of a 1x1 cell
    assert malaria_atlas.zonal(r, speck, is_count=True) == pytest.approx(10.0)


def test_a_polygon_off_the_raster_returns_nothing_rather_than_zero():
    r = _grid([[1.0, 2.0], [3.0, 4.0]])
    assert malaria_atlas.zonal(r, shapely.box(50, 50, 51, 51), is_count=True) is None


def test_population_grid_inverts_maps_own_pair():
    """cases = rate x people, so the quotient is the people."""
    count = _grid([[300.0, 50.0], [0.0, 0.0]])
    rate = _grid([[0.3, 0.1], [0.0, 0.0]])
    people = malaria_atlas.population_grid(count, rate)
    np.testing.assert_allclose(people.masked()[0], [1000.0, 500.0])
    # No malaria means no recoverable population — and no vote in a malaria rate.
    assert np.isnan(people.masked()[1]).all()


def test_layers_all_name_the_configured_release():
    for layer in malaria_atlas.LAYERS:
        assert malaria_atlas.RELEASE in layer.coverage_id
        assert layer.coverage_id.startswith(f"{layer.workspace}__")


def test_every_layer_lands_in_a_registered_measure():
    from connect_labs.labs.indicators import measures

    for layer in malaria_atlas.LAYERS:
        m = measures.get(layer.indicator)
        # A layer flagged as a count must not be aggregating like a rate, or
        # the sum up the hierarchy would silently become an average.
        assert m.is_rate is not layer.is_count, f"{layer.indicator} disagrees with its measure"


# --------------------------------------------------------------------------
# Accessibility — the pair of grids, and why they have to be co-registered
# --------------------------------------------------------------------------


def test_travel_surface_is_sampled_by_coordinate_not_by_index():
    """The two grids are published on different extents.

    Assuming cell (i, j) of one is cell (i, j) of the other shifts a whole
    country by some fraction of a degree — wrong everywhere, obviously wrong
    nowhere.
    """
    from connect_labs.labs.indicators.sources import accessibility

    # Population grid: 2x2 cells starting at (0, 10), centres at x=0.5,1.5.
    population = _grid([[1.0, 1.0], [1.0, 1.0]])
    # Travel grid: same cell size, origin shifted one whole cell east.
    travel = _grid([[100.0, 200.0], [300.0, 400.0]], origin=(1.0, 10.0))
    got = accessibility.sample_onto(population, travel)
    # Population x=0.5 falls left of the travel grid; x=1.5 is its first column.
    assert np.isnan(got[0, 0])
    assert got[0, 1] == pytest.approx(100.0)


def test_access_stats_weight_by_people_not_by_area():
    from connect_labs.labs.indicators.sources import accessibility

    # One crowded cell close to care, one empty cell far from it.
    people = np.array([[9000.0, 10.0]])
    minutes = np.array([[20.0, 600.0]])
    stats = accessibility._stats(people, minutes, np.ones((1, 2), dtype=bool))
    # An area mean would say 310 minutes. Almost nobody experiences that.
    assert stats["travel_time_healthcare"] == pytest.approx(20.64, abs=0.01)
    assert stats["pop_beyond_2h"] == pytest.approx(10.0)
    assert stats["share_beyond_2h"] == pytest.approx(100 * 10 / 9010, abs=0.001)


def test_a_boundary_with_no_people_yields_nothing_rather_than_zero():
    """Zero would read as 'nobody is far from care', which is the opposite."""
    from connect_labs.labs.indicators.sources import accessibility

    assert accessibility._stats(np.array([[0.0]]), np.array([[500.0]]), np.ones((1, 1), dtype=bool)) is None
    assert accessibility._stats(np.array([[np.nan]]), np.array([[500.0]]), np.ones((1, 1), dtype=bool)) is None


def test_remote_is_strictly_beyond_the_threshold():
    from connect_labs.labs.indicators.sources import accessibility

    people = np.array([[100.0, 100.0]])
    exactly = np.array([[accessibility.REMOTE_MINUTES, accessibility.REMOTE_MINUTES + 1]])
    stats = accessibility._stats(people, exactly, np.ones((1, 2), dtype=bool))
    assert stats["pop_beyond_2h"] == pytest.approx(100.0)


def test_worldpop_zonal_sum_matches_the_cells_it_covers():
    from connect_labs.labs.indicators.sources import worldpop_raster

    r = _grid([[100.0, 200.0, 400.0], [800.0, 1600.0, 3200.0]])
    # Centres at x=0.5,1.5,2.5 and y=9.5,8.5; this box takes the first two of row one.
    assert worldpop_raster.zonal_sum(r, shapely.box(0, 9, 2, 10)) == pytest.approx(300.0)


def test_a_sub_cell_boundary_takes_only_its_share_of_the_population():
    from connect_labs.labs.indicators.sources import worldpop_raster

    r = _grid([[1000.0, 0.0], [0.0, 0.0]])
    speck = shapely.box(0.4, 9.4, 0.5, 9.5)  # a hundredth of a 1x1 cell
    assert worldpop_raster.zonal_sum(r, speck) == pytest.approx(10.0)


# --------------------------------------------------------------------------
# Windowed reading — what makes a global grid usable at all
# --------------------------------------------------------------------------


@pytest.mark.parametrize("tiled", [False, True])
def test_a_window_returns_the_right_cells_and_says_where_they_are(tiled):
    """The window's origin must move with it, or everything downstream shifts."""
    values = np.arange(16, dtype="f4").reshape(4, 4)
    raw = _tiff(values, tiled=tiled)
    # Cells are 1 degree; centres run x = 0.5..3.5 and y = 9.5..6.5.
    r = geotiff.read(raw, bbox=(1.0, 7.0, 3.0, 9.0))
    np.testing.assert_allclose(r.masked(), values[1:3, 1:3])
    assert (r.origin_x, r.origin_y) == (1.0, 9.0)
    xs, ys = r.cell_centres()
    np.testing.assert_allclose(xs, [1.5, 2.5])
    np.testing.assert_allclose(ys, [8.5, 7.5])


def test_a_window_covering_everything_is_the_whole_raster():
    values = np.arange(16, dtype="f4").reshape(4, 4)
    raw = _tiff(values)
    np.testing.assert_allclose(geotiff.read(raw, bbox=(-99, -99, 99, 99)).masked(), values)


def test_a_window_off_the_raster_says_so():
    raw = _tiff(np.zeros((4, 4), dtype="f4"))
    with pytest.raises(geotiff.UnsupportedGeoTIFF, match="does not overlap"):
        geotiff.read(raw, bbox=(50, 50, 51, 51))


def test_planar_is_only_a_problem_when_there_is_more_than_one_band():
    """GHSL's grid declares planar with a single band, where it means nothing."""
    raw = bytearray(_tiff(np.arange(4, dtype="f4").reshape(2, 2)))
    i = raw.find(struct.pack(">HHI", 284, 3, 1))
    raw[i + 8 : i + 10] = struct.pack(">H", 2)
    np.testing.assert_allclose(geotiff.read(bytes(raw)).masked(), np.arange(4).reshape(2, 2))


# --------------------------------------------------------------------------
# DEGURBA
# --------------------------------------------------------------------------


def test_rural_is_the_three_degurba_rural_classes_and_nothing_else():
    from connect_labs.labs.indicators.sources import settlement

    classes = np.array([[10, 11, 12], [13, 21, 30]])
    np.testing.assert_array_equal(
        settlement.classify(classes),
        np.array([[False, True, True], [True, False, False]]),
    )
    # Water is not rural. It is the class most likely to be swept in by a
    # "not urban" test, and it would inflate every coastal district.
    assert not settlement.classify(np.array([10])).any()


# --------------------------------------------------------------------------
# Age-sex bands
#
# A measure here is a sum over as many as seven rasters, which is a step
# ``pop_total`` never had. These pin the two ways it can go wrong without
# saying so: bands that never get added, and a band that goes missing while
# the total still looks like a total.
# --------------------------------------------------------------------------


def _bands(monkeypatch, grids):
    """Stand in for the network: hand ``grid_for`` these rasters, in order."""
    from connect_labs.labs.indicators.sources import worldpop_agesex

    served = iter(grids)

    def fake_fetch(iso, group, band, year=worldpop_agesex.YEAR):
        nxt = next(served)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(worldpop_agesex, "fetch", fake_fetch)


def test_bands_are_summed_per_cell_before_any_boundary_is_read(monkeypatch):
    """One grid comes back, not a band each, and it is the cell-wise sum.

    This is the whole shape of the loader: ``pop_f_15_49`` is seven rasters,
    and every boundary must be read from the one grid they add up to rather
    than seven times over.
    """
    from connect_labs.labs.indicators.sources import worldpop_agesex

    _bands(monkeypatch, [_grid([[1.0, 2.0], [4.0, 8.0]]), _grid([[10.0, 20.0], [40.0, 80.0]])])
    grid = worldpop_agesex.grid_for("NGA", "pop_u1")

    np.testing.assert_allclose(grid.masked(), [[11.0, 22.0], [44.0, 88.0]])
    # And it is still placed where the bands were, or every zonal sum after it
    # reads the wrong land.
    assert (grid.origin_x, grid.origin_y, grid.pixel_w, grid.pixel_h) == (0.0, 10.0, 1.0, -1.0)


def test_the_zonal_sum_reads_the_summed_grid(monkeypatch):
    """The bands' people, inside the polygon, counted once."""
    from connect_labs.labs.indicators.sources import worldpop_agesex, worldpop_raster

    _bands(monkeypatch, [_grid([[100.0, 200.0], [0.0, 0.0]]), _grid([[3.0, 5.0], [0.0, 0.0]])])
    grid = worldpop_agesex.grid_for("NGA", "pop_u5")
    # Centres at x=0.5,1.5 and y=9.5; the box takes the whole top row.
    assert worldpop_raster.zonal_sum(grid, shapely.box(0, 9, 2, 10)) == pytest.approx(308.0)


def test_a_cell_no_band_answers_stays_nodata():
    """Otherwise the country's bounding box fills out to sea with zeroes.

    ``zonal_sum`` falls back to the containing cell for a unit smaller than one,
    and a nodata cell is its signal to return nothing at all. Turning "nobody
    estimated here" into a zero turns that into a confident empty district.
    """
    from connect_labs.labs.indicators.sources import worldpop_agesex

    a = _grid([[5.0, np.nan], [np.nan, np.nan]])
    b = _grid([[6.0, np.nan], [np.nan, np.nan]])
    summed = worldpop_agesex.sum_bands([a, b]).masked()
    assert summed[0, 0] == pytest.approx(11.0)
    assert np.isnan(summed[0, 1])
    assert np.isnan(summed[1]).all()


def test_a_cell_only_some_bands_answer_keeps_the_ones_that_did():
    """A nodata band is "no estimate for this age group here", not "no people".

    Dropping the cell because one of eleven bands is blank would lose the ten
    that were not.
    """
    from connect_labs.labs.indicators.sources import worldpop_agesex

    a = _grid([[np.nan, 2.0], [0.0, 0.0]])
    b = _grid([[7.0, 3.0], [0.0, 0.0]])
    np.testing.assert_allclose(worldpop_agesex.sum_bands([a, b]).masked()[0], [7.0, 5.0])


def test_a_missing_band_raises_rather_than_returning_a_smaller_total(monkeypatch):
    """The failure that would otherwise be invisible: ten bands still add up.

    A ``pop_f_15_49`` built from six of its seven bands is a plausible number
    that is short by a whole age group, and nothing downstream could tell —
    it is a count, so there is no rate to look wrong. So a band that 404s must
    take its measure down with it.
    """
    from connect_labs.labs.indicators.sources import worldpop_agesex

    _bands(monkeypatch, [_grid([[10.0, 0.0], [0.0, 0.0]]), RuntimeError("404 Not Found")])
    with pytest.raises(RuntimeError, match="404"):
        worldpop_agesex.grid_for("NGA", "pop_u1")


def test_a_missing_band_costs_its_own_measure_and_no_other(db, monkeypatch):
    """...and the other two measures still load.

    The other half of the same decision. WorldPop's coverage of this product is
    not quite uniform, and a country missing one age group is not a reason to
    leave its other denominators unanswered.
    """
    from connect_labs.labs.indicators.sources import worldpop_agesex

    served = {}

    def fake_grid_for(iso, indicator, year=worldpop_agesex.YEAR):
        if indicator == "pop_u5":
            raise RuntimeError("404 Not Found")
        served[indicator] = True
        return _grid([[1.0, 1.0], [1.0, 1.0]])

    monkeypatch.setattr(worldpop_agesex, "grid_for", fake_grid_for)
    # No boundaries in the test database, so nothing is written — what is being
    # pinned is that the loop does not abandon the run on the first refusal.
    assert worldpop_agesex.load_country("NGA") == []


def test_bands_on_different_grids_refuse_to_be_added():
    """Adding two extents position by position is a number about no land."""
    from connect_labs.labs.indicators.sources import worldpop_agesex

    here = _grid([[1.0, 1.0], [1.0, 1.0]])
    shifted = _grid([[1.0, 1.0], [1.0, 1.0]], origin=(50.0, 60.0))
    with pytest.raises(RuntimeError, match="different grids"):
        worldpop_agesex.sum_bands([here, shifted])


def test_summing_one_band_is_that_band():
    from connect_labs.labs.indicators.sources import worldpop_agesex

    one = _grid([[1.0, 2.0], [3.0, 4.0]])
    np.testing.assert_allclose(worldpop_agesex.sum_bands([one]).masked(), [[1.0, 2.0], [3.0, 4.0]])


def test_the_measures_name_the_bands_that_define_them():
    """A wrong band list is a wrong denominator, and nothing else would catch it."""
    from connect_labs.labs.indicators import measures
    from connect_labs.labs.indicators.sources import worldpop_agesex

    assert worldpop_agesex.BANDS["pop_u1"] == (("single_age", "f_0"), ("single_age", "m_0"))
    assert worldpop_agesex.BANDS["pop_u5"] == (
        ("five_year_age_groups", "f_0_4"),
        ("five_year_age_groups", "m_0_4"),
    )
    # 15-49 is seven five-year bands, female only, contiguous and no wider.
    childbearing = [band for _, band in worldpop_agesex.BANDS["pop_f_15_49"]]
    assert childbearing == ["f_15_19", "f_20_24", "f_25_29", "f_30_34", "f_35_39", "f_40_44", "f_45_49"]

    for code in worldpop_agesex.BANDS:
        measures.get(code)  # raises if a measure this loader writes went away


def test_the_band_urls_are_the_files_worldpop_publishes():
    """Pinned longhand: a wrong path is a 404, and a 404 is a silent gap."""
    from connect_labs.labs.indicators.sources import worldpop_agesex

    root = "https://data.worldpop.org/GIS/AgeSex_structures/Global_2021_2022_1km_UNadj/unconstrained/2022"
    assert worldpop_agesex.url_for("NGA", "single_age", "f_0") == f"{root}/single_age/NGA/nga_f_0_2022_1km_UNadj.tif"
    assert (
        worldpop_agesex.url_for("nga", "five_year_age_groups", "f_45_49")
        == f"{root}/five_year_age_groups/NGA/nga_f_45_49_2022_1km_UNadj.tif"
    )


class TestBothAccessSurfaces:
    """Walking and motorized answer different questions.

    (The module is imported per-test here, matching the file's existing
    convention of importing accessibility inside the test body.)

    Walking is community reach — can a household get itself to a clinic.
    Motorized is referral — can a woman in obstructed labour reach a facility
    that can operate. A place can be fine on one and hopeless on the other,
    which is why one cannot stand in for the other.
    """

    def test_the_two_surfaces_write_different_measures(self):
        from connect_labs.labs.indicators.sources import accessibility

        walking = accessibility.SURFACES["walking"]["measures"]
        motorized = accessibility.SURFACES["motorized"]["measures"]
        assert set(walking.values()).isdisjoint(motorized.values())

    def test_the_coverage_ids_differ_in_case_and_are_not_copies(self):
        from connect_labs.labs.indicators.sources import accessibility

        """MAP writes '..._Travel_Time_To_Healthcare' for walking and
        '..._Travel_Time_to_Healthcare' for motorized. A constant copied with
        the wrong case returns an XML error page rather than a raster."""
        walking = accessibility.SURFACES["walking"]["coverage"]
        motorized = accessibility.SURFACES["motorized"]["coverage"]
        assert walking != motorized
        assert "Walking_Only" in walking
        assert "Motorized" in motorized
        assert walking.endswith("_To_Healthcare")
        assert motorized.endswith("_to_Healthcare")

    def test_stats_labels_follow_the_surface_asked_for(self):
        from connect_labs.labs.indicators.sources import accessibility

        people = np.array([[100.0, 100.0]])
        minutes = np.array([[10.0, 500.0]])
        mask = np.ones((1, 2), dtype=bool)

        motorized = accessibility._stats(people, minutes, mask, accessibility.SURFACES["motorized"]["measures"])

        assert set(motorized) == {
            "travel_time_motorized",
            "pop_beyond_2h_motorized",
            "share_beyond_2h_motorized",
        }

    def test_it_still_defaults_to_walking(self):
        """The signature stayed backwards compatible: this function existed
        before there was a second surface, and its callers assumed walking."""
        from connect_labs.labs.indicators.sources import accessibility

        people = np.array([[100.0]])
        minutes = np.array([[10.0]])
        stats = accessibility._stats(people, minutes, np.ones((1, 1), dtype=bool))
        assert "travel_time_healthcare" in stats
