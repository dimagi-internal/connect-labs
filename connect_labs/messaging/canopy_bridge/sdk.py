"""The canopy SDK's new host halves (S1-S6), or labs' own copy of their wire format until they ship.

canopy-web is adding the pieces a host needs to be the SYSTEM OF RECORD for the people it brings
to canopy (``CONTRACTS.md`` S1-S6, canopy SDK ``CONTRACT_VERSION "1"``):

    S1  canopy_sdk.host.relay.RelayClient            relay a person's conversation server-to-server
    S2  canopy_sdk.host.contacts.update_contacts     keep canopy's pointer and profile cache current
    S3  canopy_sdk.attested_context.sign_context     the context labs attests on one message
    S4  canopy_sdk.host.subject.verify_subject       canopy's word that a call is one contact's turn
    S5  canopy_sdk.host.replies.verify_reply         canopy's signed push of an agent's reply
    S6  canopy_sdk.host.contacts.erase_contacts      make canopy forget a person
        site.json ``site.person_scheme``             where a person's record lives

Labs pins ``dimagi-canopy`` 0.29.0, which has none of them. Each name below is the SDK's own when
the installed SDK has it, and otherwise a LOCAL implementation of exactly the wire format
``CONTRACTS.md`` states, built from primitives 0.29.0 already has (``canopy_sdk.jose``,
``canopy_sdk.keys``, ``canopy_sdk.contract``, ``ClientKeyResolver``). ``test_sdk_wire.py`` pins
that format, so the local copy and the SDK cannot drift silently.

**Delete the local fallbacks once the SDK ships them** (every block marked ``LOCAL FALLBACK``):
bump ``dimagi-canopy`` in ``requirements/base.txt``, check ``implementations()`` reports
``"sdk"`` for every name, delete the fallback, and keep the wire tests (they then pin the SDK).
"""

from __future__ import annotations

import importlib
import inspect
import json
import logging
import re
import threading
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from canopy_sdk import contract
from canopy_sdk.contract import ContractError, constant_time_equal, normalize_url
from canopy_sdk.jose import decode, new_jti, sign, unverified_header, verify_dpop_proof
from canopy_sdk.keys import load_private_key, public_jwk, public_key_for

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The wire, as CONTRACTS.md states it. Pinned by test_sdk_wire.py.
# ---------------------------------------------------------------------------

#: S1: the visitor assertion's relay channel claim, its grammar, and the turn origin it may name.
CHANNEL_CLAIM = "canopy_channel"
CHANNEL_PATTERN = r"^[a-z0-9_-]{1,32}$"
RELAY_ORIGIN = "site_message"
#: S1: canopy's contact routes, under its base URL.
CONTACT_SESSIONS_PATH = "/api/contact/sessions"
CONTACT_TURNS_PATH = "/api/contact/turns"
#: S2 / S6: where a host keeps canopy's copy of its people, and makes canopy forget them.
CONTACTS_UPDATE_PATH = "/api/sites/contacts"
CONTACTS_ERASE_PATH = "/api/sites/contacts/erase"
CONTACTS_MEDIA_TYPE = "application/jwt"
CONTACT_UPDATE_TYP = "canopy-contact-update+jwt"
CONTACT_ERASE_TYP = "canopy-contact-erase+jwt"
CONTACTS_MAX_LIFETIME = 60
MAX_CONTACTS = 100
MAX_PROFILE_BYTES = 4096
MAX_PROFILE_KEYS = 40
#: S3: the context a host attests on one send.
CONTEXT_TYP = "canopy-context+jwt"
CONTEXT_MAX_LIFETIME = 60
MAX_CONTEXT_BODY = 16_384
CONTEXT_FORMATS = frozenset({"markdown", "json"})
#: S4: canopy's subject statement.
SUBJECT_AUTH_SCHEME = "CanopySubject"
SUBJECT_TYP = "canopy-subject+jwt"
SUBJECT_MAX_LIFETIME = 60
SUBJECT_REQUIRED_CLAIMS = ("iss", "aud", "iat", "exp", "jti", "cnf", "sub", "site", "agent", "turn", "session")
#: S5: canopy's signed reply push.
REPLY_TYP = "canopy-reply+jwt"
REPLY_MAX_LIFETIME = 300
REPLY_REQUIRED_CLAIMS = ("iss", "aud", "iat", "exp", "jti", "sub", "site", "session", "turn", "seq", "ended")
#: site.json: the scheme of the site's records of its people.
PERSON_SCHEME_FACT = "person_scheme"

