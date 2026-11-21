"""The sync identity: Pulse's poller unless WORKERS_SYNC_USERNAME, and loud when unusable."""

import pytest
from django.test import override_settings

from connect_labs.labs.connect_tokens import ConnectTokenError
from connect_labs.users.models import User
from connect_labs.workers import identity


@pytest.mark.django_db
@override_settings(WORKERS_SYNC_USERNAME="", PULSE_POLLER_USERNAME="poller")
def test_defaults_to_pulses_poller():
    poller = User.objects.create(username="poller", email="poller@example.com")
    assert identity.get_sync_user() == poller


@pytest.mark.django_db
@override_settings(WORKERS_SYNC_USERNAME="syncer", PULSE_POLLER_USERNAME="poller")
def test_the_setting_overrides_the_poller():
    User.objects.create(username="poller", email="poller@example.com")
    syncer = User.objects.create(username="syncer", email="syncer@example.com")
    assert identity.get_sync_user() == syncer


@pytest.mark.django_db
@override_settings(WORKERS_SYNC_USERNAME="ghost")
def test_a_named_user_that_does_not_exist_fails_loudly():
    with pytest.raises(identity.WorkerSyncAuthError, match="WORKERS_SYNC_USERNAME"):
        identity.get_sync_user()


@pytest.mark.django_db
@override_settings(WORKERS_SYNC_USERNAME="", PULSE_POLLER_USERNAME="")
def test_no_poller_fails_loudly_and_names_both_fixes():
    with pytest.raises(identity.WorkerSyncAuthError) as exc:
        identity.get_sync_user()
    assert "pulse_poller" in str(exc.value) and "WORKERS_SYNC_USERNAME" in str(exc.value)


@pytest.mark.django_db
@override_settings(WORKERS_SYNC_USERNAME="syncer")
def test_an_expired_token_says_how_to_renew_it(monkeypatch):
    User.objects.create(username="syncer", email="syncer@example.com")

    def expired(user):
        raise ConnectTokenError("refresh token expired")

    monkeypatch.setattr(identity, "get_valid_access_token", expired)
    with pytest.raises(identity.WorkerSyncAuthError, match="log into labs"):
        identity.get_sync_client()
