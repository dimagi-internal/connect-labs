"""A small GeoTIFF reader, sufficient for the rasters this app reads.

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
import zlib
from dataclasses import dataclass

import numpy as np

# TIFF field types → (struct code, byte width). Only the ones a GeoTIFF uses.
# 16 is BigTIFF's 64-bit LONG8 — how a large file states a strip offset. Falling
# back to a byte for an unknown type would read three bytes of an eight-byte
# offset and report a plausible-looking wrong number, so it is listed here.
_TYPES = {1: ("B", 1), 2: ("s", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8), 11: ("f", 4), 12: ("d", 8), 16: ("Q", 8)}

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
_PREDICTOR = 317
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


# --------------------------------------------------------------------------
# Compression
#
# GeoServer answers with uncompressed rasters, but a file you download rather
# than render is nearly always LZW or Deflate — a 1 km population grid is 6 MB
# compressed and 25 MB raw, and the people publishing it are shipping terabytes.
# Deflate is zlib, so it is free. LZW is forty lines, and writing them is a
# better trade than making the production image carry a bundled GDAL.
# --------------------------------------------------------------------------

_UNCOMPRESSED = 1
_LZW = 5
_DEFLATE = (8, 32946)

_LZW_CLEAR = 256
_LZW_EOI = 257


def _lzw_decode(data: bytes) -> bytes:
    """TIFF's LZW variant: MSB-first codes, and the early-change quirk.

    TIFF widens its code length one code sooner than a plain reading of LZW
    suggests — at 511 rather than 512. The difference is invisible for the first
    few hundred codes and then corrupts everything after, which is exactly the
    kind of bug that looks like bad data rather than a bad decoder.
    """
    out = bytearray()
    table: list[bytes] = []

    def reset() -> None:
        table.clear()
        table.extend(bytes([i]) for i in range(256))
        table.extend((b"", b""))  # placeholders for Clear and EndOfInformation

    reset()
    width = 9
    previous: bytes | None = None
    bit = 0
    end = len(data) * 8

    while bit + width <= end:
        byte, shift = divmod(bit, 8)
        window = int.from_bytes(data[byte : byte + 3].ljust(3, b"\0"), "big")
        code = (window >> (24 - shift - width)) & ((1 << width) - 1)
        bit += width

        if code == _LZW_EOI:
            break
        if code == _LZW_CLEAR:
            reset()
            width = 9
            previous = None
            continue

        if previous is None:
            entry = table[code]
        elif code < len(table):
            entry = table[code]
            table.append(previous + entry[:1])
        else:
            # The encoder used a code it defined on this very symbol.
            entry = previous + previous[:1]
            table.append(entry)

        out += entry
        previous = entry
        if len(table) + 1 >= (1 << width) and width < 12:
            width += 1

    return bytes(out)


def _decompress(chunk: bytes, compression: int) -> bytes:
    if compression == _UNCOMPRESSED:
        return chunk
    if compression == _LZW:
        return _lzw_decode(chunk)
    if compression in _DEFLATE:
        return zlib.decompress(chunk)
    raise UnsupportedGeoTIFF(f"compression {compression} is not handled (uncompressed, LZW and Deflate are)")


def _unpredict(block: np.ndarray, predictor: int, samples: int, byte_order: str) -> np.ndarray:
    """Undo horizontal differencing, which stores each cell as a delta.

    Predictor 2 differences whole values along the row. The spec describes it
    for integers, and libtiff applies it to 32-bit floats by differencing their
    bit patterns as unsigned words — so the accumulation has to happen on the
    raw words, with wraparound, before they are read back as floats. Doing it
    in float arithmetic produces numbers that look almost right.
    """
    if predictor in (1, None):
        return block
    if predictor != 2:
        raise UnsupportedGeoTIFF(f"predictor {predictor} is not handled (1 and 2 are)")
    words = block.view(byte_order + _UNSIGNED[block.dtype.itemsize])
    np.cumsum(words, axis=1, dtype=words.dtype, out=words)
    return block


#: Same width, no sign — cumulative summing a delta-coded row must wrap rather
#: than saturate or promote.
_UNSIGNED = {1: "u1", 2: "u2", 4: "u4", 8: "u8"}


def _read_ifd(buf: bytes) -> tuple[str, dict]:
    """Parse the first image file directory, classic TIFF or BigTIFF.

    The two formats differ only in how wide their counts and offsets are.
    BigTIFF exists because a 32-bit offset cannot address a file over 4 GB, and
    a continental population raster is exactly that. Everything downstream is
    identical, so the widths are read into variables rather than the format
    being handled twice.
    """
    if buf[:2] == b"MM":
        bo = ">"
    elif buf[:2] == b"II":
        bo = "<"
    else:
        raise UnsupportedGeoTIFF("not a TIFF: bad byte-order mark")

    # Three widths differ between the formats and they are NOT the same width.
    # ``entries_fmt`` counts the directory's entries; ``count_fmt`` counts one
    # entry's values; ``off_fmt`` is an offset. Classic TIFF uses 2, 4 and 4
    # bytes; BigTIFF uses 8 for all three. Reusing one for another reads the
    # high half of a big-endian field, which is zero — every tag comes back
    # empty and the file looks corrupt rather than misparsed.
    magic = struct.unpack(bo + "H", buf[2:4])[0]
    if magic == 42:
        ifd = struct.unpack(bo + "I", buf[4:8])[0]
        entries_fmt, count_fmt, entry_size, off_fmt, inline = "H", "I", 12, "I", 4
    elif magic == 43:
        offset_size, pad = struct.unpack(bo + "HH", buf[4:8])
        if offset_size != 8 or pad != 0:
            raise UnsupportedGeoTIFF(f"BigTIFF with {offset_size}-byte offsets is not handled")
        ifd = struct.unpack(bo + "Q", buf[8:16])[0]
        entries_fmt, count_fmt, entry_size, off_fmt, inline = "Q", "Q", 20, "Q", 8
    else:
        raise UnsupportedGeoTIFF(f"unknown TIFF magic {magic}; expected 42 (classic) or 43 (BigTIFF)")

    entries_size = struct.calcsize(bo + entries_fmt)
    count_size = struct.calcsize(bo + count_fmt)
    tags: dict[int, list] = {}
    count = struct.unpack(bo + entries_fmt, buf[ifd : ifd + entries_size])[0]
    for i in range(count):
        entry = ifd + entries_size + i * entry_size
        tag, typ = struct.unpack(bo + "HH", buf[entry : entry + 4])
        n = struct.unpack(bo + count_fmt, buf[entry + 4 : entry + 4 + count_size])[0]
        payload = entry + 4 + count_size
        code, size = _TYPES.get(typ, ("B", 1))
        total = size * n
        if total <= inline:
            raw = buf[payload : payload + inline]
        else:
            at = struct.unpack(bo + off_fmt, buf[payload : payload + inline])[0]
            raw = buf[at : at + total]
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


def read(buf: bytes, band: int = 0, bbox: tuple[float, float, float, float] | None = None) -> Raster:
    """Read one band of a strip- or tile-organised GeoTIFF.

    ``bbox`` is (west, south, east, north) in the raster's own degrees. Pass it
    for a raster too large to hold: only the strips or tiles overlapping that
    box are decompressed, and the returned Raster carries the origin of the
    window rather than of the file, so everything downstream is unchanged. The
    global settlement grid is 43,202 x 21,384 — 1.85 GB as a whole array, and a
    few megabytes for one country.
    """
    bo, tags = _read_ifd(buf)

    def one(tag: int, default=None):
        # A present-but-empty tag is a real thing servers emit (GeoServer writes
        # ExtraSamples with count 0), and indexing it blindly turns a readable
        # raster into an IndexError from deep inside the reader.
        values = tags.get(tag)
        return values[0] if values else default

    compression = one(_COMPRESSION, _UNCOMPRESSED)
    predictor = one(_PREDICTOR, 1)
    width, height = one(_WIDTH), one(_LENGTH)
    samples = one(_SAMPLES_PER_PIXEL, 1)
    # Planar means the bands are stored one after another rather than
    # interleaved, which this reader cannot unpick — but with a single band
    # there is nothing to interleave and the two layouts are the same bytes.
    # GHSL's settlement grid declares planar for exactly that vacuous reason.
    if one(_PLANAR_CONFIG, 1) != 1 and samples > 1:
        raise UnsupportedGeoTIFF("planar (band-separate) layout is not supported for multi-band rasters")
    bits = one(_BITS_PER_SAMPLE, 8)
    fmt = one(_SAMPLE_FORMAT, 1)
    dtype = _DTYPE.get((fmt, bits))
    if dtype is None:
        raise UnsupportedGeoTIFF(f"sample format {fmt} at {bits} bits is not handled")
    if not 0 <= band < samples:
        raise UnsupportedGeoTIFF(f"band {band} out of range; the file has {samples}")
    np_dtype = np.dtype(bo + dtype)

    ox, oy, pw, ph = _geotransform(tags)

    # The pixel window to read. Without a bbox it is the whole image, and every
    # branch below is then exactly what it was before windowing existed.
    x0, y0, x1, y1 = 0, 0, width, height
    if bbox is not None:
        west, south, east, north = bbox
        cx0 = int(np.floor((west - ox) / pw))
        cx1 = int(np.ceil((east - ox) / pw))
        # A north-up raster has a negative height, so north maps to the low row.
        cy0 = int(np.floor((north - oy) / ph))
        cy1 = int(np.ceil((south - oy) / ph))
        x0, x1 = max(0, min(cx0, cx1)), min(width, max(cx0, cx1))
        y0, y1 = max(0, min(cy0, cy1)), min(height, max(cy0, cy1))
        if x0 >= x1 or y0 >= y1:
            raise UnsupportedGeoTIFF(f"bbox {bbox} does not overlap this raster")

    def block(offset: int, length: int, rows: int, cols: int) -> np.ndarray:
        """One strip or tile, decompressed, un-predicted, as (rows, cols, samples)."""
        raw = _decompress(buf[offset : offset + length], compression)
        # A copy, not a view: undoing the predictor writes in place, and a
        # buffer read straight out of the file is read-only.
        arr = np.frombuffer(raw, dtype=np_dtype, count=rows * cols * samples).reshape(rows, cols, samples).copy()
        return _unpredict(arr, predictor, samples, bo)

    out = np.zeros((y1 - y0, x1 - x0), dtype=np_dtype)
    if _TILE_OFFSETS in tags:
        tw, th = one(_TILE_WIDTH), one(_TILE_LENGTH)
        across = (width + tw - 1) // tw
        offsets, lengths = tags[_TILE_OFFSETS], tags[_TILE_BYTE_COUNTS]
        for ty in range(y0 // th, (y1 - 1) // th + 1):
            for tx in range(x0 // tw, (x1 - 1) // tw + 1):
                i = ty * across + tx
                if i >= len(offsets):
                    continue
                tile = block(offsets[i], lengths[i], th, tw)[..., band]
                # Intersect the tile with the window, in image coordinates.
                sy, sx = ty * th, tx * tw
                iy0, iy1 = max(y0, sy), min(y1, sy + th, height)
                ix0, ix1 = max(x0, sx), min(x1, sx + tw, width)
                if iy0 >= iy1 or ix0 >= ix1:
                    continue
                out[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0] = tile[iy0 - sy : iy1 - sy, ix0 - sx : ix1 - sx]
    elif _STRIP_OFFSETS in tags:
        rows_per = one(_ROWS_PER_STRIP, height)
        offsets, lengths = tags[_STRIP_OFFSETS], tags[_STRIP_BYTE_COUNTS]
        for i in range(y0 // rows_per, (y1 - 1) // rows_per + 1):
            if i >= len(offsets):
                continue
            sy = i * rows_per
            # The last strip is short, and asking for a full one would either
            # over-read the buffer or silently reshape someone else's bytes.
            rows = min(rows_per, height - sy)
            strip = block(offsets[i], lengths[i], rows, width)[..., band]
            iy0, iy1 = max(y0, sy), min(y1, sy + rows)
            if iy0 >= iy1:
                continue
            out[iy0 - y0 : iy1 - y0] = strip[iy0 - sy : iy1 - sy, x0:x1]
    else:
        raise UnsupportedGeoTIFF("neither TileOffsets nor StripOffsets present")

    nodata = None
    if _GDAL_NODATA in tags:
        try:
            nodata = float(tags[_GDAL_NODATA][0])
        except ValueError:
            nodata = None

    return Raster(
        values=out,
        origin_x=ox + x0 * pw,
        origin_y=oy + y0 * ph,
        pixel_w=pw,
        pixel_h=ph,
        nodata=nodata,
    )


def sample_onto(target: Raster, source: Raster) -> np.ndarray:
    """Read ``source`` at every cell centre of ``target``.

    Nearest-cell lookup by coordinate. Both grids are 30 arc-second, so this is
    a re-index rather than a resampling — but it is done by coordinate because
    the two are published on different extents and index alignment is a
    coincidence we must not rely on.
    """
    xs, ys = target.cell_centres()
    cols = np.floor((xs - source.origin_x) / source.pixel_w).astype(int)
    rows = np.floor((ys - source.origin_y) / source.pixel_h).astype(int)
    inside_x = (cols >= 0) & (cols < source.width)
    inside_y = (rows >= 0) & (rows < source.height)

    out = np.full((target.height, target.width), np.nan)
    if not inside_x.any() or not inside_y.any():
        return out
    values = source.masked()
    grid = values[np.ix_(np.clip(rows, 0, source.height - 1), np.clip(cols, 0, source.width - 1))]
    out[np.ix_(inside_y, inside_x)] = grid[np.ix_(inside_y, inside_x)]
    return out
