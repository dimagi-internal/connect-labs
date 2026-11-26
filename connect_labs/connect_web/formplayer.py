"""The bridge to Formplayer (commcare-core's form engine), in its labs mode.

Labs' backend drives Formplayer as the checked-out pool worker, exactly as the Android app drives
commcare-core: the browser talks only to labs (`api.py`), and labs makes every Formplayer call
here, signed and carrying the worker's HQ token. The browser never sees the token, the signing
key, the HQ username, the domain or the app id.

The contract (Formplayer ``docs/labs-mode.md``, ``auth/LabsAuthFilter.java``):

* ``POST <CONNECT_WEB_FORMPLAYER_URL>/<endpoint>``, ``Content-Type: application/json``.
* ``X-Labs-Signature`` (v1): standard padded base64 of HMAC-SHA256(key = ``CONNECT_WEB_FORMPLAYER_KEY``
  as UTF-8, message = the exact body bytes sent). For the multipart ``answer_media`` request, the
  bytes of the ``application/json`` part named ``answer``; the file part is not covered.
* ``X-Labs-Timestamp`` + ``X-Labs-Signature-V2`` (v2, `sign_v2`): the same HMAC over a canonical
  string that also binds the method, the endpoint path, the time, the HQ token (its SHA-256) and the
  HQ host, so a captured request can't be replayed later, sent to another endpoint, or re-used with
  another token or HQ server. Formplayer checks v2 when present (±300 s) and accepts v1 alone only
  while it is configured to; labs sends both, so either side can be deployed first.
* ``X-Labs-HQ-Token``: the worker's HQ access token, fresh on every request.
* The body always has ``username`` (the HQ username) and ``domain``; never ``restoreAs``.

**app_id is the released build id, not Connect's cc_app_id** (Formplayer caches installs per app
id, so a master id would pin everyone to whatever was installed first). `resolve_build_id` reads
it from the app's ``media_profile.ccpr?latest=true``: the ``PostURL`` property ends in
``/receiver/secure/<build_id>/``.

Formplayer answers most failures with HTTP 200 and ``{"status": "error", "exception": "..."}``
(``GlobalDefaultExceptionHandler``); a form session it no longer has reads "Could not find form
with id ...". Those become `FormplayerError` / `FormSessionExpired`. A ``submit-all`` that HQ
refused is *not* an exception: it is a normal ``SubmitResponseBean`` with ``status: "error"`` and
``notification.message``, passed back to the player as a result.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from . import cache as lcache
from . import http, service
from .errors import (
    ENGINE_AUTH_FAILED,
    ENGINE_BUSY,
    ENGINE_ERROR,
    ENGINE_NOT_CONFIGURED,
    NO_RELEASED_BUILD,
    FormplayerError,
    FormSessionExpired,
    HQError,
    InvalidInput,
    NotConfigured,
)

logger = logging.getLogger(__name__)

SIGNATURE_HEADER = "X-Labs-Signature"
SIGNATURE_V2_HEADER = "X-Labs-Signature-V2"
TIMESTAMP_HEADER = "X-Labs-Timestamp"
TOKEN_HEADER = "X-Labs-HQ-Token"
HQ_HOST_HEADER = "X-Labs-HQ-Host"


def hq_origin(hq_base: str) -> str:
    """scheme://host[:port] of an HQ base URL, as Formplayer's LABS_HQ_HOSTS lists them."""
    parts = urlsplit(hq_base)
    return f"{parts.scheme}://{parts.netloc}".lower()


#: Each endpoint's time budget (seconds; one attempt, never retried). Navigation and sync may
#: restore the worker and install the app on HQ first, which takes a while; a submit goes on to HQ.
#: The player (api.ts) gives up a little later than these.
TIMEOUTS = {"navigate_menu_start": 75.0, "navigate_menu": 75.0, "sync-db": 75.0, "submit-all": 60.0}
DEFAULT_TIMEOUT = 30.0
CONNECT_TIMEOUT = 3.0

