"""The canopy SDK's host halves (S1-S6) the bridge uses, from ``dimagi-canopy`` 0.34.0.

canopy-web ships the pieces a host needs to be the SYSTEM OF RECORD for the people it brings to
canopy (``CONTRACTS.md`` S1-S6, canopy SDK ``CONTRACT_VERSION "1"``):

    S1  canopy_sdk.host.relay.RelayClient            relay a person's conversation server-to-server (0.32)
    S2  canopy_sdk.host.contacts.update_contacts     keep canopy's pointer and profile cache current (0.30)
    S3  canopy_sdk.attested_context.sign_context     the context labs attests on one message (0.33)
    S4  canopy_sdk.host.subject.verify_subject       canopy's word that a call is one contact's turn (0.31)
    S5  canopy_sdk.host.replies.verify_reply         canopy's signed push of an agent's reply (0.34)
    S6  canopy_sdk.host.contacts.erase_contacts      make canopy forget a person (0.30)
        site.json ``site.person_scheme`` / ``site.retention_days``  (0.30)

Every name here IS the SDK's; this module only adapts the bridge's call shape (a ``HostConfig``
for the ``connect-messaging`` site, never ``CANOPY_HOST``, which is the agent panel's
``connect-labs`` site) and keeps one place tests replace the network. ``test_sdk_wire.py`` pins
the wire the SDK speaks, so an SDK bump that changes it fails there first.

Why not ``canopy_sdk.django.execution.verify_subject``: that wrapper verifies against
``CANOPY_HOST`` (the panel site's resource and name). The messaging site is a second host site
with its own ``HostConfig``, so ``mcp.py`` calls the host function with that config and the same
pieces the wrapper would wire: ``DjangoJtiStore`` (shared by every worker) and a
``ClientKeyResolver`` cached in Django's cache. The webhook does the same for replies.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from canopy_sdk import attested_context
from canopy_sdk import contacts as _contacts_wire
from canopy_sdk import contract, fetch
from canopy_sdk.attested_context import CONTEXT_MAX_LIFETIME, CONTEXT_TYP, MAX_CONTEXT_BODY, ContextError
from canopy_sdk.contacts import ContactRecordError, check_profile
from canopy_sdk.host import contacts as _host_contacts
from canopy_sdk.host.relay import RelayClient, RelayError
from canopy_sdk.host.replies import REPLY_MAX_LIFETIME, REPLY_TYP, Reply, ReplyRefused, verify_reply
from canopy_sdk.host.subject import Subject, SubjectRefused, verify_subject

__all__ = [
    "CONTEXT_MAX_LIFETIME",
    "CONTEXT_TYP",
    "MAX_CONTEXT_BODY",
    "REPLY_MAX_LIFETIME",
    "REPLY_TYP",
    "ContactRecordError",
    "ContextError",
    "RelayClient",
    "RelayError",
    "Reply",
    "ReplyRefused",
    "Subject",
    "SubjectRefused",
    "check_profile",
    "contact_erase_token",
    "contact_update_token",
    "erase_contacts",
    "implementations",
    "sign_context",
    "site_document_token",
    "update_contacts",
    "verify_reply",
    "verify_subject",
]

# ---------------------------------------------------------------------------
# The wire, as the SDK defines it (CONTRACTS.md). Pinned by test_sdk_wire.py.
# ---------------------------------------------------------------------------

#: S1: the visitor assertion's relay channel claim, its grammar, and the turn origin it may name.
CHANNEL_CLAIM = contract.CHANNEL_CLAIM
CHANNEL_PATTERN = contract.CHANNEL_PATTERN
RELAY_ORIGIN = contract.RELAY_ORIGIN
#: S1: canopy's contact routes, under its base URL (``RelayClient`` builds them itself).
CONTACT_SESSIONS_PATH = "/api/contact/sessions"
CONTACT_TURNS_PATH = "/api/contact/turns"
#: S2 / S6: where a host keeps canopy's copy of its people, and makes canopy forget them.
CONTACTS_UPDATE_PATH = _contacts_wire.UPDATE_PATH
CONTACTS_ERASE_PATH = _contacts_wire.ERASE_PATH
CONTACTS_MEDIA_TYPE = _contacts_wire.MEDIA_TYPE
CONTACT_UPDATE_TYP = _contacts_wire.UPDATE_TYP
CONTACT_ERASE_TYP = _contacts_wire.ERASE_TYP
CONTACTS_MAX_LIFETIME = _contacts_wire.MAX_LIFETIME
MAX_CONTACTS = _contacts_wire.MAX_CONTACTS
MAX_PROFILE_BYTES = _contacts_wire.MAX_PROFILE_BYTES
MAX_PROFILE_KEYS = _contacts_wire.MAX_PROFILE_KEYS
#: S2: per-item answers. ``refused`` with a reason starting ``erased:`` is canopy refusing an
#: update labs signed before it erased that person (a late delivery never brings them back).
CREATED, UPDATED, STALE, REFUSED = (
    _contacts_wire.CREATED,
    _contacts_wire.UPDATED,
    _contacts_wire.STALE,
    _contacts_wire.REFUSED,
)
ERASED, NOT_FOUND = _contacts_wire.ERASED, _contacts_wire.NOT_FOUND
ERASED_REASON = "erased"
#: S6: canopy answers 503 ``files_remain`` when a stored file could not yet be deleted; what is done
#: stays done, and sending the erasure again finishes it.
FILES_REMAIN = "files_remain"
#: S3: the context a host attests on one send.
CONTEXT_FORMATS = attested_context.FORMATS
#: S4: canopy's subject statement.
SUBJECT_AUTH_SCHEME = contract.SUBJECT_AUTH_SCHEME
SUBJECT_TYP = contract.SUBJECT_TYP
SUBJECT_MAX_LIFETIME = contract.SUBJECT_MAX_LIFETIME
SUBJECT_REQUIRED_CLAIMS = contract.SUBJECT_REQUIRED_CLAIMS
#: S1/S7: a send over canopy's per-contact limits (6 a minute, 200 a day) is refused with this code.
RATE_LIMITED = "rate_limited"
#: site.json: the scheme of the site's records of its people, and how long canopy keeps them.
PERSON_SCHEME_FACT = "person_scheme"
RETENTION_DAYS_FACT = "retention_days"


def implementations() -> dict[str, str]:
    """Where each name comes from: ``"sdk"`` for every one (the local fallbacks are gone)."""
    named = {
        "RelayClient": RelayClient,
        "sign_context": attested_context.sign_context,
        "update_contacts": _host_contacts.update_contacts,
        "erase_contacts": _host_contacts.erase_contacts,
        "verify_subject": verify_subject,
        "verify_reply": verify_reply,
    }
    out = {name: "sdk" if obj.__module__.startswith("canopy_sdk.") else "local" for name, obj in named.items()}
    from canopy_sdk.site import SITE_FACTS

    out["site_document"] = "sdk" if PERSON_SCHEME_FACT in SITE_FACTS else "local"
    return out


def _http_post(url: str, body: bytes, *, content_type: str):
    """POST raw bytes to canopy: ``(status, raw, headers)``, redirects refused. Replaced in tests."""
    return fetch.post_bytes(url, body, content_type=content_type)


# ===========================================================================
# S3 -- the context labs attests on one send
# ===========================================================================


def sign_context(config, *, sub: str, session: str, format: str, body: str, now: float | None = None) -> str:
    """Labs' signed context for ONE send into canopy conversation ``session`` about ``sub``
    (``typ: canopy-context+jwt``, the site key, at most 60 s). ``RelayClient.send(context=)`` signs
    its own; this is for tests and anything that needs the token alone."""
    return attested_context.sign_context(
        config.app_name, config.signing_key, sub=sub, session=str(session), format=format, body=body, now=now
    )


# ===========================================================================
# S2 / S6 -- the contact record and erasure
# ===========================================================================


def contact_update_token(config, contacts: Sequence[Mapping], *, now: float | None = None) -> str:
    """The signed S2 request body (what ``update_contacts`` POSTs)."""
    return _contacts_wire.update_token(
        config.app_name,
        config.signing_key,
        _contacts_wire.update_url(config.canopy_base_url),
        list(contacts),
        workspace=config.workspace,
        now=now,
    )


def contact_erase_token(config, subs: Iterable[str], *, now: float | None = None) -> str:
    """The signed S6 request body (what ``erase_contacts`` POSTs)."""
    return _contacts_wire.erase_token(
        config.app_name,
        config.signing_key,
        _contacts_wire.erase_url(config.canopy_base_url),
        list(subs),
        workspace=config.workspace,
        now=now,
    )


def update_contacts(config, contacts: Sequence[Mapping], *, post: Callable | None = None) -> dict:
    """S2: ``{sub, display_name?, record_uri?, profile?, version}`` items (at most 100) to canopy.
    Returns ``{"results": [{sub, contact_id, status, reason?}]}``; raises ``ContactRecordError``.
    ``record_uri: ""`` / ``profile: {}`` clear a field; a field left out is left as canopy holds it.
    ``post(url, body, content_type=)`` returns ``(status, raw, headers)``."""
    return _host_contacts.update_contacts(config, contacts, post=post or _http_post)


def erase_contacts(config, subs: Iterable[str], *, post: Callable | None = None) -> dict:
    """S6: make canopy forget up to 100 people. Returns ``{"results": [{sub, status}]}``;
    raises ``ContactRecordError`` (``files_remain`` when canopy must be asked again)."""
    return _host_contacts.erase_contacts(config, subs, post=post or _http_post)


def refused_as_erased(result: Mapping) -> bool:
    """canopy refused this update item because labs signed it before canopy erased the person."""
    reason = str(result.get("reason") or "")
    return result.get("status") == REFUSED and reason.partition(":")[0].strip() == ERASED_REASON


# ===========================================================================
# site.json with site.person_scheme (and site.retention_days)
# ===========================================================================


def site_document_token(
    config,
    *,
    person_scheme: str,
    site: Mapping,
    title: str = "",
    pages: Sequence = (),
    scope_labels: Mapping | None = None,
    retention_days: int | None = None,
    now: float | None = None,
) -> str:
    """The site's signed ``site.json`` (``typ: canopy-site+jwt``) declaring ``site.person_scheme``
    and, when set, ``site.retention_days``. Raises ``ValueError`` for a value canopy would refuse."""
    from canopy_sdk.site import SiteDocumentError, sign_site_document
    from canopy_sdk.site import site_document as build

    facts = {**dict(site), PERSON_SCHEME_FACT: person_scheme}
    if retention_days is not None:
        facts[RETENTION_DAYS_FACT] = retention_days
    try:
        document = build(config.app_name, pages=pages, site=facts, title=title, scope_labels=scope_labels, now=now)
    except SiteDocumentError as exc:
        raise ValueError(str(exc)) from exc
    return sign_site_document(config.signing_key, document)
