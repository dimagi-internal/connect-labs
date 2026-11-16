"""A text column that is encrypted at rest.

Modelled on commcare-sync's ``Database.connection_string``: Fernet, with ``MultiFernet``
over a LIST of keys. A value is encrypted with the first key and can be read with any of
them, so a key rotates by putting the new one first and keeping the old one until every
row has been re-saved (``rotate_encrypted_values`` does that).

Keys come from ``settings.LABS_FIELD_ENCRYPTION_KEYS`` (comma-separated in the
environment; mint one with ``cryptography.fernet.Fernet.generate_key()``). In an
environment that sets ``LABS_FIELD_ENCRYPTION_REQUIRED`` a value cannot be saved with no
key: it fails loudly instead of quietly writing a secret in clear.

What it does not do: the column is decrypted on every read, so a value is plaintext in
Python for as long as the instance lives. Keep such a field off admin forms, serializers
and log lines (``exclude`` it, or show only that a value is set).
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models

# Marks a stored value as ours. Anything without it is a legacy clear value, read as is and
# encrypted the next time the row is saved, so a column can be converted in place.
PREFIX = "fernet1:"


class EncryptionKeyError(Exception):
    """A stored value cannot be decrypted: no key is configured, or none of them fits."""


def _keys() -> list[str]:
    return [k.strip() for k in (getattr(settings, "LABS_FIELD_ENCRYPTION_KEYS", None) or []) if k and k.strip()]


def _fernet() -> MultiFernet | None:
    keys = _keys()
    return MultiFernet([Fernet(k.encode()) for k in keys]) if keys else None


def encrypt(value: str) -> str:
    fernet = _fernet()
    if fernet is None:
        if getattr(settings, "LABS_FIELD_ENCRYPTION_REQUIRED", False):
            raise ImproperlyConfigured(
                "LABS_FIELD_ENCRYPTION_KEYS is not set, so an encrypted field cannot be saved. "
                "Mint a key with cryptography.fernet.Fernet.generate_key()."
            )
        return value
    return PREFIX + fernet.encrypt(value.encode()).decode()


def decrypt(stored: str) -> str:
    if not stored.startswith(PREFIX):
        return stored
    fernet = _fernet()
    if fernet is None:
        raise EncryptionKeyError("A value is encrypted but LABS_FIELD_ENCRYPTION_KEYS is not set.")
    try:
        return fernet.decrypt(stored[len(PREFIX) :].encode()).decode()
    except InvalidToken as exc:
        raise EncryptionKeyError("No configured encryption key decrypts this value.") from exc


class EncryptedTextField(models.TextField):
    description = "Text, encrypted at rest with Fernet"

    def get_prep_value(self, value):
        value = super().get_prep_value(value)
        if value is None or value == "":
            return value
        return encrypt(value)

    def from_db_value(self, value, expression, connection):
        if value is None or value == "":
            return value
        return decrypt(value)


def rotate_encrypted_values(model, field_name: str, *, batch: int = 500) -> int:
    """Re-save every row of ``model`` so its ``field_name`` is encrypted with the CURRENT first key.

    Run after putting a new key first. Returns the number of rows rewritten. Only the one
    column is written, so it cannot clobber anything else a concurrent request changed.
    """
    rewritten = 0
    for pk in model.objects.values_list("pk", flat=True).iterator():
        row = model.objects.get(pk=pk)
        model.objects.filter(pk=pk).update(**{field_name: getattr(row, field_name)})
        rewritten += 1
    return rewritten
