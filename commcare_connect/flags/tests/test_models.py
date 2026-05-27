"""Unit tests for FlagRecord proxy model."""

from commcare_connect.flags.models import FlagRecord


def _record(**data_overrides):
    """Build a FlagRecord with the given data overrides."""
    return FlagRecord(
        {
            "id": 42,
            "experiment": "flags",
            "type": "Flag",
            "username": "amina",
            "opportunity_id": 10001,
            "data": {
                "workflow_run_id": 503,
                "opportunity_id": 10001,
                "flw_id": "amina",
                "flag_key": "sam_low",
                "flag_label": "SAM rate low",
                "evidence": {"sam_pct": 0.2},
                "source": "auto",
                "flagged_at": "2025-11-11T11:42:00Z",
                "flagged_by": "jane_okeke",
                **data_overrides,
            },
        }
    )


def test_property_round_trip():
    rec = _record()
    assert rec.workflow_run_id == 503
    assert rec.flw_id == "amina"
    assert rec.flag_key == "sam_low"
    assert rec.flag_label == "SAM rate low"
    assert rec.evidence == {"sam_pct": 0.2}
    assert rec.source == "auto"
    assert rec.flagged_at == "2025-11-11T11:42:00Z"
    assert rec.flagged_by == "jane_okeke"


def test_source_defaults_to_auto():
    rec = FlagRecord({"id": 1, "experiment": "flags", "type": "Flag", "opportunity_id": 0, "data": {}})
    assert rec.source == "auto"


def test_dict_defaults_when_missing():
    rec = FlagRecord({"id": 1, "experiment": "flags", "type": "Flag", "opportunity_id": 0, "data": {}})
    assert rec.evidence == {}


def test_optional_fields_return_none_when_missing():
    rec = FlagRecord({"id": 1, "experiment": "flags", "type": "Flag", "opportunity_id": 0, "data": {}})
    assert rec.flagged_at is None
    assert rec.flagged_by is None
    assert rec.workflow_run_id is None
    assert rec.flw_id is None


def test_flag_key_and_label_default_to_empty_string():
    rec = FlagRecord({"id": 1, "experiment": "flags", "type": "Flag", "opportunity_id": 0, "data": {}})
    assert rec.flag_key == ""
    assert rec.flag_label == ""
