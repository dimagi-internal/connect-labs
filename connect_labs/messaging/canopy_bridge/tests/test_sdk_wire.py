"""The wire format of every token the bridge signs or verifies, pinned to CONTRACTS.md S1-S6.

These hold whichever half ``sdk.py`` uses (the SDK's, or labs' local fallback until it ships):
when the SDK is bumped and a fallback deleted, these keep pinning the same wire.
"""

from __future__ import annotations

import json
import time

import jwt
import pytest
from canopy_sdk import contract
from canopy_sdk.host import HostConfig
from canopy_sdk.jose import make_dpop_proof
from canopy_sdk.keys import generate_private_key, load_private_key
from canopy_sdk.stores import MemoryJtiStore

from connect_labs.messaging.canopy_bridge import sdk
from connect_labs.messaging.canopy_bridge.tests.fake_canopy import BASE, CLIENT_ID, FakeCanopy, new_key_pem

SITE_KEY = new_key_pem()
RESOURCE = "https://labs.test/messaging/canopy/mcp/"
WEBHOOK = "https://labs.test/messaging/canopy/replies/"


@pytest.fixture
def config():
    return HostConfig(
        signing_key=SITE_KEY,
        canopy_base_url=BASE,
        app_name="connect-messaging",
        workspace="dimagi",
        canopy_client_id=CLIENT_ID,
        issuer="https://labs.test",
        resource=RESOURCE,
    )


@pytest.fixture
def canopy():
    return FakeCanopy(SITE_KEY)


def _public():
    return load_private_key(SITE_KEY).public_key()


def test_every_name_says_which_half_it_is():
    assert set(sdk.implementations()) == {
        "RelayClient",
        "sign_context",
        "update_contacts",
        "erase_contacts",
        "verify_subject",
        "verify_reply",
        "site_document",
    }
    assert set(sdk.implementations().values()) <= {"sdk", "local"}


def test_wire_constants_are_the_contracts():
    assert (sdk.CHANNEL_CLAIM, sdk.RELAY_ORIGIN) == ("canopy_channel", "site_message")
    assert sdk.CONTEXT_TYP == "canopy-context+jwt"
    assert (sdk.CONTACT_UPDATE_TYP, sdk.CONTACT_ERASE_TYP) == ("canopy-contact-update+jwt", "canopy-contact-erase+jwt")
    assert (sdk.CONTACTS_UPDATE_PATH, sdk.CONTACTS_ERASE_PATH) == ("/api/sites/contacts", "/api/sites/contacts/erase")
    assert sdk.CONTACTS_MEDIA_TYPE == "application/jwt"
    assert (sdk.SUBJECT_AUTH_SCHEME, sdk.SUBJECT_TYP) == ("CanopySubject", "canopy-subject+jwt")
    assert sdk.REPLY_TYP == "canopy-reply+jwt"
    assert (sdk.CONTEXT_MAX_LIFETIME, sdk.SUBJECT_MAX_LIFETIME, sdk.REPLY_MAX_LIFETIME) == (60, 60, 300)
    assert (sdk.MAX_CONTACTS, sdk.MAX_CONTEXT_BODY) == (100, 16_384)


# --- S3: the context labs attests ---------------------------------------------------------------


def test_context_is_signed_by_the_site_for_one_person_and_session(config):
    token = sdk.sign_context(config, sub="personalid:amina", session="s-1", format="markdown", body="# Brief")
    header = jwt.get_unverified_header(token)
    assert header["typ"] == "canopy-context+jwt" and header["kid"] == config.kid
    claims = jwt.decode(token, _public(), algorithms=["EdDSA"], options={"verify_aud": False})
    assert set(claims) == {"iss", "sub", "session", "iat", "exp", "jti", "format", "body"}
    assert (claims["iss"], claims["sub"], claims["session"]) == ("connect-messaging", "personalid:amina", "s-1")
    assert (claims["format"], claims["body"]) == ("markdown", "# Brief")
    assert 0 < claims["exp"] - claims["iat"] <= 60


