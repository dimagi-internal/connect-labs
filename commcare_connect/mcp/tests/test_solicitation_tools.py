"""Tests for migrated solicitation MCP tools (B.2).

Covers all 7 tools: list_solicitations, get_solicitation, list_responses,
get_response (reads), create_solicitation, update_solicitation, award_response
(writes).

Each write tool also has an is_write registry check to confirm rate-limiting
and audit apply.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from commcare_connect.labs.models import UserConnectToken
from commcare_connect.mcp.models import MCPAccessToken
from commcare_connect.mcp.testing import call_tool
from commcare_connect.mcp.tool_registry import get_tool
from commcare_connect.users.models import User

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_user(db):
    """User with a PAT AND a UserConnectToken (fully set up for tool calls)."""
    user = User.objects.create(username="sol-test")
    _, raw = MCPAccessToken.create_token(user, name="t")
    UserConnectToken.objects.create(
        user=user,
        access_token="connect-tok",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    return user, raw


def _call_tool(client, raw_pat, tool_name, arguments):
    # client is unused: the MCP protocol endpoint is now a FastMCP ASGI app,
    # not a Django view. call_tool drives the same auth/handler/audit/rate-limit
    # path in-process and returns the same JSON-RPC-shaped envelope.
    return call_tool(raw_pat, tool_name, arguments)


def _make_mock_record(record_id, rtype, experiment="25", program_id=25, data=None, labs_record_id=None, public=False):
    """Build a MagicMock that mimics a LocalLabsRecord."""
    rec = MagicMock()
    rec.id = record_id
    rec.type = rtype
    rec.experiment = experiment
    rec.program_id = program_id
    rec.labs_record_id = labs_record_id
    rec.data = data or {}
    rec.public = public
    return rec


# ---------------------------------------------------------------------------
# Registry tests — is_write flags
# ---------------------------------------------------------------------------


def test_write_tools_flagged_is_write():
    """Writes must be registered with is_write=True so rate limiting and audit apply."""
    for name in ("create_solicitation", "update_solicitation", "award_response", "delete_solicitation"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is True, f"{name} should have is_write=True"


def test_read_tools_not_flagged_is_write():
    """Read tools must NOT be flagged as writes."""
    for name in ("list_solicitations", "get_solicitation", "list_responses", "get_response"):
        tool = get_tool(name)
        assert tool is not None, f"{name} not registered"
        assert tool.is_write is False, f"{name} should have is_write=False"


# ---------------------------------------------------------------------------
# list_solicitations
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_happy_path(mock_client_cls, client, auth_user):
    """Returns {solicitations: [...]} with flattened record dicts."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = [
        _make_mock_record(1, "solicitation", data={"title": "Sol A", "status": "active"}),
        _make_mock_record(2, "solicitation", data={"title": "Sol B", "status": "closed"}),
    ]

    data = _call_tool(client, raw, "list_solicitations", {"program_id": "25"})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "solicitations" in content
    assert len(content["solicitations"]) == 2
    # Flat merge: top-level fields + data fields
    first = content["solicitations"][0]
    assert first["id"] == 1
    assert first["title"] == "Sol A"
    assert first["status"] == "active"
    assert first["program_id"] == 25


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_filters_passed(mock_client_cls, client, auth_user):
    """Status and solicitation_type kwargs are forwarded as data__{field} filters."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = []

    _call_tool(
        client,
        raw,
        "list_solicitations",
        {"program_id": "7", "status": "active", "solicitation_type": "grant"},
    )

    mock_client.get_records.assert_called_once_with(
        type="solicitation",
        experiment="7",
        status="active",
        solicitation_type="grant",
    )


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_no_scope(mock_client_cls, client, auth_user):
    """Without program_id/organization_id the experiment kwarg is omitted."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = []

    _call_tool(client, raw, "list_solicitations", {})

    mock_client.get_records.assert_called_once_with(type="solicitation")


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_solicitations_propagates_scope_to_client(mock_client_cls, client, auth_user):
    """program_id/organization_id must be threaded into LabsRecordAPIClient init.

    Without scope at the client level, the prod-side membership check never runs
    and only is_public=true records come back. This test locks in that scope is
    actually forwarded so non-public reads work.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = []

    _call_tool(client, raw, "list_solicitations", {"program_id": "130"})

    init_kwargs = mock_client_cls.call_args.kwargs
    assert init_kwargs["program_id"] == 130
    assert init_kwargs.get("organization_id") is None

    mock_client_cls.reset_mock()
    _call_tool(client, raw, "list_solicitations", {"organization_id": "45"})
    init_kwargs = mock_client_cls.call_args.kwargs
    assert init_kwargs["organization_id"] == 45
    assert init_kwargs.get("program_id") is None


# ---------------------------------------------------------------------------
# get_solicitation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_solicitation_found(mock_client_cls, client, auth_user):
    """Returns the flat record dict when found."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(42, "solicitation", data={"title": "My Sol"})

    data = _call_tool(client, raw, "get_solicitation", {"solicitation_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["title"] == "My Sol"
    mock_client.get_record_by_id.assert_called_once_with(42, type="solicitation")


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_solicitation_not_found(mock_client_cls, client, auth_user):
    """Returns an error when the record is missing."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "get_solicitation", {"solicitation_id": 999})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_solicitation_propagates_scope_to_client(mock_client_cls, client, auth_user):
    """program_id/organization_id must be threaded into LabsRecordAPIClient init.

    get_record_by_id only adds prod-side scope params from self.* attributes, so
    without scope at init time the read falls back to public-only and any non-
    public solicitation comes back as 'not found'.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(42, "solicitation", data={"title": "x"})

    _call_tool(client, raw, "get_solicitation", {"solicitation_id": 42, "program_id": "130"})

    init_kwargs = mock_client_cls.call_args.kwargs
    assert init_kwargs["program_id"] == 130


# ---------------------------------------------------------------------------
# list_responses
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_list_responses_happy_path(mock_client_cls, client, auth_user):
    """Returns {responses: [...]} scoped by solicitation_id via labs_record_id."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_records.return_value = [
        _make_mock_record(10, "solicitation_response", labs_record_id=42, data={"org": "Org A"}),
    ]

    data = _call_tool(client, raw, "list_responses", {"solicitation_id": 42})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert "responses" in content
    assert len(content["responses"]) == 1
    assert content["responses"][0]["org"] == "Org A"

    mock_client.get_records.assert_called_once_with(
        type="solicitation_response",
        labs_record_id=42,
    )


# ---------------------------------------------------------------------------
# get_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_response_found(mock_client_cls, client, auth_user):
    """Returns the flat dict when found."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        55, "solicitation_response", data={"status": "submitted"}
    )

    data = _call_tool(client, raw, "get_response", {"response_id": 55})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 55
    assert content["status"] == "submitted"
    mock_client.get_record_by_id.assert_called_once_with(55, type="solicitation_response")


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_response_not_found(mock_client_cls, client, auth_user):
    """Returns an error when the response record is missing."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "get_response", {"response_id": 888})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# create_solicitation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.SolicitationsDataAccess")
def test_create_solicitation_happy_path(mock_da_cls, client, auth_user):
    """The MCP tool forwards flat kwargs as a canonical data dict to data-access.

    Validation correctness is exercised in solicitations/tests/test_validation.py.
    This test pins the MCP wiring: kwargs → data dict → da.create_solicitation,
    response → serialized record.
    """
    _, raw = auth_user
    mock_da = MagicMock()
    mock_da_cls.return_value = mock_da
    mock_da.create_solicitation.return_value = _make_mock_record(
        77, "solicitation", data={"title": "New Sol", "status": "draft"}
    )

    data = _call_tool(
        client,
        raw,
        "create_solicitation",
        {
            "program_id": "25",
            "title": "New Sol",
            "description": "A useful solicitation.",
            "solicitation_type": "eoi",
            "status": "draft",
        },
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 77
    assert content["title"] == "New Sol"

    # Verify data-access was constructed with the right scope...
    mock_da_cls.assert_called_once()
    da_init_kwargs = mock_da_cls.call_args.kwargs
    assert da_init_kwargs["program_id"] == "25"

    # ...and called with the canonical-shape data dict.
    mock_da.create_solicitation.assert_called_once()
    payload = mock_da.create_solicitation.call_args.args[0]
    assert payload["title"] == "New Sol"
    assert payload["description"] == "A useful solicitation."
    assert payload["solicitation_type"] == "eoi"
    assert payload["status"] == "draft"
    assert payload["is_public"] is False  # data-access strips this and forwards to envelope


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.SolicitationsDataAccess")
def test_create_solicitation_propagates_is_public(mock_da_cls, client, auth_user):
    """is_public=true flows through to the data-access layer (which forwards to envelope)."""
    _, raw = auth_user
    mock_da = MagicMock()
    mock_da_cls.return_value = mock_da
    mock_da.create_solicitation.return_value = _make_mock_record(
        78, "solicitation", data={"title": "Public Sol"}, public=True
    )

    _call_tool(
        client,
        raw,
        "create_solicitation",
        {
            "program_id": "25",
            "title": "Public Sol",
            "description": "Publicly listed.",
            "solicitation_type": "rfp",
            "is_public": True,
        },
    )

    payload = mock_da.create_solicitation.call_args.args[0]
    assert payload["is_public"] is True


@pytest.mark.django_db
def test_create_solicitation_missing_scope(client, auth_user):
    """Fails with INVALID_SCHEMA if both program_id and organization_id are absent."""
    _, raw = auth_user

    data = _call_tool(
        client,
        raw,
        "create_solicitation",
        {
            "title": "Scopeless",
            "description": "x",
            "solicitation_type": "eoi",
        },
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.SolicitationsDataAccess")
def test_create_solicitation_maps_validation_error_to_invalid_schema(mock_da_cls, client, auth_user):
    """ValidationError from the shared validator surfaces as MCP INVALID_SCHEMA.

    This is the contract between the MCP transport and the data-access layer:
    schema drift caught by validate_solicitation_payload becomes a structured
    INVALID_SCHEMA error with per-field details — not an internal-error blob.
    """
    from django.core.exceptions import ValidationError

    _, raw = auth_user
    mock_da = MagicMock()
    mock_da_cls.return_value = mock_da
    mock_da.create_solicitation.side_effect = ValidationError({"description": "required, must be a non-empty string"})

    data = _call_tool(
        client,
        raw,
        "create_solicitation",
        {
            "program_id": "25",
            "title": "Drifted",
            "description": "x",
            "solicitation_type": "eoi",
        },
    )

    assert data["result"]["isError"] is True, data
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    # Field-level structure is preserved in details so callers can render per-field.
    assert "fields" in err["details"]
    assert "description" in err["details"]["fields"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.SolicitationsDataAccess")
def test_create_solicitation_forwards_connect_opportunity_id(mock_da_cls, client, auth_user):
    """connect_opportunity_id rides into the data dict for downstream review/award linkage."""
    _, raw = auth_user
    mock_da = MagicMock()
    mock_da_cls.return_value = mock_da
    mock_da.create_solicitation.return_value = _make_mock_record(
        99, "solicitation", data={"title": "Linked", "connect_opportunity_id": 1821}
    )

    _call_tool(
        client,
        raw,
        "create_solicitation",
        {
            "program_id": "25",
            "title": "Linked",
            "description": "x",
            "solicitation_type": "eoi",
            "connect_opportunity_id": 1821,
        },
    )

    payload = mock_da.create_solicitation.call_args.args[0]
    assert payload["connect_opportunity_id"] == 1821


# ---------------------------------------------------------------------------
# update_solicitation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_happy_path(mock_client_cls, client, auth_user):
    """Merges update_data into existing data and returns the updated record."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_record(42, "solicitation", data={"title": "Old Title", "status": "draft"})
    mock_client.get_record_by_id.return_value = existing

    updated = _make_mock_record(42, "solicitation", data={"title": "New Title", "status": "active"})
    mock_client.update_record.return_value = updated

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 42, "update_data": {"title": "New Title", "status": "active"}},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 42
    assert content["title"] == "New Title"

    # Verify the merged data passed to update_record
    mock_client.update_record.assert_called_once_with(
        record_id=42,
        experiment=existing.experiment,
        type=existing.type,
        data={"title": "New Title", "status": "active"},
        current_record=existing,
    )


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_propagates_scope_to_client(mock_client_cls, client, auth_user):
    """program_id/organization_id must be threaded into LabsRecordAPIClient init.

    The update path starts with get_record_by_id; without scope, that read only
    sees public records and a non-public solicitation is unupdatable.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    existing = _make_mock_record(42, "solicitation", data={"title": "Old"})
    mock_client.get_record_by_id.return_value = existing
    mock_client.update_record.return_value = existing

    _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 42, "update_data": {"status": "active"}, "program_id": "130"},
    )

    init_kwargs = mock_client_cls.call_args.kwargs
    assert init_kwargs["program_id"] == 130


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_get_solicitation_returns_canonical_is_public_from_envelope(mock_client_cls, client, auth_user):
    """`is_public` in the response is sourced from record.public, not data.

    Stale ``is_public`` keys in legacy ``data`` payloads are dropped — the
    response always has exactly one source of truth (the server flag).
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # Legacy record: data contains a stale is_public=True from before the
    # cleanup, but the actual server flag is False.
    mock_client.get_record_by_id.return_value = _make_mock_record(
        42, "solicitation", data={"title": "Sol", "is_public": True}, public=False
    )

    data = _call_tool(client, raw, "get_solicitation", {"solicitation_id": 42})
    content = data["result"]["structuredContent"]

    assert content["is_public"] is False  # sourced from envelope, NOT data
    assert "public" not in content  # the duplicate key is gone


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND error when the solicitation does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 999, "update_data": {"status": "closed"}},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_propagates_is_public_to_server_flag(mock_client_cls, client, auth_user):
    """is_public in update_data flips the server-side `public` ACL flag.

    The key is also stripped from the merged data dict — visibility lives on the
    LabsRecord envelope, not duplicated inside `data`. Solicitations are exempt
    from the broader MCP no-public-records policy because their content is
    public-facing by design.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_record(10, "solicitation", data={"title": "Test Sol", "status": "open"}, public=False)
    mock_client.get_record_by_id.return_value = existing
    mock_client.update_record.return_value = _make_mock_record(
        10, "solicitation", data={"title": "Test Sol", "status": "closed"}, public=True
    )

    _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 10, "update_data": {"status": "closed", "is_public": True, "public": True}},
    )

    call_kwargs = mock_client.update_record.call_args.kwargs
    # public=True is forwarded to the envelope so the marketplace flag flips
    assert call_kwargs["public"] is True
    # but the keys are stripped from the merged data dict — no duplication
    merged = call_kwargs["data"]
    assert "is_public" not in merged
    assert "public" not in merged


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_rejects_unknown_field(mock_client_cls, client, auth_user):
    """Same drift surface as create — unknown fields in update_data are rejected.

    Validation runs against the merged post-fetch shape, so we mock the fetch
    to return a benign existing record.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        42, "solicitation", data={"title": "Old", "description": "x", "solicitation_type": "eoi"}
    )

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {
            "solicitation_id": 42,
            "update_data": {"overview": "drifted name for description"},
        },
    )

    assert data["result"]["isError"] is True, data
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    assert "overview" in err["message"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_rejects_bad_enum(mock_client_cls, client, auth_user):
    """Field-level validation fires in partial mode for present fields."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        42, "solicitation", data={"title": "Old", "description": "x", "solicitation_type": "eoi"}
    )

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 42, "update_data": {"status": "open"}},
    )

    assert data["result"]["isError"] is True
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    assert "fields" in err["details"]
    assert "status" in err["details"]["fields"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_rejects_dangling_linked_question(mock_client_cls, client, auth_user):
    """Nested-shape checks fire in partial mode just like in create."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        42, "solicitation", data={"title": "Old", "description": "x", "solicitation_type": "eoi"}
    )

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {
            "solicitation_id": 42,
            "update_data": {
                "questions": [{"id": "q1", "text": "?", "type": "text"}],
                "evaluation_criteria": [
                    {
                        "id": "ec1",
                        "name": "X",
                        "weight": 100,
                        "linked_questions": ["q_does_not_exist"],
                    }
                ],
            },
        },
    )

    assert data["result"]["isError"] is True
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "INVALID_SCHEMA"
    assert "q_does_not_exist" in err["message"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_criteria_only_against_existing_questions(mock_client_cls, client, auth_user):
    """Updating ONLY criteria validates against the existing record's questions.

    Locks in the post-merge validation: a partial update touching just
    evaluation_criteria with linked_questions referencing the existing record's
    question ids must succeed. Before the merged-shape fix, this case
    falsely rejected — the validator saw an empty question_ids set because
    questions weren't in the partial payload.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # Existing record has q1 already.
    existing_data = {
        "title": "Existing",
        "description": "x",
        "solicitation_type": "eoi",
        "questions": [{"id": "q1", "text": "Original?", "type": "text"}],
    }
    mock_client.get_record_by_id.return_value = _make_mock_record(42, "solicitation", data=existing_data)
    mock_client.update_record.return_value = _make_mock_record(42, "solicitation", data=existing_data)

    # Partial update sends ONLY criteria. linked_questions=["q1"] resolves
    # against the merged shape (existing.questions ∪ none-from-payload).
    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {
            "solicitation_id": 42,
            "update_data": {
                "evaluation_criteria": [{"id": "ec1", "name": "Quality", "weight": 100, "linked_questions": ["q1"]}],
            },
        },
    )

    assert data["result"]["isError"] is False, data
    mock_client.update_record.assert_called_once()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_criteria_only_dangling_against_existing(mock_client_cls, client, auth_user):
    """Negative pair to the above: criteria-only update referencing a question
    that DOESN'T exist in the merged shape is still rejected.
    """
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = _make_mock_record(
        42,
        "solicitation",
        data={
            "title": "Existing",
            "description": "x",
            "solicitation_type": "eoi",
            "questions": [{"id": "q1", "text": "Original?", "type": "text"}],
        },
    )

    data = _call_tool(
        client,
        raw,
        "update_solicitation",
        {
            "solicitation_id": 42,
            "update_data": {
                "evaluation_criteria": [{"id": "ec1", "name": "X", "weight": 100, "linked_questions": ["q_nope"]}],
            },
        },
    )

    assert data["result"]["isError"] is True
    assert data["result"]["structuredContent"]["error"]["code"] == "INVALID_SCHEMA"
    assert "q_nope" in data["result"]["structuredContent"]["error"]["message"]


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_update_solicitation_omits_public_when_is_public_absent(mock_client_cls, client, auth_user):
    """Updates that don't touch is_public must not send a public kwarg, preserving the existing flag."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing = _make_mock_record(42, "solicitation", data={"title": "Old"}, public=True)
    mock_client.get_record_by_id.return_value = existing
    mock_client.update_record.return_value = _make_mock_record(42, "solicitation", data={"title": "New"}, public=True)

    _call_tool(
        client,
        raw,
        "update_solicitation",
        {"solicitation_id": 42, "update_data": {"title": "New"}},
    )

    assert "public" not in mock_client.update_record.call_args.kwargs


# ---------------------------------------------------------------------------
# award_response
# ---------------------------------------------------------------------------


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_happy_path_no_fund(mock_client_cls, client, auth_user):
    """Awards a response without fund allocation when fund_id is absent."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={"status": "submitted", "solicitation_id": "42", "llo_entity_name": "Org X"},
    )
    awarded_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={"status": "awarded", "reward_budget": 1000, "org_id": "org-1"},
    )

    def _get_record_by_id(record_id, **kwargs):
        if record_id == 10:
            return existing_response
        return None

    mock_client.get_record_by_id.side_effect = _get_record_by_id
    mock_client.update_record.return_value = awarded_response

    data = _call_tool(
        client,
        raw,
        "award_response",
        {"response_id": 10, "reward_budget": 1000, "org_id": "org-1"},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 10
    assert content["status"] == "awarded"

    # update_record called exactly once (response update only — no fund)
    mock_client.update_record.assert_called_once()
    call_kwargs = mock_client.update_record.call_args
    assert call_kwargs.kwargs["record_id"] == 10
    passed_data = call_kwargs.kwargs["data"]
    assert passed_data["status"] == "awarded"
    assert passed_data["reward_budget"] == 1000
    assert passed_data["org_id"] == "org-1"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_with_fund_allocation(mock_client_cls, client, auth_user):
    """When fund_id is provided, the fund record gets an allocation appended."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    existing_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={
            "status": "submitted",
            "solicitation_id": "42",
            "llo_entity_name": "Org X",
        },
    )
    awarded_response = _make_mock_record(
        10,
        "solicitation_response",
        experiment="llo-entity-1",
        data={"status": "awarded", "reward_budget": 500, "org_id": "org-2"},
    )
    sol_record = _make_mock_record(42, "solicitation", data={"title": "Big Grant"})
    fund_record = _make_mock_record(200, "fund", experiment="test-fund", data={"name": "Test Fund", "allocations": []})
    updated_fund = _make_mock_record(
        200,
        "fund",
        experiment="test-fund",
        data={"name": "Test Fund", "allocations": [{"amount": 500}]},
    )

    def _get_record_by_id(record_id, **kwargs):
        mapping = {10: existing_response, 42: sol_record, 200: fund_record}
        return mapping.get(record_id)

    mock_client.get_record_by_id.side_effect = _get_record_by_id

    call_count = [0]

    def _update_record(**kwargs):
        call_count[0] += 1
        if kwargs["record_id"] == 10:
            return awarded_response
        return updated_fund

    mock_client.update_record.side_effect = _update_record

    data = _call_tool(
        client,
        raw,
        "award_response",
        {"response_id": 10, "reward_budget": 500, "org_id": "org-2", "fund_id": 200},
    )

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content["id"] == 10

    # Should have called update_record twice: once for response, once for fund
    assert call_count[0] == 2

    # Verify the fund update included the new allocation with the solicitation title
    fund_update_call = [c for c in mock_client.update_record.call_args_list if c.kwargs["record_id"] == 200][0]
    fund_data = fund_update_call.kwargs["data"]
    assert len(fund_data["allocations"]) == 1
    allocation = fund_data["allocations"][0]
    assert allocation["amount"] == 500
    assert allocation["type"] == "award"
    assert allocation["response_id"] == 10
    assert allocation["org_id"] == "org-2"
    assert allocation["notes"] == "Award from Big Grant"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_award_response_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND error when the response record does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(
        client,
        raw,
        "award_response",
        {"response_id": 999, "reward_budget": 100, "org_id": "org-x"},
    )

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


