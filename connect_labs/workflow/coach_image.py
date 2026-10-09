"""A picture of a worker's own figures, for the coach to send alongside the briefing.

When "Start coaching" runs with ``include_image`` (or a ``picture``), each briefed
worker's session state gains ``coach_image_url`` and ``coach_image_caption``
(``tasks/ai_sessions.py``). The URL is a SIGNED LINK. Two kinds of payload ride in it:

* a CHART link -- ``{"chart": <id>, "opportunity_id"}`` -- names the frozen chart the
  person previewed (``coach_charts/store.py``, a ``CoachChart`` row): any type an agent
  asked for, with the data Labs resolved at preview, drawn on fetch from that row.
  Every picture sent since generated charts is one of these;
* a LEGACY link -- ``{"worker", "topics", "opportunity_id"}`` -- carries the figures
  themselves, drawn as ``topic_bars``. Links issued before charts still work.

The link expires after ``MAX_AGE`` (a worker may reply days later) and is fetched by
Open Chat Studio with a ``coach-images`` token (``mcp/token_scopes.COACH_IMAGES``),
checked by ``coach_image_views.coach_image``. A signed-in Labs user may also open it
in a browser -- to see the picture before it is sent -- when the link names an
opportunity they can see.

The topics are the briefing's own, after ``fit_briefing`` dropped what did not fit:
``payload_from_briefing`` reads them back from the briefing text the coach receives,
so the picture and the conversation can never disagree. Figures only -- no targets,
no goal lines.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core import signing
from django.urls import reverse

from connect_labs.workflow import coach_briefing

SALT = "coach-image"
MAX_AGE = timedelta(days=7)

#: The picture's size (``coach_charts.theme``: 600 x 400 CSS px drawn at 2x). Landscape,
#: 3:2, whatever the number of topics: Connect's messenger sizes the bubble to a
#: portrait picture's narrow width (connect-labs#2413).
WIDTH = 1200
HEIGHT = 800


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
    if not (_is_payload(payload) or _is_chart_link(payload)):
        raise BadImageLink("not a picture payload")
    return payload


def _is_chart_link(payload: Any) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("chart"), str) and len(payload["chart"]) == 32


def png_for(payload: dict) -> bytes:
    """The PNG a link names: its stored chart, or a legacy payload's ``topic_bars``.
    Raises ``BadImageLink`` when the chart it names is gone."""
    if _is_chart_link(payload):
        from connect_labs.workflow.coach_charts import chart as charts
        from connect_labs.workflow.coach_charts import store

        record = store.get(payload["chart"])
        if record is None:
            raise BadImageLink("no such chart")
        return charts.png(record.chart)
    return render_png(payload)


def chart_attachment(record, opportunity_id: int | None = None) -> dict | None:
    """``{"url", "caption"}`` for a stored chart (``coach_charts/store.py``), or None
    when the site has no public origin to link from. The caption is Labs' own,
    written when the chart was built, naming no peer."""
    payload: dict[str, Any] = {"chart": record.pk}
    if opportunity_id is not None:
        payload["opportunity_id"] = int(opportunity_id)
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    if not base:
        return None
    return {
        "url": base + reverse("labs:coach_image", args=[sign(payload)]),
        "caption": record.chart.get("caption") or "",
    }


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


def attachment(briefing: str, opportunity_id: int | None = None) -> dict | None:
    """``{"url", "caption"}`` for a briefing's picture, or None when there is none
    to give (not a briefing, no topics, or no public origin to link from).

    ``opportunity_id`` is the worker's opportunity. Signed into the link, it is what
    lets a signed-in Labs user who can see that opportunity open the picture in a
    browser (``coach_image_views``); a link without one opens for Open Chat Studio's
    token only."""
    payload = payload_from_briefing(briefing)
    if payload is None:
        return None
    if opportunity_id is not None:
        payload["opportunity_id"] = int(opportunity_id)
    url = image_url(payload)
    if url is None:
        return None
    return {"url": url, "caption": caption(payload)}


# ---------------------------------------------------------------------------
# Drawing it
# ---------------------------------------------------------------------------


def render_png(payload: dict) -> bytes:
    """The picture: the ``topic_bars`` chart (``coach_charts``) of the payload's topics --
    a title, then per topic its label, the figure in bold under it, and a bar of
    numerator over denominator in the band's colour (grey when the band is unknown) --
    in Connect's theme. The card is as tall as its content. Deterministic: the same
    payload draws the same bytes. Every link ever signed carries a payload of this
    shape, so every one still draws."""
    from connect_labs.workflow.coach_charts import render, types

    rows = types.topic_rows(payload.get("topics") or [])
    spec = types.topic_bars(rows, first_name=coach_briefing.first_name(payload.get("worker")))
    return render.render_png(spec, {"worker_topics": rows})
