"""Tests for analysis_config form-field extraction helpers."""
from django.test import Client

from connect_labs.audit.analysis_config import (
    extract_additional_case_info,
    extract_field_paths,
    extract_images_with_question_ids,
)


def test_extract_field_paths_flattens_leaf_scalars():
    form_json = {
        "form": {
            "child_weight": "12.5",
            "group": {"photo_a": "img1.jpg", "muac": "11.0"},
            "meta": {"timeEnd": "2026-01-01"},  # SKIP_KEYS -> excluded
            "@name": "Form",  # SKIP_KEYS -> excluded
            "repeat": [{"x": "1"}, {"x": "2"}],  # list -> skipped in v1
        }
    }
    paths = extract_field_paths(form_json)
    assert paths == ["child_weight", "group/muac", "group/photo_a"]


def test_extract_field_paths_handles_top_level_without_form_key():
    assert extract_field_paths({"a": "1", "b": {"c": "2"}}) == ["a", "b/c"]


def test_extract_field_paths_empty():
    assert extract_field_paths({}) == []
    assert extract_field_paths(None) == []


def test_extract_additional_case_info_reads_the_three_fields():
    form_data = {
        "additional_case_info": {
            "child_name": "Aliyu Musa",
            "childs_dob": "2024-06-10",
            "household_name": "Musa Household",
            "hh_case_id": "unrelated-other-field",
        }
    }
    assert extract_additional_case_info(form_data) == {
        "child_name": "Aliyu Musa",
        "childs_dob": "2024-06-10",
        "household_name": "Musa Household",
    }


def test_extract_additional_case_info_missing_group_returns_empty_strings():
    assert extract_additional_case_info({}) == {"child_name": "", "childs_dob": "", "household_name": ""}
    assert extract_additional_case_info({"additional_case_info": "not-a-dict"}) == {
        "child_name": "",
        "childs_dob": "",
        "household_name": "",
    }


def test_extract_images_with_question_ids_attaches_case_info_not_entity_id():
    """entity_id was replaced with child_name/childs_dob/household_name on each
    image -- entity_name (used elsewhere, e.g. CSV export) is untouched."""
    visit_data = {
        "form_json": {
            "form": {
                "group": {"photo_a": "img1.jpg"},
                "additional_case_info": {
                    "child_name": "Aliyu Musa",
                    "childs_dob": "2024-06-10",
                    "household_name": "Musa Household",
                },
            }
        },
        "images": [{"blob_id": "b1", "name": "img1.jpg"}],
        "entity_name": "Aliyu Musa",
        "entity_id": "should-no-longer-appear",
    }
    result = extract_images_with_question_ids(visit_data)
    assert len(result) == 1
    image = result[0]
    assert image["child_name"] == "Aliyu Musa"
    assert image["childs_dob"] == "2024-06-10"
    assert image["household_name"] == "Musa Household"
    assert image["entity_name"] == "Aliyu Musa"
    assert "entity_id" not in image


def test_field_questions_requires_oauth(db):
    from connect_labs.users.models import User

    user, _ = User.objects.update_or_create(username="noauth", defaults={"email": "noauth@example.com"})
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    resp = client.get("/audit/api/opportunity/42/field-questions/")
    assert resp.status_code == 401