#: The player resyncs the worker's data (Formplayer ``sync-db``) when it starts, if labs hasn't
#: for this long (cases created elsewhere — on a phone, another worker — would otherwise stay stale).
SYNC_EVERY_SECONDS = 10 * 60

#: Formplayer's own limit (``services/MediaValidator.kt`` MAX_BYTES_PER_ATTACHMENT).
FORMPLAYER_MEDIA_MAX_BYTES = 4 * 1048576 - 1024

#: A media profile is a few KB; refuse anything absurd before parsing it.
MAX_PROFILE_BYTES = 512 * 1024

#: Keys of Formplayer responses that are heavy and unused by the player.
DROPPED_RESPONSE_KEYS = ("instanceXml",)

_SESSION_EXPIRED_MARKER = "Could not find form with id"


# ---------------------------------------------------------------------------
# Configuration and signing
# ---------------------------------------------------------------------------


def base_url() -> str:
    return (getattr(settings, "CONNECT_WEB_FORMPLAYER_URL", "") or "").strip().rstrip("/")


def _key() -> str:
    return getattr(settings, "CONNECT_WEB_FORMPLAYER_KEY", "") or ""


def is_configured() -> bool:
    """True when both the engine's URL and the signing key are set."""
    return bool(base_url() and _key().strip())


def require_configured() -> None:
    if not is_configured():
        raise NotConfigured(
            "The form engine is not configured (CONNECT_WEB_FORMPLAYER_URL / CONNECT_WEB_FORMPLAYER_KEY).",
            error_code=ENGINE_NOT_CONFIGURED,
        )


