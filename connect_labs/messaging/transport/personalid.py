"""PersonalID messaging: labs as a connect-id message server.

Ported from open-chat-studio's ``apps/channels/clients/connect_client.py`` and checked
against connect-id's ``messaging/views.py``:

* Every call we make authenticates with HTTP Basic ``client_id:secret`` (connect-id's
  ``MessagingServerAuth``).
* ``POST /messaging/create_channel/ {connectid, channel_source, channel_name}`` makes or
  finds the worker's channel for that source (201 new, 200 existing) and answers
  ``{channel_id, consent}``.
* ``POST /messaging/create_message/`` is multipart: a ``message`` JSON part plus one part per
  attachment. connect-id checks the channel is OURS before storing. We never use
  ``send_fcm``: it looks the channel up without checking who owns it.
* A retried send carries the same ``message_id``; ``MESSAGE_ID_ALREADY_EXISTS`` therefore
  means an earlier attempt arrived, and counts as success.
* The worker's phone fetches the channel key from us with its PersonalID bearer token, which
  we check with ``GET /o/userinfo/`` (``userinfo_sub``).

The HTTP transport is swappable (``HTTP_TRANSPORT``) so tests run against a fake connect-id.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import httpx
from django.conf import settings
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from connect_labs.messaging import crypto
from connect_labs.messaging.transport.base import (
    ChannelGone,
    ConsentRefused,
    Transport,
    TransportError,
    TransportUnavailable,
    register_transport,
)

logger = logging.getLogger(__name__)

MESSAGE_ID_ALREADY_EXISTS = "MESSAGE_ID_ALREADY_EXISTS"
NO_USER_CONSENT = "NO_USER_CONSENT"
CHANNEL_DOES_NOT_EXIST = "CHANNEL_DOES_NOT_EXIST"
RICH_MESSAGING_DISABLED = "RICH_MESSAGING_DISABLED"

#: The message format version we are written for (connect-id's ``X-Messaging-Version``).
MESSAGING_VERSION = "2"

LEGACY_APP_MESSAGE = (
    "This message contains attachments but your version of the app is too old to see them. "
    "Please update your Android app."
)
MAX_ATTACHMENTS_PER_MESSAGE = 10

_TIMEOUT = 10
_UPLOAD_TIMEOUT = httpx.Timeout(_TIMEOUT, read=60)

#: Tests put an ``httpx.MockTransport`` here (``tests/fake_connectid.py``).
HTTP_TRANSPORT: httpx.BaseTransport | None = None

_RETRY = dict(
    wait=wait_exponential(multiplier=1, min=1, max=5),
    reraise=True,
    retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
    stop=stop_after_attempt(3),
    before_sleep=before_sleep_log(logger, logging.INFO),
)


class NotConfigured(TransportError):
    pass


@dataclass(frozen=True)
class OutgoingAttachment:
    name: str
    content_type: str
    content: bytes = field(repr=False)


def _error_code(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return ""
    return body.get("errors", "") if isinstance(body, dict) and isinstance(body.get("errors"), str) else ""


def _raise_for(response: httpx.Response, what: str) -> None:
    if response.status_code < 400:
        return
    code = _error_code(response)
    if code == NO_USER_CONSENT:
        raise ConsentRefused(code, f"{what}: the Connect Worker has turned this channel off")
    if code == CHANNEL_DOES_NOT_EXIST:
        raise ChannelGone(code, f"{what}: PersonalID has no such channel of ours")
    if response.status_code >= 500 or response.status_code == 429:
        raise TransportUnavailable(
            f"http_{response.status_code}", f"{what}: PersonalID answered {response.status_code}"
        )
    raise TransportError(
        code or f"http_{response.status_code}", f"{what}: PersonalID refused ({code or response.status_code})"
    )


class PersonalIDClient:
    def __init__(self, base_url: str | None = None, client_id: str | None = None, secret: str | None = None):
        base_url = base_url or settings.PERSONALID_SERVER_URL
        client_id = client_id or settings.PERSONALID_MESSAGING_CLIENT_ID
        secret = secret or settings.PERSONALID_MESSAGING_CLIENT_SECRET
        if not (base_url and client_id and secret):
            raise NotConfigured(
                "not_configured",
                "PERSONALID_MESSAGING_CLIENT_ID and PERSONALID_MESSAGING_CLIENT_SECRET must be set",
            )
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(auth=httpx.BasicAuth(client_id, secret), timeout=_TIMEOUT, transport=HTTP_TRANSPORT)

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @retry(**_RETRY)
    def _post(self, path: str, **kwargs) -> httpx.Response:
        return self.client.post(f"{self.base_url}{path}", **kwargs)

    def create_channel(self, connectid: str, channel_source: str, channel_name: str | None = None) -> dict:
        """Make (or find) the worker's channel for ``channel_source``. Returns ``{channel_id, consent}``."""
        data = {"connectid": connectid, "channel_source": channel_source}
        if channel_name:
            data["channel_name"] = channel_name
        try:
            response = self._post("/messaging/create_channel/", json=data)
        except (httpx.NetworkError, httpx.TimeoutException) as e:
            raise TransportUnavailable("unreachable", f"create_channel: {type(e).__name__}") from e
        if response.status_code == 404:
            raise TransportError("no_such_user", "create_channel: PersonalID has no user by that username")
        _raise_for(response, "create_channel")
        body = response.json()
        return {"channel_id": str(body["channel_id"]), "consent": bool(body.get("consent", True))}

    def create_message(
        self,
        *,
        channel_id: str,
        message_id: str,
        encryption_key: str,
        text: str,
        attachments: Sequence[OutgoingAttachment] = (),
        expires_at=None,
    ) -> None:
        """Send one message. Encrypted once, outside the retried request, so a retry sends the same bytes."""
        if len(attachments) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise TransportError("too_many_attachments", f"at most {MAX_ATTACHMENTS_PER_MESSAGE} attachments")
        message: dict = {"channel": channel_id, "message_id": message_id}
        if text:
            message["content"] = crypto.encrypt_text(encryption_key, text)
        parts = []
        if attachments:
            legacy = f"{text}\n\n{LEGACY_APP_MESSAGE}" if text else LEGACY_APP_MESSAGE
            message["content_legacy_msg"] = crypto.encrypt_text(encryption_key, legacy)
            message["attachments"] = []
            for index, attachment in enumerate(attachments):
                blob = crypto.encrypt_attachment(encryption_key, attachment.content)
                message["attachments"].append(
                    {"name": attachment.name, "type": attachment.content_type, "size": len(blob)}
                )
                part = f"attachment_{index}"
                parts.append((part, (part, blob, "application/octet-stream")))
        if expires_at is not None:
            message["expires_at"] = expires_at.isoformat()
        # No filename on the JSON part, so PersonalID's multipart parser reads it as a field.
        files = [("message", (None, json.dumps(message).encode(), "application/json")), *parts]
        try:
            response = self._post(
                "/messaging/create_message/",
                files=files,
                headers={"X-Messaging-Version": MESSAGING_VERSION},
                timeout=_UPLOAD_TIMEOUT if attachments else _TIMEOUT,
            )
        except (httpx.NetworkError, httpx.TimeoutException) as e:
            raise TransportUnavailable("unreachable", f"create_message: {type(e).__name__}") from e
        if response.status_code == 400 and _error_code(response) == MESSAGE_ID_ALREADY_EXISTS:
            logger.info("PersonalID already has message %s; an earlier attempt arrived", message_id)
            return
        _raise_for(response, "create_message")