# ---------------------------------------------------------------------------
# delete_solicitation
# ---------------------------------------------------------------------------


def _wire_cascade(mock_client, sol, responses=(), reviews_by_response=None):
    """Wire up get_record_by_id + get_records to return a synthetic cascade."""
    reviews_by_response = reviews_by_response or {}
    mock_client.get_record_by_id.return_value = sol

    def _get_records(**kwargs):
        if kwargs.get("type") == "solicitation_response":
            return list(responses)
        if kwargs.get("type") == "solicitation_review":
            return list(reviews_by_response.get(kwargs.get("labs_record_id"), []))
        return []

    mock_client.get_records.side_effect = _get_records


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_delete_solicitation_empty_cascade_no_force(mock_client_cls, client, auth_user):
    """Empty cascade deletes without force=true."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    _wire_cascade(mock_client, _make_mock_record(7, "solicitation", data={"status": "active"}))

    data = _call_tool(client, raw, "delete_solicitation", {"solicitation_id": 7})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content == {
        "solicitation_id": 7,
        "deleted": {"solicitations": 1, "responses": 0, "reviews": 0},
    }
    mock_client.delete_record.assert_called_once_with(7)
    mock_client.delete_records.assert_not_called()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_delete_solicitation_non_empty_cascade_no_force_refuses(mock_client_cls, client, auth_user):
    """Non-empty cascade without force returns FAILED_PRECONDITION and deletes nothing."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    _wire_cascade(
        mock_client,
        _make_mock_record(42, "solicitation", data={"status": "draft"}),
        responses=[
            _make_mock_record(101, "solicitation_response", labs_record_id=42),
            _make_mock_record(102, "solicitation_response", labs_record_id=42),
        ],
        reviews_by_response={101: [_make_mock_record(201, "solicitation_review", labs_record_id=101)]},
    )

    data = _call_tool(client, raw, "delete_solicitation", {"solicitation_id": 42})

    assert data["result"]["isError"] is True, data
    err = data["result"]["structuredContent"]["error"]
    assert err["code"] == "FAILED_PRECONDITION"
    assert "2 responses" in err["message"]
    assert "1 reviews" in err["message"]
    assert "force=true" in err["message"]
    mock_client.delete_record.assert_not_called()
    mock_client.delete_records.assert_not_called()


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_delete_solicitation_non_empty_cascade_force_deletes(mock_client_cls, client, auth_user):
    """force=true destroys the populated cascade bottom-up."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    _wire_cascade(
        mock_client,
        _make_mock_record(42, "solicitation", data={"status": "awarded"}),
        responses=[
            _make_mock_record(101, "solicitation_response", labs_record_id=42),
            _make_mock_record(102, "solicitation_response", labs_record_id=42),
        ],
        reviews_by_response={
            101: [
                _make_mock_record(201, "solicitation_review", labs_record_id=101),
                _make_mock_record(202, "solicitation_review", labs_record_id=101),
            ]
        },
    )

    data = _call_tool(client, raw, "delete_solicitation", {"solicitation_id": 42, "force": True})

    assert data["result"]["isError"] is False, data
    content = data["result"]["structuredContent"]
    assert content == {
        "solicitation_id": 42,
        "deleted": {"solicitations": 1, "responses": 2, "reviews": 2},
    }

    # Verify cascade order: reviews first, then responses, then solicitation.
    delete_records_calls = mock_client.delete_records.call_args_list
    assert delete_records_calls[0].args[0] == [201, 202]
    assert delete_records_calls[1].args[0] == [101, 102]
    mock_client.delete_record.assert_called_once_with(42)


@pytest.mark.django_db
@pytest.mark.parametrize("status", ["active", "awarded", "draft", "closed"])
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_delete_solicitation_status_does_not_affect_gate(mock_client_cls, status, client, auth_user):
    """Status field is no longer load-bearing — the cascade is the gate.

    An empty cascade deletes regardless of status; a non-empty cascade refuses
    regardless of status. Status only affects marketplace display.
    """
    _, raw = auth_user

    # Empty cascade: deletes for every status.
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    _wire_cascade(mock_client, _make_mock_record(7, "solicitation", data={"status": status}))

    data = _call_tool(client, raw, "delete_solicitation", {"solicitation_id": 7})
    assert data["result"]["isError"] is False, (status, data)

    # Non-empty cascade: refuses for every status.
    mock_client.reset_mock()
    _wire_cascade(
        mock_client,
        _make_mock_record(7, "solicitation", data={"status": status}),
        responses=[_make_mock_record(101, "solicitation_response", labs_record_id=7)],
    )

    data = _call_tool(client, raw, "delete_solicitation", {"solicitation_id": 7})
    assert data["result"]["isError"] is True, (status, data)
    assert data["result"]["structuredContent"]["error"]["code"] == "FAILED_PRECONDITION"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_delete_solicitation_not_found(mock_client_cls, client, auth_user):
    """Returns NOT_FOUND when the solicitation does not exist."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.get_record_by_id.return_value = None

    data = _call_tool(client, raw, "delete_solicitation", {"solicitation_id": 999})

    assert data["result"]["isError"] is True, data
    assert data["result"]["structuredContent"]["error"]["code"] == "NOT_FOUND"


