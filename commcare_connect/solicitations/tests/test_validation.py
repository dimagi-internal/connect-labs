"""Unit tests for the canonical solicitation schema validator.

These tests exercise validate_solicitation_payload directly — no Django ORM,
no API plumbing. Every write path (UI form view, HTTP API, MCP tool) routes
through SolicitationsDataAccess.create_solicitation which calls this
validator, so these tests are the contract for what all three paths accept.
"""
import pytest
from django.core.exceptions import ValidationError

from commcare_connect.solicitations.validation import ALLOWED_FIELDS, validate_solicitation_payload


def _minimal_valid() -> dict:
    return {
        "title": "T",
        "description": "D",
        "solicitation_type": "eoi",
    }


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------


def test_minimal_payload_passes():
    validate_solicitation_payload(_minimal_valid())


def test_full_canonical_payload_passes():
    """The fully-shaped payload ACE should send round-trips cleanly."""
    validate_solicitation_payload(
        {
            "title": "Full",
            "description": "Why this matters",
            "scope_of_work": "- bullet one\n- bullet two",
            "solicitation_type": "eoi",
            "status": "active",
            "application_deadline": "2026-06-30",
            "expected_start_date": "2026-07-01",
            "expected_end_date": "2026-08-30",
            "estimated_scale": "30 visits",
            "contact_email": "ace@dimagi.com",
            "program_name": "Malaria SBC",
            "connect_opportunity_id": 1821,
            "fund_id": 42,
            "questions": [
                {"id": "q1", "text": "Languages?", "type": "text", "required": True},
                {"id": "q2", "text": "Pick", "type": "multiple_choice", "options": ["A", "B"]},
            ],
            "evaluation_criteria": [
                {
                    "id": "ec1",
                    "name": "Capacity",
                    "weight": 60,
                    "linked_questions": ["q1"],
                },
                {
                    "id": "ec2",
                    "name": "Approach",
                    "weight": 40,
                    "scoring_guide": "10 = strong",
                    "linked_questions": ["q2"],
                },
            ],
        }
    )


def test_is_public_alongside_data_is_tolerated():
    """is_public lives on the LabsRecord envelope; we don't flag it as drift."""
    validate_solicitation_payload({**_minimal_valid(), "is_public": True})


# ---------------------------------------------------------------------------
# Drift rejection — the bug ACE shipped should be caught here.
# ---------------------------------------------------------------------------


def test_rejects_unknown_top_level_fields():
    """ACE drifted by sending `overview` instead of `description`. Reject."""
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "overview": "wrong name for description",
                "response_window_days": 14,
            }
        )
    err = exc_info.value
    assert "overview" in str(err)
    assert "response_window_days" in str(err)


def test_allowed_fields_set_is_explicit():
    """Sanity: the canonical set hasn't grown silently."""
    assert "description" in ALLOWED_FIELDS
    assert "overview" not in ALLOWED_FIELDS  # The ACE-drift smell


# ---------------------------------------------------------------------------
# Required fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", ["title", "description", "solicitation_type"])
def test_required_fields(field):
    payload = _minimal_valid()
    del payload[field]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_blank_title_rejected():
    with pytest.raises(ValidationError):
        validate_solicitation_payload({**_minimal_valid(), "title": "   "})


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


def test_invalid_solicitation_type():
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload({**_minimal_valid(), "solicitation_type": "grant"})
    assert "solicitation_type" in exc_info.value.message_dict


def test_invalid_status():
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload({**_minimal_valid(), "status": "open"})
    assert "status" in exc_info.value.message_dict


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------


def test_invalid_date_string():
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload({**_minimal_valid(), "application_deadline": "next Tuesday"})
    assert "application_deadline" in exc_info.value.message_dict


def test_empty_date_string_tolerated():
    """Empty string is treated as 'not set' — matches form-to-API roundtrip."""
    validate_solicitation_payload({**_minimal_valid(), "expected_start_date": ""})


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


def test_invalid_email():
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload({**_minimal_valid(), "contact_email": "no-at-sign"})
    assert "contact_email" in exc_info.value.message_dict


