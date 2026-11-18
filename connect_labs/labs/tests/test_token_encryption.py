"""OAuth tokens are encrypted at rest; rows stored in clear before that still read."""

from datetime import timedelta

import pytest
from cryptography.fernet import Fernet
from django.db import connection
from django.utils import timezone

from connect_labs.labs.models import UserCCHQToken, UserConnectToken, UserOCSToken
from connect_labs.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture
def keys(settings):
    settings.LABS_FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    settings.LABS_FIELD_ENCRYPTION_REQUIRED = True


def _stored(model, pk):
    with connection.cursor() as cur:
        cur.execute(f"SELECT access_token, refresh_token FROM {model._meta.db_table} WHERE id = %s", [pk])
        return cur.fetchone()


@pytest.mark.parametrize("model", [UserConnectToken, UserCCHQToken, UserOCSToken])
def test_a_saved_token_is_not_in_the_database_in_clear(keys, model):
    user = User.objects.create(username=f"u-{model.__name__}")
    row = model.objects.create(
        user=user,
        access_token="ACCESS-123",
        refresh_token="REFRESH-456",
        expires_at=timezone.now() + timedelta(hours=1),
    )
    access, refresh = _stored(model, row.pk)
    assert "ACCESS-123" not in access and "REFRESH-456" not in refresh
    fresh = model.objects.get(pk=row.pk)
    assert (fresh.access_token, fresh.refresh_token) == ("ACCESS-123", "REFRESH-456")
    assert model.objects.filter(user=user).values_list("access_token", flat=True).first() == "ACCESS-123"


def test_a_token_stored_in_clear_before_the_change_still_reads(keys):
    user = User.objects.create(username="legacy")
    row = UserConnectToken.objects.create(
        user=user, access_token="x", refresh_token="", expires_at=timezone.now() + timedelta(hours=1)
    )
    with connection.cursor() as cur:
        cur.execute("UPDATE labs_user_connect_token SET access_token = 'OLD-CLEAR' WHERE id = %s", [row.pk])
    assert UserConnectToken.objects.get(pk=row.pk).access_token == "OLD-CLEAR"
