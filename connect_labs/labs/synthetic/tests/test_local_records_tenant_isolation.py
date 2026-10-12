"""A client scoped to one labs-only programme reaches only that programme's records.

The local backend has no Connect check behind it, so the scope a client carries is
the only thing separating one labs-only tenant from another. A by-id read or an
update made with a PROGRAMME scope (no opportunity selected) has to stay inside that
programme -- its own records and those of the opportunities filed under it --
exactly as the access gates assume when they approve the programme.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory

from connect_labs.labs.integrations.connect.api_client import LabsAPIError, LabsRecordAPIClient
from connect_labs.labs.synthetic.models import LabsLocalRecord, SyntheticOpportunity

pytestmark = pytest.mark.django_db

MINE_OPP, MINE_PROG = 10_601, 10_600
THEIRS_OPP, THEIRS_PROG = 10_501, 10_500


@pytest.fixture
def tenants():
    user = get_user_model().objects.create(username="kofi", email="kofi@partner-one.example")
    SyntheticOpportunity.objects.create(
        opportunity_id=MINE_OPP,
        program_id=MINE_PROG,
        gdrive_folder_id="f-mine",
        labs_only=True,
        created_by=user,
        allowed_domains=["@partner-one.example"],
    )
    SyntheticOpportunity.objects.create(
        opportunity_id=THEIRS_OPP,
        program_id=THEIRS_PROG,
        gdrive_folder_id="f-theirs",
        labs_only=True,
        allowed_domains=["@partner-two.example"],
    )
    theirs_def = LabsLocalRecord.objects.create(
        opportunity_id=THEIRS_OPP,
        experiment="workflow",
        type="workflow_definition",
        data={"name": "Their review", "opportunity_ids": [THEIRS_OPP]},
    )
    LabsLocalRecord.objects.create(
        opportunity_id=THEIRS_OPP,
        experiment="workflow",
        type="workflow_run",
        data={"definition_id": theirs_def.id, "name": "Their week"},
    )
    theirs_prog_def = LabsLocalRecord.objects.create(
        opportunity_id=None,
        program_id=THEIRS_PROG,
        experiment="workflow",
        type="workflow_definition",
        data={"name": "Their programme report"},
    )
    mine_def = LabsLocalRecord.objects.create(
        opportunity_id=MINE_OPP,
        experiment="workflow",
        type="workflow_definition",
        data={"name": "My review", "opportunity_ids": [MINE_OPP]},
    )
    mine_prog_def = LabsLocalRecord.objects.create(
        opportunity_id=None,
        program_id=MINE_PROG,
        experiment="workflow",
        type="workflow_definition",
        data={"name": "My programme report"},
    )
    return {
        "user": user,
        "theirs": theirs_def,
        "theirs_prog": theirs_prog_def,
        "mine": mine_def,
        "mine_prog": mine_prog_def,
    }


def _client():
    return LabsRecordAPIClient(access_token="stub", program_id=MINE_PROG)


def test_a_programme_scoped_read_by_id_does_not_reach_another_programmes_record(tenants):
    client = _client()
    assert client.get_record_by_id(tenants["theirs"].id, experiment="workflow", type="workflow_definition") is None
    assert (
        client.get_record_by_id(tenants["theirs_prog"].id, experiment="workflow", type="workflow_definition") is None
    )


def test_a_programme_scoped_read_by_id_still_finds_the_programmes_own_records(tenants):
    client = _client()
    own = client.get_record_by_id(tenants["mine_prog"].id, experiment="workflow", type="workflow_definition")
    member = client.get_record_by_id(tenants["mine"].id, experiment="workflow", type="workflow_definition")
    assert own.data["name"] == "My programme report"
    assert member.data["name"] == "My review"


def test_a_programme_scoped_update_cannot_overwrite_another_programmes_record(tenants):
    with pytest.raises(LabsAPIError):
        _client().update_record(
            tenants["theirs_prog"].id, experiment="workflow", type="workflow_definition", data={"name": "changed"}
        )
    tenants["theirs_prog"].refresh_from_db()
    assert tenants["theirs_prog"].data["name"] == "Their programme report"


def test_a_programme_scoped_update_still_writes_the_programmes_own_record(tenants):
    _client().update_record(
        tenants["mine_prog"].id, experiment="workflow", type="workflow_definition", data={"name": "Renamed"}
    )
    tenants["mine_prog"].refresh_from_db()
    assert tenants["mine_prog"].data["name"] == "Renamed"


def test_a_workflow_source_owned_by_the_viewers_own_programme_cannot_read_another_tenants_runs(tenants):
    """The owner is a programme the viewer may use; the target workflow is someone else's."""
    from connect_labs.workflow import workflow_sources

    request = RequestFactory().get("/labs/p/program/10600/")
    request.session = {"labs_oauth": {"access_token": "stub"}}
    request.user = tenants["user"]
    request.labs_context = {"program_id": MINE_PROG}
    out = workflow_sources.read(
        request, {"alias": "x", "workflow": tenants["theirs"].id, "read": "latest_run", "program_id": MINE_PROG}
    )
    assert "latest_run" not in out
    assert "cannot open it" in out["error"]
