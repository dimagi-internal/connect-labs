"""Tests for converting v2 export records into labs visit dicts."""
from commcare_connect.labs.analysis.backends.visit_record import SLIM_VISIT_KEYS, record_to_visit_dict


def test_full_record_with_form_json_extracts_xform_id():
    record = {
        "id": 123,
        "opportunity_id": 42,
        "username": "alice",
        "deliver_unit": "DU1",
        "deliver_unit_id": 7,
        "entity_id": "ent-1",
        "entity_name": "Household 1",
        "visit_date": "2026-04-01",
        "status": "approved",
        "reason": "",
        "location": "12.34 56.78 0 5",
        "flagged": False,
        "flag_reason": "",
        "form_json": {"id": "xform-abc-123", "form": {"q1": "v1"}},
        "completed_work": "cw-1",
        "status_modified_date": "2026-04-02T00:00:00Z",
        "review_status": "ok",
        "review_created_on": "2026-04-02T01:00:00Z",
        "justification": "",
        "date_created": "2026-04-01T00:00:00Z",
        "completed_work_id": 9,
        "images": [{"blob_id": "b1", "name": "img.jpg", "parent_id": "p1"}],
    }

    visit = record_to_visit_dict(record, opportunity_id=42)

    assert visit["id"] == 123
    assert visit["xform_id"] == "xform-abc-123"
    assert visit["form_json"] == {"id": "xform-abc-123", "form": {"q1": "v1"}}
    assert visit["images"] == [{"blob_id": "b1", "name": "img.jpg", "parent_id": "p1"}]
    assert visit["flagged"] is False


def test_missing_form_json_defaults_to_empty_dict_and_none_xform_id():
    record = {"id": 1, "username": "alice"}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["form_json"] == {}
    assert visit["xform_id"] is None


def test_missing_images_defaults_to_empty_list():
    record = {"id": 1}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["images"] == []


def test_opportunity_id_falls_back_to_argument_when_missing_from_record():
    record = {"id": 1}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["opportunity_id"] == 42


def test_opportunity_id_from_record_takes_precedence_when_present():
    record = {"id": 1, "opportunity_id": 99}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["opportunity_id"] == 99


def test_skip_form_json_returns_empty_dict_and_no_xform_id():
    record = {
        "id": 1,
        "form_json": {"id": "xform-1", "huge": "data" * 1000},
    }
    visit = record_to_visit_dict(record, opportunity_id=42, skip_form_json=True)
    assert visit["form_json"] == {}
    assert visit["xform_id"] is None


def test_flagged_coerces_truthy_to_bool():
    assert record_to_visit_dict({"id": 1, "flagged": True}, 42)["flagged"] is True
    assert record_to_visit_dict({"id": 1, "flagged": False}, 42)["flagged"] is False
    assert record_to_visit_dict({"id": 1}, 42)["flagged"] is False


def test_form_json_passed_as_dict_when_already_dict():
    """v2 returns form_json as a real JSON object, not a Python repr string."""
    record = {"id": 1, "form_json": {"a": 1, "b": [2, 3]}}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["form_json"] == {"a": 1, "b": [2, 3]}


def test_slim_visit_keys_excludes_form_json():
    assert "form_json" not in SLIM_VISIT_KEYS
    # All other expected keys still present
    assert "id" in SLIM_VISIT_KEYS
    assert "username" in SLIM_VISIT_KEYS
    assert "images" in SLIM_VISIT_KEYS


def test_deliver_unit_int_coerced_to_string_for_cache_parity():
    """v2 JSON returns deliver_unit as an int FK PK; labs code expects a string
    (RawVisitCache.deliver_unit is CharField, v1 CSV always returned strings)."""
    record = {"id": 1, "deliver_unit": 1707}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["deliver_unit"] == "1707"
    assert isinstance(visit["deliver_unit"], str)


def test_deliver_unit_none_stays_none():
    record = {"id": 1, "deliver_unit": None}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["deliver_unit"] is None


def test_deliver_unit_string_passes_through():
    """Defensive: if v1 code path ever hits this, strings pass through unchanged."""
    record = {"id": 1, "deliver_unit": "1707"}
    visit = record_to_visit_dict(record, opportunity_id=42)
    assert visit["deliver_unit"] == "1707"
