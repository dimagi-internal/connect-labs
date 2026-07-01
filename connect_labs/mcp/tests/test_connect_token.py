from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.users.models import User


@pytest.mark.django_db
def test_returns_token_for_user_with_stored_token():
    user = User.objects.create(username="ok")
    UserConnectToken.objects.create(
        user=user,
        access_token="tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert require_connect_token(user) == "tok"


@pytest.mark.django_db
def test_raises_mcp_tool_error_when_no_token():
    user = User.objects.create(username="none")
    with pytest.raises(MCPToolError) as exc:
        require_connect_token(user)
    assert exc.value.code == "PERMISSION_DENIED"
