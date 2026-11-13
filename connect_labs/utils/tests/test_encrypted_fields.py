import pytest
from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.db import connection, models
from django.test.utils import isolate_apps

from connect_labs.utils.encrypted_fields import (
    PREFIX,
    EncryptedTextField,
    EncryptionKeyError,
    decrypt,
    encrypt,
    rotate_encrypted_values,
)


def new_key() -> str:
    return Fernet.generate_key().decode()


@pytest.fixture
def keys(settings):
    settings.LABS_FIELD_ENCRYPTION_KEYS = [new_key()]
    settings.LABS_FIELD_ENCRYPTION_REQUIRED = True
    return settings.LABS_FIELD_ENCRYPTION_KEYS


def test_a_value_round_trips_and_is_not_stored_in_clear(keys):
    stored = encrypt("s3cret-password")
    assert stored.startswith(PREFIX) and "s3cret" not in stored
    assert decrypt(stored) == "s3cret-password"


def test_the_same_value_encrypts_differently_each_time(keys):
    assert encrypt("same") != encrypt("same")


def test_a_key_rotates_without_losing_old_values(settings, keys):
    old = keys[0]
    stored_under_old = encrypt("hello")
    new = new_key()
    settings.LABS_FIELD_ENCRYPTION_KEYS = [new, old]  # new first, old kept for reading
    assert decrypt(stored_under_old) == "hello"
    stored_under_new = encrypt("world")
    settings.LABS_FIELD_ENCRYPTION_KEYS = [new]  # old retired
    assert decrypt(stored_under_new) == "world"
    with pytest.raises(EncryptionKeyError):
        decrypt(stored_under_old)


def test_a_wrong_or_missing_key_is_a_typed_error_not_garbage(settings, keys):
    stored = encrypt("x")
    settings.LABS_FIELD_ENCRYPTION_KEYS = [new_key()]
    with pytest.raises(EncryptionKeyError):
        decrypt(stored)
    settings.LABS_FIELD_ENCRYPTION_KEYS = []
    with pytest.raises(EncryptionKeyError):
        decrypt(stored)


def test_a_legacy_clear_value_is_read_as_is(keys):
    assert decrypt("plain-old-value") == "plain-old-value"


def test_saving_with_no_key_is_refused_where_a_key_is_required(settings):
    settings.LABS_FIELD_ENCRYPTION_KEYS = []
    settings.LABS_FIELD_ENCRYPTION_REQUIRED = True
    with pytest.raises(ImproperlyConfigured):
        encrypt("secret")


def test_saving_with_no_key_is_allowed_where_it_is_not_required(settings):
    settings.LABS_FIELD_ENCRYPTION_KEYS = []
    settings.LABS_FIELD_ENCRYPTION_REQUIRED = False
    assert encrypt("dev-value") == "dev-value"


def test_empty_and_null_pass_through(keys):
    field = EncryptedTextField()
    assert field.get_prep_value(None) is None and field.get_prep_value("") == ""
    assert field.from_db_value(None, None, None) is None and field.from_db_value("", None, None) == ""


@pytest.mark.django_db(transaction=True)
def test_the_column_holds_ciphertext_and_rotation_rewrites_it(settings, keys):
    with isolate_apps("connect_labs.hqdata"):

        class Secret(models.Model):
            value = EncryptedTextField()

            class Meta:
                app_label = "hqdata"
                db_table = "test_encrypted_secret"

        with connection.schema_editor() as editor:
            editor.create_model(Secret)
        try:
            row = Secret.objects.create(value="hunter2")
            with connection.cursor() as cursor:
                cursor.execute("SELECT value FROM test_encrypted_secret WHERE id = %s", [row.pk])
                raw = cursor.fetchone()[0]
            assert raw.startswith(PREFIX) and "hunter2" not in raw
            assert Secret.objects.get(pk=row.pk).value == "hunter2"

            old, new = keys[0], new_key()
            settings.LABS_FIELD_ENCRYPTION_KEYS = [new, old]
            assert rotate_encrypted_values(Secret, "value") == 1
            settings.LABS_FIELD_ENCRYPTION_KEYS = [new]  # old retired: only the rewrite makes this readable
            assert Secret.objects.get(pk=row.pk).value == "hunter2"
        finally:
            with connection.schema_editor() as editor:
                editor.delete_model(Secret)
