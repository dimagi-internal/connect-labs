from io import StringIO

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from connect_labs.mcp.models import MCPAccessToken
from connect_labs.users.models import User


@pytest.mark.django_db
def test_create_token_command_creates_and_prints():
    User.objects.create(username="zora")
    out = StringIO()
    call_command("mcp_create_token", "--user", "zora", "--name", "test", stdout=out)
    output = out.getvalue()
    assert "Token:" in output
    assert MCPAccessToken.objects.filter(user__username="zora", name="test").exists()


@pytest.mark.django_db
def test_create_token_unknown_user_errors():
    with pytest.raises(CommandError):
        call_command("mcp_create_token", "--user", "ghost", "--name", "t")


@pytest.mark.django_db
def test_create_token_no_expiry_when_ttl_zero():
    User.objects.create(username="perm")
    call_command("mcp_create_token", "--user", "perm", "--name", "t", "--ttl-days", "0", stdout=StringIO())
    token = MCPAccessToken.objects.get(user__username="perm")
    assert token.expires_at is None
