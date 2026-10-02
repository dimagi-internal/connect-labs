"""pipeline_update_schema / pipeline_preview stamp a gdrive source's authorization.

The fetcher refuses an unstamped source (test_gdrive_fetcher.py); these pin the
save side: a Dimagi staff save is stamped for the call's opportunity, anyone
else's save of a NEW Drive target is refused before anything is written.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.gdrive_fetcher import verify_gdrive_authorization
from connect_labs.labs.analysis.config import DataSourceConfig
from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.users.models import User

DRIVE_SCHEMA = {
    "data_source": {"type": "gdrive", "folder_id": "folderA", "file_pattern": "answers_scored_*.csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": [{"name": "quality", "path": "row.quality_score", "aggregation": "first"}],
}


def _user(email):
    user = User.objects.create(username=email.split("@")[0], email=email)
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(user=user, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    return raw


def _update(raw, mock_pda_cls):
    current = MagicMock()
    current.version = 1
    mock_pda_cls.return_value.get_definition.return_value = current
    mock_pda_cls.return_value.update_definition.return_value = MagicMock(version=2)
    return call_tool(
        raw,
        "pipeline_update_schema",
        {"pipeline_id": 7, "opportunity_id": 1251, "schema": DRIVE_SCHEMA, "expected_version": 1},
    )


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_staff_save_is_stamped_for_the_opportunity(mock_pda_cls):
    data = _update(_user("analyst@dimagi.com"), mock_pda_cls)
    assert data["result"]["isError"] is False, data
    saved = mock_pda_cls.return_value.update_definition.call_args.kwargs["schema"]["data_source"]
    assert saved["authorization"]["authorized_by"] == "analyst@dimagi.com"
    verify_gdrive_authorization(DataSourceConfig(**saved), 1251)


@pytest.mark.django_db
@patch("connect_labs.mcp.tools.pipelines.PipelineDataAccess")
def test_partner_save_of_a_drive_target_is_refused_and_nothing_is_written(mock_pda_cls):
    data = _update(_user("someone@partner.org"), mock_pda_cls)
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "PERMISSION_DENIED"
    mock_pda_cls.return_value.update_definition.assert_not_called()


@pytest.mark.django_db
def test_malformed_drive_source_is_invalid_schema():
    bad = {**DRIVE_SCHEMA, "data_source": {"type": "gdrive"}}
    data = call_tool(
        _user("analyst@dimagi.com"),
        "pipeline_update_schema",
        {"pipeline_id": 7, "opportunity_id": 1251, "schema": bad, "expected_version": 1},
    )
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"
