from unittest.mock import MagicMock, patch

from connect_labs.mcp.tools import pages as pages_tools


@patch("connect_labs.mcp.tools.pages.list_providers")
def test_pages_list_providers_returns_catalog(mock_list):
    prov = MagicMock(key="audit", label="Audit summary", target_kind="opportunity")
    mock_list.return_value = [prov]
    result = pages_tools.pages_list_providers(user=MagicMock())
    assert result["providers"][0]["key"] == "audit"
    assert result["providers"][0]["target_kind"] == "opportunity"


@patch("connect_labs.mcp.tools.pages.require_connect_token", return_value="tok")
@patch("connect_labs.mcp.tools.pages.SurfaceDataAccess")
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
    mock_da_cls.assert_called_once_with(access_token="tok", program_id=25, opportunity_id=None, organization_id=None)
    mock_da_cls.return_value.create_surface.assert_called_once()


@patch("connect_labs.mcp.tools.pages.require_connect_token", return_value="tok")
@patch("connect_labs.mcp.tools.pages.SurfaceDataAccess")
def test_pages_update_writes_surface(mock_da_cls, _tok):
    mock_da_cls.return_value.update_surface.return_value = {
        "id": 1,
        "slug": "s",
        "title": "T2",
        "cards": [],
        "options": {},
    }
    result = pages_tools.pages_update(user=MagicMock(), record_id=1, slug="s", title="T2", cards=[], program_id="25")
    assert result["title"] == "T2"
    mock_da_cls.assert_called_once_with(access_token="tok", program_id=25, opportunity_id=None)
    mock_da_cls.return_value.update_surface.assert_called_once()


@patch("connect_labs.mcp.tools.pages.require_connect_token", return_value="tok")
@patch("connect_labs.mcp.tools.pages.SurfaceDataAccess")
def test_pages_create_opp_scoped_not_public(mock_da_cls, _tok):
    mock_da_cls.return_value.create_surface.return_value = {"id": 1, "slug": "s"}
    pages_tools.pages_create(user=MagicMock(), slug="s", title="T", cards=[], opportunity_id="1973")
    assert mock_da_cls.call_args.kwargs["opportunity_id"] == 1973
    ckw = mock_da_cls.return_value.create_surface.call_args.kwargs
    assert ckw.get("public", False) is False


@patch("connect_labs.mcp.tools.pages.require_connect_token", return_value="tok")
@patch("connect_labs.mcp.tools.pages.resolve_surface")
def test_pages_get_uses_scope_context(mock_resolve, _tok):
    mock_resolve.return_value = {"id": 1, "slug": "eha-muac"}
    out = pages_tools.pages_get(user=MagicMock(), slug="eha-muac", opportunity_id="1973")
    assert out["surface"]["slug"] == "eha-muac"
    assert mock_resolve.call_args.args[1] == {"opportunity_id": 1973}