@pytest.mark.parametrize(
    "kwargs, code",
    [
        ({"body": "x" * 16_385}, "too_large"),
        ({"format": "html"}, "bad_format"),
        ({"body": "  "}, "bad_body"),
        ({"session": ""}, "incomplete"),
    ],
)
def test_a_context_canopy_would_refuse_is_never_signed(config, kwargs, code):
    args = {"sub": "personalid:amina", "session": "s-1", "format": "markdown", "body": "ok", **kwargs}
    with pytest.raises(sdk.ContextError) as exc:
        sdk.sign_context(config, **args)
    assert exc.value.code == code


# --- S1: the relay -----------------------------------------------------------------------------


def test_the_relay_arrival_names_the_channel_and_binds_the_agent(config, canopy):
    relay = sdk.RelayClient(config, transport=canopy.transport, mint=canopy.mint)
    sid = relay.open_session(
        "personalid:amina", agent_slug="connect-coach", title="t", metadata={"x_skill": "learn"}, channel="personalid"
    )
    assertion = canopy.minted[-1]
    assert assertion["sub"] == "personalid:amina"
    assert assertion[sdk.CHANNEL_CLAIM] == "personalid"
    assert assertion[contract.AGENT_CLAIM] == "connect-coach"
    assert assertion[contract.WORKSPACE_CLAIM] == "dimagi"
    assert canopy.sessions[sid]["metadata"] == {"x_skill": "learn"}

    answer = relay.send(
        sid, "personalid:amina", "hello", context={"format": "markdown", "body": "# B"}, client_id="m1"
    )
    sent = canopy.sends[-1]
    assert answer["turn_id"] == sent["turn"]
    assert (sent["text"], sent["client_id"]) == ("hello", "m1")
    assert sent["context"]["body"] == "# B" and sent["context"]["session"] == sid
    # One mint for the person, reused until near its expiry.
    assert len(canopy.minted) == 1


def test_a_refused_send_names_canopys_code(config, canopy):
    relay = sdk.RelayClient(config, transport=canopy.transport, mint=canopy.mint)
    sid = relay.open_session("personalid:amina", agent_slug="connect-coach", channel="personalid")
    canopy.fail_next["send"] = (429, "rate_limited: slow down")
    with pytest.raises(sdk.RelayError) as exc:
        relay.send(sid, "personalid:amina", "hello")
    assert exc.value.code == "rate_limited" and exc.value.status == 429


# --- S2 / S6: the contact record and erasure ---------------------------------------------------


def test_a_contact_update_is_a_site_signed_jws_for_canopys_route(config, canopy):
    item = {
        "sub": "personalid:amina",
        "display_name": "Amina",
        "record_uri": "labs-worker://amina",
        "profile": {"stage": "learning", "language": "ha"},
        "version": 7,
    }
    sdk.update_contacts(config, [item], post=canopy.http_post)
    claims = canopy.contact_requests[-1]
    assert set(claims) == {"iss", "aud", "iat", "exp", "jti", "contacts", contract.WORKSPACE_CLAIM}
    assert claims["aud"] == BASE + "/api/sites/contacts"
    assert claims[contract.WORKSPACE_CLAIM] == "dimagi"
    assert claims["contacts"] == [item]


def test_a_contact_update_names_at_most_100_people_and_a_bounded_profile(config):
    many = [{"sub": f"personalid:w{i}", "version": 1} for i in range(101)]
    with pytest.raises(sdk.ContactRecordError):
        sdk.contact_update_token(config, many)
    with pytest.raises(sdk.ContactRecordError):
        sdk.contact_update_token(config, [{"sub": "personalid:a", "version": 1, "profile": {"x": "y" * 5000}}])
    with pytest.raises(sdk.ContactRecordError):
        sdk.contact_update_token(config, [{"sub": "personalid:a", "version": 0}])


