"""Tests for Documentations page comments (labs-local DocComment storage)."""

import json

import pytest
from django.urls import reverse

from connect_labs.labs.models import DocComment

COMMENTS_URL = reverse("labs:docs_comments", args=["chc"])


@pytest.fixture
def author(django_user_model):
    return django_user_model.objects.create(username="author", email="author@dimagi.com", name="Ada Author")


@pytest.fixture
def other_user(django_user_model):
    return django_user_model.objects.create(username="other", email="other@dimagi.com", name="Otto Other")


def _post(client, body, doc_key="chc", **extra):
    return client.post(
        reverse("labs:docs_comments", args=[doc_key]),
        data=json.dumps({"body": body, **extra}),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_login_required(client):
    response = client.get(COMMENTS_URL)
    assert response.status_code == 302
    assert "/labs/login/" in response.url or "login" in response.url


@pytest.mark.django_db
def test_post_then_list_comment(client, author):
    client.force_login(author)

    response = _post(client, "The MUAC cutoff needs a note.")
    assert response.status_code == 201
    created = response.json()["comment"]
    assert created["body"] == "The MUAC cutoff needs a note."
    assert created["author_name"] == "Ada Author"

    listed = client.get(COMMENTS_URL).json()
    assert [c["body"] for c in listed["comments"]] == ["The MUAC cutoff needs a note."]
    assert listed["current_username"] == "author"


@pytest.mark.django_db
def test_comments_are_visible_to_everyone(client, author, other_user):
    """The whole point of the section: no program/opportunity scoping."""
    client.force_login(author)
    _post(client, "Left by the author")

    client.force_login(other_user)
    payload = client.get(COMMENTS_URL).json()
    assert [c["body"] for c in payload["comments"]] == ["Left by the author"]
    assert payload["current_username"] == "other"


@pytest.mark.django_db
def test_comments_are_stored_locally_not_on_connect(client, author, monkeypatch):
    """A comment write must never touch the production LabsRecord API."""

    def explode(*args, **kwargs):
        raise AssertionError("comments must not be written to the Connect LabsRecord API")

    monkeypatch.setattr(
        "connect_labs.labs.integrations.connect.api_client.LabsRecordAPIClient.create_record",
        explode,
    )
    client.force_login(author)
    assert _post(client, "stays local").status_code == 201
    assert DocComment.objects.filter(body="stays local").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("body", ["", "   ", "\n\t"])
def test_empty_comment_rejected(client, author, body):
    client.force_login(author)
    response = _post(client, body)
    assert response.status_code == 400
    assert "empty" in response.json()["error"].lower()
    assert DocComment.objects.count() == 0


@pytest.mark.django_db
def test_overlong_comment_rejected(client, author):
    client.force_login(author)
    response = _post(client, "x" * 5001)
    assert response.status_code == 400
    assert "too long" in response.json()["error"].lower()
    assert DocComment.objects.count() == 0


@pytest.mark.django_db
def test_invalid_json_rejected(client, author):
    client.force_login(author)
    response = client.post(COMMENTS_URL, data="not json", content_type="application/json")
    assert response.status_code == 400


@pytest.mark.django_db
def test_unknown_doc_key_rejected(client, author):
    client.force_login(author)
    assert client.get(reverse("labs:docs_comments", args=["nope"])).status_code == 404
    assert _post(client, "hi", doc_key="nope").status_code == 404
    assert DocComment.objects.count() == 0


@pytest.mark.django_db
def test_author_can_delete_own_comment(client, author):
    client.force_login(author)
    comment_id = _post(client, "delete me").json()["comment"]["id"]

    response = client.post(reverse("labs:docs_comment_delete", args=["chc", comment_id]))
    assert response.status_code == 200
    assert not DocComment.objects.filter(id=comment_id).exists()


@pytest.mark.django_db
def test_other_user_cannot_delete_someone_elses_comment(client, author, other_user):
    client.force_login(author)
    comment_id = _post(client, "not yours").json()["comment"]["id"]

    client.force_login(other_user)
    response = client.post(reverse("labs:docs_comment_delete", args=["chc", comment_id]))
    assert response.status_code == 403
    assert DocComment.objects.filter(id=comment_id).exists()


@pytest.mark.django_db
def test_delete_requires_matching_doc_key(client, author):
    """A comment can't be deleted through another page's endpoint."""
    client.force_login(author)
    comment = DocComment.objects.create(doc_key="other", body="elsewhere", author=author)

    response = client.post(reverse("labs:docs_comment_delete", args=["chc", comment.id]))
    assert response.status_code == 403
    assert DocComment.objects.filter(id=comment.id).exists()


@pytest.mark.django_db
def test_comments_are_scoped_per_doc_key(client, author):
    client.force_login(author)
    _post(client, "about chc")
    DocComment.objects.create(doc_key="other", body="about something else", author=author)

    bodies = [c["body"] for c in client.get(COMMENTS_URL).json()["comments"]]
    assert bodies == ["about chc"]


@pytest.mark.django_db
def test_comments_ordered_oldest_first(client, author):
    client.force_login(author)
    for body in ["first", "second", "third"]:
        _post(client, body)

    bodies = [c["body"] for c in client.get(COMMENTS_URL).json()["comments"]]
    assert bodies == ["first", "second", "third"]


@pytest.mark.django_db
def test_get_not_allowed_on_delete_endpoint(client, author):
    client.force_login(author)
    comment_id = _post(client, "x").json()["comment"]["id"]
    response = client.get(reverse("labs:docs_comment_delete", args=["chc", comment_id]))
    assert response.status_code == 405


@pytest.mark.django_db
def test_comment_bodies_are_never_served_as_html(client, author):
    """A body containing markup must only ever come back as JSON data.

    The panel injects bodies with textContent, so the defence that matters is
    that comment text is never interpolated into the HTML document itself and
    is delivered under a content type the browser won't parse as markup.
    """
    client.force_login(author)
    payload = "<script>alert(1)</script>"
    _post(client, payload)

    api_response = client.get(COMMENTS_URL)
    assert api_response["Content-Type"].startswith("application/json")
    assert api_response.json()["comments"][0]["body"] == payload

    page = client.get(reverse("labs:docs_chc")).content.decode()
    assert payload not in page


@pytest.mark.django_db
def test_comment_anchors_to_a_section(client, author):
    client.force_login(author)
    response = _post(
        client,
        "This step needs the case property spelled out.",
        section_id="setup-task-types",
        section_label="Configure the relearn task types on Connect",
    )
    assert response.status_code == 201

    comment = client.get(COMMENTS_URL).json()["comments"][0]
    assert comment["section_id"] == "setup-task-types"
    assert comment["section_label"] == "Configure the relearn task types on Connect"


@pytest.mark.django_db
def test_comment_without_section_is_page_wide(client, author):
    client.force_login(author)
    _post(client, "general note")
    comment = client.get(COMMENTS_URL).json()["comments"][0]
    assert comment["section_id"] == ""
    assert comment["section_label"] == ""


@pytest.mark.django_db
def test_overlong_section_fields_are_truncated_not_rejected(client, author):
    """A renamed or absurd heading must not cost the user their comment."""
    client.force_login(author)
    response = _post(client, "kept", section_id="x" * 400, section_label="y" * 900)
    assert response.status_code == 201

    comment = DocComment.objects.get()
    assert len(comment.section_id) == 128
    assert len(comment.section_label) == 255


@pytest.mark.django_db
@pytest.mark.parametrize("payload", ["[]", '"a string"', "null", "42"])
def test_non_object_json_body_rejected(client, author, payload):
    """A JSON array/scalar must 400, not raise on .get()."""
    client.force_login(author)
    response = client.post(COMMENTS_URL, data=payload, content_type="application/json")
    assert response.status_code == 400
    assert DocComment.objects.count() == 0


@pytest.mark.django_db
def test_non_string_section_id_does_not_error(client, author):
    client.force_login(author)
    response = _post(client, "body", section_id={"nope": 1})
    assert response.status_code == 201


@pytest.mark.django_db
def test_docs_page_renders_comment_sidebar_and_new_setup_steps(client, author):
    client.force_login(author)
    content = client.get(reverse("labs:docs_chc")).content.decode()
    # Sidebar rail, not the old bottom panel.
    assert 'id="dc-rail"' in content
    # The two documented setup steps and their anchors.
    assert 'id="setup-task-types"' in content
    assert 'id="setup-verification-rules"' in content
    assert "trigger_forced_module_capturing_a_valid_image" in content
    assert "form.check_dob_gap_suspicious_duplicate" in content


@pytest.mark.django_db
def test_docs_page_wires_up_the_comments_endpoint(client, author):
    client.force_login(author)
    response = client.get(reverse("labs:docs_chc"))
    assert response.status_code == 200
    assert COMMENTS_URL in response.content.decode()
