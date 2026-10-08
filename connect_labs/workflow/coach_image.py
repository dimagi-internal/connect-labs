"""A picture of a worker's own figures, for the coach to send alongside the briefing.

When "Start coaching" runs with ``include_image``, each briefed worker's session state
gains ``coach_image_url`` and ``coach_image_caption`` (``tasks/ai_sessions.py``). The
URL is a SIGNED LINK, not a stored file: the payload -- the worker's name and the
figures of the topics the briefing raises -- travels inside the link, signed so it
cannot be altered, and the PNG is drawn when the link is fetched. Nothing is stored.

The link expires after ``MAX_AGE`` (a worker may reply days later) and is fetched by
Open Chat Studio with a ``coach-images`` token (``mcp/token_scopes.COACH_IMAGES``),
checked by ``coach_image_views.coach_image``.

The topics are the briefing's own, after ``fit_briefing`` dropped what did not fit:
``payload_from_briefing`` reads them back from the briefing text the coach receives,
so the picture and the conversation can never disagree. Figures only -- no targets,
no goal lines.
"""

from __future__ import annotations

import io
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core import signing
from django.urls import reverse

from connect_labs.workflow import coach_briefing

SALT = "coach-image"
MAX_AGE = timedelta(days=7)

#: The picture's width. Its height follows the content -- a phone shows the card in a
#: chat bubble about 930 px wide, so empty space below the last topic only shrinks
#: the type -- with a floor so a single topic is not a sliver.
WIDTH = 1080
MIN_HEIGHT = 480

BAND_COLOURS = {
    "red": (214, 69, 69),
    "yellow": (224, 161, 0),
    "amber": (224, 161, 0),
    "green": (46, 158, 91),
}
NEUTRAL = (154, 160, 166)
INK = (31, 41, 51)
MUTED = (96, 108, 118)
TRACK = (234, 236, 240)
WHITE = (255, 255, 255)

# Sizes chosen for a phone, where the card is shown at about 0.85x.
_MARGIN = 80
_TITLE_SIZE = 72
_TITLE_LINE = 88
_NAME_SIZE = 40
_NAME_LINE = 52
_HEADER_GAP = 56
_LABEL_SIZE = 44
_LABEL_LINE = 56
_FIGURE_SIZE = 44
_FIGURE_LINE = 58
_BAR_GAP = 24
_BAR_HEIGHT = 44
_BLOCK_GAP = 44
_MAX_LABEL_LINES = 3
#: A stroke in the text's own colour: the bundled font has no bold weight.
_BOLD_STROKE = 1


class BadImageLink(Exception):
    """A link that is not ours, was altered, has expired, or carries no picture."""


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------


def build_image_payload(worker_name: str, topics: list[dict]) -> dict:
    """The picture's content: the worker's display name and, per topic, its label,
    band and figure -- ``numerator`` / ``denominator`` / ``pct`` (whole percent, as
    the briefing words it) for a count, else the briefing's ``figure`` words.

    ``topics`` are topic dicts as ``coach_briefing.coachable_topics`` gives them (or
    ``topics_from_briefing`` reads them back), already trimmed by ``fit_briefing``."""
    out = []
    for t in topics:
        item: dict[str, Any] = {"label": str(t.get("label") or t.get("key") or ""), "band": t.get("band")}
        if "numerator" in t and "denominator" in t:
            item["numerator"], item["denominator"] = int(t["numerator"]), int(t["denominator"])
            if t.get("pct") is not None:
                item["pct"] = int(round(t["pct"]))
        else:
            item["figure"] = coach_briefing.topic_figure(t)
        out.append(item)
    return {"worker": str(worker_name or ""), "topics": out}


def payload_from_briefing(briefing: str) -> dict | None:
    """The picture for a briefing: exactly its worker and topics. None when the text
    is not a briefing or names no topic."""
    if not coach_briefing.is_briefing(briefing):
        return None
    topics = coach_briefing.topics_from_briefing(briefing)
    if not topics:
        return None
    return build_image_payload(coach_briefing.briefing_worker(briefing), topics)


def sign(payload: dict) -> str:
    return signing.dumps(payload, salt=SALT, compress=True)


def unsign(token: str) -> dict:
    """The payload a link carries. Raises ``BadImageLink`` when the signature is
    wrong, the link has expired, or what it carries is not a picture's payload."""
    try:
        payload = signing.loads(token, salt=SALT, max_age=MAX_AGE)
    except signing.BadSignature as e:  # SignatureExpired is a BadSignature
        raise BadImageLink(str(e)) from e
    if not _is_payload(payload):
        raise BadImageLink("not a picture payload")
    return payload


def _is_payload(payload: Any) -> bool:
    if not isinstance(payload, dict) or not isinstance(payload.get("topics"), list) or not payload["topics"]:
        return False
    return all(isinstance(t, dict) and isinstance(t.get("label"), str) for t in payload["topics"])


def image_url(payload: dict) -> str | None:
    """The absolute link to the picture: ``LABS_PUBLIC_URL`` + the view's path. None
    when the site's public origin is not configured (a background job has no request
    to borrow a host from, and a relative link is useless to Open Chat Studio)."""
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    if not base:
        return None
    return base + reverse("labs:coach_image", args=[sign(payload)])


