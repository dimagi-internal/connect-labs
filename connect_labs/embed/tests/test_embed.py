"""Embedding labs: the grant, the session, the framing headers, and the session's reach."""

from __future__ import annotations

import time
import uuid
from datetime import timedelta

import jwt
import pytest
from canopy_sdk.contract import JWT_BEARER_GRANT
from canopy_sdk.keys import generate_private_key, public_jwk
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse
from django.test import RequestFactory
from django.utils import timezone

from connect_labs.embed import kinds, sessions
from connect_labs.embed.middleware import EmbedSessionMiddleware
from connect_labs.embed.models import EmbedClient, EmbedSession
from connect_labs.labs.models import UserConnectToken
from connect_labs.users.tests.factories import UserFactory

LABS = "https://labs.example.org"
PARENT = "https://connect.example.org"
RESOURCE = f"{LABS}/embed/c/connect/workflow/123/"
TOKEN_URL = "/embed/token/"

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def _labs_url(settings):
    settings.LABS_PUBLIC_URL = LABS
    settings.LABS_EMBED_ENABLED = True
    settings.ALLOWED_HOSTS = ["*"]


@pytest.fixture(autouse=True)
def _readable(monkeypatch):
    """Every definition is readable unless a test says otherwise (prod is not reached)."""
    monkeypatch.setattr(kinds.WorkflowKind, "check_access", lambda self, user, token, entry: None)


ORG_DATA = {"organizations": [], "programs": [], "opportunities": [{"id": 456}]}


@pytest.fixture(autouse=True)
def _org_data(monkeypatch):
    """Connect's org list, as login would cache it (prod is not reached)."""
    monkeypatch.setattr(
        "connect_labs.labs.integrations.connect.oauth.fetch_user_organization_data",
        lambda token, force_refresh=False, owner=None: ORG_DATA,
    )


@pytest.fixture
def key():
    return generate_private_key("EdDSA")


@pytest.fixture
def client_row(key):
    return EmbedClient.objects.create(
        client_id="connect",
        name="Connect",
        identity_authority=True,
        jwks={"keys": [public_jwk(key)]},
        allowed_origins=[PARENT],
        resources=[{"kind": "workflow", "id": 123, "opportunity_id": 456}],
    )


