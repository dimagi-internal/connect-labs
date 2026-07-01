from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.labs.models import UserConnectToken
from connect_labs.users.models import User


@pytest.mark.django_db
def test_stores_and_retrieves_token():
    user = User.objects.create(username="alice")
    token = UserConnectToken.objects.create(
        user=user,
        access_token="abc",
        refresh_token="def",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert user.connect_token == token
    assert UserConnectToken.objects.get(user=user).access_token == "abc"


@pytest.mark.django_db
def test_is_expired_true_when_past_expiry():
    user = User.objects.create(username="bob")
    token = UserConnectToken.objects.create(
        user=user,
        access_token="x",
        expires_at=timezone.now() - timedelta(seconds=1),
    )
    assert token.is_expired is True


@pytest.mark.django_db
def test_is_expired_true_within_safety_window():
    """Tokens expiring in <60s are treated as expired to avoid races."""
    user = User.objects.create(username="carol")
    token = UserConnectToken.objects.create(
        user=user,
        access_token="x",
        expires_at=timezone.now() + timedelta(seconds=30),
    )
    assert token.is_expired is True


@pytest.mark.django_db
def test_is_expired_false_when_fresh():
    user = User.objects.create(username="dave")
    token = UserConnectToken.objects.create(
        user=user,
        access_token="x",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    assert token.is_expired is False
