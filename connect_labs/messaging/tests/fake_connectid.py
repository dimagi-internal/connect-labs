"""A fake connect-id, for tests: the parts of its messaging API labs uses, behaving as connect-id does.

Mirrors `messaging/views.py` and `messaging/rich_messages.py` in connect-id:

* ``create_channel`` (Basic auth as a message server) is idempotent per (user, channel_source):
  201 the first time, 200 after, answering ``{channel_id, consent}``; 404 for an unknown user.
* ``create_message`` (multipart) refuses a channel that is not this server's
  (``CHANNEL_DOES_NOT_EXIST``), a channel without consent (``NO_USER_CONSENT``) and a repeated
  message id (``MESSAGE_ID_ALREADY_EXISTS``), each a 400 with ``{"errors": code}``.
* ``send_fcm`` exists and must never be called: labs does not use it.
* ``GET /o/userinfo/`` answers ``{"sub": username}`` for a known bearer token, else 401.

It also plays the phone and connect-id's outbound calls to labs: ``reply`` signs and posts a
worker's message to labs' delivery endpoint exactly as connect-id would, ``set_consent`` and
``receipt`` do the same for consent and callback.
"""

from __future__ import annotations

import base64
import json
import uuid
from email import policy
from email.parser import BytesParser

import httpx

from connect_labs.messaging import crypto
from connect_labs.messaging.personalid_views import MAC_HEADER, mac_digest

CLIENT_ID = "labs-test-server"
SECRET = "labs-test-secret"


