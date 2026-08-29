"""Tests for the raster path: reading a GeoTIFF, and reading a polygon out of it.

The MAP loader is the only source that computes its own aggregate rather than
receiving one, so the arithmetic is ours to get wrong. These tests build rasters
whose right answer is known by construction, which is the only way to tell a
plausible number from a correct one.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest
import shapely

from connect_labs.labs.indicators.sources import geotiff, malaria_atlas


def _tiff(values: np.ndarray, *, origin=(0.0, 10.0), pixel=(1.0, -1.0), nodata=-9999.0, tiled=False) -> bytes:
    """A minimal big-endian float32 GeoTIFF, written to be read back.

    Round-tripping through a file we produced would only prove the reader agrees
    with itself, so this writer is deliberately independent of it: it lays out
    the bytes from the TIFF spec rather than from ``geotiff.py``.
    """
    height, width = values.shape
    body = values.astype(">f4").tobytes()
    if tiled:
        tags = [(322, 3, 1, width), (323, 3, 1, height), (324, 4, 1, None), (325, 4, 1, len(body))]
    else:
        tags = [(273, 4, 1, None), (278, 3, 1, height), (279, 4, 1, len(body))]

    nodata_bytes = f"{nodata}\0".encode()
    transform = struct.pack(">16d", pixel[0], 0, 0, origin[0], 0, pixel[1], 0, origin[1], 0, 0, 0, 0, 0, 0, 0, 1)

    entries = [
        (256, 3, 1, width),
        (257, 3, 1, height),
        (258, 3, 1, 32),
        (259, 3, 1, 1),
        (277, 3, 1, 1),
        (284, 3, 1, 1),
        (339, 3, 1, 3),
        *tags,
        (34264, 12, 16, transform),
        (42113, 2, len(nodata_bytes), nodata_bytes),
    ]
    entries.sort(key=lambda e: e[0])

    header_len = 8 + 2 + len(entries) * 12 + 4
    # Everything that does not fit in four bytes is laid down after the IFD.
    heap = b""
    offsets = {}
    for tag, typ, count, value in entries:
        if isinstance(value, bytes):
            offsets[tag] = header_len + len(heap)
            heap += value
    data_offset = header_len + len(heap)

    ifd = struct.pack(">H", len(entries))
    for tag, typ, count, value in entries:
        if isinstance(value, bytes):
            payload = struct.pack(">I", offsets[tag])
        elif value is None:  # strip/tile offset — points at the pixel data
            payload = struct.pack(">I", data_offset)
        elif typ == 3:
            payload = struct.pack(">HH", value, 0)
        else:
            payload = struct.pack(">I", value)
        ifd += struct.pack(">HHI", tag, typ, count) + payload
    ifd += struct.pack(">I", 0)

    return b"MM" + struct.pack(">HI", 42, 8) + ifd + heap + body


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


def test_rejects_compression_rather_than_guessing():
    raw = bytearray(_tiff(np.zeros((2, 2), dtype="f4")))
    i = raw.find(struct.pack(">HHI", 259, 3, 1))
    raw[i + 8 : i + 10] = struct.pack(">H", 5)  # LZW
    with pytest.raises(geotiff.UnsupportedGeoTIFF, match="compression"):
        geotiff.read(bytes(raw))


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
