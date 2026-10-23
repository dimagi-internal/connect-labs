"""The reference embedder: its protocol code, its pages, its registration, and the whole
round trip through labs' real token endpoint with the example's own key."""

from __future__ import annotations

from datetime import timedelta

import httpx
import jwt
import pytest
from canopy_sdk.contract import jwk_thumbprint
from canopy_sdk.keys import generate_private_key, private_pem
from django.core.management import CommandError, call_command
from django.test import Client
from django.utils import timezone

from connect_labs.embed import kinds
from connect_labs.embed.models import EmbedClient, EmbedSession
from connect_labs.embed_example.embedder import Embed, Embedder, EmbedError
from connect_labs.labs.models import UserConnectToken
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.users.tests.factories import UserFactory

LABS = "https://labs.example.org"
RESOURCES = [{"kind": "workflow", "id": 1, "opportunity_id": 10001, "label": "Stock review"}]

pytestmark = pytest.mark.django_db


@pytest.fixture
def pem():
    return private_pem(generate_private_key("EdDSA"))


@pytest.fixture(autouse=True)
def example(settings, pem):
    settings.LABS_PUBLIC_URL = LABS
    settings.ALLOWED_HOSTS = ["*"]
    settings.EMBED_EXAMPLE_PRIVATE_KEY = pem.replace("\n", "\\n")  # as an env var carries it
    settings.EMBED_EXAMPLE_CLIENT_ID = "labs-example"
    settings.EMBED_EXAMPLE_LABS_URL = ""
    settings.EMBED_EXAMPLE_BASE_URL = ""
    settings.EMBED_EXAMPLE_RESOURCES = RESOURCES


@pytest.fixture
def generated_opp():
    return SyntheticOpportunity.objects.create(
        opportunity_id=10001, labs_only=True, enabled=True, gdrive_folder_id="f1", generated_folder_id="f1"
    )


@pytest.fixture
def person():
    user = UserFactory(username="pat")
    UserConnectToken.objects.create(
        user=user, access_token="t", refresh_token="r", expires_at=timezone.now() + timedelta(hours=1)
    )
    return user


def embedder_for(pem) -> Embedder:
    return Embedder(client_id="labs-example", private_key_pem=pem, labs_url=LABS)


# --- embedder.py: the protocol ----------------------------------------------------------


def test_the_statement_is_what_the_integration_guide_says(pem):
    embedder = embedder_for(pem)
    token = embedder.statement("pat", "workflow", 1)
    jwk = embedder.jwks()["keys"][0]
    claims = jwt.decode(token, jwt.PyJWK.from_dict(jwk).key, algorithms=["EdDSA"], audience=f"{LABS}/embed/token/")
    assert claims["iss"] == "labs-example" and claims["sub"] == "pat"
    assert claims["resource"] == f"{LABS}/embed/c/labs-example/workflow/1/"
    assert claims["exp"] - claims["iat"] == 60 and claims["jti"]
    assert jwt.get_unverified_header(token)["kid"] == jwk["kid"]


def test_the_published_keys_are_public_and_the_kid_is_the_rfc7638_thumbprint(pem):
    jwk = embedder_for(pem).jwks()["keys"][0]
    assert "d" not in jwk and jwk["alg"] == "EdDSA" and jwk["use"] == "sig"
    assert jwk["kid"] == jwk_thumbprint(jwk)


def test_embed_returns_labs_answer_and_raises_its_refusal(pem):
    def answer(status, body):
        return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))

    ok = embedder_for(pem).embed(
        "pat",
        "workflow",
        1,
        client=answer(200, {"access_token": "tok", "embed_url": "u", "expires_in": 120, "token_type": "Bearer"}),
    )
    assert ok == Embed(token="tok", embed_url="u", expires_in=120)
    with pytest.raises(EmbedError) as refused:
        embedder_for(pem).embed(
            "pat", "workflow", 1, client=answer(400, {"error": "invalid_grant", "error_description": "No."})
        )
    assert (refused.value.error, refused.value.description, refused.value.status) == ("invalid_grant", "No.", 400)


# --- the whole round trip, through labs' real token endpoint ------------------------------


@pytest.fixture
def published_keys(monkeypatch):
    """Labs fetches the example's JWKS from its registered https URL, as for any system:
    answered by the example's own JWKS view, in-process, instead of over the network."""
    from urllib.parse import urlsplit

    from django.core.cache import cache

    cache.clear()
    fetched = []

    def get_json(url):
        fetched.append(url)
        return Client().get(urlsplit(url).path).json()

    monkeypatch.setattr("connect_labs.embed.keys.fetch.get_json", get_json)
    return fetched


def labs_transport():
    """Send the example's HTTP calls to this labs, in-process, instead of over the network."""
    django_client = Client()

    def handle(request: httpx.Request) -> httpx.Response:
        response = django_client.post(
            request.url.path, data=request.content, content_type=request.headers["content-type"]
        )
        return httpx.Response(response.status_code, content=response.content)

    return httpx.Client(transport=httpx.MockTransport(handle))


def test_the_example_round_trips_through_labs(generated_opp, person, pem, monkeypatch, published_keys):
    monkeypatch.setattr(kinds.WorkflowKind, "check_access", lambda self, *a: None)
    monkeypatch.setattr(kinds.WorkflowKind, "reads_only_generated", lambda self, user, entry: True)
    call_command("embed_example_register")
    embed = embedder_for(pem).embed("pat", "workflow", 1, client=labs_transport())
    assert embed.embed_url == f"{LABS}/embed/c/labs-example/workflow/1/"
    assert EmbedSession.objects.get().user == person
    assert published_keys == [f"{LABS}/embed-example/.well-known/jwks.json"]


