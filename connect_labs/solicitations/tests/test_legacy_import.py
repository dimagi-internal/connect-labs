"""Importing prod's solicitation LabsRecords: idempotent, owned, confidential."""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.models import LocalLabsRecord, UserConnectToken
from connect_labs.mcp.tool_registry import MCPToolError, get_tool
from connect_labs.solicitations.legacy_import import import_records, is_smoke
from connect_labs.solicitations.local_models import (
    LOCAL_ID_FLOOR,
    SOURCE_LABS_RECORD,
    Solicitation,
    SolicitationResponse,
    SolicitationReview,
)
from connect_labs.solicitations.tests.factories import make_call, make_org, make_user

pytestmark = pytest.mark.django_db


def rec(id, type, data, *, program_id=None, labs_record_id=None, public=True, username=None, experiment=None):
    return LocalLabsRecord(
        {
            "id": id,
            "experiment": experiment or (str(program_id) if program_id else "x"),
            "type": type,
            "data": data,
            "opportunity_id": None,
            "program_id": program_id,
            "labs_record_id": labs_record_id,
            "public": public,
            "username": username,
        }
    )


def records():
    return {
        "solicitations": {
            2841: rec(
                2841,
                "solicitation",
                {
                    "title": "Household survey RFP",
                    "description": "d",
                    "solicitation_type": "rfp",
                    "status": "active",
                    "application_deadline": "2026-08-15",
                    "criteria_locked": True,
                    "created_by": "creator",
                },
                program_id=25,
            ),
            2842: rec(2842, "solicitation", {"title": "Smoke test call", "status": "active"}, program_id=26),
            2843: rec(2843, "solicitation", {"title": "Plain call", "program_name": "E2E programme"}, program_id=27),
        },
        "responses": {
            3001: rec(
                3001,
                "solicitation_response",
                {
                    "solicitation_id": 2841,
                    "responses": {"q1": "yes"},
                    "status": "awarded",
                    "org_name": "Firm Org",
                    "submitted_by_email": "lead@firm.example",
                    "reward_budget": 5000,
                    "awardee_notified": True,
                    "submitted_by_username": "applicant",
                },
                labs_record_id=2841,
            ),
            3002: rec(3002, "solicitation_response", {"org_name": "Unknown Firm"}, labs_record_id=2841),
            3003: rec(3003, "solicitation_response", {}, labs_record_id=9999),
        },
        "reviews": {
            4001: rec(
                4001,
                "solicitation_review",
                {"score": 70, "reviewer_username": "reviewer", "criteria_scores": {"c1": 7}},
                labs_record_id=3001,
            ),
            4002: rec(
                4002, "solicitation_review", {"score": 40, "reviewer_username": "reviewer"}, labs_record_id=3001
            ),
        },
    }


@pytest.fixture
def setup():
    from connect_labs.supply_chain.runner import declare

    runner = make_org("runner-org")
    declare(25, runner)
    default = make_org("default-org")
    make_org("firm-org", name="Firm Org")
    make_user("applicant")
    make_user("reviewer")
    make_user("creator")
    return {"runner": runner, "default": default}


def test_is_smoke():
    assert is_smoke("Smoke test", "")
    assert is_smoke("x", "e2e run")
    assert is_smoke("Test run 4", "")
    assert not is_smoke("Survey call", "Kenya programme")


def test_import_maps_owner_listing_orgs_and_reviews(setup):
    stats = import_records(records(), default_owner=setup["default"])
    assert stats["solicitations"] == {"created": 3, "updated": 0, "unlisted_as_test": 2, "unowned": 0}
    assert stats["responses"]["created"] == 2
    assert stats["responses"]["orphaned"] == 1
    assert stats["responses"]["matched_to_org"] == 1
    assert stats["reviews"]["created"] == 2

    survey = Solicitation.objects.get(legacy_record_id=2841)
    assert survey.source == SOURCE_LABS_RECORD
    assert survey.owner_org == setup["runner"]
    assert survey.listed is True
    assert survey.program_id == 25
    assert survey.criteria_locked
    assert survey.created_by.username == "creator"
    assert survey.pk >= LOCAL_ID_FLOOR
    assert Solicitation.objects.get(legacy_record_id=2842).listed is False
    assert Solicitation.objects.get(legacy_record_id=2843).listed is False
    assert Solicitation.objects.get(legacy_record_id=2843).owner_org == setup["default"]

    awarded = SolicitationResponse.objects.get(legacy_record_id=3001)
    assert awarded.llo_entity.slug == "firm-org"
    assert awarded.submitted_by.username == "applicant"
    assert awarded.reward_budget == 5000
    assert awarded.awardee_notified is False  # prod never actually sent anything
    unknown = SolicitationResponse.objects.get(legacy_record_id=3002)
    assert unknown.llo_entity is None
    assert unknown.org_name == "Unknown Firm"

    # two prod reviews by one person on one response: the second keeps the name, not the user
    reviews = SolicitationReview.objects.filter(response=awarded).order_by("legacy_record_id")
    assert [r.reviewer_username for r in reviews] == ["reviewer", "reviewer"]
    assert sum(1 for r in reviews if r.reviewer_id) == 1