@pytest.mark.parametrize("bad", ["a@", "@b", "@@", "foo@bar", "spaces in@example.com"])
def test_email_validator_rejects_malformed_shapes(bad):
    """Use Django's EmailValidator — a bare ``@``-substring check tolerated these."""
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload({**_minimal_valid(), "contact_email": bad})
    assert "contact_email" in exc_info.value.message_dict


def test_valid_email_passes():
    validate_solicitation_payload({**_minimal_valid(), "contact_email": "ace@dimagi.com"})


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


def test_questions_must_be_list():
    with pytest.raises(ValidationError):
        validate_solicitation_payload({**_minimal_valid(), "questions": {"q1": "?"}})


def test_question_id_must_be_unique():
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [
                    {"id": "q1", "text": "A?", "type": "text"},
                    {"id": "q1", "text": "B?", "type": "text"},
                ],
            }
        )
    assert "duplicates" in str(exc_info.value)


def test_question_type_must_be_known():
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [{"id": "q1", "text": "?", "type": "essay"}],
            }
        )


def test_multiple_choice_requires_options():
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [{"id": "q1", "text": "?", "type": "multiple_choice"}],
            }
        )


def test_question_rejects_unknown_keys():
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [{"id": "q1", "text": "?", "type": "text", "question": "drift"}],
            }
        )


def test_question_framing_passes_when_present():
    """Optional 'why we're asking' preface accepts non-empty strings."""
    validate_solicitation_payload(
        {
            **_minimal_valid(),
            "questions": [
                {
                    "id": "q1",
                    "text": "Propose your per-HH FLW rate.",
                    "type": "text",
                    "framing": "Helps us calibrate against the payment band.",
                }
            ],
        }
    )


def test_question_framing_rejects_empty_string():
    """Blank framing would render an awkward 'Why we're asking: ' label with no content."""
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [{"id": "q1", "text": "?", "type": "text", "framing": "   "}],
            }
        )
    assert "questions[0].framing" in exc_info.value.message_dict


def test_question_framing_rejects_non_string():
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [{"id": "q1", "text": "?", "type": "text", "framing": 123}],
            }
        )


# ---------------------------------------------------------------------------
# Evaluation criteria
# ---------------------------------------------------------------------------


def test_dangling_linked_question_id():
    """The most subtle drift surface — linked_questions referencing nonexistent qs."""
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "questions": [{"id": "q1", "text": "?", "type": "text"}],
                "evaluation_criteria": [
                    {
                        "id": "ec1",
                        "name": "Quality",
                        "weight": 100,
                        "linked_questions": ["q1", "q_nope"],
                    }
                ],
            }
        )
    assert "q_nope" in str(exc_info.value)


def test_criterion_weight_must_be_in_range():
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "evaluation_criteria": [{"id": "ec1", "name": "X", "weight": 150}],
            }
        )


def test_criterion_weight_must_be_numeric():
    """Booleans must not satisfy isinstance(weight, (int, float))."""
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "evaluation_criteria": [{"id": "ec1", "name": "X", "weight": True}],
            }
        )


def test_criterion_rejects_unknown_keys():
    """ACE drifted with `criterion` and `dimension`; reject anything not canonical."""
    with pytest.raises(ValidationError):
        validate_solicitation_payload(
            {
                **_minimal_valid(),
                "evaluation_criteria": [{"id": "ec1", "name": "X", "weight": 50, "criterion": "drift"}],
            }
        )


# ---------------------------------------------------------------------------
# Type-correctness on optional scalars
# ---------------------------------------------------------------------------


def test_connect_opportunity_id_must_be_int():
    with pytest.raises(ValidationError):
        validate_solicitation_payload({**_minimal_valid(), "connect_opportunity_id": "1821"})


def test_connect_opportunity_id_int_passes():
    validate_solicitation_payload({**_minimal_valid(), "connect_opportunity_id": 1821})


def test_fund_id_must_be_int():
    with pytest.raises(ValidationError):
        validate_solicitation_payload({**_minimal_valid(), "fund_id": "42"})


# ---------------------------------------------------------------------------
# Partial mode (update path)
# ---------------------------------------------------------------------------


def test_partial_skips_required_fields():
    """Update payloads only carry the fields being changed — required not enforced."""
    validate_solicitation_payload({"status": "closed"}, partial=True)


