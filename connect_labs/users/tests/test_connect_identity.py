import pytest

from connect_labs.users.connect_identity import EmailHeldByAnotherAccount, resolve_connect_user
from connect_labs.users.models import User


@pytest.mark.django_db
def test_new_person_gets_a_row():
    user = resolve_connect_user("person", "person@example.com", "Per Son")
    assert (user.username, user.email, user.name) == ("person", "person@example.com", "Per Son")


@pytest.mark.django_db
def test_returning_person_is_found_by_username_and_updated():
    existing = User.objects.create(username="person", email="old@example.com", name="Old")
    user = resolve_connect_user("person", "new@example.com", "New")
    assert user.id == existing.id
    assert (user.email, user.name) == ("new@example.com", "New")


@pytest.mark.django_db
def test_a_login_without_email_keeps_the_one_on_file():
    User.objects.create(username="person", email="person@example.com")
    assert resolve_connect_user("person", "", "P").email == "person@example.com"


@pytest.mark.django_db
def test_row_made_by_hq_sign_in_is_adopted_and_renamed():
    """The campaign sign-in keys rows by HQ username == email; that is the same person."""
    hq_row = User.objects.create(username="Person@Example.com", email="person@example.com", name="")
    user = resolve_connect_user("person", "person@example.com", "Per Son")
    assert user.id == hq_row.id
    assert user.username == "person"
    assert user.name == "Per Son"
    assert User.objects.filter(email__iexact="person@example.com").count() == 1
    # The next sign-in finds it by username.
    assert resolve_connect_user("person", "person@example.com", "Per Son").id == hq_row.id


@pytest.mark.django_db
def test_email_held_by_another_connect_username_is_refused():
    other = User.objects.create(username="someone", email="person@example.com")
    with pytest.raises(EmailHeldByAnotherAccount) as exc:
        resolve_connect_user("person", "PERSON@example.com", "P")
    assert exc.value.holder.id == other.id
    assert not User.objects.filter(username="person").exists()
    other.refresh_from_db()
    assert other.username == "someone"


@pytest.mark.django_db
def test_own_row_still_signs_in_when_its_new_email_is_taken():
    mine = User.objects.create(username="person", email="person@example.com")
    User.objects.create(username="someone", email="taken@example.com")
    user = resolve_connect_user("person", "taken@example.com", "P")
    assert user.id == mine.id
    assert user.email == "person@example.com"