def test_import_is_idempotent_and_keeps_human_changes(setup):
    import_records(records(), default_owner=setup["default"])
    survey = Solicitation.objects.get(legacy_record_id=2841)
    survey.listed = False
    survey.save()
    stats = import_records(records(), default_owner=setup["default"])
    assert stats["solicitations"]["created"] == 0
    assert stats["solicitations"]["updated"] == 3
    assert Solicitation.objects.filter(source=SOURCE_LABS_RECORD).count() == 3
    assert SolicitationResponse.objects.exclude(legacy_record_id=None).count() == 2
    assert SolicitationReview.objects.count() == 2
    assert Solicitation.objects.get(legacy_record_id=2841).listed is False


def test_without_default_owner_unowned_calls_are_staff_only(setup):
    stats = import_records(records())
    assert stats["solicitations"]["unowned"] == 2
    assert Solicitation.objects.get(legacy_record_id=2842).owner_org is None


def test_dry_run_writes_nothing(setup):
    stats = import_records(records(), default_owner=setup["default"], dry_run=True)
    assert stats["dry_run"] is True
    assert stats["solicitations"]["created"] == 3
    assert not Solicitation.objects.filter(source=SOURCE_LABS_RECORD).exists()


def test_imported_ids_resolve_old_links(setup):
    from connect_labs.solicitations.data_access import SolicitationsDataAccess

    import_records(records(), default_owner=setup["default"])
    assert SolicitationsDataAccess().get_solicitation_by_id(2841).title == "Household survey RFP"


# --- the MCP tool ------------------------------------------------------------


def _tool():
    return get_tool("solicitations_import_records")


def test_tool_is_long_running_staff_write():
    tool = _tool()
    assert tool.long_running and tool.is_write


def test_tool_refuses_non_staff(setup):
    with pytest.raises(MCPToolError) as exc:
        _tool().handler(make_user("someone"))
    assert exc.value.code == "PERMISSION_DENIED"


def test_tool_reads_prod_as_the_caller_and_imports(setup):
    staff = make_user("support", staff=True)
    UserConnectToken.objects.create(user=staff, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    data = records()
    client = MagicMock()

    def get_records(type, **kw):
        if type == "solicitation" and kw.get("program_id"):
            return [rec(2900, "solicitation", {"title": "Private call"}, program_id=kw["program_id"], public=False)]
        return list(
            {
                "solicitation": data["solicitations"],
                "solicitation_response": data["responses"],
                "solicitation_review": data["reviews"],
            }[type].values()
        )

    client.get_records.side_effect = get_records
    with patch("connect_labs.labs.integrations.connect.api_client.LabsRecordAPIClient", return_value=client) as cls:
        result = _tool().handler(staff, default_owner_org_slug="default-org", program_ids=[25])
    assert cls.call_args.kwargs["access_token"] == "tok"
    assert result["read"] == {"solicitations": 4, "responses": 3, "reviews": 2}
    assert result["solicitations"]["created"] == 4
    assert Solicitation.objects.get(legacy_record_id=2900).listed is False


def test_tool_rejects_unknown_default_owner(setup):
    with pytest.raises(MCPToolError):
        _tool().handler(make_user("support2", staff=True), default_owner_org_slug="nobody")


def test_local_calls_are_untouched(setup):
    mine = make_call(setup["default"], title="Local call")
    import_records(records(), default_owner=setup["default"])
    mine.refresh_from_db()
    assert mine.title == "Local call"


def test_import_refuses_prod_ids_at_or_above_the_local_floor(setup):
    data = records()
    data["responses"][LOCAL_ID_FLOOR] = rec(LOCAL_ID_FLOOR, "solicitation_response", {}, labs_record_id=2841)
    with pytest.raises(ValueError, match="local id floor"):
        import_records(data, default_owner=setup["default"])
    assert not Solicitation.objects.exists()
    assert not SolicitationResponse.objects.exists()


def test_tool_reports_a_prod_id_above_the_floor(setup):
    staff = make_user("support", staff=True)
    UserConnectToken.objects.create(user=staff, access_token="tok", expires_at=timezone.now() + timedelta(hours=1))
    client = MagicMock()
    big = LOCAL_ID_FLOOR + 1
    client.get_records.side_effect = lambda type, **kw: (
        [rec(big, "solicitation", {})] if type == "solicitation" else []
    )
    with patch("connect_labs.labs.integrations.connect.api_client.LabsRecordAPIClient", return_value=client):
        with pytest.raises(MCPToolError, match="local id floor") as exc:
            _tool().handler(staff)
    assert exc.value.code == "FAILED_PRECONDITION"