REQUEST_TIMEOUT_SECONDS = 15
#: Re-mint a cached contact token this long before canopy says it expires.
REFRESH_BEFORE_SECONDS = 5 * 60


def _sdk(module: str, name: str):
    """``canopy_sdk.<module>.<name>`` when the installed SDK has it, else None."""
    try:
        return getattr(importlib.import_module(f"canopy_sdk.{module}"), name)
    except (ImportError, AttributeError):
        return None


def _sdk_site_has_person_scheme() -> bool:
    facts = _sdk("site", "SITE_FACTS") or ()
    return PERSON_SCHEME_FACT in facts


def implementations() -> dict[str, str]:
    """Which half each name is today: ``"sdk"`` or ``"local"`` (the fallback below)."""
    return {
        "RelayClient": "sdk" if _SDK_RELAY is not None else "local",
        "sign_context": "sdk" if _SDK_SIGN_CONTEXT is not None else "local",
        "update_contacts": "sdk" if _SDK_UPDATE_CONTACTS is not None else "local",
        "erase_contacts": "sdk" if _SDK_ERASE_CONTACTS is not None else "local",
        "verify_subject": "sdk" if _SDK_VERIFY_SUBJECT is not None else "local",
        "verify_reply": "sdk" if _SDK_VERIFY_REPLY is not None else "local",
        "site_document": "sdk" if _sdk_site_has_person_scheme() else "local",
    }


_SDK_SIGN_CONTEXT = _sdk("attested_context", "sign_context")
# The SDK's relay signs host context itself from 0.30 (attested_context); an SDK with a relay
# but no attested context cannot carry the brief, so it is not used.
_SDK_RELAY = _sdk("host.relay", "RelayClient") if _SDK_SIGN_CONTEXT is not None else None
_SDK_UPDATE_CONTACTS = _sdk("host.contacts", "update_contacts")
_SDK_ERASE_CONTACTS = _sdk("host.contacts", "erase_contacts")
_SDK_VERIFY_SUBJECT = _sdk("host.subject", "verify_subject")
_SDK_VERIFY_REPLY = _sdk("host.replies", "verify_reply")


def _kid(key) -> str:
    return public_jwk(key)["kid"]


def _http_post(url: str, body: bytes, *, content_type: str, timeout: float = REQUEST_TIMEOUT_SECONDS):
    """POST raw bytes; ``(status, raw)``. Redirects are not followed. Replaced in tests."""
    import httpx

    response = httpx.post(
        url, content=body, headers={"Content-Type": content_type}, timeout=timeout, follow_redirects=False
    )
    return response.status_code, response.content


# ===========================================================================
# S3 -- the context labs attests on one send
# ===========================================================================


class ContextError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def sign_context(config, *, sub: str, session: str, format: str, body: str, now: float | None = None) -> str:
    """Labs' signed context for ONE send into canopy conversation ``session`` about ``sub``.

    ``typ: canopy-context+jwt``, signed with the site key; claims ``{iss: site, sub, session, iat,
    exp (<=60s), jti, format, body}``; ``body`` at most 16,384 characters."""
    if _SDK_SIGN_CONTEXT is not None:
        error = _sdk("attested_context", "ContextError")
        try:
            return _SDK_SIGN_CONTEXT(
                config.app_name, config.signing_key, sub=sub, session=str(session), format=format, body=body, now=now
            )
        except Exception as exc:
            if error is not None and isinstance(exc, error):
                raise ContextError(exc.code, exc.message) from exc
            raise
    # LOCAL FALLBACK (delete once canopy_sdk.attested_context ships)
    name = str(config.app_name or "").strip()
    if not name:
        raise ContextError("not_configured", "the context needs the site's host-site name")
    if not str(sub or "").strip() or not str(session or "").strip():
        raise ContextError("incomplete", "the context names the person (sub) and the conversation (session)")
    if format not in CONTEXT_FORMATS:
        raise ContextError("bad_format", f"format must be one of {', '.join(sorted(CONTEXT_FORMATS))}")
    if not isinstance(body, str) or not body.strip():
        raise ContextError("bad_body", "the context needs a body")
    if len(body) > MAX_CONTEXT_BODY:
        raise ContextError("too_large", f"the body is limited to {MAX_CONTEXT_BODY} characters")
    key = load_private_key(config.signing_key, what="the site signing key")
    issued = int(time.time() if now is None else now)
    claims = {
        "iss": name,
        "sub": str(sub).strip(),
        "session": str(session).strip(),
        "iat": issued,
        "exp": issued + CONTEXT_MAX_LIFETIME,
        "jti": new_jti(),
        "format": format,
        "body": body,
    }
    return sign(claims, key, headers={"kid": _kid(key), "typ": CONTEXT_TYP})


