import pytest

from connect_labs.hqdata.tests.conftest import make_hq_user
from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.users.tests.factories import UserFactory

pytestmark = pytest.mark.django_db


def test_a_commcare_only_person_mints_a_token_that_is_shown_once_and_reaches_only_commcare(client):
    user = make_hq_user(client)
    resp = client.post("/commcare/tokens/", {"action": "create", "name": "notebook", "days": "30"})
    body = resp.content.decode()
    token = MCPAccessToken.objects.get(user=user)
    assert token.scope == token_scopes.COMMCARE and token.name == "notebook"
    raw = body.split('<code class="block break-all rounded bg-white p-2">')[1].split("</code>")[0]
    assert MCPAccessToken.verify(raw).pk == token.pk
    assert raw not in client.get("/commcare/tokens/").content.decode()  # not shown again


def test_the_lifetime_is_clamped_and_a_bad_one_is_ignored(client):
    make_hq_user(client)
    client.post("/commcare/tokens/", {"action": "create", "name": "a", "days": "999999"})
    client.post("/commcare/tokens/", {"action": "create", "name": "b", "days": "soon"})
    longest = max(t.expires_at for t in MCPAccessToken.objects.all()) - min(
        t.created_at for t in MCPAccessToken.objects.all()
    )
    assert longest.days <= MCPAccessToken.MAX_TTL_DAYS + 1 and MCPAccessToken.objects.count() == 2


def test_a_token_is_revoked_only_by_its_owner(client):
    owner = make_hq_user(client, username="a@example.org")
    token, raw = MCPAccessToken.create_token(owner, "t", scope=token_scopes.COMMCARE)
    other_token, other_raw = MCPAccessToken.create_token(UserFactory(), "t", scope=token_scopes.COMMCARE)
    client.post("/commcare/tokens/", {"action": "revoke", "token_id": other_token.pk})
    assert MCPAccessToken.verify(other_raw) is not None
    client.post("/commcare/tokens/", {"action": "revoke", "token_id": token.pk})
    assert MCPAccessToken.verify(raw) is None


def test_the_page_lists_commcare_tokens_only_and_needs_a_sign_in(client):
    assert client.get("/commcare/tokens/").status_code == 302
    user = make_hq_user(client)
    MCPAccessToken.create_token(user, "narrow", scope=token_scopes.COMMCARE)
    MCPAccessToken.create_token(user, "other", scope=token_scopes.NO_USERVISIT_DATA)
    page = client.get("/commcare/tokens/").content.decode()
    assert "narrow" in page and ">other<" not in page
