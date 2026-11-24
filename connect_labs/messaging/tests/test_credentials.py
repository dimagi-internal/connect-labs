"""Where labs' PersonalID client id and secret come from: Labs Admin first, env as the fallback.

Every name and secret here is invented.
"""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from django.db import connection

from connect_labs.messaging import credentials, service
from connect_labs.messaging.models import PersonalIDCredentials, TransportEvent
from connect_labs.messaging.tests.conftest import PROGRAM
from connect_labs.messaging.tests.fake_connectid import CLIENT_ID, SECRET
from connect_labs.messaging.transport.personalid import NotConfigured, PersonalIDClient

pytestmark = pytest.mark.django_db


def _save(client_id, secret):
    PersonalIDCredentials.objects.update_or_create(
        pk=PersonalIDCredentials.SINGLETON_PK, defaults={"client_id": client_id, "secret": secret}
    )


@pytest.fixture
def env_creds(settings):
    settings.PERSONALID_MESSAGING_CLIENT_ID = "env-client"
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = "env-secret"


@pytest.fixture
def no_env_creds(settings):
    settings.PERSONALID_MESSAGING_CLIENT_ID = ""
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = ""


# --- precedence -------------------------------------------------------------------------


def test_the_saved_pair_wins_over_env(env_creds):
    _save("db-client", "db-secret")
    assert credentials.personalid_credentials() == ("db-client", "db-secret", credentials.DB)


def test_env_is_used_when_nothing_is_saved(env_creds):
    assert credentials.personalid_credentials() == ("env-client", "env-secret", credentials.ENV)


@pytest.mark.parametrize("client_id,secret", [("db-client", ""), ("", "db-secret")])
def test_a_half_saved_pair_is_not_mixed_with_env(env_creds, client_id, secret):
    _save(client_id, secret)
    assert credentials.personalid_credentials() == ("env-client", "env-secret", credentials.ENV)


def test_none_when_neither_is_set(no_env_creds):
    assert credentials.personalid_credentials() == ("", "", credentials.NONE)


def test_a_save_is_seen_at_once(env_creds):
    assert credentials.personalid_credentials().source == credentials.ENV
    _save("db-client", "db-secret")
    assert credentials.personalid_credentials().source == credentials.DB
    PersonalIDCredentials.objects.all().delete()
    assert credentials.personalid_credentials().source == credentials.ENV


def test_the_secret_is_not_in_the_repr(env_creds):
    _save("db-client", "db-secret")
    assert "db-secret" not in repr(credentials.personalid_credentials())


def test_the_secret_is_encrypted_at_rest(settings, no_env_creds):
    settings.LABS_FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    _save("db-client", "db-secret")
    with connection.cursor() as cursor:
        cursor.execute("SELECT secret FROM messaging_personalidcredentials")
        (raw,) = cursor.fetchone()
    assert "db-secret" not in raw
    assert credentials.personalid_credentials().secret == "db-secret"


def test_a_secret_that_cannot_be_decrypted_falls_back_to_env(settings, env_creds):
    settings.LABS_FIELD_ENCRYPTION_KEYS = [Fernet.generate_key().decode()]
    _save("db-client", "db-secret")
    settings.LABS_FIELD_ENCRYPTION_KEYS = []
    assert credentials.personalid_credentials() == ("env-client", "env-secret", credentials.ENV)


# --- the consumers ----------------------------------------------------------------------


def test_the_client_sends_with_the_saved_pair(settings, fake_connectid, messaging_on, worker):
    """fake connect-id accepts only CLIENT_ID:SECRET; env holds something else."""
    settings.PERSONALID_MESSAGING_CLIENT_ID = "stale-env-client"
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = "stale-env-secret"
    _save(CLIENT_ID, SECRET)
    fake_connectid.add_user(worker.username)
    address = service.address_for(worker, PROGRAM, channel_name="Bednet Program")
    assert address.channel_id in fake_connectid.channels


def test_the_client_refuses_to_start_with_no_credentials(no_env_creds):
    with pytest.raises(NotConfigured) as e:
        PersonalIDClient(base_url="https://connectid.test")
    assert "Labs Admin" in str(e.value)


def test_a_post_signed_with_the_saved_secret_is_accepted(client, settings, fake_connectid, messaging_on, worker):
    fake_connectid.add_user(worker.username)
    channel = service.address_for(worker, PROGRAM, channel_name="Bednet Program")
    _save(CLIENT_ID, SECRET)
    settings.PERSONALID_MESSAGING_CLIENT_ID = "stale-env-client"
    settings.PERSONALID_MESSAGING_CLIENT_SECRET = "stale-env-secret"
    assert fake_connectid.set_consent(client, channel.channel_id, False).status_code == 200
    assert TransportEvent.objects.count() == 1


def test_a_post_signed_with_the_env_secret_is_refused_once_one_is_saved(
    client, settings, fake_connectid, messaging_on, worker
):
    fake_connectid.add_user(worker.username)
    channel = service.address_for(worker, PROGRAM, channel_name="Bednet Program")
    _save(CLIENT_ID, "a-newer-secret")  # fake connect-id still signs with SECRET, the env one
    assert fake_connectid.set_consent(client, channel.channel_id, False).status_code == 401
    assert TransportEvent.objects.count() == 0