# ===========================================================================
# S1 -- the relay
# ===========================================================================

#: ``(method, url, json_body_or_None, headers) -> (status, parsed_json_or_text)``
Transport = Callable[[str, str, "dict | None", dict], tuple]


class _LocalRelayError(RuntimeError):
    """canopy refused a relay call, or could not be reached. ``code`` is canopy's reason where it
    gave one (``rate_limited``, ``context_mismatch``, ...), ``status`` its HTTP status."""

    def __init__(self, message: str, *, code: str = "", status: int | None = None):
        super().__init__(message)
        self.code = code
        self.status = status


RelayError = _sdk("host.relay", "RelayError") if _SDK_RELAY is not None else _LocalRelayError


def _httpx_transport(method: str, url: str, body, headers: dict) -> tuple:
    import httpx

    try:
        response = httpx.request(
            method,
            url,
            json=body if body is not None else None,
            headers={"Accept": "application/json", **headers},
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        raise RelayError(f"canopy could not be reached: {type(exc).__name__}") from exc
    try:
        return response.status_code, response.json()
    except ValueError:
        return response.status_code, response.text[:500]


def _code_of(detail: str) -> str:
    head, sep, _ = str(detail or "").partition(":")
    head = head.strip()
    return head if sep and head.isidentifier() else ""


def _expiry(value) -> float:
    import datetime as dt

    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.timestamp()


def _channel_kwargs(channel: str) -> dict:
    """The assertion's channel claim: the SDK's own ``channel=`` where its signer takes one (it
    drops an ``extra`` claim of that name), else as an extra claim (0.29 keeps extras)."""
    from canopy_sdk.host.signing import sign_visitor_assertion

    if "channel" in inspect.signature(sign_visitor_assertion).parameters:
        return {"channel": channel}
    return {"extra": {CHANNEL_CLAIM: channel}}


class _LocalRelayClient:
    """LOCAL FALLBACK for ``canopy_sdk.host.relay.RelayClient`` (delete once it ships).

    The same calls and the same wire: a contact token minted from a visitor assertion that carries
    ``canopy_channel`` and is bound to the agent, then canopy's ``/api/contact/...`` routes with it.
    """

    def __init__(
        self,
        config,
        *,
        channel: str = "",
        agent_slug: str = "",
        transport: Transport | None = None,
        mint: Callable | None = None,
        clock: Callable[[], float] = time.time,
    ):
        from canopy_sdk.host.signing import mint_contact_token

        self.config = config
        self.default_channel = channel
        self.default_agent_slug = agent_slug
        self._transport = transport or _httpx_transport
        self._mint = mint or mint_contact_token
        self._clock = clock
        self._lock = threading.Lock()
        self._tokens: dict[tuple, tuple[str, float]] = {}
        self._last: dict[str, tuple[str, str]] = {}

    def token_for(
        self, sub: str, *, name: str | None = None, channel: str, agent_slug: str, workspace: str | None = None
    ) -> str:
        from canopy_sdk.host.signing import MintFailed, arrival_payload

        sub = str(sub or "").strip()
        if not sub:
            raise ValueError("a relay needs the host's own id for the person")
        if not re.fullmatch(CHANNEL_PATTERN, channel or ""):
            raise ValueError(f"a relay names its channel ({CHANNEL_PATTERN})")
        agent_slug = contract.parse_agent_claim(agent_slug or None)
        if not agent_slug:
            raise ValueError("a relay names the agent the person is talking to")
        key = (sub, channel, agent_slug)
        now = self._clock()
        with self._lock:
            self._last[sub] = (channel, agent_slug)
            cached = self._tokens.get(key)
            if cached and cached[1] - REFRESH_BEFORE_SECONDS > now:
                return cached[0]
        payload = arrival_payload(
            self.config, sub, agent_slug=agent_slug, bind_agent=True, name=name or "", **_channel_kwargs(channel)
        )
        try:
            minted = self._mint(self.config, payload)
        except MintFailed as exc:
            raise RelayError(str(exc), code=_code_of(str(exc).partition(": ")[2]), status=exc.status) from exc
        token = str(minted["token"])
        with self._lock:
            self._tokens[key] = (token, _expiry(minted.get("expires_at")))
        return token

    def forget(self, sub: str) -> None:
        with self._lock:
            for key in [k for k in self._tokens if k[0] == sub]:
                del self._tokens[key]
            self._last.pop(sub, None)

    def _call(self, method: str, path: str, sub: str, body=None):
        last = self._last.get(sub) or (self.default_channel, self.default_agent_slug)
        if not all(last):
            raise RelayError("no token for this person: call token_for (or open_session) first", code="no_token")
        url = normalize_url(self.config.canopy_base_url) + path
        for attempt in (0, 1):
            token = self.token_for(sub, channel=last[0], agent_slug=last[1])
            status, data = self._transport(method, url, body, {"Authorization": f"Bearer {token}"})
            if status == 401 and attempt == 0:
                with self._lock:
                    for key in [k for k, v in self._tokens.items() if v[0] == token]:
                        del self._tokens[key]
                continue
            break
        if not 200 <= int(status) < 300:
            detail = data.get("detail") if isinstance(data, Mapping) else data
            detail = str(detail or f"HTTP {status}")[:500]
            raise RelayError(f"canopy returned {status}: {detail}", code=_code_of(detail), status=int(status))
        return data

    def open_session(
        self, sub: str, *, agent_slug: str, title: str = "", metadata: Mapping | None = None, channel: str
    ) -> str:
        self.token_for(sub, channel=channel, agent_slug=agent_slug)
        data = self._call(
            "POST",
            CONTACT_SESSIONS_PATH,
            sub,
            {"agent_slug": agent_slug, "title": title or "", "metadata": dict(metadata or {})},
        )
        return str(data["id"])

    def sign_context(self, session_id: str, sub: str, context: Mapping) -> str:
        fmt = str(context.get("format") or "markdown")
        body = context.get("body")
        if fmt == "json" and not isinstance(body, str):
            body = json.dumps(body)
        try:
            return sign_context(
                self.config, sub=sub, session=str(session_id), format=fmt, body=body, now=self._clock()
            )
        except ContextError as exc:
            raise RelayError(exc.message, code=exc.code) from exc

    def send(
        self, session_id: str, sub: str, text: str, *, context: Mapping | None = None, client_id: str | None = None
    ) -> dict:
        import urllib.parse

        body: dict = {"text": text, "origin": RELAY_ORIGIN}
        if client_id:
            body["client_id"] = str(client_id)
        if context is not None:
            body["context"] = self.sign_context(session_id, sub, context)
        return self._call(
            "POST", f"{CONTACT_SESSIONS_PATH}/{urllib.parse.quote(str(session_id), safe='')}/send", sub, body
        )

    def turn(self, turn_id: str, sub: str) -> dict:
        import urllib.parse

        return self._call("GET", f"{CONTACT_TURNS_PATH}/{urllib.parse.quote(str(turn_id), safe='')}", sub)

    def messages(self, session_id: str, sub: str, after: int | None = None) -> list[dict]:
        import urllib.parse

        out: list[dict] = []
        before = 2**31 - 1
        while True:
            query = urllib.parse.urlencode({"before": before, "limit": 200})
            sid = urllib.parse.quote(str(session_id), safe="")
            page = self._call("GET", f"{CONTACT_SESSIONS_PATH}/{sid}/messages?{query}", sub) or {}
            rows = list(page.get("messages") or [])
            fresh = [m for m in rows if after is None or int(m.get("turn_index", -1)) > after]
            out = fresh + out
            if not page.get("has_more_before") or not rows or len(fresh) < len(rows):
                break
            before = min(int(m.get("turn_index", 0)) for m in rows)
        return out


RelayClient = _SDK_RELAY if _SDK_RELAY is not None else _LocalRelayClient


# ===========================================================================
# S2 / S6 -- the contact record and erasure
# ===========================================================================


class _LocalContactRecordError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


ContactRecordError = _sdk("contacts", "ContactRecordError") or _LocalContactRecordError


def check_profile(profile: Mapping) -> dict:
    """A host profile canopy keeps: a JSON object, at most 40 keys and 4,096 bytes serialized."""
    if not isinstance(profile, Mapping):
        raise ContactRecordError("bad_profile", "profile must be a JSON object")
    if len(profile) > MAX_PROFILE_KEYS:
        raise ContactRecordError("profile_too_large", f"profile has more than {MAX_PROFILE_KEYS} keys")
    size = len(json.dumps(profile, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode())
    if size > MAX_PROFILE_BYTES:
        raise ContactRecordError("profile_too_large", f"profile is {size} bytes; at most {MAX_PROFILE_BYTES}")
    return dict(profile)


def _contacts_token(config, audience: str, typ: str, body: dict, *, now: float | None = None) -> str:
    name = str(config.app_name or "").strip()
    if not name:
        raise ContactRecordError("not_configured", "the request needs the site's host-site name")
    key = load_private_key(config.signing_key, what="the site signing key")
    issued = int(time.time() if now is None else now)
    claims = {
        "iss": name,
        "aud": audience,
        "iat": issued,
        "exp": issued + CONTACTS_MAX_LIFETIME,
        "jti": new_jti(),
        **body,
    }
    workspace = contract.parse_workspace_claim(getattr(config, "workspace", "") or None)
    if workspace:
        claims[contract.WORKSPACE_CLAIM] = workspace
    return sign(claims, key, headers={"kid": _kid(key), "typ": typ})


def contact_update_token(config, contacts: Sequence[Mapping], *, now: float | None = None) -> str:
    """LOCAL FALLBACK: the signed S2 request body."""
    items = list(contacts)
    if not items or len(items) > MAX_CONTACTS:
        raise ContactRecordError("too_many", f"1 to {MAX_CONTACTS} contacts per request")
    out = []
    for item in items:
        extra = set(item) - {"sub", "display_name", "record_uri", "profile", "version"}
        if extra:
            raise ContactRecordError("malformed", f"unknown key(s) {sorted(extra)}")
        version = item.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise ContactRecordError("bad_version", "version must be a whole number of at least 1")
        clean = {k: v for k, v in item.items() if v is not None}
        if "profile" in clean:
            clean["profile"] = check_profile(clean["profile"])
        out.append(clean)
    audience = normalize_url(config.canopy_base_url) + CONTACTS_UPDATE_PATH
    return _contacts_token(config, audience, CONTACT_UPDATE_TYP, {"contacts": out}, now=now)


def contact_erase_token(config, subs: Iterable[str], *, now: float | None = None) -> str:
    """LOCAL FALLBACK: the signed S6 request body."""
    unique = list(dict.fromkeys(str(s) for s in subs if s))
    if not unique or len(unique) > MAX_CONTACTS:
        raise ContactRecordError("too_many", f"1 to {MAX_CONTACTS} subs per request")
    audience = normalize_url(config.canopy_base_url) + CONTACTS_ERASE_PATH
    return _contacts_token(config, audience, CONTACT_ERASE_TYP, {"subs": unique}, now=now)


def _post_contacts(url: str, token: str, post) -> dict:
    send = post or (lambda u, b, content_type: _http_post(u, b, content_type=content_type)[:2])
    try:
        status, raw = send(url, token.encode("ascii"), content_type=CONTACTS_MEDIA_TYPE)[:2]
    except Exception as exc:  # noqa: BLE001 -- unreachable, whatever the transport raised
        raise ContactRecordError("unreachable", f"canopy could not be reached: {type(exc).__name__}") from exc
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        body = {}
    if status >= 400:
        detail = str((body or {}).get("detail") or f"HTTP {status}") if isinstance(body, dict) else f"HTTP {status}"
        raise ContactRecordError(_code_of(detail) or "refused", detail)
    return body if isinstance(body, dict) else {}


def update_contacts(config, contacts: Sequence[Mapping], *, post: Callable | None = None) -> dict:
    """S2: ``{sub, display_name?, record_uri?, profile?, version}`` items (at most 100) to canopy.
    Returns ``{"results": [{sub, contact_id, status, reason?}]}``. ``post(url, body, content_type=)``
    returns ``(status, raw_body, ...)``; tests pass one."""
    if _SDK_UPDATE_CONTACTS is not None:
        return _SDK_UPDATE_CONTACTS(config, contacts, post=post)
    # LOCAL FALLBACK (delete once canopy_sdk.host.contacts ships)
    if not (config.canopy_base_url and config.app_name):
        raise ContactRecordError("not_configured", "the host config needs canopy_base_url and app_name")
    url = normalize_url(config.canopy_base_url) + CONTACTS_UPDATE_PATH
    return _post_contacts(url, contact_update_token(config, contacts), post)


def erase_contacts(config, subs: Iterable[str], *, post: Callable | None = None) -> dict:
    """S6: make canopy forget up to 100 people. Returns ``{"results": [{sub, status}]}``."""
    if _SDK_ERASE_CONTACTS is not None:
        return _SDK_ERASE_CONTACTS(config, subs, post=post)
    # LOCAL FALLBACK (delete once canopy_sdk.host.contacts ships)
    if not (config.canopy_base_url and config.app_name):
        raise ContactRecordError("not_configured", "the host config needs canopy_base_url and app_name")
    url = normalize_url(config.canopy_base_url) + CONTACTS_ERASE_PATH
    return _post_contacts(url, contact_erase_token(config, subs), post)


# ===========================================================================
# Verifying canopy's statements (S4, S5): shared pieces
# ===========================================================================


def _headers(request) -> Mapping:
    headers = getattr(request, "headers", None)
    if headers is None and isinstance(request, Mapping):
        headers = request
    if headers is None:
        raise ValueError("no headers")
    return headers


def _header(headers: Mapping, name: str) -> str:
    value = headers.get(name)
    if value is None and hasattr(headers, "items"):
        for k, v in headers.items():
            if str(k).lower() == name.lower():
                return str(v)
    return str(value or "")


def _client_keys(client_keys):
    if client_keys is not None:
        return client_keys
    from canopy_sdk.host.client_keys import ClientKeyResolver

    return ClientKeyResolver()


def _canopy_key(client_keys, client_id: str, token: str, typ: str):
    """The verified-against key: canopy's client key its metadata names by ``kid``."""
    header = unverified_header(token)
    if header.get("typ") != typ:
        raise ContractError("bad_typ", f"not a {typ} statement")
    jwk = client_keys.key_for(client_id, header.get("kid"))
    return public_key_for(jwk, header["alg"]), header["alg"]


# ===========================================================================
# S4 -- canopy's subject statement on a call to labs' contact-scoped tools
# ===========================================================================


class _LocalSubjectRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


SubjectRefused = _sdk("host.subject", "SubjectRefused") or _LocalSubjectRefused


@dataclass(frozen=True)
class _LocalSubject:
    site: str
    sub: str
    agent: str
    turn: str
    session: str
    capability: str
    claims: dict = field(repr=False, default_factory=dict)

    @property
    def jkt(self) -> str:
        cnf = self.claims.get("cnf")
        return str(cnf.get("jkt") or "") if isinstance(cnf, Mapping) else ""


Subject = _sdk("host.subject", "Subject") or _LocalSubject


def verify_subject(
    request,
    *,
    config,
    client_keys=None,
    replay_store=None,
    method: str | None = None,
    rpc: tuple[str, Mapping] | None = None,
    now: float | None = None,
):
    """S4: verify ``Authorization: CanopySubject <jws>`` + ``DPoP`` on ``request`` (anything with
    ``.headers``, or a header mapping). ``config`` names ``canopy_client_id`` and ``resource`` (the
    messaging MCP endpoint). Returns ``Subject`` or raises ``SubjectRefused``. The host then
    authorizes EVERY call against ``Subject.sub``."""
    if _SDK_VERIFY_SUBJECT is not None:
        return _SDK_VERIFY_SUBJECT(
            request, config=config, client_keys=client_keys, replay_store=replay_store, method=method, rpc=rpc, now=now
        )
    # LOCAL FALLBACK (delete once canopy_sdk.host.subject ships)
    from canopy_sdk.host.client_keys import MetadataError
    from canopy_sdk.stores import Replayed

    try:
        headers = _headers(request)
    except ValueError as exc:
        raise SubjectRefused("no_statement", "the request carries no headers") from exc
    scheme, _, raw = _header(headers, "Authorization").partition(" ")
    raw = raw.strip()
    if scheme.lower() != SUBJECT_AUTH_SCHEME.lower() or not raw or "," in raw or " " in raw:
        raise SubjectRefused("no_statement", f"send exactly one Authorization: {SUBJECT_AUTH_SCHEME}")
    client_id = getattr(config, "canopy_client_id", "")
    resource = getattr(config, "resource", "")
    if not client_id or not resource:
        raise SubjectRefused("not_configured", "this host trusts no canopy (canopy_client_id, resource)")
    keys = _client_keys(client_keys)
    try:
        key, alg = _canopy_key(keys, client_id, raw, SUBJECT_TYP)
        claims = decode(
            raw,
            key,
            algorithms=[alg],
            audience=[resource],
            required=SUBJECT_REQUIRED_CLAIMS,
            leeway=contract.LEEWAY_SECONDS,
        )
        contract.check_lifetime(claims, SUBJECT_MAX_LIFETIME, what="a subject statement")
        jti = contract.check_jti(claims)
    except MetadataError as exc:
        raise SubjectRefused("client_metadata", "canopy's keys could not be read") from exc
    except ContractError as exc:
        raise SubjectRefused(exc.code, exc.message) from exc
    if not constant_time_equal(str(claims.get("iss") or ""), client_id):
        raise SubjectRefused("bad_issuer", "the statement is not from the canopy this host trusts")
    for name in ("sub", "site", "agent", "turn", "session"):
        if not isinstance(claims.get(name), str) or not claims[name]:
            raise SubjectRefused("missing_claim", f"the statement names no {name}")
    cnf = claims.get("cnf") if isinstance(claims.get("cnf"), Mapping) else {}
    bound = str(cnf.get("jkt") or "")
    proof = _header(headers, contract.DPOP_HEADER)
    if not proof or "," in proof:
        raise SubjectRefused("no_proof", "send exactly one DPoP proof with a subject statement")
    http_method = method or getattr(request, "method", None) or "POST"
    try:
        jkt, proof_jti, proof_iat = verify_dpop_proof(proof, htm=http_method, htu=resource, access_token=None, now=now)
    except ContractError as exc:
        raise SubjectRefused("bad_proof", exc.message) from exc
    if not bound or not constant_time_equal(bound, jkt):
        raise SubjectRefused("unbound", "the statement is not bound to the key proved on this request")
    if rpc is not None and not contract.rpc_matches(claims.get("rpc"), *rpc):
        raise SubjectRefused("wrong_request", "the statement is for a different request")
    store = replay_store
    if store is None:
        from canopy_sdk.stores import MemoryJtiStore

        store = _MEMORY_JTIS.setdefault("store", MemoryJtiStore())
    try:
        store.consume(
            [
                (f"subject:{client_id}", jti, int(claims["exp"]) + contract.LEEWAY_SECONDS),
                (f"dpop:{jkt}", proof_jti, proof_iat + contract.DPOP_IAT_WINDOW_SECONDS),
            ]
        )
    except Replayed as exc:
        raise SubjectRefused("replayed", "this statement or proof has already been used") from exc
    except Exception as exc:  # noqa: BLE001 -- a store that cannot answer fails CLOSED
        raise SubjectRefused("jti_store_error", "the statement could not be recorded as used") from exc
    return Subject(
        site=str(claims["site"]),
        sub=str(claims["sub"]),
        agent=str(claims["agent"]),
        turn=str(claims["turn"]),
        session=str(claims["session"]),
        capability=str(claims.get("capability") or ""),
        claims=dict(claims),
    )


_MEMORY_JTIS: dict = {}


# ===========================================================================
# S5 -- canopy's reply push
# ===========================================================================


class ReplyRefused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Reply:
    site: str
    sub: str
    session: str
    turn: str
    seq: int
    text: str
    ended: bool
    error: str = ""
    claims: dict = field(repr=False, default_factory=dict)


def _as_reply(value) -> Reply:
    if isinstance(value, Reply):
        return value
    claims = dict(getattr(value, "claims", {}) or {})
    return Reply(
        site=str(getattr(value, "site", claims.get("site", ""))),
        sub=str(getattr(value, "sub", claims.get("sub", ""))),
        session=str(getattr(value, "session", claims.get("session", ""))),
        turn=str(getattr(value, "turn", claims.get("turn", ""))),
        seq=int(getattr(value, "seq", claims.get("seq", 0)) or 0),
        text=str(getattr(value, "text", claims.get("text", "")) or ""),
        ended=bool(getattr(value, "ended", claims.get("ended", False))),
        error=str(getattr(value, "error", claims.get("error", "")) or ""),
        claims=claims,
    )


def verify_reply(token: str, *, config, audience: str, client_keys=None, replay_store=None, now: float | None = None):
    """S5: verify canopy's ``canopy-reply+jwt`` push. ``audience`` is labs' reply webhook URL (the
    ``aud`` canopy signs). Returns ``Reply`` or raises ``ReplyRefused``. Duplicate deliveries of
    one ``(turn, seq)`` are the caller's to collapse."""
    if _SDK_VERIFY_REPLY is not None:
        params = inspect.signature(_SDK_VERIFY_REPLY).parameters
        offered = {"audience": audience, "client_keys": client_keys, "replay_store": replay_store, "now": now}
        kwargs = {k: v for k, v in offered.items() if k in params and v is not None}
        refused = _sdk("host.replies", "ReplyRefused")
        try:
            return _as_reply(_SDK_VERIFY_REPLY(token, config=config, **kwargs))
        except Exception as exc:
            if refused is not None and isinstance(exc, refused):
                raise ReplyRefused(getattr(exc, "code", "refused"), str(exc)) from exc
            raise
    # LOCAL FALLBACK (delete once canopy_sdk.host.replies ships)
    from canopy_sdk.host.client_keys import MetadataError
    from canopy_sdk.stores import Replayed

    client_id = getattr(config, "canopy_client_id", "")
    if not client_id or not audience:
        raise ReplyRefused("not_configured", "this host trusts no canopy, or has no reply webhook")
    if not isinstance(token, str) or not token or len(token) > 64 * 1024:
        raise ReplyRefused("malformed", "not a compact JWS of acceptable size")
    keys = _client_keys(client_keys)
    try:
        import jwt

        header = jwt.get_unverified_header(token)
        if header.get("alg") not in contract.GRANT_ALGORITHMS:
            raise ContractError("bad_alg", f"algorithm {header.get('alg')!r} is not accepted")
        if header.get("typ") != REPLY_TYP:
            raise ContractError("bad_typ", f"not a {REPLY_TYP} statement")
        jwk = keys.key_for(client_id, header.get("kid"))
        key = public_key_for(jwk, header["alg"])
        claims = decode(
            token,
            key,
            algorithms=[header["alg"]],
            audience=[audience],
            required=REPLY_REQUIRED_CLAIMS,
            leeway=contract.LEEWAY_SECONDS,
        )
        contract.check_lifetime(claims, REPLY_MAX_LIFETIME, what="a reply")
        jti = contract.check_jti(claims)
    except MetadataError as exc:
        raise ReplyRefused("client_metadata", "canopy's keys could not be read") from exc
    except jwt.PyJWTError as exc:
        raise ReplyRefused("malformed", "not a compact JWS") from exc
    except ContractError as exc:
        raise ReplyRefused(exc.code, exc.message) from exc
    if not constant_time_equal(str(claims.get("iss") or ""), client_id):
        raise ReplyRefused("bad_issuer", "the reply is not from the canopy this host trusts")
    for name in ("sub", "site", "session", "turn"):
        if not isinstance(claims.get(name), str) or not claims[name]:
            raise ReplyRefused("missing_claim", f"the reply names no {name}")
    seq = claims.get("seq")
    if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
        raise ReplyRefused("bad_seq", "seq must be a whole number")
    if not isinstance(claims.get("ended"), bool):
        raise ReplyRefused("bad_ended", "ended must be true or false")
    if not isinstance(claims.get("text", ""), str):
        raise ReplyRefused("bad_text", "text must be a string")
    if replay_store is not None:
        try:
            replay_store.consume([(f"reply:{client_id}", jti, int(claims["exp"]) + contract.LEEWAY_SECONDS)])
        except Replayed as exc:
            raise ReplyRefused("replayed", "this reply has already been used") from exc
        except Exception as exc:  # noqa: BLE001 -- a store that cannot answer fails CLOSED
            raise ReplyRefused("jti_store_error", "the reply could not be recorded as used") from exc
    return Reply(
        site=claims["site"],
        sub=claims["sub"],
        session=claims["session"],
        turn=claims["turn"],
        seq=seq,
        text=claims.get("text") or "",
        ended=claims["ended"],
        error=str(claims.get("error") or ""),
        claims=dict(claims),
    )


# ===========================================================================
# site.json with site.person_scheme
# ===========================================================================


def site_document_token(
    config,
    *,
    person_scheme: str,
    site: Mapping,
    title: str = "",
    pages: Sequence = (),
    scope_labels: Mapping | None = None,
    now: float | None = None,
) -> str:
    """The site's signed ``site.json`` (``typ: canopy-site+jwt``) declaring ``site.person_scheme``.

    An SDK whose ``site`` block knows ``person_scheme`` builds it whole; 0.29's refuses the key, so
    the LOCAL FALLBACK builds the rest with the SDK and adds the scheme (checked like a resource
    scheme) before signing."""
    from canopy_sdk.resources import ResourceError, check_scheme
    from canopy_sdk.site import sign_site_document
    from canopy_sdk.site import site_document as build

    try:
        scheme = check_scheme(person_scheme)
    except ResourceError as exc:
        raise ValueError(f"person_scheme: {exc.message}") from exc
    if _sdk_site_has_person_scheme():
        document = build(
            config.app_name,
            pages=pages,
            site={**dict(site), PERSON_SCHEME_FACT: scheme},
            title=title,
            scope_labels=scope_labels,
            now=now,
        )
        return sign_site_document(config.signing_key, document)
    # LOCAL FALLBACK (delete once the SDK's site block takes person_scheme, SDK 0.30+)
    document = build(config.app_name, pages=pages, site=site, title=title, scope_labels=scope_labels, now=now)
    document["site"] = {**document.get("site", {}), PERSON_SCHEME_FACT: scheme}
    return sign_site_document(config.signing_key, document)
