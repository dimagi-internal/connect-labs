"""The photos a case card carries: one per visit, from the form question its registry
names (a series panel's ``photo.question``).

Each photo goes down once, small, inside the card's ``index.html`` (a ``data:`` URI of
about 20 KB, sharp enough to read a scale when a tap enlarges it to the phone's
width). The card-html sandbox has no network, and the app downloads every file of a
message when it arrives, so a separate full-size file would not load lazily anyway. The
photo is read once at preview (the same read the audit pages use: a synthetic
``synth-*`` photo from the stock folder, any other from Connect) and frozen with the
card. Best effort, always: a photo that cannot be read leaves its
visit photo-less, and the card still says everything else.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Longest side in pixels and JPEG quality: a scale's digits stay readable at a phone's
#: width, at about 20 KB a photo.
SIZE = (480, 60)
#: At most this many photos in one card (the latest ones).
MAX_PHOTOS = 6


def photo_question(state: dict) -> str | None:
    """The form question a case state's card shows a photo of, if any."""
    for panel in (state.get("picture") or {}).get("panels") or []:
        if isinstance(panel, dict) and panel.get("kind") == "series" and isinstance(panel.get("photo"), dict):
            q = panel["photo"].get("question")
            if q:
                return str(q)
    return None


def shrink(data: bytes) -> str | None:
    """``data`` (any image) as a small JPEG ``data:`` URI, or None if it is not an image."""
    from PIL import Image, ImageOps

    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        img.thumbnail((SIZE[0], SIZE[0]))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=SIZE[1], optimize=True)
    except Exception:  # noqa: BLE001 -- an unreadable photo is a visit without one
        return None
    return "data:image/jpeg;base64," + base64.b64encode(out.getvalue()).decode()


def _matches(image: dict, question: str) -> bool:
    qid = str(image.get("question_id") or "")
    return qid == question or qid.endswith("/" + question)


def visit_photos(
    visit_ids: list[Any],
    question: str,
    *,
    opportunity_id: int,
    access_token: str | None,
    request=None,
) -> dict[str, str]:
    """``{visit_id: data URI}`` for the latest ``MAX_PHOTOS`` of ``visit_ids`` that have a
    photo for ``question``. Never raises."""
    ids = [int(v) for v in visit_ids if str(v or "").isdigit()][-MAX_PHOTOS:]
    if not ids or not question:
        return {}
    try:
        from connect_labs.audit.analysis_config import extract_images_with_question_ids
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline

        pipeline = AnalysisPipeline(request=request, access_token=access_token)
        raw = pipeline.fetch_raw_visits(
            opportunity_id=int(opportunity_id), filter_visit_ids=set(ids), include_images=True
        )
    except Exception:  # noqa: BLE001
        logger.warning("case card: could not read visit photos for opp %s", opportunity_id, exc_info=True)
        return {}
    out: dict[str, str] = {}
    for visit in raw:
        image = next((i for i in extract_images_with_question_ids(visit) if _matches(i, question)), None)
        if not image or not image.get("blob_id"):
            continue
        data = _download(str(image["blob_id"]), int(opportunity_id), access_token)
        uri = shrink(data) if data else None
        if uri:
            out[str(visit.get("id"))] = uri
    return out


def _download(blob_id: str, opportunity_id: int, access_token: str | None) -> bytes | None:
    from connect_labs.labs.synthetic import image_server

    try:
        if image_server.SyntheticImageServer.is_synthetic_blob(blob_id):
            return image_server.get_image_server().get_image(blob_id)
        if not access_token:
            return None
        import httpx
        from django.conf import settings

        with httpx.Client(headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0) as client:
            resp = client.get(
                f"{settings.CONNECT_PRODUCTION_URL.rstrip('/')}/export/opportunity/{opportunity_id}/image/",
                params={"blob_id": blob_id},
            )
            resp.raise_for_status()
            return resp.content
    except Exception:  # noqa: BLE001
        logger.warning("case card: photo %s could not be read", blob_id, exc_info=True)
        return None
