"""Tests for the semantic_registry_* MCP tools.

The one these exist for is `semantic_registry_explain`. Asked for every
indicator it used to return ~1.17M characters for the KMC registry -- 37
indicators, each carrying its own copy of one 27KB compiled statement -- so the
obvious first call from a second engine ("explain all of your logic") failed
outright against every client's tool-result limit
(dimagi-internal/connect-labs#1743).

Mirrors the pipeline tool tests: mock the data access, drive the real tool
in-process through `call_tool`.
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.testing import call_tool
from connect_labs.semantic.seed import registry_payload
from connect_labs.users.models import User

#: What a client will take in one tool result, roughly: Claude Code's default cap
#: is 25k tokens. Characters are the stable thing to assert on, at ~3.5 per token.
_CLIENT_RESULT_LIMIT_CHARS = 25_000 * 3


@pytest.fixture
def auth_user(db):
    user = User.objects.create(username="semantictest")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


@pytest.fixture
def kmc_record():
    """A registry record holding the real KMC documents, straight off disk."""
    payload = registry_payload("kmc")
    return MagicMock(
        id=5500,
        version=11,
        properties_doc=payload["properties"],
        indicators_doc=payload["indicators"],
        deployment=payload["deployment"],
    )


def _explain(raw, kmc_record, arguments):
    with patch("connect_labs.mcp.tools.semantic.SemanticRegistryDataAccess") as access_cls:
        access_cls.return_value.get_registry.return_value = kmc_record
        return call_tool(raw, "semantic_registry_explain", arguments)


@pytest.mark.django_db
def test_explaining_everything_returns_an_index_a_client_can_actually_receive(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert len(json.dumps(content)) < _CLIENT_RESULT_LIMIT_CHARS, "the index is too big to return in one result"
    # Every top-level indicator is named, so nothing is hidden by the summary.
    named = {row["indicator"] for row in content["indicators"]}
    assert {"C01", "C14", "N15"} <= named
    # ...as one line each, not a chain, and with no compiled statement per row.
    row = next(r for r in content["indicators"] if r["indicator"] == "C14")
    assert row["title"] == "Mortality"
    # Both readings: the authored wording, and the one rendered from the SQL so it
    # cannot drift from the number.
    assert "deaths" in row["plain"].lower()
    assert "died" in row["definition"].lower()
    assert "properties" not in row and "compiled_sql" not in row
    assert "Call again with `indicators`" in content["detail"]


@pytest.mark.django_db
def test_naming_indicators_returns_their_full_chain(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["C14"]})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    explanation = content["indicators"][0]
    assert explanation["indicator"] == "C14"
    # The chain a second engine needs to reproduce the number.
    assert explanation["expression"]["compiled"]
    assert [c["name"] for c in explanation["components"]] == ["c14_numerator", "c14_denominator"]
    assert {p["name"] for p in explanation["properties"]} >= {"died", "eligible", "outcome_known"}
    assert explanation["constants"]["ELIG_DAYS"] == 28


@pytest.mark.django_db
def test_the_compiled_statement_comes_back_once_not_once_per_indicator(auth_user, kmc_record):
    """It is the whole scope's SELECT, identical for every indicator asked for.

    Returning a copy inside each one is what made asking for several cost
    megabytes.
    """
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["C14", "C15", "N15"]})

    content = data["result"]["structuredContent"]
    assert "pipeline_visit_rows" in content["compiled_sql"]
    assert content["layer1"].startswith("Visit rows come from")
    assert len(content["indicators"]) == 3
    for explanation in content["indicators"]:
        assert "compiled_sql" not in explanation, "the statement is repeated per indicator again"


@pytest.mark.django_db
def test_the_scope_still_decides_what_the_statement_groups_by(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["C14"], "scope": "llo"})

    content = data["result"]["structuredContent"]
    assert content["scope"] == "llo"
    assert "GROUP BY props.llo" in content["compiled_sql"]


@pytest.mark.django_db
def test_an_unknown_indicator_is_still_a_clean_not_found(auth_user, kmc_record):
    _, raw = auth_user

    data = _explain(raw, kmc_record, {"registry_id": 5500, "indicators": ["C99"]})

    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"