def test_partial_still_rejects_unknown_fields():
    """The drift-protection point: unknown fields are still rejected in partial mode."""
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload({"overview": "drift"}, partial=True)
    assert "overview" in str(exc_info.value)


def test_partial_validates_present_enum_value():
    """If a field IS sent, it must be valid — even in partial mode."""
    with pytest.raises(ValidationError):
        validate_solicitation_payload({"status": "open"}, partial=True)


def test_partial_validates_nested_shapes():
    """Nested questions/criteria checks fire identically in partial mode."""
    with pytest.raises(ValidationError) as exc_info:
        validate_solicitation_payload(
            {
                "questions": [
                    {"id": "q1", "text": "?", "type": "text"},
                    {"id": "q1", "text": "dup", "type": "text"},
                ]
            },
            partial=True,
        )
    assert "duplicates" in str(exc_info.value)


def test_partial_empty_dict_passes():
    """No-op update is a valid (if uninteresting) partial payload."""
    validate_solicitation_payload({}, partial=True)


def test_partial_does_not_require_solicitation_type():
    """Distinguishes update from create — missing required is fine in partial."""
    # In non-partial mode, this would fail on missing title/description/type.
    validate_solicitation_payload({"status": "active"}, partial=True)
    with pytest.raises(ValidationError):
        validate_solicitation_payload({"status": "active"}, partial=False)


# ---------------------------------------------------------------------------
# plans[] + source refs (create-from-microplan)
# ---------------------------------------------------------------------------


def _plan_entry(**overrides) -> dict:
    base = {
        "plan_id": 123,
        "name": "Ikorodu",
        "region": "Lagos",
        "wards": ["Ikorodu North", "Ikorodu South"],
        "arms": ["intervention", "control"],
        "work_area_count": 42,
        "population": 50000,
    }
    base.update(overrides)
    return base


def test_plans_full_entry_passes():
    payload = _minimal_valid()
    payload["plans"] = [_plan_entry()]
    payload["source_program_id"] = 25
    payload["source_group_id"] = 88
    payload["source_plan_ids"] = [123, 124]
    validate_solicitation_payload(payload)


def test_plans_minimal_entry_passes():
    payload = _minimal_valid()
    payload["plans"] = [{"plan_id": 1, "name": "Solo"}]
    payload["source_group_id"] = None  # single-plan origin
    validate_solicitation_payload(payload)


def test_no_plans_still_valid():
    validate_solicitation_payload(_minimal_valid())


def test_plans_must_be_list():
    payload = _minimal_valid()
    payload["plans"] = {"plan_id": 1}
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_plan_entry_unknown_key_rejected():
    payload = _minimal_valid()
    payload["plans"] = [_plan_entry(geometry={"type": "Polygon"})]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_plan_missing_name_rejected():
    payload = _minimal_valid()
    payload["plans"] = [{"plan_id": 1}]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_plan_missing_plan_id_rejected():
    payload = _minimal_valid()
    payload["plans"] = [{"name": "No id"}]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_plan_duplicate_plan_id_rejected():
    payload = _minimal_valid()
    payload["plans"] = [{"plan_id": 1, "name": "A"}, {"plan_id": 1, "name": "B"}]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_plan_boundaries_geometry_passes():
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    payload = _minimal_valid()
    payload["plans"] = [_plan_entry(boundaries=[{"name": "North", "arm": "intervention", "geometry": geom}])]
    validate_solicitation_payload(payload)


def test_plan_boundary_unknown_key_rejected():
    geom = {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]}
    payload = _minimal_valid()
    payload["plans"] = [_plan_entry(boundaries=[{"name": "North", "geometry": geom, "color": "red"}])]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_plan_boundary_geometry_must_be_geojson():
    payload = _minimal_valid()
    payload["plans"] = [_plan_entry(boundaries=[{"name": "North", "geometry": "not-a-geometry"}])]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_source_program_id_bool_rejected():
    payload = _minimal_valid()
    payload["source_program_id"] = True
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)


def test_source_plan_ids_must_be_int_list():
    payload = _minimal_valid()
    payload["source_plan_ids"] = ["123"]
    with pytest.raises(ValidationError):
        validate_solicitation_payload(payload)
