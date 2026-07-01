from unittest.mock import MagicMock, patch

from commcare_connect.mcp.tools import pages as pages_tools


@patch("commcare_connect.mcp.tools.pages.list_providers")
def test_pages_list_providers_returns_catalog(mock_list):
    prov = MagicMock(key="audit", label="Audit summary", target_kind="opportunity")
    mock_list.return_value = [prov]
    result = pages_tools.pages_list_providers(user=MagicMock())
    assert result["providers"][0]["key"] == "audit"
    assert result["providers"][0]["target_kind"] == "opportunity"


@patch("commcare_connect.mcp.tools.pages.require_connect_token", return_value="tok")
@patch("commcare_connect.mcp.tools.pages.SurfaceDataAccess")
def test_pages_create_writes_surface(mock_da_cls, _tok):
    mock_da_cls.return_value.create_surface.return_value = {
        "id": 1,
        "slug": "s",
        "title": "T",
        "cards": [],
        "options": {},
    }
    result = pages_tools.pages_create(user=MagicMock(), slug="s", title="T", cards=[], program_id="25")
    assert result["slug"] == "s"
    mock_da_cls.assert_called_once_with(access_token="tok", program_id=25, opportunity_id=None)
    mock_da_cls.return_value.create_surface.assert_called_once()
