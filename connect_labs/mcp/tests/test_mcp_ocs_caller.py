"""Under an MCP call, a pipeline reads Open Chat Studio as the caller, never with the
server's team key -- which reads every bot's sessions."""

import datetime as dt

import pytest
from django.test import override_settings
from django.utils import timezone

from connect_labs.labs.integrations.ocs.ocs_tokens import current_mcp_caller, mcp_caller
from connect_labs.labs.models import UserOCSToken
from connect_labs.users.models import User

SCHEMA = {"data_source": {"type": "ocs_sessions", "experiment_id": "bot-1"}, "fields": []}


def _config(schema):
    from connect_labs.workflow.data_access import PipelineDataAccess

    access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
    return access._schema_to_config(schema, definition_id=20001)


@pytest.fixture
def user(db):
    return User.objects.create(username="ocs-caller", email="ocs-caller@dimagi.com")


@override_settings(OCS_API_KEY="SERVER-TEAM-KEY")
def test_the_web_keeps_the_server_key(user):
    source = _config(SCHEMA).data_source
    assert source.api_key == "SERVER-TEAM-KEY"
    assert source.bearer_token == ""


@override_settings(OCS_API_KEY="SERVER-TEAM-KEY")
def test_an_mcp_call_reads_with_the_callers_own_token(user):
    UserOCSToken.objects.create(
        user=user, access_token="CALLER-TOKEN", expires_at=timezone.now() + dt.timedelta(hours=1)
    )
    with mcp_caller(user):
        source = _config(SCHEMA).data_source
    assert source.api_key == ""
    assert source.bearer_token == "CALLER-TOKEN"
    assert current_mcp_caller() is None  # reset after the call


@override_settings(OCS_API_KEY="SERVER-TEAM-KEY")
def test_an_mcp_caller_without_ocs_is_told_to_connect_not_given_the_server_key(user):
    with mcp_caller(user), pytest.raises(ValueError) as e:
        _config(SCHEMA)
    assert "/labs/ocs/initiate/" in str(e.value)


@override_settings(OCS_API_KEY="SERVER-TEAM-KEY")
def test_a_non_ocs_pipeline_is_untouched_under_mcp(user):
    with mcp_caller(user):
        source = _config({"data_source": {"type": "connect_csv"}, "fields": []}).data_source
    assert source.bearer_token == ""


def test_the_fetcher_sends_the_callers_token_as_a_bearer(monkeypatch):
    import httpx

    from connect_labs.labs.analysis.backends.sql import ocs_fetcher
    from connect_labs.labs.analysis.config import DataSourceConfig

    seen = {}

    class _Client:
        def __init__(self, headers=None, timeout=None):
            seen.update(headers or {})

        def get(self, url, params=None):
            raise RuntimeError("stop")

        def close(self):
            pass

    monkeypatch.setattr(httpx, "Client", _Client)
    source = DataSourceConfig(type="ocs_sessions", experiment_id="bot-1", bearer_token="CALLER-TOKEN")
    with pytest.raises(Exception):
        ocs_fetcher.fetch_ocs_sessions_as_visit_dicts(None, source)
    assert seen == {"Authorization": "Bearer CALLER-TOKEN"}