def test_an_erasure_is_a_site_signed_jws_naming_the_subs(config, canopy):
    canopy.contacts["personalid:amina"] = {"version": 1, "contact_id": 5}
    answer = sdk.erase_contacts(
        config, ["personalid:amina", "personalid:amina", "personalid:nobody"], post=canopy.http_post
    )
    assert answer == {
        "results": [
            {"sub": "personalid:amina", "status": "erased"},
            {"sub": "personalid:nobody", "status": "not_found"},
        ]
    }
    token = sdk.contact_erase_token(config, ["personalid:amina"])
    assert jwt.get_unverified_header(token)["typ"] == "canopy-contact-erase+jwt"
    claims = jwt.decode(token, _public(), algorithms=["EdDSA"], audience=BASE + "/api/sites/contacts/erase")
    assert claims["subs"] == ["personalid:amina"] and claims["exp"] - claims["iat"] <= 60


def test_a_refusal_from_canopy_is_a_contact_record_error(config, canopy):
    canopy.fail_next["contacts"] = (403, "unknown_site: no such site")
    with pytest.raises(sdk.ContactRecordError) as exc:
        sdk.update_contacts(config, [{"sub": "personalid:a", "version": 1}], post=canopy.http_post)
    assert exc.value.code == "unknown_site"


# --- S4: canopy's subject statement --------------------------------------------------------------

RPC = ("tools/call", {"name": "worker_profile", "arguments": {}})


def _verify(config, canopy, headers, *, rpc=RPC, store=None):
    return sdk.verify_subject(
        headers,
        config=config,
        client_keys=canopy.client_keys(),
        replay_store=store or MemoryJtiStore(),
        method="POST",
        rpc=rpc,
    )


def test_a_subject_statement_names_the_contact_and_is_bound_to_the_proof(config, canopy):
    headers = canopy.subject_headers(RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC)
    subject = _verify(config, canopy, headers)
    assert (subject.sub, subject.session, subject.turn, subject.site) == (
        "personalid:amina",
        "s-1",
        "t-1",
        "connect-messaging",
    )
    assert subject.agent == "connect-coach"


@pytest.mark.parametrize(
    "change, code",
    [
        ({"typ": "canopy-execution+jwt"}, "bad_typ"),
        ({"exp_in": -120}, "expired"),
        ({"exp_in": 600}, "too_long"),
        ({"aud": "https://elsewhere.test/mcp/"}, "bad_audience"),
        ({"iss": "https://evil.test/oauth/client.json"}, "bad_issuer"),
        ({"jkt": "not-the-proof-key"}, "unbound"),
    ],
)
def test_a_subject_statement_canopy_did_not_make_for_this_call_is_refused(config, canopy, change, code):
    headers = canopy.subject_headers(RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC, **change)
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, headers)
    assert exc.value.code == code


def test_a_subject_statement_signed_by_another_key_is_refused(config, canopy):
    other = generate_private_key("EdDSA")
    headers = canopy.subject_headers(RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC, key=other)
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, headers)
    assert exc.value.code in ("unknown_key", "bad_signature")


def test_a_subject_statement_for_another_request_or_url_is_refused(config, canopy):
    headers = canopy.subject_headers(RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC)
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, headers, rpc=("tools/call", {"name": "worker_facts", "arguments": {}}))
    assert exc.value.code == "wrong_request"
    headers = canopy.subject_headers(
        RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC, htu="https://labs.test/mcp/"
    )
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, headers)
    assert exc.value.code == "bad_proof"


def test_a_subject_statement_is_used_once(config, canopy):
    store = MemoryJtiStore()
    headers = canopy.subject_headers(RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC)
    _verify(config, canopy, headers, store=store)
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, headers, store=store)
    assert exc.value.code == "replayed"


def test_no_statement_or_a_bearer_or_a_proof_with_ath_is_refused(config, canopy):
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, {})
    assert exc.value.code == "no_statement"
    headers = canopy.subject_headers(RESOURCE, sub="personalid:amina", session="s-1", turn="t-1", rpc=RPC)
    bearer = {**headers, "Authorization": headers["Authorization"].replace("CanopySubject", "Bearer")}
    with pytest.raises(sdk.SubjectRefused):
        _verify(config, canopy, bearer)
    with_ath = {**headers, "DPoP": make_dpop_proof(canopy.dpop_key, "POST", RESOURCE, access_token="tok")}
    with pytest.raises(sdk.SubjectRefused) as exc:
        _verify(config, canopy, with_ath)
    assert exc.value.code == "bad_proof"