class FakeConnectID:
    def __init__(self):
        self.users: set[str] = set()
        self.tokens: dict[str, str] = {}
        self.channels: dict[str, dict] = {}
        self.messages: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.fail_next: list[httpx.Response | Exception] = []
        self.other_servers_channels: set[str] = set()

    # --- setup ---------------------------------------------------------------------------

    def add_user(self, username: str, token: str | None = None) -> str:
        self.users.add(username)
        token = token or f"token-{username}"
        self.tokens[token] = username
        return token

    def channel_for(self, username: str) -> dict | None:
        return next((c for c in self.channels.values() if c["username"].lower() == username.lower()), None)

    # --- the HTTP side -------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if self.fail_next:
            failure = self.fail_next.pop(0)
            if isinstance(failure, Exception):
                raise failure
            return failure
        if path == "/o/userinfo/":
            token = (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
            username = self.tokens.get(token)
            if username is None:
                return httpx.Response(401, json={"error": "invalid_token"})
            return httpx.Response(200, json={"sub": username})
        if not self._authorised(request):
            return httpx.Response(401, json={"detail": "Authentication credentials were not provided."})
        if path == "/messaging/create_channel/":
            return self._create_channel(json.loads(request.content))
        if path == "/messaging/create_message/":
            return self._create_message(request)
        if path == "/messaging/send_fcm/":
            raise AssertionError("labs must never call send_fcm: it does not check who owns the channel")
        return httpx.Response(404)

    def _authorised(self, request) -> bool:
        auth = request.headers.get("Authorization") or ""
        if not auth.startswith("Basic "):
            return False
        return base64.b64decode(auth[6:]).decode() == f"{CLIENT_ID}:{SECRET}"

    def _create_channel(self, data: dict) -> httpx.Response:
        username = next((u for u in self.users if u.lower() == str(data["connectid"]).lower()), None)
        if username is None:
            return httpx.Response(404, json={"detail": "Not found."})
        for channel_id, channel in self.channels.items():
            if channel["username"] == username and channel["source"] == data["channel_source"]:
                if data.get("channel_name"):
                    channel["name"] = data["channel_name"]
                return httpx.Response(200, json={"channel_id": channel_id, "consent": channel["consent"]})
        channel_id = str(uuid.uuid4())
        self.channels[channel_id] = {
            "username": username,
            "source": data["channel_source"],
            "name": data.get("channel_name"),
            "consent": True,
        }
        return httpx.Response(201, json={"channel_id": channel_id, "consent": True})

    def _create_message(self, request: httpx.Request) -> httpx.Response:
        content_type = request.headers["Content-Type"]
        body = request.read()
        parsed = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode() + b"\r\n\r\n" + body
        )
        parts = {
            part.get_param("name", header="content-disposition"): part.get_payload(decode=True)
            for part in parsed.iter_parts()
        }
        message = json.loads(parts["message"])
        channel = self.channels.get(message["channel"])
        if channel is None or message["channel"] in self.other_servers_channels:
            return httpx.Response(400, json={"errors": "CHANNEL_DOES_NOT_EXIST"})
        if not channel["consent"]:
            return httpx.Response(400, json={"errors": "NO_USER_CONSENT"})
        if message["message_id"] in self.messages:
            return httpx.Response(400, json={"errors": "MESSAGE_ID_ALREADY_EXISTS"})
        self.messages[message["message_id"]] = {
            **message,
            "version_header": request.headers.get("X-Messaging-Version"),
            "parts": sorted(parts),
            "blobs": {name: data for name, data in parts.items() if name != "message"},
        }
        return httpx.Response(200, json={"message_id": message["message_id"]})

    # --- reading what labs sent ----------------------------------------------------------

    def texts_to(self, username: str, key: str) -> list[str]:
        """What the worker's phone would show, decrypted with the key it fetched."""
        out = []
        for message in self.messages.values():
            channel = self.channels[message["channel"]]
            if channel["username"].lower() == username.lower():
                out.append(crypto.decrypt_text(key, message["content"]))
        return out

    def card_to(self, username: str, key: str) -> str | None:
        """The page of the last card the worker's phone was sent, decrypted (None if no card)."""
        for message in reversed(list(self.messages.values())):
            channel = self.channels[message["channel"]]
            if channel["username"].lower() == username.lower() and message.get("format") == "card-html":
                [entry] = [i for i, a in enumerate(message["attachments"]) if a["name"] == "index.html"]
                return crypto.decrypt_attachment(key, message["blobs"][f"attachment_{entry}"]).decode()
        return None

    # --- connect-id calling labs ---------------------------------------------------------

    @staticmethod
    def signed_post(client, path: str, body: dict, *, secret: str = SECRET, signature: str | None = None):
        raw = json.dumps(body).encode()
        digest = signature if signature is not None else mac_digest(secret, raw)
        return client.post(path, data=raw, content_type="application/json", headers={MAC_HEADER: digest})

    def reply(self, client, channel_id: str, key: str, *texts: str, message_ids: list[str] | None = None):
        """The worker writes; connect-id forwards it to labs' delivery endpoint."""
        ids = message_ids or [str(uuid.uuid4()) for _ in texts]
        messages = [
            {
                "message_id": message_id,
                "channel": channel_id,
                "channel_name": "x",
                **crypto.encrypt_text(key, text),
                "timestamp": "2026-11-21T10:00:00Z",
                "status": "PENDING",
                "action": "ccc_message",
                "notification_type": "MESSAGING",
                "title": "New Connect Message",
                "body": "You received a new message",
            }
            for message_id, text in zip(ids, texts)
        ]
        return self.signed_post(
            client, "/messaging/personalid/delivery/", {"channel_id": channel_id, "messages": messages}
        )

    def set_consent(self, client, channel_id: str, consent: bool):
        self.channels[channel_id]["consent"] = consent
        return self.signed_post(
            client, "/messaging/personalid/consent/", {"channel_id": channel_id, "consent": consent}
        )

    def receipt(self, client, channel_id: str, message_ids: list[str]):
        body = {
            "channel_id": channel_id,
            "messages": [{"message_id": m, "received_on": "2026-11-21 10:05:00+00:00"} for m in message_ids],
        }
        return self.signed_post(client, "/messaging/personalid/callback/", body)
