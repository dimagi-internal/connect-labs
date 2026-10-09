"""Connect's look for a coaching chart: the design tokens, and the Vega-Lite ``config``
built from them.

Every chart Labs draws -- a named type or an agent's own spec -- is rendered with
``THEME`` as its whole ``config``; a spec's own ``config`` is discarded (``render.py``),
so no chart can restyle itself. Change the look here, once, for every chart.

Where each token comes from (``dimagi/commcare-connect`` at 36c50217d, 2026-10-08, and
Labs' own Connect-styled pages):

==================  =========  ===========================================================
token               value      source
==================  =========  ===========================================================
font                Work Sans  ``tailwind/tailwind.css`` ``@theme --font-sans``; loaded at
                               400-700 by ``templates/base.html`` (Google Fonts). Labs
                               uses the same (``tailwind/tailwind.css``,
                               ``static/pulse/pulse.css`` "Work Sans is the labs
                               typeface"). Bundled here as TTF (SIL OFL, ``fonts/``).
title ink           #16006d    ``--color-brand-deep-purple``; ``.card_title`` is
                               ``text-brand-deep-purple text-lg font-medium``
text ink            #101828    Tailwind v4 ``gray-900`` (``.title``)
muted ink           #4a5565    Tailwind v4 ``gray-600`` (``.card_description``)
rule / bar track    #e2e8f0    ``--color-brand-border-light``
background          #ffffff    ``.card_bg`` is ``bg-white``
corner radius       8 px       ``.card_bg`` is ``rounded-lg`` (Tailwind v4 0.5rem)
band red            #e44434    ``--color-brand-sunset``; ``.status-error``
band yellow         #feaf31    ``--color-brand-marigold``; ``.status-warning``
band green          #00a63e    Tailwind v4 ``green-600``; ``.status-active``
neutral / peers     #94a3b8    ``--color-brand-blue-light``
the worker (series) #3843d0    ``--color-brand-indigo``
second accent       #5d70d2    ``--color-brand-cornflower-blue``
third accent        #8ea1ff    ``--color-brand-sky``
==================  =========  ===========================================================

Status colours are reserved for bands and never reused as a series colour (the rule
Labs' Pulse palette states, ``static/pulse/pulse.css``). Peers are always the neutral
colour and labelled directly (``Peer A``), so identity is never colour alone.

Sizes are CSS pixels for a phone. A picture is LANDSCAPE: ``WIDTH`` x ``HEIGHT`` (600 x
400, 3:2), drawn at ``SCALE`` 2, so 1200 x 800 px. Connect's messenger caps an image's
height at half the message list and sizes the bubble to the image's width
(``ConnectMessageMediaSizer.fitImage``, dimagi/commcare-android#3946): a portrait picture
hits the height cap and shrinks the whole bubble, a 3:2 one is held by the width cap and
fills it (connect-labs#2413). On a phone the bubble is about 340 dp wide, so 1 CSS px here
is about 0.57 dp and the smallest text (20 px) about 11 dp -- larger than a portrait
card's once the height cap had shrunk it. A webview rendering the
same spec at 600 CSS px gets the same proportions.
"""

from __future__ import annotations

FONT = "Work Sans"

DEEP_PURPLE = "#16006d"
INK = "#101828"
MUTED = "#4a5565"
RULE = "#e2e8f0"
BACKGROUND = "#ffffff"
RADIUS = 8

INDIGO = "#3843d0"
CORNFLOWER = "#5d70d2"
SKY = "#8ea1ff"
NEUTRAL = "#94a3b8"

BAND_COLOURS = {
    "red": "#e44434",
    "yellow": "#feaf31",
    "amber": "#feaf31",
    "green": "#00a63e",
}

#: A series colour for anything that is not a band and not a peer, in order.
SERIES = [INDIGO, CORNFLOWER, SKY]

#: Every colour a chart may use. An agent's spec that names any other is stripped of it
#: (``custom.py``), so a chart cannot step outside the palette.
PALETTE = frozenset(
    {DEEP_PURPLE, INK, MUTED, RULE, BACKGROUND, INDIGO, CORNFLOWER, SKY, NEUTRAL, *BAND_COLOURS.values()}
)

#: CSS size of a chart (landscape, 3:2), and the factor the PNG is drawn at.
WIDTH = 600
HEIGHT = 400
SCALE = 2
#: Space around the chart (CSS px).
PADDING = 32

# Type sizes (CSS px; x2 in the PNG). Chosen for a phone, where the picture is shown
# about 340 dp wide: the smallest text in the PNG is 40 px.
TITLE_SIZE = 36
SUBTITLE_SIZE = 20
LABEL_SIZE = 22
FIGURE_SIZE = 22
AXIS_SIZE = 20
#: The smallest font any themed text may use, in CSS px.
MIN_FONT_SIZE = 20

#: How far the title sits above the chart (CSS px).
TITLE_OFFSET = 20
#: The height the title takes, and the subtitle under it, title offset included
#: (measured from vl-convert with the bundled Work Sans).
TITLE_BLOCK = 57
SUBTITLE_BLOCK = 29


def body_height(subtitle: bool = True) -> int:
    """The CSS height left for a chart's body under its title (and subtitle) in a
    ``WIDTH`` x ``HEIGHT`` picture."""
    return HEIGHT - 2 * PADDING - TITLE_BLOCK - (SUBTITLE_BLOCK if subtitle else 0)


THEME: dict = {
    "font": FONT,
    "background": BACKGROUND,
    "padding": PADDING,
    "autosize": {"type": "pad"},
    "view": {"stroke": None},
    "title": {
        "font": FONT,
        "fontSize": TITLE_SIZE,
        "fontWeight": 600,
        "color": DEEP_PURPLE,
        "anchor": "start",
        "frame": "group",
        "subtitleFont": FONT,
        "subtitleFontSize": SUBTITLE_SIZE,
        "subtitleColor": MUTED,
        "subtitlePadding": 8,
        "offset": TITLE_OFFSET,
    },
    "axis": {
        "labelFont": FONT,
        "labelFontSize": AXIS_SIZE,
        "labelColor": MUTED,
        "titleFont": FONT,
        "titleFontSize": AXIS_SIZE,
        "titleFontWeight": 500,
        "titleColor": MUTED,
        "gridColor": RULE,
        "domain": False,
        "ticks": False,
        "labelPadding": 8,
    },
    "legend": {
        "labelFont": FONT,
        "labelFontSize": AXIS_SIZE,
        "labelColor": INK,
        "titleFont": FONT,
        "titleFontSize": AXIS_SIZE,
        "titleColor": MUTED,
        "orient": "bottom",
        "symbolSize": 300,
    },
    "header": {
        "labelFont": FONT,
        "labelFontSize": LABEL_SIZE,
        "labelColor": INK,
        "titleFont": FONT,
        "titleFontSize": LABEL_SIZE,
        "titleColor": INK,
    },
    "text": {"font": FONT, "fontSize": LABEL_SIZE, "color": INK},
    "bar": {"cornerRadius": RADIUS, "color": INDIGO},
    "rect": {"cornerRadius": RADIUS, "color": INDIGO},
    "arc": {"color": INDIGO},
    "area": {"color": INDIGO, "opacity": 0.25},
    "line": {"color": INDIGO, "strokeWidth": 4},
    "point": {"color": INDIGO, "filled": True, "size": 120},
    "rule": {"color": RULE, "strokeWidth": 2},
    "tick": {"color": INDIGO},
    "range": {"category": SERIES + [NEUTRAL], "ordinal": SERIES},
}
