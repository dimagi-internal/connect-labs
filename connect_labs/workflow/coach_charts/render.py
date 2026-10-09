"""Drawing a coaching chart: a Vega-Lite spec and its data, in Connect's theme, as a PNG.

``vl-convert-python`` runs Vega-Lite itself (a bundled JavaScript runtime), so the PNG
is what a browser would draw from the same spec -- the same spec a webview can render
live later. Labs sends the PNG to Open Chat Studio and the phone today.

The theme is forced here and nowhere else: whatever ``config`` a spec carries is
dropped and ``theme.THEME`` takes its place. No external loads: ``allowed_base_urls``
is empty, so a spec that names a URL (for data or an image) draws nothing from it.
"""

from __future__ import annotations

import copy
import threading
from pathlib import Path

from connect_labs.workflow.coach_charts import theme

#: The Vega-Lite version every chart is compiled with (bundled by vl-convert 1.9).
VL_VERSION = "6.4"
VL_SCHEMA = "https://vega.github.io/schema/vega-lite/v6.json"

FONTS_DIR = Path(__file__).parent / "fonts"

#: Bounds on what is drawn. The width is fixed by the theme; a chart taller than
#: ``MAX_HEIGHT`` px (at scale) is refused rather than sent as a scroll the phone shrinks.
PNG_WIDTH = theme.WIDTH * theme.SCALE
MAX_HEIGHT = 2400
MAX_PNG_BYTES = 1024 * 1024

#: Top-level keys a spec may not set: the theme owns its look and size.
_THEME_OWNED = ("config", "background", "padding", "autosize", "$schema", "usermeta")

_fonts_lock = threading.Lock()
_fonts_registered = False


class RenderError(Exception):
    """The chart could not be drawn within bounds; ``message`` is for a person."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(message)


def _register_fonts(vlc) -> None:
    global _fonts_registered
    with _fonts_lock:
        if not _fonts_registered:
            vlc.register_font_directory(str(FONTS_DIR))
            _fonts_registered = True


def themed(spec: dict, datasets: dict | None = None) -> dict:
    """The complete spec that is drawn: ``spec`` without anything the theme owns,
    its ``datasets`` replaced by ``datasets``, the width fixed, and ``theme.THEME``
    as its config."""
    out = {k: copy.deepcopy(v) for k, v in spec.items() if k not in _THEME_OWNED and k != "datasets"}
    out["$schema"] = VL_SCHEMA
    if datasets is not None:
        out["datasets"] = copy.deepcopy(datasets)
    if not any(k in out for k in ("vconcat", "hconcat", "concat", "facet", "repeat")):
        out["width"] = theme.WIDTH - 2 * theme.PADDING
    out["config"] = copy.deepcopy(theme.THEME)
    return out


def render_png(spec: dict, datasets: dict | None = None) -> bytes:
    """The chart as PNG bytes, ``PNG_WIDTH`` wide (narrower only if the spec is), in
    Connect's theme. Deterministic for the same spec and data. Raises
    ``RenderError`` when Vega-Lite refuses the spec or the picture is out of bounds."""
    import vl_convert as vlc
    from PIL import Image

    _register_fonts(vlc)
    try:
        png = vlc.vegalite_to_png(
            themed(spec, datasets), vl_version=VL_VERSION, scale=theme.SCALE, allowed_base_urls=[]
        )
    except ValueError as e:
        raise RenderError(f"Vega-Lite could not draw this chart: {str(e)[:300]}") from e
    import io

    width, height = Image.open(io.BytesIO(png)).size
    if height > MAX_HEIGHT or width > PNG_WIDTH + 2 * theme.SCALE:
        raise RenderError(f"the chart is {width}x{height} px; at most {PNG_WIDTH}x{MAX_HEIGHT} fits a phone")
    if len(png) > MAX_PNG_BYTES:
        raise RenderError(f"the chart is {len(png)} bytes; at most {MAX_PNG_BYTES}")
    return png
