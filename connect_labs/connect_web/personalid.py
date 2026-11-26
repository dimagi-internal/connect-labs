"""PersonalID (connect-id) client: sign a pool worker in the way the Android app's recovery flow does.

Base URL ``settings.PERSONALID_SERVER_URL`` — the same setting Labs Messaging uses. Checked
against connect-id (``users/views.py``, ``users/auth.py``):

1. ``POST /users/start_configuration`` (JSON, ``Accept: application/json;version=3.0``, no auth)
   ``{phone_number, application_id, cc_device_id, device, gps_location?}`` →
   ``{token, required_lock, demo_user, toggles, sms_method, otp_fallback}``. Play Integrity headers
   are not sent: connect-id skips the integrity check for numbers ever invited to an opportunity,
   which every pool worker is, and pool numbers (``+7426…``) are demo numbers, so the phone is
   validated without an OTP.
2. ``POST /users/recover/complete_recovery`` with ``Authorization: Bearer <token>``
   (``SessionTokenAuthentication``, keyword ``Bearer``) and ``{method: "backup_code", backup_code}``.
   A wrong code is **HTTP 200** with ``{attempts_left}`` and no ``username``: that is a failure
   (`IncorrectBackupCode`), never retried — connect-id locks the account after a few. Success is
   ``_complete_recovery_for_user``'s ``{username, password, db_key, invited_user, email?,
   previous_device?, last_accessed?}``; it rotates the worker's password every time.
3. ``POST /o/token/`` (form) ``grant_type=password, client_id, scope=openid, username, password`` →
   ``{access_token, expires_in, refresh_token?}`` (a refresh token is not kept: the password grant
   is as cheap, and the password is what survives). ``client_id`` is the PersonalID public client the
   Android app uses, from ``CONNECT_WEB_PERSONALID_CLIENT_ID`` (no default in code).

Nothing here logs or raises a token, password or backup code.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.conf import settings
from django.views.decorators.debug import sensitive_variables

from . import http
from .errors import IncorrectBackupCode, NotConfigured, PersonalIDError

ACCEPT_V3 = "application/json;version=3.0"
APPLICATION_ID = "org.commcare.dalvik"
DEVICE_NAME = "Connect Web (labs)"


def _base() -> str:
    return settings.PERSONALID_SERVER_URL.rstrip("/")


@dataclass(frozen=True)
class ConfigurationSession:
    token: str = field(repr=False)
    demo_user: bool = False
    required_lock: str | None = None
    sms_method: str | None = None


@dataclass(frozen=True)
class RecoveredCredentials:
    username: str
    password: str = field(repr=False)
    db_key: str = field(default="", repr=False)
    invited_user: bool | None = None


@dataclass(frozen=True)
class AccessToken:
    access_token: str = field(repr=False)
    expires_in: int


def start_configuration(phone_number: str, device_id: str, gps_location: str | None = None) -> ConfigurationSession:
    payload = {
        "phone_number": phone_number,
        "application_id": APPLICATION_ID,
        "cc_device_id": device_id,
        "device": DEVICE_NAME,
    }
    if gps_location:
        payload["gps_location"] = gps_location
    response = http.request(
        "POST",
        f"{_base()}/users/start_configuration",
        PersonalIDError,
        log_name="personalid.start_configuration",
        json=payload,
        headers={"Accept": ACCEPT_V3},
    )
    body = http.json_or_none(response) or {}
    token = body.get("token") if isinstance(body, dict) else None
    if not token:
        raise PersonalIDError("PersonalID started no configuration session", status=response.status_code)
    return ConfigurationSession(
        token=token,
        demo_user=bool(body.get("demo_user")),
        required_lock=body.get("required_lock"),
        sms_method=body.get("sms_method"),
    )


@sensitive_variables("session_token", "backup_code", "body", "response")
def complete_recovery(session_token: str, backup_code: str) -> RecoveredCredentials:
    response = http.request(
        "POST",
        f"{_base()}/users/recover/complete_recovery",
        PersonalIDError,
        log_name="personalid.complete_recovery",
        json={"method": "backup_code", "backup_code": backup_code},
        headers={"Accept": ACCEPT_V3, "Authorization": f"Bearer {session_token}"},
    )
    body = http.json_or_none(response)
    if not isinstance(body, dict) or not body.get("username") or not body.get("password"):
        # connect-id answers a wrong backup code with 200 {"attempts_left": n}.
        raise IncorrectBackupCode(attempts_left=body.get("attempts_left") if isinstance(body, dict) else None)
    return RecoveredCredentials(
        username=body["username"],
        password=body["password"],
        db_key=body.get("db_key") or "",
        invited_user=body.get("invited_user"),
    )


def client_id() -> str:
    value = (getattr(settings, "CONNECT_WEB_PERSONALID_CLIENT_ID", "") or "").strip()
    if not value:
        raise NotConfigured("CONNECT_WEB_PERSONALID_CLIENT_ID is not set, so Connect Web can't sign workers in.")
    return value


@sensitive_variables("password", "body", "response")
def password_grant(username: str, password: str) -> AccessToken:
    response = http.request(
        "POST",
        f"{_base()}/o/token/",
        PersonalIDError,
        log_name="personalid.token",
        data={
            "grant_type": "password",
            "client_id": client_id(),
            "scope": "openid",
            "username": username,
            "password": password,
        },
    )
    body = http.json_or_none(response) or {}
    if not isinstance(body, dict) or not body.get("access_token"):
        raise PersonalIDError("PersonalID issued no access token", status=response.status_code)
    return AccessToken(
        access_token=body["access_token"],
        expires_in=http.seconds(body.get("expires_in")),
    )
