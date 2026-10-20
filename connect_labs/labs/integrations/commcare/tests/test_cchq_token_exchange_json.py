"""A refresh-token exchange answered 200 with a non-JSON body is a CCHQTokenError.

Every caller (run_scheduled_workflow, workflow_run_default, the history and cache MCP
tools) catches CCHQTokenError and carries on without an HQ token. A bare ValueError from
response.json() escaped them instead.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from connect_labs.labs.integrations.commcare import cchq_tokens


@override_settings(COMMCARE_OAUTH_CLIENT_ID="id", COMMCARE_OAUTH_CLIENT_SECRET="secret")
def test_a_200_with_an_unreadable_body_is_a_token_error():
    def not_json():
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    response = MagicMock(status_code=200, json=not_json, text="<html>proxy error</html>")
    with patch.object(cchq_tokens.httpx, "post", return_value=response):
        with pytest.raises(cchq_tokens.CCHQTokenError, match="invalid JSON") as caught:
            cchq_tokens._exchange_refresh_token("refresh-token")

    assert isinstance(caught.value.__cause__, ValueError)


@override_settings(COMMCARE_OAUTH_CLIENT_ID="id", COMMCARE_OAUTH_CLIENT_SECRET="secret")
def test_a_200_with_json_is_returned_unchanged():
    body = {"access_token": "a", "refresh_token": "r", "expires_in": 900}
    response = MagicMock(status_code=200, json=lambda: body, text=str(body))
    with patch.object(cchq_tokens.httpx, "post", return_value=response):
        assert cchq_tokens._exchange_refresh_token("refresh-token") == body
