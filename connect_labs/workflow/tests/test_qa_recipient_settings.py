"""Your profile on the settings page: proper fields, not a JSON blob (scope_config/views.py
``_fields``), and the PersonalID username "Send to me" uses (workflow/qa_recipient.py)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from connect_labs.workflow import qa_recipient


@pytest.fixture
def me(client, django_user_model):
    account = django_user_model.objects.create_user(username="jonathan", password="x", email="jonathan@dimagi.com")
    client.force_login(account)
    return account


def _url(username="jonathan"):
    return reverse("labs:settings", args=["user", username])


def test_your_profile_is_a_form_with_labelled_fields(client, me):
    body = client.get(_url()).content.decode()
    form = body.split('data-testid="settings-fields-profile"', 1)[1].split("</form>", 1)[0]
    assert "My PersonalID username" in form and "Sign my emails as" in form
    assert 'name="field_personalid_username"' in form and 'name="field_signed_as"' in form
    assert "Advanced: edit as JSON" in body  # still there, no longer the way in


def test_saving_the_field_keeps_it_for_send_to_me_and_emptying_it_clears_it(client, me):
    from connect_labs.scope_config import service
    from connect_labs.scope_config.scopes import Scope

    version = service.get("profile", Scope.of("user", "jonathan"), qa_recipient_caller(me))["version"]
    client.post(
        _url(),
        {
            "namespace": "profile",
            "action": "save_fields",
            "version": version,
            "field_personalid_username": " abc123xyz ",
        },
    )
    assert qa_recipient.saved(me) == "abc123xyz"
    body = client.get(_url()).content.decode()
    assert 'value="abc123xyz"' in body

    version = service.get("profile", Scope.of("user", "jonathan"), qa_recipient_caller(me))["version"]
    client.post(
        _url(), {"namespace": "profile", "action": "save_fields", "version": version, "field_personalid_username": ""}
    )
    assert qa_recipient.saved(me) == ""


def test_a_value_that_is_not_a_personalid_username_is_refused_and_says_why(client, me):
    response = client.post(
        _url(),
        {"namespace": "profile", "action": "save_fields", "version": 0, "field_personalid_username": "a b"},
        follow=True,
    )
    assert qa_recipient.saved(me) == ""
    assert [str(m) for m in response.context["messages"]] == [
        "My PersonalID username: 'a b' is not valid: a PersonalID username is 3 or more letters and digits, "
        "with no spaces"
    ]


def qa_recipient_caller(user):
    from connect_labs.labs.access.scopes import Caller

    return Caller(user=user)