# --- S5: canopy's reply push ---------------------------------------------------------------------


def test_a_reply_is_canopys_signed_word_for_labs_webhook(config, canopy):
    token = canopy.reply_token(WEBHOOK, sub="personalid:amina", session="s-1", turn="t-1", seq=0, text="Hi")
    reply = sdk.verify_reply(token, config=config, audience=WEBHOOK, client_keys=canopy.client_keys())
    assert (reply.sub, reply.session, reply.turn, reply.seq, reply.text, reply.ended) == (
        "personalid:amina",
        "s-1",
        "t-1",
        0,
        "Hi",
        True,
    )


@pytest.mark.parametrize(
    "change, code",
    [
        ({"typ": "canopy-subject+jwt"}, "bad_typ"),
        ({"exp_in": -120}, "expired"),
        ({"exp_in": 3600}, "too_long"),
        ({"iss": "https://evil.test/oauth/client.json"}, "bad_issuer"),
    ],
)
def test_a_reply_canopy_did_not_sign_for_labs_is_refused(config, canopy, change, code):
    token = canopy.reply_token(WEBHOOK, sub="personalid:amina", session="s-1", turn="t-1", text="Hi", **change)
    with pytest.raises(sdk.ReplyRefused) as exc:
        sdk.verify_reply(token, config=config, audience=WEBHOOK, client_keys=canopy.client_keys())
    if code:
        assert exc.value.code == code


def test_a_reply_for_another_webhook_or_from_another_key_is_refused(config, canopy):
    token = canopy.reply_token("https://elsewhere.test/hook/", sub="personalid:a", session="s", turn="t")
    with pytest.raises(sdk.ReplyRefused) as exc:
        sdk.verify_reply(token, config=config, audience=WEBHOOK, client_keys=canopy.client_keys())
    assert exc.value.code == "bad_audience"
    token = canopy.reply_token(WEBHOOK, sub="personalid:a", session="s", turn="t", key=generate_private_key("EdDSA"))
    with pytest.raises(sdk.ReplyRefused):
        sdk.verify_reply(token, config=config, audience=WEBHOOK, client_keys=canopy.client_keys())
    with pytest.raises(sdk.ReplyRefused):
        sdk.verify_reply("not.a.token", config=config, audience=WEBHOOK, client_keys=canopy.client_keys())


def test_a_reply_jti_is_spent_once_with_a_store(config, canopy):
    store = MemoryJtiStore()
    token = canopy.reply_token(WEBHOOK, sub="personalid:a", session="s", turn="t", text="x")
    sdk.verify_reply(token, config=config, audience=WEBHOOK, client_keys=canopy.client_keys(), replay_store=store)
    with pytest.raises(sdk.ReplyRefused) as exc:
        sdk.verify_reply(token, config=config, audience=WEBHOOK, client_keys=canopy.client_keys(), replay_store=store)
    assert exc.value.code == "replayed"


# --- site.json ---------------------------------------------------------------------------------


def test_site_json_declares_the_person_scheme(config):
    token = sdk.site_document_token(
        config,
        person_scheme="labs-worker",
        site={
            "origins": ["https://labs.test"],
            "jwks_url": "https://labs.test/messaging/canopy/jwks.json",
            "mcp_resource": RESOURCE,
            "resource_schemes": ["labs-worker"],
        },
        title="Connect Worker messaging",
    )
    assert jwt.get_unverified_header(token)["typ"] == "canopy-site+jwt"
    doc = jwt.decode(token, _public(), algorithms=["EdDSA"], options={"verify_aud": False})
    assert doc["iss"] == "connect-messaging" and doc["v"] == 1
    assert doc["site"]["person_scheme"] == "labs-worker"
    assert doc["site"]["mcp_resource"] == RESOURCE
    with pytest.raises(ValueError):
        sdk.site_document_token(config, person_scheme="Not A Scheme!", site={})


def test_contact_tokens_round_trip_as_json(config):
    token = sdk.contact_update_token(config, [{"sub": "personalid:a", "version": int(time.time())}])
    assert json.loads(json.dumps(token)) == token