def test_labs_refuses_the_example_anything_but_generated_data(generated_opp, person, pem, monkeypatch, published_keys):
    monkeypatch.setattr(kinds.WorkflowKind, "check_access", lambda self, *a: None)
    monkeypatch.setattr(kinds.WorkflowKind, "reads_only_generated", lambda self, user, entry: False)
    call_command("embed_example_register")
    with pytest.raises(EmbedError) as refused:
        embedder_for(pem).embed("pat", "workflow", 1, client=labs_transport())
    assert refused.value.error == "invalid_target"
    assert refused.value.description == "This system may show only generated data."


# --- registration -----------------------------------------------------------------------


def test_registration_limits_the_example_to_generated_data(generated_opp, settings):
    settings.EMBED_EXAMPLE_BASE_URL = "https://labs.example.org"
    call_command("embed_example_register")
    client = EmbedClient.objects.get(client_id="labs-example")
    assert client.identity_authority and client.generated_data_only and client.active
    assert client.jwks_uri == "https://labs.example.org/embed-example/.well-known/jwks.json" and client.jwks is None
    assert client.allowed_origins == ["https://labs.example.org"]
    assert client.resources == [{"kind": "workflow", "id": 1, "opportunity_id": 10001}]


def test_a_local_http_build_registers_its_keys_inline(generated_opp, settings, pem):
    settings.DEBUG = True
    settings.EMBED_EXAMPLE_LABS_URL = "http://localhost:8100"
    call_command("embed_example_register")
    client = EmbedClient.objects.get(client_id="labs-example")
    assert client.jwks == embedder_for(pem).jwks() and client.jwks_uri == ""


def test_registration_refuses_a_real_opportunity():
    with pytest.raises(CommandError, match="not generated data"):
        call_command("embed_example_register")
    assert not EmbedClient.objects.exists()


def test_registration_refuses_a_programme_scope(settings, generated_opp):
    settings.EMBED_EXAMPLE_RESOURCES = [{"kind": "workflow", "id": 1, "program_id": 5}]
    with pytest.raises(CommandError, match="opportunity_id"):
        call_command("embed_example_register")


def test_registration_needs_a_key(settings):
    settings.EMBED_EXAMPLE_PRIVATE_KEY = ""
    with pytest.raises(CommandError):
        call_command("embed_example_register")


# --- the example's own pages ------------------------------------------------------------------


@pytest.fixture
def signed_in(client, person):
    client.force_login(person)
    return client


@pytest.fixture
def minted(monkeypatch):
    calls = []

    def embed(self, username, kind, resource_id, client=None):
        calls.append((username, kind, resource_id))
        return Embed(token="one-time", embed_url=f"{LABS}/embed/c/labs-example/{kind}/{resource_id}/", expires_in=120)

    monkeypatch.setattr(Embedder, "embed", embed)
    return calls


def test_the_page_needs_a_labs_sign_in(client):
    response = client.get("/embed-example/")
    assert response.status_code == 302 and "login" in response["Location"]


def test_the_fragment_handoff_puts_the_token_after_the_hash(signed_in, minted, person):
    response = signed_in.get("/embed-example/")
    assert f'src="{LABS}/embed/c/labs-example/workflow/1/#token=one-time"'.encode() in response.content
    assert minted == [("pat", "workflow", "1")]


def test_the_message_handoff_frames_labs_with_no_token(signed_in, minted):
    response = signed_in.get("/embed-example/?handoff=message")
    assert f'src="{LABS}/embed/c/labs-example/workflow/1/"'.encode() in response.content
    assert b"#token=" not in response.content and minted == []
    assert response.context["example_config"]["labsOrigin"] == LABS


def test_the_token_endpoint_only_ever_names_the_signed_in_person(signed_in, minted):
    response = signed_in.post("/embed-example/token/?resource=workflow/1&sub=someone-else&username=root")
    assert response.json() == {"token": "one-time", "embed_url": f"{LABS}/embed/c/labs-example/workflow/1/"}
    assert minted == [("pat", "workflow", "1")]


def test_the_token_endpoint_shows_only_the_examples_resources(signed_in, minted):
    assert signed_in.post("/embed-example/token/?resource=workflow/999").status_code == 404


def test_a_refusal_from_labs_is_shown_not_hidden(signed_in, monkeypatch):
    def refuse(self, *a, **k):
        raise EmbedError("invalid_grant", "That person has not signed in to labs yet.", 400)

    monkeypatch.setattr(Embedder, "embed", refuse)
    response = signed_in.get("/embed-example/")
    assert b"That person has not signed in to labs yet." in response.content


def test_the_jwks_is_public(client, pem):
    response = client.get("/embed-example/.well-known/jwks.json")
    assert response.status_code == 200 and response.json() == embedder_for(pem).jwks()


def test_an_unconfigured_example_does_nothing(client, signed_in, settings):
    settings.EMBED_EXAMPLE_PRIVATE_KEY = ""
    assert b"not configured" in signed_in.get("/embed-example/").content
    assert client.get("/embed-example/.well-known/jwks.json").status_code == 404
