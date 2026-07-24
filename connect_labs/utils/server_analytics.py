"""Server-side event sender for self-hosted Umami.

Fire-and-forget product analytics from backend code (Celery-dispatched so a
slow/broken analytics service never blocks a request). Complements the
client-side tracker in static/js/labs-analytics.js.

PHI rule: event names and data carry opaque identifiers only.
"""
import logging
from urllib.parse import urlparse

import httpx
from django.conf import settings

from config import celery_app

logger = logging.getLogger(__name__)

# Umami silently drops events whose User-Agent trips its isbot check (it
# answers {"beep":"boop"} and stores nothing) — even a custom suffix appended
# to a browser UA gets flagged, so this must be a plain browser UA. Server
# events are identifiable by their "/server/..." url instead.
SERVER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"
)


def send_event(name: str, data: dict | None = None, url: str = "/server") -> None:
    """Queue a server-side analytics event. No-op when Umami is unconfigured."""
    if not (settings.UMAMI_HOST_URL and settings.UMAMI_WEBSITE_ID):
        return
    try:
        send_event_task.delay(name, data or {}, url)
    except Exception:  # broker down — analytics is never worth an error
        logger.warning("Failed to queue analytics event %s", name, exc_info=True)


@celery_app.task(ignore_result=True)
def send_event_task(name: str, data: dict, url: str = "/server"):
    host_url = settings.UMAMI_HOST_URL.rstrip("/")
    payload = {
        "type": "event",
        "payload": {
            "website": settings.UMAMI_WEBSITE_ID,
            "hostname": urlparse(host_url).hostname or "labs",
            "url": url,
            "name": name,
            "data": data,
        },
    }
    try:
        response = httpx.post(
            f"{host_url}/api/send",
            json=payload,
            headers={"User-Agent": SERVER_USER_AGENT},
            timeout=10.0,
        )
        response.raise_for_status()
    except httpx.HTTPError:
        logger.warning("Umami event send failed for %s", name, exc_info=True)
