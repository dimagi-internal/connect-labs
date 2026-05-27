"""Unit tests for FlagsDataAccess. API client is mocked."""

from unittest.mock import MagicMock

import pytest

from commcare_connect.flags.data_access import FlagsDataAccess
from commcare_connect.flags.models import FlagRecord
from commcare_connect.labs.models import LocalLabsRecord


@pytest.fixture
def flags_da():
    """A FlagsDataAccess with a mocked labs_api client."""
    da = FlagsDataAccess.__new__(FlagsDataAccess)
    da.labs_api = MagicMock()
    da.opportunity_id = 10001
    return da


def test_create_flag_persists_all_fields(flags_da):
    flags_da.labs_api.create_record.return_value = LocalLabsRecord(
        {
            "id": 99,
            "experiment": "flags",
            "type": "Flag",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {},
        }
    )

    result = flags_da.create_flag(
        workflow_run_id=503,
        opportunity_id=10001,
        flw_id="amina",
        flag_key="sam_low",
        flag_label="SAM rate low",
        evidence={"sam_pct": 0.2},
        flagged_by="jane_okeke",
    )

    assert isinstance(result, FlagRecord)
    call = flags_da.labs_api.create_record.call_args.kwargs
    assert call["experiment"] == "flags"
    assert call["type"] == "Flag"
    assert call["username"] == "amina"
    data = call["data"]
    assert data["workflow_run_id"] == 503
    assert data["opportunity_id"] == 10001
    assert data["flw_id"] == "amina"
    assert data["flag_key"] == "sam_low"
    assert data["flag_label"] == "SAM rate low"
    assert data["evidence"] == {"sam_pct": 0.2}
    assert data["source"] == "auto"
    assert data["flagged_by"] == "jane_okeke"
    # flagged_at defaulted to "now" — present and ISO-ish
    assert "T" in data["flagged_at"]


def test_create_flag_defaults_label_to_key(flags_da):
    flags_da.labs_api.create_record.return_value = LocalLabsRecord(
        {"id": 1, "experiment": "flags", "type": "Flag", "username": "b", "opportunity_id": 10001, "data": {}}
    )
    flags_da.create_flag(
        workflow_run_id=503,
        opportunity_id=10001,
        flw_id="binta",
        flag_key="gender_skew",
    )
    data = flags_da.labs_api.create_record.call_args.kwargs["data"]
    assert data["flag_label"] == "gender_skew"
    assert data["evidence"] == {}


def test_create_flag_accepts_manual_source(flags_da):
    flags_da.labs_api.create_record.return_value = LocalLabsRecord(
        {"id": 1, "experiment": "flags", "type": "Flag", "username": "b", "opportunity_id": 10001, "data": {}}
    )
    flags_da.create_flag(
        workflow_run_id=503,
        opportunity_id=10001,
        flw_id="binta",
        flag_key="manual_concern",
        source="manual",
    )
    data = flags_da.labs_api.create_record.call_args.kwargs["data"]
    assert data["source"] == "manual"


def test_create_flag_rejects_invalid_source(flags_da):
    with pytest.raises(ValueError, match="source must be one of"):
        flags_da.create_flag(
            workflow_run_id=503,
            opportunity_id=10001,
            flw_id="amina",
            flag_key="sam_low",
            source="bogus",
        )


def test_create_flag_rejects_empty_flw_id(flags_da):
    with pytest.raises(ValueError, match="flw_id"):
        flags_da.create_flag(
            workflow_run_id=503,
            opportunity_id=10001,
            flw_id="",
            flag_key="sam_low",
        )


def test_create_flag_rejects_empty_flag_key(flags_da):
    with pytest.raises(ValueError, match="flag_key"):
        flags_da.create_flag(
            workflow_run_id=503,
            opportunity_id=10001,
            flw_id="amina",
            flag_key="",
        )


def test_get_flag_returns_record_when_found(flags_da):
    flags_da.labs_api.get_record_by_id.return_value = FlagRecord(
        {
            "id": 99,
            "experiment": "flags",
            "type": "Flag",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {"flw_id": "amina", "flag_key": "sam_low"},
        }
    )
    result = flags_da.get_flag(99)
    assert isinstance(result, FlagRecord)
    assert result.id == 99
    call = flags_da.labs_api.get_record_by_id.call_args.kwargs
    assert call["record_id"] == 99
    assert call["experiment"] == "flags"
    assert call["type"] == "Flag"


def test_get_flag_returns_none_when_missing(flags_da):
    flags_da.labs_api.get_record_by_id.return_value = None
    assert flags_da.get_flag(404) is None


def test_get_flags_for_run_filters_by_workflow_run_id(flags_da):
    flags_da.labs_api.get_records.return_value = []
    flags_da.get_flags_for_run(503)
    call = flags_da.labs_api.get_records.call_args.kwargs
    assert call["experiment"] == "flags"
    assert call["type"] == "Flag"
    assert call["workflow_run_id"] == 503


def test_get_flags_for_run_returns_records(flags_da):
    flags_da.labs_api.get_records.return_value = [
        FlagRecord({"id": 1, "experiment": "flags", "type": "Flag", "opportunity_id": 0, "data": {}}),
        FlagRecord({"id": 2, "experiment": "flags", "type": "Flag", "opportunity_id": 0, "data": {}}),
    ]
    result = flags_da.get_flags_for_run(503)
    assert len(result) == 2
    assert [r.id for r in result] == [1, 2]