@pytest.fixture
def person():
    user = UserFactory(username="pat")
    UserConnectToken.objects.create(
        user=user, access_token="connect-token", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    return user


def assertion(key, **overrides):
    now = int(time.time())
    claims = {
        "iss": "connect",
        "sub": "pat",
        "aud": f"{LABS}/embed/token/",
        "iat": now,
        "exp": now + 60,
        "jti": str(uuid.uuid4()),
        "resource": RESOURCE,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    if hasattr(key, "key_size"):  # RSA: the client's only key, so no kid is needed
        return jwt.encode(claims, key, algorithm="RS256")
    return jwt.encode(claims, key, algorithm="EdDSA", headers={"kid": public_jwk(key)["kid"]})


def redeem(client, token):
    return client.post(TOKEN_URL, {"grant_type": JWT_BEARER_GRANT, "assertion": token})


# --- the grant ----------------------------------------------------------------------------


def test_a_signed_assertion_gets_a_single_use_bootstrap_token(client, key, client_row, person):
    response = redeem(client, assertion(key))
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 120
    assert body["embed_url"] == RESOURCE
    assert response["Cache-Control"] == "no-store"
    session = EmbedSession.objects.get()
    assert (session.user, session.kind, session.resource_id) == (person, "workflow", "123")
    assert session.started_at is None
    assert body["access_token"] not in session.bootstrap_checksum


def test_an_assertion_works_once(client, key, client_row, person):
    token = assertion(key)
    assert redeem(client, token).status_code == 200
    again = redeem(client, token)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


def test_a_refused_request_does_not_burn_its_assertion(client, key, client_row, person):
    token = assertion(key)
    UserConnectToken.objects.filter(user=person).delete()
    assert redeem(client, token).json()["error"] == "invalid_grant"
    UserConnectToken.objects.create(
        user=person, access_token="t", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    assert redeem(client, token).status_code == 200


@pytest.mark.parametrize(
    "overrides, error",
    [
        ({"aud": "https://elsewhere.example.org/"}, "invalid_grant"),
        ({"exp": int(time.time()) + 3600}, "invalid_grant"),  # longer than five minutes
        ({"iat": int(time.time()) - 600, "exp": int(time.time()) - 400}, "invalid_grant"),  # expired
        ({"jti": None}, "invalid_grant"),
        ({"sub": "nobody"}, "invalid_grant"),
        ({"resource": f"{LABS}/embed/c/connect/workflow/999/"}, "invalid_target"),
        ({"resource": "https://evil.example.org/embed/c/connect/workflow/123/"}, "invalid_target"),
        ({"resource": f"{LABS}/embed/c/other/workflow/123/"}, "invalid_target"),
        ({"iss": "nobody"}, "invalid_client"),
    ],
)
def test_assertions_that_are_refused(client, key, client_row, person, overrides, error):
    response = redeem(client, assertion(key, **overrides))
    assert response.status_code in (400, 401)
    assert response.json()["error"] == error
    assert not EmbedSession.objects.exists()


def test_a_key_the_client_did_not_register_is_refused(client, client_row, person):
    stranger = generate_private_key("EdDSA")
    assert redeem(client, assertion(stranger)).json()["error"] == "invalid_client"


def test_a_shared_secret_is_never_accepted(client, client_row, person):
    now = int(time.time())
    forged = jwt.encode(
        {
            "iss": "connect",
            "sub": "pat",
            "aud": f"{LABS}/embed/token/",
            "iat": now,
            "exp": now + 60,
            "jti": "x",
            "resource": RESOURCE,
        },
        "secret",
        algorithm="HS256",
    )
    assert redeem(client, forged).json()["error"] == "invalid_client"


def test_an_unsigned_token_is_refused(client, client_row, person):
    now = int(time.time())
    unsigned = jwt.encode(
        {
            "iss": "connect",
            "sub": "pat",
            "aud": f"{LABS}/embed/token/",
            "iat": now,
            "exp": now + 60,
            "jti": "x",
            "resource": RESOURCE,
        },
        None,
        algorithm="none",
    )
    assert redeem(client, unsigned).json()["error"] == "invalid_client"


def test_rsa_keys_are_accepted_from_2048_bits(client, client_row, person):
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True)
    client_row.jwks = {"keys": [jwk]}
    client_row.save()
    assert redeem(client, assertion(rsa_key)).status_code == 200


def test_only_an_identity_authority_may_assert_users(client, key, client_row, person):
    client_row.identity_authority = False
    client_row.save()
    assert redeem(client, assertion(key)).json()["error"] == "unauthorized_client"


def test_an_inactive_client_gets_nothing(client, key, client_row, person):
    client_row.active = False
    client_row.save()
    assert redeem(client, assertion(key)).json()["error"] == "invalid_client"


def test_a_person_who_cannot_open_the_resource_is_refused(client, key, client_row, person, monkeypatch):
    monkeypatch.setattr(kinds.WorkflowKind, "check_access", lambda self, *a: "This person cannot open that workflow.")
    response = redeem(client, assertion(key))
    assert response.json() == {"error": "invalid_grant", "error_description": "This person cannot open that workflow."}


def test_the_workflow_kind_reads_the_definition_as_the_person(monkeypatch, person):
    monkeypatch.undo()  # the real check_access
    monkeypatch.setattr("connect_labs.labs.access.scopes.may_use", lambda caller, **scope: None)
    seen = {}

    class FakeAccess:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def get_definition(self, definition_id):
            seen["definition_id"] = definition_id
            return None

        def close(self):
            seen["closed"] = True

    monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", FakeAccess)
    refusal = kinds.WorkflowKind().check_access(person, "tok", {"kind": "workflow", "id": 123, "program_id": 9})
    assert refusal == "This person cannot open that workflow."
    assert seen == {"access_token": "tok", "user": person, "program_id": 9, "definition_id": 123, "closed": True}


def test_the_workflow_kind_asks_labs_first_whether_the_person_holds_the_scope(monkeypatch, person):
    """A labs-only opportunity's records are read with no permission check, so reading the
    definition proves nothing for one: labs' own scope check must refuse first."""
    monkeypatch.undo()
    asked = {}

    def may_use(caller, **scope):
        asked.update(user=caller.user, token=caller.access_token, **scope)
        return "opportunity 10001 is not accessible to your account"

    monkeypatch.setattr("connect_labs.labs.access.scopes.may_use", may_use)
    monkeypatch.setattr(
        "connect_labs.workflow.data_access.WorkflowDataAccess",
        lambda **kw: pytest.fail("the definition must not be read once labs has refused"),
    )
    refusal = kinds.WorkflowKind().check_access(person, "tok", {"kind": "workflow", "id": 1, "opportunity_id": 10001})
    assert refusal == "This person cannot open that workflow."
    assert asked == {"user": person, "token": "tok", "opportunity_id": 10001}


def test_a_generated_data_only_client_is_refused_real_data(client, key, client_row, person, monkeypatch):
    client_row.generated_data_only = True
    client_row.save()
    monkeypatch.setattr(kinds.WorkflowKind, "reads_only_generated", lambda self, user, entry: False)
    response = redeem(client, assertion(key))
    assert response.json() == {
        "error": "invalid_target",
        "error_description": "This system may show only generated data.",
    }
    monkeypatch.setattr(kinds.WorkflowKind, "reads_only_generated", lambda self, user, entry: True)
    assert redeem(client, assertion(key)).status_code == 200


def test_an_ordinary_client_is_not_asked_about_generated_data(client, key, client_row, person, monkeypatch):
    monkeypatch.setattr(
        kinds.WorkflowKind, "reads_only_generated", lambda *a: pytest.fail("only a generated-data-only client asks")
    )
    assert redeem(client, assertion(key)).status_code == 200


class TestWorkflowReadsOnlyGenerated:
    ENTRY = {"kind": "workflow", "id": 1, "opportunity_id": 10001}

    @pytest.fixture
    def reads(self, monkeypatch):
        state = {"reads": [10001], "generated": True, "data": {}}
        monkeypatch.setattr(
            "connect_labs.mcp.visit_access.workflow_reads", lambda user, definition_id, **scope: state["reads"]
        )
        monkeypatch.setattr("connect_labs.labs.synthetic.provenance.all_generated", lambda ids: state["generated"])

        class FakeAccess:
            def __init__(self, **kwargs):
                pass

            def get_definition(self, definition_id):
                return type("Definition", (), {"data": state["data"]})()

            def close(self):
                pass

        monkeypatch.setattr("connect_labs.workflow.data_access.WorkflowDataAccess", FakeAccess)
        return state

    def test_everything_generated_and_no_other_workflows(self, reads, person):
        assert kinds.WorkflowKind().reads_only_generated(person, self.ENTRY) is True

    def test_a_real_opportunity_anywhere_is_not(self, reads, person):
        reads["generated"] = False
        assert kinds.WorkflowKind().reads_only_generated(person, self.ENTRY) is False

    def test_cannot_tell_is_not(self, reads, person):
        reads["reads"] = None
        assert kinds.WorkflowKind().reads_only_generated(person, self.ENTRY) is False

    def test_reading_other_workflows_runs_is_not(self, reads, person):
        reads["data"] = {"workflow_sources": [{"definition_id": 9}]}
        assert kinds.WorkflowKind().reads_only_generated(person, self.ENTRY) is False


def test_the_token_endpoint_refuses_other_grants(client, client_row):
    response = client.post(TOKEN_URL, {"grant_type": "client_credentials"})
    assert response.json()["error"] == "unsupported_grant_type"


def test_embedding_switched_off_answers_404(client, key, client_row, person, settings):
    settings.LABS_EMBED_ENABLED = False
    assert redeem(client, assertion(key)).status_code == 404


# --- the session ----------------------------------------------------------------------------


def bootstrap(client, key):
    return redeem(client, assertion(key)).json()["access_token"]


def start(client, token, **overrides):
    payload = {"token": token, "client_id": "connect", "kind": "workflow", "id": "123", **overrides}
    return client.post("/embed/session/", payload, content_type="application/json")


def test_a_bootstrap_token_starts_a_partitioned_session(client, key, client_row, person):
    response = start(client, bootstrap(client, key))
    assert response.status_code == 200
    header = response.cookies["labs_embed_workflow_123"].output()
    for attribute in ("Secure", "HttpOnly", "SameSite=None", "Partitioned", "Path=/", "Max-Age=3600"):
        assert attribute in header
    session = EmbedSession.objects.get()
    assert session.started_at and session.session_checksum and session.live


def test_a_bootstrap_token_starts_one_session_only(client, key, client_row, person):
    token = bootstrap(client, key)
    assert start(client, token).status_code == 200
    assert start(client, token).status_code == 400


def test_a_bootstrap_token_is_for_its_own_resource(client, key, client_row, person):
    assert start(client, bootstrap(client, key), id="124").status_code == 400


def test_an_expired_bootstrap_token_starts_nothing(client, key, client_row, person):
    token = bootstrap(client, key)
    EmbedSession.objects.update(bootstrap_expires_at=timezone.now() - timedelta(seconds=1))
    assert start(client, token).status_code == 400


# --- the frame and its headers ----------------------------------------------------------------


def test_the_frame_without_a_session_is_a_data_free_bootstrap_page(client, client_row):
    response = client.get("/embed/c/connect/workflow/123/")
    assert response.status_code == 200
    assert "X-Frame-Options" not in response
    assert f"frame-ancestors {PARENT}" in response["Content-Security-Policy"]
    report_only = response.get("Content-Security-Policy-Report-Only", "")
    assert "frame-ancestors 'none'" not in report_only
    assert b"labs-embed-token" in response.content
    assert response["Cache-Control"] == "no-store"


def test_an_unregistered_resource_has_no_frame(client, client_row):
    assert client.get("/embed/c/connect/workflow/999/").status_code == 404
    assert client.get("/embed/c/connect/nothing/123/").status_code == 404
    assert client.get("/embed/c/nobody/workflow/123/").status_code == 404


def test_the_rest_of_labs_still_refuses_to_be_framed(client, client_row):
    response = client.get("/embed/c/connect/workflow/999/")
    assert response["X-Frame-Options"] == "DENY"


def test_a_live_session_draws_the_resource_by_its_kind(client, key, client_row, person, monkeypatch):
    drawn = {}

    def render(self, request, session):
        drawn["user"] = request.user
        drawn["context"] = request.labs_context
        return HttpResponse("drawn")

    monkeypatch.setattr(kinds.WorkflowKind, "render", render)
    start(client, bootstrap(client, key))
    response = client.get("/embed/c/connect/workflow/123/")
    assert response.content == b"drawn"
    assert drawn == {"user": person, "context": {"opportunity_id": 456}}
    assert f"frame-ancestors {PARENT}" in response["Content-Security-Policy"]


def test_a_session_draws_its_frame_once(client, key, client_row, person, monkeypatch):
    """A second load of the frame -- a reload, or the embedder's page opened again by
    someone else -- never shows the earlier person: it asks its parent for a new token."""
    monkeypatch.setattr(kinds.WorkflowKind, "render", lambda self, request, session: HttpResponse("drawn"))
    start(client, bootstrap(client, key))
    assert client.get("/embed/c/connect/workflow/123/").content == b"drawn"
    again = client.get("/embed/c/connect/workflow/123/")
    assert again.content != b"drawn" and b"needs-token" in again.content
    # ...while the session still serves the reads of the document it drew.
    assert EmbedSession.objects.get().live


def test_a_new_token_replaces_the_frames_session(client, key, client_row, person, monkeypatch):
    monkeypatch.setattr(
        kinds.WorkflowKind, "render", lambda self, request, session: HttpResponse(request.user.username)
    )
    start(client, bootstrap(client, key))
    assert client.get("/embed/c/connect/workflow/123/").content == b"pat"
    other = UserFactory(username="sam")
    UserConnectToken.objects.create(
        user=other, access_token="t2", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    token = redeem(client, assertion(key, sub="sam")).json()["access_token"]
    assert start(client, token).status_code == 200
    assert client.get("/embed/c/connect/workflow/123/").content == b"sam"


def test_the_sandbox_shell_may_sit_under_an_embedder(client, client_row):
    from django.core.cache import cache

    cache.delete("embed-all-ancestor-origins")
    response = client.get("/labs/sandbox/render/")
    ancestors = [p for p in response["Content-Security-Policy"].split("; ") if p.startswith("frame-ancestors")]
    assert ancestors and PARENT in ancestors[0]


# --- what a session may reach ----------------------------------------------------------------


@pytest.fixture
def live_session(key, client_row, person):
    raw = "raw-session-token"
    from canopy_sdk.contract import token_checksum

    session = EmbedSession.objects.create(
        client=client_row,
        user=person,
        kind="workflow",
        resource_id="123",
        entry=client_row.resources[0],
        state={"run_id": 77},
        bootstrap_checksum="b",
        bootstrap_expires_at=timezone.now(),
        started_at=timezone.now(),
        session_checksum=token_checksum(raw),
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return session, raw


def through_middleware(path, raw, method="get", cookie="labs_embed_workflow_123", **extra):
    request = getattr(RequestFactory(), method)(path, **extra)
    request.COOKIES[cookie] = raw
    request.user = AnonymousUser()
    original = object()
    request.session = original
    seen = {}

    def get_response(req):
        seen["user"] = req.user
        seen["context"] = getattr(req, "labs_context", None)
        seen["oauth"] = req.session.get("labs_oauth") if req.session is not original else None
        seen["csrf_exempt"] = getattr(req, "_dont_enforce_csrf_checks", False)
        return HttpResponse("ok")

    response = EmbedSessionMiddleware(get_response)(request)
    assert request.session is original  # SessionMiddleware never sees the embed's
    return response, seen


@pytest.mark.parametrize(
    "path",
    [
        "/labs/workflow/api/123/pipeline-rows/",
        "/labs/workflow/api/123/pipeline-data/stream/",
        "/labs/workflow/api/123/semantic/",
        "/labs/workflow/api/run/77/snapshot/",
        "/labs/workflow/api/workers/",
        "/embed/c/connect/workflow/123/",
    ],
)
def test_a_session_acts_as_its_person_on_its_own_reads(live_session, person, path):
    _, raw = live_session
    response, seen = through_middleware(path, raw)
    assert response.status_code == 200
    assert seen["user"] == person
    assert seen["context"] == {"opportunity_id": 456}
    assert seen["oauth"]["access_token"] == "connect-token"
    assert seen["oauth"]["organization_data"] == ORG_DATA


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", "/labs/workflow/api/124/pipeline-rows/"),  # another workflow
        ("get", "/labs/workflow/api/run/78/snapshot/"),  # another run
        ("post", "/labs/workflow/api/run/77/state/"),  # a write
        ("post", "/labs/workflow/api/123/render-code/"),
        ("post", "/labs/workflow/api/run/77/actions/coach/run/"),
        ("get", "/labs/workflow/123/run/"),  # the full labs page
        ("get", "/labs/admin/"),
        ("get", "/mcp/"),
        ("post", "/labs/mcp/tokens/create/"),
    ],
)
def test_a_session_carries_no_authority_anywhere_else(live_session, method, path):
    _, raw = live_session
    _, seen = through_middleware(path, raw, method=method, HTTP_ORIGIN=LABS)
    assert not seen["user"].is_authenticated
    assert seen["oauth"] is None


def test_a_post_read_must_come_from_the_labs_frame(live_session, settings):
    _, raw = live_session
    response, seen = through_middleware(
        "/labs/workflow/api/123/pipeline-query/", raw, method="post", HTTP_ORIGIN="https://evil.example.org"
    )
    assert response.status_code == 403 and not seen
    response, seen = through_middleware("/labs/workflow/api/123/pipeline-query/", raw, method="post", HTTP_ORIGIN=LABS)
    assert response.status_code == 200 and seen["csrf_exempt"]


@pytest.mark.parametrize(
    "change",
    [
        lambda s: EmbedSession.objects.filter(pk=s.pk).update(revoked_at=timezone.now()),
        lambda s: EmbedSession.objects.filter(pk=s.pk).update(expires_at=timezone.now() - timedelta(seconds=1)),
        lambda s: EmbedClient.objects.filter(pk=s.client_id).update(active=False),
        lambda s: type(s.user).objects.filter(pk=s.user_id).update(is_active=False),
    ],
)
def test_an_ended_session_is_nobody(live_session, change):
    session, raw = live_session
    change(session)
    _, seen = through_middleware("/labs/workflow/api/123/pipeline-rows/", raw)
    assert not seen["user"].is_authenticated


def test_a_session_cookie_names_its_own_resource(live_session):
    _, raw = live_session
    _, seen = through_middleware("/labs/workflow/api/123/pipeline-rows/", raw, cookie="labs_embed_workflow_124")
    assert not seen["user"].is_authenticated


def test_a_lapsed_connect_sign_in_asks_for_a_new_embed(live_session, person):
    _, raw = live_session
    UserConnectToken.objects.filter(user=person).delete()
    response, seen = through_middleware("/labs/workflow/api/123/pipeline-rows/", raw)
    assert response.status_code == 401 and not seen


# --- the registry ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "fields",
    [
        {"jwks": None, "jwks_uri": ""},
        {"jwks_uri": "http://keys.example.org/jwks.json", "jwks": None},
        {"allowed_origins": ["https://connect.example.org/path"]},
        {"allowed_origins": ["*"]},
        {"resources": [{"kind": "nothing", "id": 1, "opportunity_id": 2}]},
        {"resources": [{"kind": "workflow", "id": 1}]},
        {"resources": [{"kind": "workflow", "id": 1, "opportunity_id": 2, "program_id": 3}]},
        {"session_seconds": 9 * 3600},
    ],
)
def test_the_registry_refuses_a_bad_client(client_row, fields):
    from django.core.exceptions import ValidationError

    for name, value in fields.items():
        setattr(client_row, name, value)
    with pytest.raises(ValidationError):
        client_row.full_clean()


def test_a_good_client_is_valid(client_row):
    client_row.full_clean()


def test_the_cookie_name_is_safe():
    assert sessions.cookie_name("workflow", "12;3") == "labs_embed_workflow_12-3"


@pytest.mark.parametrize(
    "method, path",
    [("get", "/labs/workflow/api/123/pipeline-rows/"), ("post", "/labs/workflow/api/123/pipeline-query/")],
)
def test_a_request_from_an_opaque_origin_never_acts_as_the_person(live_session, method, path):
    """The sandboxed workflow frame inside an embedded page sends Origin: null. The
    embed cookie is SameSite=None, so this, not the browser, keeps it off the session."""
    _, raw = live_session
    response, seen = through_middleware(path, raw, method=method, HTTP_ORIGIN="null")
    assert response.status_code == 403 and not seen