def userinfo_sub(authorization: str) -> str | None:
    """Who a phone's PersonalID bearer token belongs to (its ``sub``), or None if PersonalID refuses it."""
    if not authorization:
        return None
    try:
        with httpx.Client(timeout=_TIMEOUT, transport=HTTP_TRANSPORT) as client:
            response = client.get(
                f"{settings.PERSONALID_SERVER_URL.rstrip('/')}/o/userinfo/", headers={"Authorization": authorization}
            )
    except httpx.HTTPError:
        logger.warning("PersonalID userinfo could not be reached")
        return None
    if response.status_code != 200:
        return None
    try:
        sub = response.json().get("sub")
    except ValueError:
        return None
    return str(sub) if sub else None


class PersonalIDTransport(Transport):
    name = "personalid"

    def ensure_channel(self, address) -> None:
        with PersonalIDClient() as client:
            result = client.create_channel(
                address.worker.username, address.channel_source, address.channel_name or None
            )
        address.channel_id = result["channel_id"]
        if not address.key:
            address.key = crypto.new_key()
        if address.consent != result["consent"] or not address.consent_history:
            address.record_consent(result["consent"], source="create_channel")
        address.save()

    def send(self, message) -> None:
        address = message.thread.address
        if address is None or not address.channel_id:
            raise ChannelGone("no_channel", "this thread has no PersonalID channel")
        # Text only for now: labs does not keep attachment bytes yet (the client can send them).
        with PersonalIDClient() as client:
            client.create_message(
                channel_id=address.channel_id,
                message_id=message.transport_message_id,
                encryption_key=address.key,
                text=message.text,
            )


register_transport(PersonalIDTransport())
