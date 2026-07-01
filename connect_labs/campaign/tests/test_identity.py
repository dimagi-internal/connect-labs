from unittest.mock import MagicMock, patch

import pytest

from connect_labs.campaign.auth import identity


def _resp(status, payload):
    m = MagicMock()
    m.status_code = status
    m.json.return_value = payload
    return m


def test_fetch_identity_normalizes_name():
    payload = {
        "username": "amara@dimagi.com",
        "email": "amara@dimagi.com",
        "first_name": "Amara",
        "last_name": "Okafor",
        "domains": ["ng-campaign"],
    }
    with patch.object(identity.httpx, "get", return_value=_resp(200, payload)) as g:
        out = identity.fetch_identity("tok")
    assert out == {
        "username": "amara@dimagi.com",
        "email": "amara@dimagi.com",
        "name": "Amara Okafor",
        "domains": ["ng-campaign"],
    }
    # Bearer header sent
    assert g.call_args.kwargs["headers"]["Authorization"] == "Bearer tok"


def test_fetch_identity_raises_on_401():
    with patch.object(identity.httpx, "get", return_value=_resp(401, {})):
        with pytest.raises(identity.IdentityError):
            identity.fetch_identity("bad")