@pytest.mark.django_db
@patch("commcare_connect.mcp.tools.solicitations.LabsRecordAPIClient")
def test_delete_solicitation_propagates_scope_to_client(mock_client_cls, client, auth_user):
    """program_id/organization_id are forwarded so the underlying read is authorized."""
    _, raw = auth_user
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    _wire_cascade(mock_client, _make_mock_record(5, "solicitation", data={"status": "draft"}))

    _call_tool(
        client,
        raw,
        "delete_solicitation",
        {"solicitation_id": 5, "program_id": "25"},
    )

    init_kwargs = mock_client_cls.call_args.kwargs
    assert init_kwargs["program_id"] == 25


# ---------------------------------------------------------------------------
# Missing Connect token
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_tools_require_connect_token(client, db):
    """All tools fail with PERMISSION_DENIED when the user has no Connect token."""
    user = User.objects.create(username="no-conn-sol")
    _, raw = MCPAccessToken.create_token(user, name="t")

    for name, args in [
        ("list_solicitations", {}),
        ("get_solicitation", {"solicitation_id": 1}),
        ("list_responses", {"solicitation_id": 1}),
        ("get_response", {"response_id": 1}),
        (
            "create_solicitation",
            {
                "program_id": "1",
                "title": "X",
                "description": "Y",
                "solicitation_type": "eoi",
            },
        ),
        ("update_solicitation", {"solicitation_id": 1, "update_data": {"title": "X"}}),
        ("award_response", {"response_id": 1, "reward_budget": 100, "org_id": "o"}),
        ("delete_solicitation", {"solicitation_id": 1}),
    ]:
        resp_data = _call_tool(client, raw, name, args)
        assert (
            resp_data["result"]["structuredContent"]["error"]["code"] == "PERMISSION_DENIED"
        ), f"{name} should return PERMISSION_DENIED without a Connect token"
