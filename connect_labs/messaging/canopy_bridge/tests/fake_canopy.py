"""A fake canopy for the bridge's tests: no network.

It holds canopy's CLIENT key (what canopy signs subject statements and reply pushes with) and a
DPoP key, serves canopy's client metadata to a ``ClientKeyResolver``, and answers the routes
labs calls -- the contact-token arrival, ``/api/contact/...`` (sessions, send, turns, messages)
and ``/api/sites/contacts`` (+ ``/erase``) -- verifying every signature labs makes with the
labs site's public key, the way canopy would.
"""

from __future__ import annotations

import itertools
import json
import time
import uuid

import jwt
from canopy_sdk import contract
from canopy_sdk.host.client_keys import ClientKeyResolver
from canopy_sdk.jose import make_dpop_proof, new_jti, sign
from canopy_sdk.keys import generate_private_key, private_pem, public_jwk

from connect_labs.messaging.canopy_bridge import sdk

BASE = "https://canopy.test"
CLIENT_ID = contract.client_id_for(BASE)
JWKS_URI = BASE + contract.CLIENT_JWKS_PATH


def new_key_pem() -> str:
    return private_pem(generate_private_key("EdDSA"))


class FakeCanopy:
    def __init__(self, site_key_pem: str = "", site: str = "connect-messaging"):
        self.client_key = generate_private_key("EdDSA")
        self.dpop_key = generate_private_key("EdDSA")
        self.site = site
        self.site_public = None
        if site_key_pem:
            from canopy_sdk.keys import load_private_key

            self.site_public = load_private_key(site_key_pem).public_key()
        self.contacts: dict[str, dict] = {}  # sub -> {version, profile, record_uri, display_name}
        self.contact_requests: list[dict] = []
        self.erased: list[str] = []
        self.tombstones: dict[str, int] = {}  # sub -> when canopy erased them (epoch seconds)
        self.minted: list[dict] = []  # verified visitor assertions
        self.sessions: dict[str, dict] = {}  # id -> {sub, agent, metadata, title, messages: []}
        self.sends: list[dict] = []  # {session, sub, text, context_claims, client_id, turn}
        self.turns: dict[str, dict] = {}  # id -> {status, session}
        self.answers: list[str] = []  # what the agent will say, in order
        self.fail_next: dict[str, tuple[int, str]] = {}  # route kind -> (status, detail)
        self._index = itertools.count(1)
        self.contact_ids = itertools.count(100)

    # --- canopy's keys, as a host learns them -------------------------------------------------

    def client_keys(self) -> ClientKeyResolver:
        docs = {
            CLIENT_ID: {
                "client_id": CLIENT_ID,
                "token_endpoint_auth_method": contract.TOKEN_ENDPOINT_AUTH_METHOD,
                "jwks_uri": JWKS_URI,
            },
            JWKS_URI: {"keys": [{**public_jwk(self.client_key), "use": "sig"}]},
        }
        return ClientKeyResolver(fetch_json=lambda url: docs[url])

    # --- what canopy signs --------------------------------------------------------------------

    def subject_statement(
        self,
        resource: str,
        *,
        sub: str,
        session: str,
        turn: str,
        agent: str = "connect-coach",
        site: str | None = None,
        rpc=None,
        exp_in: int = 60,
        typ: str = sdk.SUBJECT_TYP,
        key=None,
        jkt: str | None = None,
        iss: str = CLIENT_ID,
        aud: str | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": iss,
            "aud": aud or resource,
            "iat": now,
            "exp": now + exp_in,
            "jti": new_jti(),
            "sub": sub,
            "site": site or self.site,
            "agent": agent,
            "turn": turn,
            "session": session,
            "capability": "contact",
            "cnf": {"jkt": jkt or public_jwk(self.dpop_key)["kid"]},
        }
        if rpc is not None:
            claims["rpc"] = contract.rpc_digest(*rpc)
        key = key or self.client_key
        return sign(claims, key, headers={"kid": public_jwk(key)["kid"], "typ": typ})

    def subject_headers(
        self, resource: str, *, rpc=None, htu: str | None = None, method: str = "POST", dpop_key=None, **kwargs
    ) -> dict:
        statement = self.subject_statement(resource, rpc=rpc, **kwargs)
        proof = make_dpop_proof(dpop_key or self.dpop_key, method, htu or resource)
        return {"Authorization": f"{sdk.SUBJECT_AUTH_SCHEME} {statement}", "DPoP": proof}

    def reply_token(
        self,
        audience: str,
        *,
        sub: str,
        session: str,
        turn: str,
        seq: int = 0,
        text: str = "",
        ended: bool = True,
        error: str = "",
        exp_in: int = 300,
        typ: str = sdk.REPLY_TYP,
        key=None,
        iss: str = CLIENT_ID,
        site: str | None = None,
    ) -> str:
        now = int(time.time())
        claims = {
            "iss": iss,
            "aud": audience,
            "sub": sub,
            "site": site or self.site,
            "session": session,
            "turn": turn,
            "seq": seq,
            "text": text,
            "ended": ended,
            "iat": now,
            "exp": now + exp_in,
            "jti": new_jti(),
        }
        if error:
            claims["error"] = error
        key = key or self.client_key
        return sign(claims, key, headers={"kid": public_jwk(key)["kid"], "typ": typ})

    # --- verifying what labs signed -----------------------------------------------------------

    def _verify_site(self, token: str, typ: str, audience: str | None = None) -> dict:
        header = jwt.get_unverified_header(token)
        assert header.get("typ") == typ, header
        options = {"verify_aud": audience is not None}
        claims = jwt.decode(token, self.site_public, algorithms=["EdDSA"], audience=audience, options=options)
        assert claims["iss"] == self.site
        assert claims["exp"] - claims["iat"] <= 60
        return claims

    # --- labs -> canopy: /api/sites/contacts (S2) and /erase (S6) -----------------------------

    def http_post(self, url: str, body: bytes, *, content_type: str, timeout=None):
        """canopy's answer as the SDK's sender returns it: ``(status, raw, headers)``."""
        assert content_type == sdk.CONTACTS_MEDIA_TYPE
        token = body.decode()
        if url == BASE + sdk.CONTACTS_UPDATE_PATH:
            if "contacts" in self.fail_next:
                status, detail = self.fail_next.pop("contacts")
                return status, json.dumps({"detail": detail}).encode(), {}
            claims = self._verify_site(token, sdk.CONTACT_UPDATE_TYP, audience=url)
            self.contact_requests.append(claims)
            results = []
            for item in claims["contacts"]:
                if item["sub"] in self.tombstones and claims["iat"] <= self.tombstones[item["sub"]]:
                    # Signed before canopy erased the person: a late delivery never brings them back.
                    results.append(
                        {
                            "sub": item["sub"],
                            "contact_id": None,
                            "status": "refused",
                            "reason": "erased: this update was signed before the site erased this person",
                        }
                    )
                    continue
                held = self.contacts.get(item["sub"])
                if held is not None and item["version"] <= held["version"]:
                    results.append({"sub": item["sub"], "contact_id": held["contact_id"], "status": "stale"})
                    continue
                status = "updated" if held else "created"
                contact_id = held["contact_id"] if held else next(self.contact_ids)
                self.contacts[item["sub"]] = {**item, "contact_id": contact_id}
                results.append({"sub": item["sub"], "contact_id": contact_id, "status": status})
            return 200, json.dumps({"results": results}).encode(), {}
        if url == BASE + sdk.CONTACTS_ERASE_PATH:
            if "erase" in self.fail_next:
                status, detail = self.fail_next.pop("erase")
                return status, json.dumps({"detail": detail}).encode(), {}
            claims = self._verify_site(token, sdk.CONTACT_ERASE_TYP, audience=url)
            results = []
            for sub in claims["subs"]:
                found = self.contacts.pop(sub, None) is not None
                self.erased.append(sub)
                if found:
                    self.tombstones[sub] = int(time.time())
                results.append({"sub": sub, "status": "erased" if found else "not_found"})
            return 200, json.dumps({"results": results}).encode(), {}
        raise AssertionError(f"unexpected POST {url}")

    # --- labs -> canopy: the relay (S1, S3) ---------------------------------------------------

    def mint(self, config, payload: dict) -> dict:
        claims = jwt.decode(
            payload[contract.ARRIVAL_ASSERTION_FIELD],
            self.site_public,
            algorithms=["EdDSA"],
            audience=BASE,
        )
        assert claims["iss"] == self.site
        self.minted.append(claims)
        return {"token": f"tok-{claims['sub']}-{uuid.uuid4().hex[:6]}", "expires_at": "2999-01-01T00:00:00Z"}

    def _sub_of(self, headers: dict) -> str:
        token = headers["Authorization"].removeprefix("Bearer ")
        return token.split("-", 1)[1].rsplit("-", 1)[0]

    def transport(self, method: str, url: str, body, headers: dict):
        path = url.removeprefix(BASE)
        sub = self._sub_of(headers)
        if path == sdk.CONTACT_SESSIONS_PATH and method == "POST":
            if "open" in self.fail_next:
                return self.fail_next.pop("open")[0], {"detail": "refused: no"}
            sid = str(uuid.uuid4())
            self.sessions[sid] = {
                "sub": sub,
                "agent": body["agent_slug"],
                "metadata": body["metadata"],
                "title": body["title"],
                "messages": [],
            }
            return 201, {"id": sid}
        if path.startswith(sdk.CONTACT_SESSIONS_PATH + "/") and path.endswith("/send"):
            sid = path.split("/")[4]
            if "send" in self.fail_next:
                status, detail = self.fail_next.pop("send")
                return status, {"detail": detail}
            session = self.sessions[sid]
            assert session["sub"] == sub
            assert body["origin"] == sdk.RELAY_ORIGIN
            context = None
            if body.get("context"):
                context = self._verify_site(body["context"], sdk.CONTEXT_TYP)
                assert context["sub"] == sub and context["session"] == sid
            turn = str(uuid.uuid4())
            session["messages"].append(
                {
                    "id": str(uuid.uuid4()),
                    "turn_index": next(self._index),
                    "role": "user",
                    "plaintext": body["text"],
                    "created_at": _now_iso(),
                }
            )
            self.turns[turn] = {"status": "running", "session": sid}
            self.sends.append(
                {
                    "session": sid,
                    "sub": sub,
                    "text": body["text"],
                    "context": context,
                    "client_id": body.get("client_id"),
                    "turn": turn,
                }
            )
            return 200, {"turn_id": turn, "message_id": 1}
        if path.startswith(sdk.CONTACT_TURNS_PATH + "/"):
            turn = path.rsplit("/", 1)[1]
            return 200, {"id": turn, **self.turns[turn]}
        if path.startswith(sdk.CONTACT_SESSIONS_PATH + "/") and "/messages" in path:
            sid = path.split("/")[4]
            return 200, {"messages": list(self.sessions[sid]["messages"]), "has_more_before": False}
        raise AssertionError(f"unexpected {method} {url}")

    def finish(self, turn: str, text: str | None = None, *, status: str = "done") -> None:
        """The agent's turn ends, saying ``text`` (or the next queued answer)."""
        session = self.sessions[self.turns[turn]["session"]]
        say = text if text is not None else (self.answers.pop(0) if self.answers else "")
        if say:
            session["messages"].append(
                {
                    "id": str(uuid.uuid4()),
                    "turn_index": next(self._index),
                    "role": "assistant",
                    "plaintext": say,
                    "created_at": _now_iso(),
                }
            )
        self.turns[turn]["status"] = status


def _now_iso() -> str:
    import datetime as dt

    return dt.datetime.now(dt.UTC).isoformat()
