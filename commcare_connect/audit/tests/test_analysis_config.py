"""Tests for analysis_config form-field extraction helpers."""
from django.test import Client

from commcare_connect.audit.analysis_config import extract_field_paths


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


def test_field_questions_requires_oauth(db):
    from commcare_connect.users.models import User

    user, _ = User.objects.update_or_create(username="noauth", defaults={"email": "noauth@example.com"})
    client = Client(enforce_csrf_checks=False)
    client.force_login(user)
    resp = client.get("/audit/api/opportunity/42/field-questions/")
    assert resp.status_code == 401