@sensitive_variables("key")
def sign(key: str, body: bytes) -> str:
    """Standard padded base64 of HMAC-SHA256(key as UTF-8, body) — ``LabsAuthFilter.sign``."""
    return base64.b64encode(hmac.new(key.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")


@sensitive_variables("key", "hq_token")
def canonical_v2(method: str, path: str, timestamp: int, hq_token: str, hq_host: str | None, signed: bytes) -> bytes:
    """What `sign_v2` signs: ``v2\\nMETHOD\\npath\\nts\\nsha256hex(hq token)\\nhq host\\n`` + the signed bytes."""
    head = "\n".join(
        [
            "v2",
            method.upper(),
            path,
            str(int(timestamp)),
            hashlib.sha256(hq_token.encode("utf-8")).hexdigest(),
            hq_host or "",
        ]
    )
    return head.encode("utf-8") + b"\n" + signed


@sensitive_variables("key", "hq_token")
def sign_v2(
    key: str, method: str, path: str, timestamp: int, hq_token: str, hq_host: str | None, signed: bytes
) -> str:
    """``X-Labs-Signature-V2``: padded base64 of HMAC-SHA256(key, `canonical_v2`)."""
    return sign(key, canonical_v2(method, path, timestamp, hq_token, hq_host, signed))


def _now() -> int:
    return int(time.time())


def encode(payload: dict) -> bytes:
    """The exact bytes sent (and signed). Compact; non-ASCII kept as UTF-8."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


# ---------------------------------------------------------------------------
# The app's released build id
# ---------------------------------------------------------------------------


def parse_build_id(profile: bytes) -> str | None:
    """The build id from a media profile's ``PostURL`` property, or None.

    ``<property key="PostURL" value="https://<hq>/a/<domain>/receiver/secure/<build_id>/" .../>``.
    Namespaces are ignored; a document with a DTD is refused (no entity expansion); if the XML
    does not parse, a plain attribute match is tried.
    """
    if not profile or len(profile) > MAX_PROFILE_BYTES:
        return None
    head = profile[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in profile.upper():
        return None
    post_url = None
    try:
        root = ET.fromstring(profile)
    except ET.ParseError:
        match = re.search(rb"""key\s*=\s*["']PostURL["'][^>]*?value\s*=\s*["']([^"']+)["']""", profile)
        if not match:
            match = re.search(rb"""value\s*=\s*["']([^"']+)["'][^>]*?key\s*=\s*["']PostURL["']""", profile)
        post_url = match.group(1).decode("utf-8", "replace") if match else None
    else:
        for element in root.iter():
            tag = element.tag.rsplit("}", 1)[-1] if isinstance(element.tag, str) else ""
            if tag == "property" and element.get("key") == "PostURL":
                post_url = element.get("value")
                break
    if not post_url:
        return None
    match = re.search(r"/receiver/(?:secure/)?([A-Za-z0-9]{8,64})/?(?:[?#].*)?$", post_url.strip())
    return match.group(1) if match else None


def resolve_build_id(hq_base: str, domain: str, cc_app_id: str) -> str:
    """The released build id for Connect's `cc_app_id`, cached per (HQ, domain, app) for a few minutes.

    "No released build" is cached too, for 30 seconds (`cache.NO_BUILD_TTL`), so a player retrying
    against an unreleased app doesn't hammer HQ.

    ``GET <hq>/a/<domain>/apps/download/<cc_app_id>/media_profile.ccpr?latest=true`` — no auth;
    `hq_base` has already been checked against ``CONNECT_WEB_HQ_HOSTS``.
    """
    host = urlsplit(hq_base).netloc
    cached = lcache.get_build(host, domain, cc_app_id)
    if cached == lcache.NO_BUILD:
        raise HQError("CommCare HQ has no released build of this app", error_code=NO_RELEASED_BUILD)
    if cached:
        return cached
    app_path = f"/a/{quote(domain, safe='')}/apps/download/{quote(cc_app_id, safe='')}/media_profile.ccpr"
    url = f"{hq_base.rstrip('/')}{app_path}"
    try:
        response = http.request("GET", url, HQError, log_name="hq.media_profile", params={"latest": "true"})
    except HQError as exc:
        if exc.status is not None and (exc.status == 404 or 300 <= exc.status < 400):
            lcache.set_no_build(host, domain, cc_app_id)
            raise HQError(
                "CommCare HQ has no released build of this app", status=exc.status, error_code=NO_RELEASED_BUILD
            ) from None
        raise
    build_id = parse_build_id(response.content)
    if not build_id:
        lcache.set_no_build(host, domain, cc_app_id)
        raise HQError("The app's profile names no build (PostURL)", error_code=NO_RELEASED_BUILD)
    lcache.set_build(host, domain, cc_app_id, build_id)
    return build_id


# ---------------------------------------------------------------------------
# Which app a request runs: always derived on the server
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppTarget:
    """Where one opportunity's learn or deliver app runs. Nothing in it comes from the browser."""

    opportunity_id: str
    app_kind: str
    hq_base: str
    domain: str
    cc_app_id: str
    build_id: str
    hq_username: str


def target_for(session, opportunity_id: str, app_kind: str) -> AppTarget:
    """The app to run for the worker's opportunity, from `service.form_engine_context` (cached, `cache.TARGET_TTL`).

    The opportunity is looked up in the worker's own list (so it must be theirs), a deliver app
    needs a claim (the service refuses otherwise), and the build id comes from HQ. Cached in the
    worker's namespace; only a successful lookup is kept.
    """

    def build() -> dict:
        opp = service.get_opportunity(session, opportunity_id)
        ctx = service.form_engine_context(session, opp, app_kind, with_token=False)
        username = ctx["hq_username"]
        if not username:
            # The PersonalID username is learnt at the first sign-in.
            service.ensure_signed_in(session)
            ctx = service.form_engine_context(session, opp, app_kind, with_token=False)
            username = ctx["hq_username"]
        if not username:
            raise HQError("The worker has no CommCare username yet")
        return AppTarget(
            opportunity_id=str(opp["opportunity_id"]),
            app_kind=app_kind,
            hq_base=ctx["hq_base"],
            domain=ctx["domain"],
            cc_app_id=ctx["app_id"],
            build_id=resolve_build_id(ctx["hq_base"], ctx["domain"], ctx["app_id"]),
            hq_username=username,
        ).__dict__

    return AppTarget(**lcache.cached(session, lcache.target_name(opportunity_id, app_kind), lcache.TARGET_TTL, build))


def forget_target(session, opportunity_id: str, app_kind: str, target: AppTarget | None = None) -> None:
    """Drop the cached target (and, given the target, its build id): after an engine error, so the
    next request re-resolves which build to run."""
    lcache.forget(session, lcache.target_name(opportunity_id, app_kind))
    if target is not None:
        lcache.forget_build(urlsplit(target.hq_base).netloc, target.domain, target.cc_app_id)


# ---------------------------------------------------------------------------
# The signed client
# ---------------------------------------------------------------------------


@dataclass
class Formplayer:
    """One worker running one app: every call is signed and carries the worker's HQ token."""

    target: AppTarget
    hq_token: str = field(repr=False)
    tz_offset_millis: int | None = None
    tz_from_browser: str | None = None

    def _payload(self, extra: dict) -> dict:
        payload: dict[str, Any] = {"username": self.target.hq_username, "domain": self.target.domain}
        if self.tz_offset_millis is not None:
            payload["tz_offset_millis"] = self.tz_offset_millis
        if self.tz_from_browser:
            payload["tz_from_browser"] = self.tz_from_browser
        payload.update(extra)
        for forbidden in ("restoreAs", "restoreAsCaseId"):
            payload.pop(forbidden, None)
        # Whoever the caller says, the user and domain are the server's.
        payload["username"] = self.target.hq_username
        payload["domain"] = self.target.domain
        return payload

    @sensitive_variables("headers")
    def _headers(self, endpoint: str, signed: bytes) -> dict:
        # The worker's HQ server: Formplayer sends restore/install/submit there, not to its
        # configured host (formplayer patch 0004). An EU opportunity lives on eu.commcarehq.org.
        hq_host = hq_origin(self.target.hq_base)
        timestamp = _now()
        return {
            SIGNATURE_HEADER: sign(_key(), signed),
            TIMESTAMP_HEADER: str(timestamp),
            SIGNATURE_V2_HEADER: sign_v2(_key(), "POST", f"/{endpoint}", timestamp, self.hq_token, hq_host, signed),
            TOKEN_HEADER: self.hq_token,
            HQ_HOST_HEADER: hq_host,
        }

    def call(self, endpoint: str, extra: dict | None = None) -> dict:
        """POST one JSON request and return Formplayer's JSON (errors raised, see module docstring)."""
        body = encode(self._payload(extra or {}))
        headers = {"Content-Type": "application/json", **self._headers(endpoint, body)}
        return self._send(endpoint, content=body, headers=headers)

    def call_media(self, extra: dict, *, filename: str, content: bytes, content_type: str) -> dict:
        """``answer_media``: multipart with a signed ``answer`` JSON part and an unsigned ``file`` part."""
        answer = encode(self._payload(extra))
        files = {
            "file": (filename, content, content_type),
            "answer": ("answer.json", answer, "application/json"),
        }
        return self._send("answer_media", files=files, headers=self._headers("answer_media", answer))

    def _send(self, endpoint: str, **kwargs) -> dict:
        url = f"{base_url()}/{endpoint}"
        budget = TIMEOUTS.get(endpoint, DEFAULT_TIMEOUT)
        try:
            response = http.shared_client("formplayer").post(
                url, timeout=httpx.Timeout(budget, connect=CONNECT_TIMEOUT), **kwargs
            )
        except httpx.TimeoutException:
            logger.info("connect_web formplayer.%s: timeout", endpoint)
            raise FormplayerError("The form engine took too long to answer") from None
        except httpx.HTTPError as exc:
            logger.info("connect_web formplayer.%s: %s", endpoint, type(exc).__name__)
            raise FormplayerError(f"The form engine could not be reached ({type(exc).__name__})") from None
        return parse_response(endpoint, response)


def parse_response(endpoint: str, response: httpx.Response) -> dict:
    """Formplayer's JSON, or the error it stands for. Never logs a body."""
    status = response.status_code
    body = http.json_or_none(response)
    if status == 401:
        logger.warning("connect_web formplayer.%s: 401 (signature or token refused)", endpoint)
        raise FormplayerError(status=401, error_code=ENGINE_AUTH_FAILED)
    if status == 423:
        raise FormplayerError(status=423, error_code=ENGINE_BUSY)
    exception = body.get("exception") if isinstance(body, dict) else None
    if isinstance(exception, str) and _SESSION_EXPIRED_MARKER in exception:
        raise FormSessionExpired()
    if not response.is_success:
        logger.info("connect_web formplayer.%s: HTTP %d", endpoint, status)
        raise FormplayerError(status=status, error_code=ENGINE_ERROR, detail=exception or "")
    if not isinstance(body, dict):
        raise FormplayerError("The form engine sent an answer labs couldn't read", status=status)
    if body.get("status") == "error" and isinstance(exception, str):
        logger.info("connect_web formplayer.%s: engine error (%s)", endpoint, body.get("type") or "")
        raise FormplayerError(status=status, error_code=ENGINE_ERROR, detail=exception)
    for key in DROPPED_RESPONSE_KEYS:
        body.pop(key, None)
    return body


def client_for(session, target: AppTarget, *, tz_offset_millis=None, tz_from_browser=None) -> Formplayer:
    """A signed client for `target`, carrying a live HQ token for the worker (cached by the service)."""
    require_configured()
    token = service.hq_token(session, target.domain, target.hq_base)
    return Formplayer(target, token, tz_offset_millis=tz_offset_millis, tz_from_browser=tz_from_browser)


# ---------------------------------------------------------------------------
# Validating what the browser sends
# ---------------------------------------------------------------------------

#: A FormIndex as Formplayer prints it (``FormIndex.toString``): ``2``, ``1,0``, ``3_0,1``, ``4_-10``,
#: with an optional ``J`` (repeat juncture) suffix, plus ``<`` / ``>``.
IX_RE = re.compile(r"^(?:<|>|-?\d{1,4}(?:[_.:]-?\d{1,5})?J?(?:,\s?-?\d{1,4}(?:[_.:]-?\d{1,5})?J?){0,24})$")
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{7,63}$")
#: A menu index (``0``), a case id, ``action 0`` or ``use_selected_values``.
SELECTION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:\- ]{0,127}$")
TZ_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+\-]{0,30}(?:/[A-Za-z0-9_+\-]{1,30}){0,2}$")
LOCALE_RE = re.compile(r"^[A-Za-z0-9_\-]{1,32}$")
_NUM = r"-?\d{1,3}(?:\.\d{1,12})?"
META_LOCATION_RE = re.compile(
    rf"^({_NUM}) ({_NUM})(?: (-?\d{{1,6}}(?:\.\d{{1,6}})?)(?: (\d{{1,7}}(?:\.\d{{1,6}})?))?)?$"
)

MAX_SELECTIONS = 30
MAX_TEXT_ANSWER = 20_000
MAX_LIST_ANSWER = 1_000
MAX_ANSWERS = 5_000
MAX_SEARCH = 200
MAX_CASES_PER_PAGE = 100
MAX_OFFSET = 1_000_000


def clean_ix(value: Any) -> str:
    if not isinstance(value, str) or not IX_RE.match(value.strip()):
        raise InvalidInput("That question isn't one this form has.")
    return value.strip()


def clean_session_id(value: Any) -> str:
    if not isinstance(value, str) or not SESSION_ID_RE.match(value):
        raise InvalidInput("That form session isn't valid.")
    return value


def clean_selections(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_SELECTIONS:
        raise InvalidInput("The menu path isn't valid.")
    out = []
    for item in value:
        if isinstance(item, int) and not isinstance(item, bool):
            item = str(item)
        if not isinstance(item, str) or not SELECTION_RE.match(item):
            raise InvalidInput("The menu path isn't valid.")
        out.append(item)
    return out


def clean_answer(value: Any) -> Any:
    """An answer as Formplayer takes it: null, text, a number, or a list of numbers/text (multi-select, geo)."""
    if value is None or isinstance(value, bool):
        return None if value is None else str(value).lower()
    if isinstance(value, int | float):
        if isinstance(value, float) and value != value:  # NaN
            raise InvalidInput("That answer isn't valid.")
        return value
    if isinstance(value, str):
        if len(value) > MAX_TEXT_ANSWER:
            raise InvalidInput("That answer is too long.")
        return value
    if isinstance(value, list):
        if len(value) > MAX_LIST_ANSWER:
            raise InvalidInput("That answer has too many parts.")
        for item in value:
            if isinstance(item, bool) or not isinstance(item, int | float | str):
                raise InvalidInput("That answer isn't valid.")
            if isinstance(item, str) and len(item) > 200:
                raise InvalidInput("That answer isn't valid.")
        return value
    raise InvalidInput("That answer isn't valid.")


def clean_answers(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or len(value) > MAX_ANSWERS:
        raise InvalidInput("The form's answers aren't valid.")
    return {clean_ix(k): clean_answer(v) for k, v in value.items()}


def clean_int(value: Any, *, low: int, high: int, name: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise InvalidInput(f"{name} isn't valid.")
    try:
        number = int(value)
    except ValueError:
        raise InvalidInput(f"{name} isn't valid.") from None
    if not low <= number <= high:
        raise InvalidInput(f"{name} isn't valid.")
    return number


def clean_search(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str) or len(value) > MAX_SEARCH or any(ord(c) < 32 for c in value):
        raise InvalidInput("The search isn't valid.")
    return value


def clean_meta_location(value: Any) -> str | None:
    """``"lat lon [altitude [accuracy]]"`` (an XForms geopoint), range-checked; None when absent."""
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        raise InvalidInput("The location isn't valid.")
    match = META_LOCATION_RE.match(value.strip())
    if not match:
        raise InvalidInput("The location isn't valid.")
    lat, lon = float(match.group(1)), float(match.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise InvalidInput("The location isn't valid.")
    return value.strip()


def clean_timezone(offset: Any, name: Any) -> tuple[int | None, str | None]:
    tz_offset = clean_int(offset, low=-16 * 3600_000, high=16 * 3600_000, name="The time zone")
    tz_name = None
    if name not in (None, ""):
        if not isinstance(name, str) or not TZ_NAME_RE.match(name):
            raise InvalidInput("The time zone isn't valid.")
        tz_name = name
    return tz_offset, tz_name


def clean_locale(value: Any) -> str:
    if not isinstance(value, str) or not LOCALE_RE.match(value):
        raise InvalidInput("That language isn't valid.")
    return value


#: Image types Formplayer keeps (``MediaValidator`` SUPPORTED_FILE_EXTS), by their magic bytes.
_IMAGE_MAGIC = ((b"\xff\xd8\xff", "image/jpeg", "jpg"), (b"\x89PNG\r\n\x1a\n", "image/png", "png"))


def media_max_bytes() -> int:
    configured = int(getattr(settings, "CONNECT_WEB_MEDIA_MAX_BYTES", FORMPLAYER_MEDIA_MAX_BYTES))
    return min(configured, FORMPLAYER_MEDIA_MAX_BYTES)


def clean_image(content: bytes) -> tuple[str, str]:
    """``(content_type, filename)`` for an uploaded photo; refuses anything but a JPEG or PNG within the limit.

    The type comes from the bytes, never from the browser's name or header.
    """
    if not content:
        raise InvalidInput("The photo is empty.")
    if len(content) > media_max_bytes():
        raise InvalidInput(f"The photo is too large (the limit is {media_max_bytes() // (1024 * 1024)} MB).")
    for magic, content_type, ext in _IMAGE_MAGIC:
        if content.startswith(magic):
            return content_type, f"photo.{ext}"
    raise InvalidInput("Only JPEG or PNG photos can be attached.")


# ---------------------------------------------------------------------------
# The endpoints
# ---------------------------------------------------------------------------


def navigate(fp: Formplayer, selections: list[str], **options) -> dict:
    """The root menu (no selections) or the screen a menu path leads to: menu, case list or form."""
    extra: dict[str, Any] = {"app_id": fp.target.build_id, "preview": False}
    if not selections and not any(v is not None for v in options.values()):
        return fp.call("navigate_menu_start", extra)
    extra["selections"] = selections
    for name in ("offset", "search_text", "cases_per_page", "geo_location"):
        if options.get(name) is not None:
            extra[name] = options[name]
    return fp.call("navigate_menu", extra)


def details(fp: Formplayer, selections: list[str]) -> dict:
    """A case's detail screens (``EntityDetailListResponse``: JSON key ``details``)."""
    return fp.call("get_details", {"app_id": fp.target.build_id, "selections": selections, "isShortDetail": False})


def answer(fp: Formplayer, session_id: str, ix: str, value: Any) -> dict:
    return fp.call("answer", {"session_id": session_id, "ix": ix, "answer": value})


def answer_media(fp: Formplayer, session_id: str, ix: str, *, content: bytes) -> dict:
    content_type, filename = clean_image(content)
    return fp.call_media(
        {"session_id": session_id, "ix": ix, "answer": None},
        filename=filename,
        content=content,
        content_type=content_type,
    )


def new_repeat(fp: Formplayer, session_id: str, ix: str) -> dict:
    return fp.call("new-repeat", {"session_id": session_id, "ix": ix})


def delete_repeat(fp: Formplayer, session_id: str, ix: str) -> dict:
    return fp.call("delete-repeat", {"session_id": session_id, "ix": ix})


def change_locale(fp: Formplayer, session_id: str, locale: str) -> dict:
    return fp.call("change_locale", {"session_id": session_id, "locale": locale})


def submit(fp: Formplayer, session_id: str, answers: dict, meta_location: str | None) -> dict:
    extra: dict[str, Any] = {"session_id": session_id, "answers": answers, "prevalidated": True}
    if meta_location:
        extra["meta_location"] = meta_location
    return fp.call("submit-all", extra)


def sync(fp: Formplayer, worker=None) -> dict:
    """Formplayer ``sync-db``: restore the worker's cases from HQ now. Records when (`cache.mark_synced`)."""
    result = fp.call("sync-db", {"app_id": fp.target.build_id})
    if worker is not None:
        lcache.mark_synced(worker, fp.target.domain)
    return result


def ensure_fresh(fp: Formplayer, worker) -> bool:
    """When the player starts: sync the worker's data first if labs hasn't for `SYNC_EVERY_SECONDS`.

    Formplayer keeps the worker's restored cases between requests and only re-reads HQ when asked,
    so cases created elsewhere (a phone, another tool) would otherwise stay stale until a new app
    build. "Unknown" (a cold cache, or never) counts as stale. Best effort: a failed sync is logged
    and the player carries on. True when a sync ran and succeeded.
    """
    last = lcache.last_synced(worker, fp.target.domain)
    if last is not None and time.time() - last < SYNC_EVERY_SECONDS:
        return False
    try:
        sync(fp, worker)
    except FormplayerError as exc:
        logger.info("connect_web formplayer: sync before start failed (%s); continuing", exc.error_code or exc)
        return False
    return True