def caption(payload: dict) -> str:
    """One sentence for the coach: what the picture shows. Labels only, no numbers."""
    first = coach_briefing.first_name(payload.get("worker"))
    whose = f"{first}'s" if first else "the worker's"
    labels = "; ".join(t["label"] for t in payload.get("topics") or [])
    return f"A bar chart of {whose} figures for: {labels}."


def attachment(briefing: str) -> dict | None:
    """``{"url", "caption"}`` for a briefing's picture, or None when there is none
    to give (not a briefing, no topics, or no public origin to link from)."""
    payload = payload_from_briefing(briefing)
    if payload is None:
        return None
    url = image_url(payload)
    if url is None:
        return None
    return {"url": url, "caption": caption(payload)}


# ---------------------------------------------------------------------------
# Drawing it
# ---------------------------------------------------------------------------


def _font(size: int):
    from PIL import ImageFont

    # Pillow >= 10.1 bundles a scalable FreeType font, so no font file ships with labs.
    return ImageFont.load_default(size=size)


def _wrap(text: str, font, width: int, max_lines: int) -> list[str]:
    """``text`` broken into lines no wider than ``width``; a word too long for a line
    is broken by character, and anything past ``max_lines`` ends in an ellipsis."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if font.getlength(candidate) <= width:
            current = candidate
            continue
        if current:
            lines.append(current)
        while font.getlength(word) > width:
            cut = len(word)
            while cut > 1 and font.getlength(word[:cut]) > width:
                cut -= 1
            lines.append(word[:cut])
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and font.getlength(last + "…") > width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines or [""]


def _figure_text(topic: dict) -> str:
    if "numerator" in topic and "denominator" in topic:
        text = f"{topic['numerator']} of {topic['denominator']}"
        return text + (f" · {topic['pct']}%" if topic.get("pct") is not None else "")
    return str(topic.get("figure") or "")


def _layout(payload: dict, label_font) -> tuple[list, int]:
    """Each topic's wrapped label and whether it has a bar, and the card's height:
    padding + title block + the topic blocks + padding, never under ``MIN_HEIGHT``."""
    inner = WIDTH - 2 * _MARGIN
    blocks = []
    for topic in payload.get("topics") or []:
        label_lines = _wrap(str(topic.get("label") or ""), label_font, inner, _MAX_LABEL_LINES)
        has_bar = "numerator" in topic and "denominator" in topic
        height = len(label_lines) * _LABEL_LINE + _FIGURE_LINE + (_BAR_GAP + _BAR_HEIGHT if has_bar else 0)
        blocks.append((topic, label_lines, has_bar, height))
    header = _MARGIN + _TITLE_LINE + (_NAME_LINE if coach_briefing.first_name(payload.get("worker")) else 0)
    body = sum(b[3] for b in blocks) + _BLOCK_GAP * max(len(blocks) - 1, 0)
    return blocks, max(MIN_HEIGHT, header + _HEADER_GAP + body + _MARGIN)


def render_png(payload: dict) -> bytes:
    """The picture: a title, then one block per topic -- its label, the figure in
    bold directly under it, and a bar of numerator over denominator in the band's
    colour (grey when the band is unknown). The card is as tall as its content.
    Deterministic: the same payload draws the same bytes."""
    from PIL import Image, ImageDraw

    title_font, name_font = _font(_TITLE_SIZE), _font(_NAME_SIZE)
    label_font, figure_font = _font(_LABEL_SIZE), _font(_FIGURE_SIZE)
    inner = WIDTH - 2 * _MARGIN
    blocks, height = _layout(payload, label_font)
    image = Image.new("RGB", (WIDTH, height), WHITE)
    draw = ImageDraw.Draw(image)

    y = _MARGIN
    draw.text((_MARGIN, y), "Your figures", font=title_font, fill=INK, stroke_width=_BOLD_STROKE, stroke_fill=INK)
    y += _TITLE_LINE
    first = coach_briefing.first_name(payload.get("worker"))
    if first:
        draw.text((_MARGIN, y), first, font=name_font, fill=MUTED)
        y += _NAME_LINE
    y += _HEADER_GAP

    for topic, label_lines, has_bar, block_height in blocks:
        top = y
        colour = BAND_COLOURS.get(str(topic.get("band") or "").lower(), NEUTRAL)
        for line in label_lines:
            draw.text((_MARGIN, y), line, font=label_font, fill=INK)
            y += _LABEL_LINE
        draw.text(
            (_MARGIN, y), _figure_text(topic), font=figure_font, fill=INK, stroke_width=_BOLD_STROKE, stroke_fill=INK
        )
        y += _FIGURE_LINE
        if has_bar:
            y += _BAR_GAP
            draw.rounded_rectangle((_MARGIN, y, _MARGIN + inner, y + _BAR_HEIGHT), radius=_BAR_HEIGHT // 2, fill=TRACK)
            num, den = topic["numerator"], topic["denominator"]
            share = min(max(num / den, 0.0), 1.0) if den else 0.0
            filled = int(round(inner * share))
            if filled > 0:
                # A sliver narrower than the bar's height is drawn as a full round cap.
                filled = max(filled, _BAR_HEIGHT)
                draw.rounded_rectangle(
                    (_MARGIN, y, _MARGIN + filled, y + _BAR_HEIGHT), radius=_BAR_HEIGHT // 2, fill=colour
                )
        y = top + block_height + _BLOCK_GAP

    out = io.BytesIO()
    image.save(out, format="PNG", optimize=True)
    return out.getvalue()
