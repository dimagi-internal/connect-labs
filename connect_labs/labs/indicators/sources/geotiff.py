"""A small GeoTIFF reader, sufficient for what OGC servers return.

Deliberately not rasterio. The one thing we need — read an uncompressed
float32 band and know where its cells sit on the globe — is about a hundred
lines, whereas rasterio arrives with a bundled GDAL that would have to be
built into the production image and kept in step with the GEOS/GDAL pair
GeoDjango already links against. That trade is only worth making for code
that needs GDAL's breadth. This does not: the writer is a GeoServer we ask
for ``image/geotiff``, so the layout is uncompressed strips or tiles, one
sample format, no overviews, no colour management.

It raises on anything outside that envelope rather than guessing, so the day
a server starts sending something else we get an error naming the tag instead
of a silently wrong number.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

import numpy as np

# TIFF field types → (struct code, byte width). Only the ones a GeoTIFF uses.
_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 11: ("f", 4), 12: ("d", 8)}

_WIDTH = 256
_LENGTH = 257
_BITS_PER_SAMPLE = 258
_COMPRESSION = 259
_STRIP_OFFSETS = 273
_SAMPLES_PER_PIXEL = 277
_ROWS_PER_STRIP = 278
_STRIP_BYTE_COUNTS = 279
_PLANAR_CONFIG = 284
_TILE_WIDTH = 322
_TILE_LENGTH = 323
_TILE_OFFSETS = 324
_TILE_BYTE_COUNTS = 325
_SAMPLE_FORMAT = 339
_PIXEL_SCALE = 33550
_TIE_POINT = 33922
_TRANSFORM = 34264
_GDAL_NODATA = 42113

#: SampleFormat 3 is IEEE float; 1 and 2 are unsigned and signed integers.
_DTYPE = {(3, 32): "f4", (3, 64): "f8", (1, 8): "u1", (1, 16): "u2", (1, 32): "u4", (2, 16): "i2", (2, 32): "i4"}


class UnsupportedGeoTIFF(ValueError):
    """The file is a TIFF, but not one this reader will guess at."""


@dataclass(frozen=True)
class Raster:
    """One band, plus the affine that places its cells in degrees.

    ``origin_x``/``origin_y`` are the outer corner of cell (0, 0) — the GeoTIFF
    convention — and the pixel sizes carry their own sign, so ``pixel_h`` is
    normally negative for a north-up image.
    """

    values: np.ndarray
    origin_x: float
    origin_y: float
    pixel_w: float
    pixel_h: float
    nodata: float | None

    @property
    def height(self) -> int:
        return self.values.shape[0]

    @property
    def width(self) -> int:
        return self.values.shape[1]

    def cell_centres(self) -> tuple[np.ndarray, np.ndarray]:
        """Longitude and latitude of every cell centre, as 1-D axes.

        Centres rather than corners because the test that follows is "does this
        cell belong to this polygon", and a corner belongs to four cells.
        """
        xs = self.origin_x + (np.arange(self.width) + 0.5) * self.pixel_w
        ys = self.origin_y + (np.arange(self.height) + 0.5) * self.pixel_h
        return xs, ys

    def masked(self) -> np.ndarray:
        """The band as float64 with nodata replaced by NaN."""
        out = self.values.astype("f8")
        if self.nodata is not None:
            out[self.values == self.nodata] = np.nan
        # Some servers emit their nodata as a very negative sentinel *and* leave
        # NaNs in the same band. Both mean "no estimate here".
        return out


def _read_ifd(buf: bytes) -> tuple[str, dict]:
    if buf[:2] == b"MM":
        bo = ">"
    elif buf[:2] == b"II":
        bo = "<"
    else:
        raise UnsupportedGeoTIFF("not a TIFF: bad byte-order mark")
    magic, ifd = struct.unpack(bo + "HI", buf[2:8])
    if magic != 42:
        raise UnsupportedGeoTIFF(f"BigTIFF or unknown magic {magic}; this reader handles classic TIFF only")

    tags: dict[int, list] = {}
    count = struct.unpack(bo + "H", buf[ifd : ifd + 2])[0]
    for i in range(count):
        entry = ifd + 2 + i * 12
        tag, typ, n = struct.unpack(bo + "HHI", buf[entry : entry + 8])
        code, size = _TYPES.get(typ, ("B", 1))
        total = size * n
        raw = (
            buf[entry + 8 : entry + 12]
            if total <= 4
            else buf[struct.unpack(bo + "I", buf[entry + 8 : entry + 12])[0] :][:total]
        )
        if typ == 2:
            tags[tag] = [raw.split(b"\0")[0].decode("latin-1")]
        elif typ == 5:
            tags[tag] = [struct.unpack(bo + "II", raw[j * 8 : j * 8 + 8]) for j in range(n)]
        else:
            tags[tag] = list(struct.unpack(bo + code * n, raw[:total]))
    return bo, tags


def _geotransform(tags: dict) -> tuple[float, float, float, float]:
    """Origin and pixel size, from either of the two ways GeoTIFF says it."""
    if _TRANSFORM in tags:
        m = tags[_TRANSFORM]
        if m[1] or m[4]:
            raise UnsupportedGeoTIFF("rotated raster; only north-up grids are supported")
        return m[3], m[7], m[0], m[5]
    if _PIXEL_SCALE in tags and _TIE_POINT in tags:
        sx, sy = tags[_PIXEL_SCALE][0], tags[_PIXEL_SCALE][1]
        tie = tags[_TIE_POINT]
        return tie[3], tie[4], sx, -sy
    raise UnsupportedGeoTIFF("no ModelTransformation and no PixelScale/TiePoint pair")


def read(buf: bytes, band: int = 0) -> Raster:
    """Read one band of an uncompressed strip- or tile-organised GeoTIFF."""
    bo, tags = _read_ifd(buf)

    def one(tag: int, default=None):
        return tags[tag][0] if tag in tags else default

    if one(_COMPRESSION, 1) != 1:
        raise UnsupportedGeoTIFF(f"compression {one(_COMPRESSION)} not supported; request an uncompressed GeoTIFF")
    if one(_PLANAR_CONFIG, 1) != 1:
        raise UnsupportedGeoTIFF("planar (band-separate) layout not supported")

    width, height = one(_WIDTH), one(_LENGTH)
    samples = one(_SAMPLES_PER_PIXEL, 1)
    bits = one(_BITS_PER_SAMPLE, 8)
    fmt = one(_SAMPLE_FORMAT, 1)
    dtype = _DTYPE.get((fmt, bits))
    if dtype is None:
        raise UnsupportedGeoTIFF(f"sample format {fmt} at {bits} bits is not handled")
    if not 0 <= band < samples:
        raise UnsupportedGeoTIFF(f"band {band} out of range; the file has {samples}")
    np_dtype = np.dtype(bo + dtype)

    out = np.zeros((height, width), dtype=np_dtype)
    if _TILE_OFFSETS in tags:
        tw, th = one(_TILE_WIDTH), one(_TILE_LENGTH)
        across = (width + tw - 1) // tw
        for i, (offset, length) in enumerate(zip(tags[_TILE_OFFSETS], tags[_TILE_BYTE_COUNTS])):
            tile = np.frombuffer(buf[offset : offset + length], dtype=np_dtype).reshape(th, tw, samples)[..., band]
            row, col = divmod(i, across)
            y0, x0 = row * th, col * tw
            chunk = tile[: min(th, height - y0), : min(tw, width - x0)]
            out[y0 : y0 + chunk.shape[0], x0 : x0 + chunk.shape[1]] = chunk
    elif _STRIP_OFFSETS in tags:
        rows = one(_ROWS_PER_STRIP, height)
        for i, (offset, length) in enumerate(zip(tags[_STRIP_OFFSETS], tags[_STRIP_BYTE_COUNTS])):
            y0 = i * rows
            n = min(rows, height - y0)
            strip = np.frombuffer(buf[offset : offset + length], dtype=np_dtype).reshape(-1, width, samples)[..., band]
            out[y0 : y0 + n] = strip[:n]
    else:
        raise UnsupportedGeoTIFF("neither TileOffsets nor StripOffsets present")

    nodata = None
    if _GDAL_NODATA in tags:
        try:
            nodata = float(tags[_GDAL_NODATA][0])
        except ValueError:
            nodata = None

    ox, oy, pw, ph = _geotransform(tags)
    return Raster(values=out, origin_x=ox, origin_y=oy, pixel_w=pw, pixel_h=ph, nodata=nodata)
