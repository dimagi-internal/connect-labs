"""A fake labs-mode Formplayer, mounted in `fakes.FakeServers` at ``FORMPLAYER``.

It checks what the real one checks (``auth/LabsAuthFilter.java``): a POST, the HMAC-SHA256 of the
exact body bytes (or of the multipart's ``application/json`` part) in ``X-Labs-Signature``, an HQ
token in ``X-Labs-HQ-Token``, ``username`` and ``domain`` in the body, no ``restoreAs``. A failed
check is a 401, as there. Responses come from ``fixtures/formplayer_responses.json``.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import json
import time
from email.parser import BytesParser
from email.policy import default as default_policy
from pathlib import Path

import httpx

FORMPLAYER = "https://formplayer.test"
KEY = "labs-test-signing-key"
V2_SKEW_SECONDS = 300

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "formplayer_responses.json").read_text())


def fixture(name: str):
    return copy.deepcopy(FIXTURES[name])


def multipart_parts(request: httpx.Request) -> dict[str, tuple[str | None, bytes]]:
    """{part name: (content type, bytes)} of a multipart request."""
    raw = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n" + request.content
    message = BytesParser(policy=default_policy).parsebytes(raw)
    parts = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        parts[name] = (part.get_content_type(), part.get_payload(decode=True))
    return parts


class FakeFormplayer:
    def __init__(self, key: str = KEY):
        self.key = key
        self.require_v2 = False
        self.v2_verified = 0
        self.requests: list[httpx.Request] = []
        self.bodies: list[tuple[str, dict]] = []  # (endpoint, the signed JSON) of accepted calls
        self.uploads: list[tuple[str, bytes, str]] = []  # (filename, bytes, content type)
        self.responses: dict[str, object] = {}  # endpoint -> fixture name, dict, or (status, body)
        self.open_sessions = {fixture("form")["session_id"]}
        self.trees: dict[str, list] = {}  # session id -> the form's tree as answered so far
        self.navigation = {
            (): "menu_start",
            ("0",): "menu_forms",
            ("1",): "case_list",
        }

    def body_for(self, endpoint: str) -> list[dict]:
        return [b for e, b in self.bodies if e == endpoint]

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        endpoint = request.url.path.lstrip("/")
        if request.method != "POST":
            return httpx.Response(401, text="Labs auth failed: only POST is supported")
        token = request.headers.get("X-Labs-HQ-Token", "")
        if not token:
            return httpx.Response(401, text="Labs auth failed: missing X-Labs-HQ-Token")
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            parts = multipart_parts(request)
            json_parts = [v for v in parts.values() if v[0] == "application/json"]
            if not json_parts:
                return httpx.Response(401, text="Labs auth failed: multipart request has no application/json part")
            signed = json_parts[0][1]
            if "file" in parts:
                filename = None
                for chunk in request.content.split(b"\r\n"):
                    if b'name="file"' in chunk and b"filename=" in chunk:
                        filename = chunk.split(b'filename="')[1].split(b'"')[0].decode()
                self.uploads.append((filename, parts["file"][1], parts["file"][0]))
        else:
            if content_type != "application/json":
                return httpx.Response(415)
            signed = request.content
        expected = base64.b64encode(hmac.new(self.key.encode(), signed, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, request.headers.get("X-Labs-Signature", "")):
            return httpx.Response(401, text="Labs auth failed: invalid signature")
        if refused := self._check_v2(request, endpoint, token, signed):
            return refused
        body = json.loads(signed)
        if not body.get("username") or not body.get("domain"):
            return httpx.Response(401, text="Labs auth failed: body must include username and domain")
        if body.get("restoreAs") is not None or body.get("restoreAsCaseId") is not None:
            return httpx.Response(401, text="Labs auth failed: restoreAs")
        self.bodies.append((endpoint, body))
        return self._respond(endpoint, body)

    def _check_v2(self, request: httpx.Request, endpoint: str, token: str, signed: bytes) -> httpx.Response | None:
        """Signature v2, as Formplayer checks it when present (and always, once v1 alone is refused)."""
        v2 = request.headers.get("X-Labs-Signature-V2")
        if not v2:
            return httpx.Response(401, text="Labs auth failed: v2 required") if self.require_v2 else None
        try:
            timestamp = int(request.headers.get("X-Labs-Timestamp", ""))
        except ValueError:
            return httpx.Response(401, text="Labs auth failed: bad timestamp")
        if abs(time.time() - timestamp) > V2_SKEW_SECONDS:
            return httpx.Response(401, text="Labs auth failed: stale timestamp")
        canonical = (
            "\n".join(
                [
                    "v2",
                    request.method,
                    request.url.path,
                    str(timestamp),
                    hashlib.sha256(token.encode()).hexdigest(),
                    request.headers.get("X-Labs-HQ-Host", ""),
                ]
            ).encode()
            + b"\n"
            + signed
        )
        expected = base64.b64encode(hmac.new(self.key.encode(), canonical, hashlib.sha256).digest()).decode()
        if not hmac.compare_digest(expected, v2):
            return httpx.Response(401, text="Labs auth failed: invalid v2 signature")
        self.v2_verified += 1
        return None

    def _respond(self, endpoint: str, body: dict) -> httpx.Response:
        if endpoint in self.responses:
            override = self.responses[endpoint]
            if isinstance(override, tuple):
                status, payload = override
                return httpx.Response(status, json=payload)
            return httpx.Response(200, json=fixture(override) if isinstance(override, str) else override)
        if endpoint == "navigate_menu_start":
            return httpx.Response(200, json=fixture("menu_start"))
        if endpoint == "navigate_menu":
            name = self.navigation.get(tuple(body.get("selections") or []), "form")
            screen = fixture(name)
            if name == "form":
                self.trees[screen["session_id"]] = copy.deepcopy(screen["tree"])
                self.open_sessions.add(screen["session_id"])
            return httpx.Response(200, json=screen)
        if endpoint == "get_details":
            return httpx.Response(200, json=fixture("case_details"))
        if endpoint in ("answer", "answer_media", "new-repeat", "delete-repeat", "change_locale", "submit-all"):
            session_id = body.get("session_id")
            if session_id not in self.open_sessions:
                return httpx.Response(200, json=fixture("engine_session_gone"))
            if endpoint == "submit-all":
                self.open_sessions.discard(session_id)
                return httpx.Response(200, json=fixture("submit_success"))
            tree = self.trees.setdefault(session_id, fixture("form")["tree"])
            return httpx.Response(200, json=self._edit(endpoint, body, tree, session_id))
        if endpoint == "sync-db":
            return httpx.Response(200, json=fixture("sync_ok"))
        return httpx.Response(404)

    # A little of Formplayer's behaviour, enough to drive the player end to end.
    @staticmethod
    def _find(nodes, ix):
        for n in nodes:
            if n.get("ix") == ix:
                return n, nodes
            found = FakeFormplayer._find(n.get("children") or [], ix)
            if found:
                return found
        return None

    def _edit(self, endpoint: str, body: dict, tree: list, session_id: str) -> dict:
        ix = body.get("ix")
        found = self._find(tree, ix)
        if endpoint in ("answer", "answer_media"):
            if not found:
                return {"status": "error", "exception": f"No question at {ix}", "type": "text"}
            node = found[0]
            value = "1700000000000.jpg" if endpoint == "answer_media" else body.get("answer")
            if node.get("datatype") == "int" and isinstance(value, int) and node.get("question_id") == "hh_size":
                if not 1 <= value <= 30:
                    return fixture("answer_constraint")
            node["answer"] = value
        elif endpoint == "new-repeat" and found:
            node, siblings = found
            at = siblings.index(node)
            n = int(ix.split("_")[-1])
            siblings.insert(
                at,
                {
                    "type": "sub-group",
                    "ix": f"15_{n}",
                    "caption": f"Member {n + 1}",
                    "repeatable": "true",
                    "exists": "true",
                    "delete": True,
                    "children": [
                        {
                            "type": "question",
                            "ix": f"15_{n},0",
                            "datatype": "str",
                            "control": 1,
                            "caption": "Member name",
                            "required": 1,
                            "relevant": 0,
                            "answer": None,
                            "style": None,
                            "choices": None,
                        }
                    ],
                },
            )
            node["ix"] = f"15_{n + 1}"
        elif endpoint == "delete-repeat" and found:
            node, siblings = found
            siblings.remove(node)
        return {"status": "accepted", "session_id": session_id, "tree": copy.deepcopy(tree), "errors": {}}
