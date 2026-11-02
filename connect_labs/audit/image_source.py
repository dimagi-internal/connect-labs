"""Where an audit's photos come from: Connect (default) or CommCare HQ.

Kept dependency-free so the criteria parser, the resume key
(``run_checkpoints``), image extraction and the proxy view can all import it.

An HQ image entry in ``session.data["visit_images"][visit_id]`` carries, on top
of the usual ``blob_id`` / ``name`` / ``question_id`` fields::

    "image_source": "commcare_hq",
    "hq_xform_id": "<CommCare form (xform) id>",
    "hq_attachment_name": "<attachment name inside the form>",
    "hq_domain": "<CommCare project space the form lives in>"

``blob_id`` stays the image's identity (assessments are keyed by it).
"""

from __future__ import annotations

import re
from urllib.parse import quote, unquote, urlparse

IMAGE_SOURCE_CONNECT = "connect"
IMAGE_SOURCE_COMMCARE_HQ = "commcare_hq"
IMAGE_SOURCES = (IMAGE_SOURCE_CONNECT, IMAGE_SOURCE_COMMCARE_HQ)

# Characters HQ uses in xform ids (uuids, sometimes with ``:``/``-``).
_XFORM_ID_RE = re.compile(r"^[\w\-:]{1,128}$")
_DOMAIN_RE = re.compile(r"^[\w\-.]{1,255}$")
# ``/a/<domain>/api/form_attachment/v1/<xform_id>/<name>`` as recorded in form_json.
_ATTACHMENT_URL_RE = re.compile(r"/a/([^/]+)/api/form_attachment/v1/")


def normalize_image_source(criteria: dict | None, *, strict: bool = False) -> str:
    """The ``image_source`` named by a criteria dict (snake or camel case).

    Missing/empty means ``"connect"``. An unknown value raises ``ValueError``
    when ``strict`` (request validation) and otherwise falls back to
    ``"connect"`` so a stored record can never break a read.
    """
    c = criteria or {}
    value = c.get("image_source") or c.get("imageSource") or IMAGE_SOURCE_CONNECT
    if value not in IMAGE_SOURCES:
        if strict:
            raise ValueError(f"image_source must be one of {', '.join(IMAGE_SOURCES)}")
        return IMAGE_SOURCE_CONNECT
    return value


def is_hq_image(image: dict | None) -> bool:
    """Does this stored image entry say it is fetched from CommCare HQ?"""
    return bool(
        isinstance(image, dict)
        and image.get("image_source") == IMAGE_SOURCE_COMMCARE_HQ
        and image.get("hq_xform_id")
        and image.get("hq_attachment_name")
    )


def valid_xform_id(value: str) -> bool:
    return bool(value and _XFORM_ID_RE.match(value))


def valid_domain(value: str) -> bool:
    return bool(value and _DOMAIN_RE.match(value))


def valid_attachment_name(value: str) -> bool:
    """An attachment name is a plain file name: no traversal, no query, no control chars."""
    if not value or len(value) > 255:
        return False
    if value.startswith("/") or "\\" in value or "?" in value or "#" in value:
        return False
    if any(part in ("", ".", "..") for part in value.split("/")):
        return False
    return not any(ord(ch) < 32 for ch in value)


def hq_fields_for_image(raw_image: dict, form_json: dict | None, visit_xform_id: str | None = None) -> dict:
    """The HQ fetch fields for one entry of a visit's ``images`` list.

    Returns ``{}`` when the entry cannot be fetched from HQ (no form id or no
    attachment name), so the caller keeps the plain Connect entry.
    """
    name = raw_image.get("name") or ""
    form_json = form_json if isinstance(form_json, dict) else {}
    xform_id = raw_image.get("parent_id") or visit_xform_id or form_json.get("id") or ""
    domain = form_json.get("domain") or ""
    if not domain:
        attachment = (form_json.get("attachments") or {}).get(name) or {}
        url = attachment.get("url") if isinstance(attachment, dict) else ""
        match = _ATTACHMENT_URL_RE.search(urlparse(url or "").path)
        domain = match.group(1) if match else ""
    if not (xform_id and name and domain):
        return {}
    return {
        "image_source": IMAGE_SOURCE_COMMCARE_HQ,
        "hq_xform_id": str(xform_id),
        "hq_attachment_name": name,
        "hq_domain": domain,
    }


def hq_attachment_path(domain: str, xform_id: str, name: str, *, legacy: bool = False) -> str:
    """Path (under the HQ server) of an attachment endpoint.

    ``legacy=False`` is the endpoint Connect itself uses; ``legacy=True`` is
    the ``form_attachment/v1`` form that appears in a form's ``attachments``.
    """
    quoted = quote(unquote(name), safe="/")
    if legacy:
        return f"/a/{domain}/api/form_attachment/v1/{xform_id}/{quoted}"
    return f"/a/{domain}/api/form/attachment/{xform_id}/{quoted}"


def sniff_image_type(data: bytes) -> str | None:
    """MIME type from magic bytes (JPEG, PNG, GIF), or None if it isn't one."""
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return None
