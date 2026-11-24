"""The client id and secret labs uses as a PersonalID message server: the one place they are read.

Labs Admin › PersonalID messaging (`PersonalIDCredentials`, one row) wins when it holds both a
client id and a secret; otherwise the env settings `PERSONALID_MESSAGING_CLIENT_ID` /
`PERSONALID_MESSAGING_CLIENT_SECRET` (Secrets Manager, optional), so dev, tests and any existing
env config keep working. The two are never mixed: a pair comes whole from one source.

Read from the database on every use (one primary-key read), never cached: a save at Labs Admin is
in force at once on the web tier (signature checks) and the worker tier (sends).

The secret never goes into a log line, an exception message or a page.
"""

from __future__ import annotations

import logging
from typing import NamedTuple

from django.conf import settings

logger = logging.getLogger(__name__)

DB = "db"
ENV = "env"
NONE = "none"


class Credentials(NamedTuple):
    client_id: str
    secret: str
    source: str  # DB | ENV | NONE

    def __repr__(self) -> str:  # never the secret, even in a traceback's locals
        secret = "<set>" if self.secret else "<empty>"
        return f"Credentials(client_id={self.client_id!r}, secret={secret}, source={self.source!r})"


def _from_db() -> tuple[str, str]:
    from connect_labs.messaging.models import PersonalIDCredentials
    from connect_labs.utils.encrypted_fields import EncryptionKeyError

    try:
        row = PersonalIDCredentials.load()
    except EncryptionKeyError:
        logger.error("messaging: the stored PersonalID secret cannot be decrypted; falling back to env settings")
        return "", ""
    if row is None:
        return "", ""
    return (row.client_id or "").strip(), row.secret or ""


def _from_env() -> tuple[str, str]:
    return (
        (getattr(settings, "PERSONALID_MESSAGING_CLIENT_ID", "") or "").strip(),
        getattr(settings, "PERSONALID_MESSAGING_CLIENT_SECRET", "") or "",
    )


def personalid_credentials() -> Credentials:
    """(client_id, secret, source): Labs Admin's pair when complete, else env's, else empty."""
    client_id, secret = _from_db()
    if client_id and secret:
        return Credentials(client_id, secret, DB)
    client_id, secret = _from_env()
    if client_id and secret:
        return Credentials(client_id, secret, ENV)
    return Credentials("", "", NONE)
