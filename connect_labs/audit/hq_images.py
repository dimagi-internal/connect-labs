"""Fetch an audit photo from CommCare HQ with the reviewer's own OAuth token.

Used by ``ExperimentAuditImageHQView``. Unlike Connect's background task, which
stores whatever HQ returned, this checks the HTTP status AND that the bytes are
an image before anything is served, so an HQ error page can never be shown as a
photo.
"""

from __future__ import annotations

import logging
import time

import httpx
from django.conf import settings

from connect_labs.audit.image_source import hq_attachment_path, sniff_image_type

logger = logging.getLogger(__name__)

HQ_FETCH_MAX_ATTEMPTS = 3
HQ_FETCH_BACKOFF_BASE = 0.3  # seconds; 0.3, 0.6
HQ_FETCH_TIMEOUT = 30.0


class HQImageError(Exception):
    """The photo could not be obtained from HQ. ``status_code`` is HQ's, if any."""

    def __init__(self, message: str, *, status_code: int | None = None, code: str = "hq_fetch_failed"):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class HQUnauthorized(HQImageError):
    """HQ answered 401: the token is expired/revoked (a refresh may fix it)."""


def hq_base_url() -> str:
    return getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org").rstrip("/")


def _get(url: str, access_token: str) -> httpx.Response:
    return httpx.get(
        url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=HQ_FETCH_TIMEOUT,
        follow_redirects=True,
    )


def fetch_hq_image(access_token: str, domain: str, xform_id: str, name: str) -> tuple[bytes, str]:
    """Return ``(bytes, content_type)`` for a form attachment that is an image.

    Tries the endpoint Connect itself uses (``/api/form/attachment/``) and, if
    HQ answers 403/404/405 for it, the ``/api/form_attachment/v1/`` form that
    appears in a form's ``attachments`` listing. Transient failures (5xx,
    connection errors) are retried with a short backoff. Never logs the token.

    Raises HQUnauthorized (HQ said 401) or HQImageError (anything else wrong,
    including a 200 whose body is not an image).
    """
    last_error: HQImageError | None = None
    for legacy in (False, True):
        url = hq_base_url() + hq_attachment_path(domain, xform_id, name, legacy=legacy)
        for attempt in range(1, HQ_FETCH_MAX_ATTEMPTS + 1):
            try:
                response = _get(url, access_token)
            except httpx.RequestError as exc:
                last_error = HQImageError(f"HQ request failed: {type(exc).__name__}", code="hq_unreachable")
                logger.warning("[Audit] HQ image request error (attempt %s): %s", attempt, type(exc).__name__)
                if attempt < HQ_FETCH_MAX_ATTEMPTS:
                    time.sleep(HQ_FETCH_BACKOFF_BASE * (2 ** (attempt - 1)))
                continue

            status = response.status_code
            if status == 200:
                content_type = sniff_image_type(response.content)
                if not content_type:
                    # The body is not a JPEG/PNG/GIF (an HQ login or error page,
                    # say). Never serve it as a photo.
                    raise HQImageError("HQ returned something that is not an image", status_code=200, code="not_image")
                return response.content, content_type
            if status == 401:
                raise HQUnauthorized("CommCare HQ rejected the sign-in", status_code=401, code="hq_unauthorized")
            if status >= 500:
                last_error = HQImageError(f"HQ error {status}", status_code=status)
                logger.warning("[Audit] HQ image HTTP %s (attempt %s/%s)", status, attempt, HQ_FETCH_MAX_ATTEMPTS)
                if attempt < HQ_FETCH_MAX_ATTEMPTS:
                    time.sleep(HQ_FETCH_BACKOFF_BASE * (2 ** (attempt - 1)))
                continue
            # 4xx: retrying the same URL won't help.
            last_error = HQImageError(
                f"HQ error {status}", status_code=status, code="hq_denied" if status == 403 else "hq_fetch_failed"
            )
            break
        if last_error is None or last_error.status_code not in (403, 404, 405):
            break
    raise last_error or HQImageError("HQ image fetch failed")
