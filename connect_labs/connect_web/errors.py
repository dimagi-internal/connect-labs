"""Structured errors for Connect Web, and the plain-language text a person is shown for each.

Every external failure becomes a `ServiceError` carrying the service it came from, the HTTP
status and the response's `error_code` (connect-id and Connect both put one in their error
bodies; an OAuth token endpoint's `error`, e.g. ``invalid_grant``, is used the same way). An
error never carries a response body, a token or a password: its message is built only from
the status and the code, so it is safe to log and to show.
"""

from __future__ import annotations


class ConnectWebError(Exception):
    """Anything Connect Web refuses or fails to do. `user_message` is what a person reads."""

    default_message = "Something went wrong. Please try again."

    def __init__(self, message: str = "", *, error_code: str = ""):
        self.error_code = error_code
        super().__init__(message or self.default_message)

    @property
    def user_message(self) -> str:
        return PLAIN_LANGUAGE.get(self.error_code) or str(self)


class NotConfigured(ConnectWebError):
    """A setting Connect Web needs (a client id) is empty."""


class WorkerUnavailable(ConnectWebError):
    """The pool worker is checked out by someone else, inactive, or blocked from signing in."""


class GuardRefused(ConnectWebError):
    """Our own rule refused the action before anything was sent (e.g. claim before learning)."""


class ServiceError(ConnectWebError):
    """An HTTP call to PersonalID, Connect or CommCare HQ failed."""

    service = "service"

    def __init__(self, message: str = "", *, status: int | None = None, error_code: str = "", **extra):
        self.status = status
        self.extra = {k: v for k, v in extra.items() if k in SAFE_EXTRA}
        if not message:
            parts = [f"{self.service} request failed"]
            if status is not None:
                parts.append(f"(HTTP {status})")
            if error_code:
                parts.append(f": {error_code}")
            message = " ".join(parts)
        super().__init__(message, error_code=error_code)

    @property
    def user_message(self) -> str:
        if self.error_code in PLAIN_LANGUAGE:
            return PLAIN_LANGUAGE[self.error_code]
        if self.status is None:
            return f"Couldn't reach {self.service}. Check your connection and try again."
        if self.status >= 500:
            return f"{self.service} had a problem (HTTP {self.status}). Please try again in a moment."
        if self.status in (401, 403):
            return f"{self.service} didn't accept this worker's sign-in (HTTP {self.status})."
        code = f", {self.error_code}" if self.error_code else ""
        return f"{self.service} refused the request (HTTP {self.status}{code})."


class PersonalIDError(ServiceError):
    service = "PersonalID"


class IncorrectBackupCode(PersonalIDError):
    """connect-id answered 200 with `attempts_left` and no username: the backup code is wrong.

    Never retried: connect-id locks the account after a few wrong codes (MAX_BACKUP_CODE_ATTEMPTS).
    """

    def __init__(self, attempts_left=None):
        super().__init__(
            "PersonalID rejected the backup code",
            status=200,
            error_code=INCORRECT_BACKUP_CODE,
            attempts_left=attempts_left,
        )
        self.attempts_left = attempts_left


class ConnectAPIError(ServiceError):
    service = "Connect"


class HQError(ServiceError):
    service = "CommCare HQ"


class FormplayerError(ServiceError):
    """A Formplayer call failed, or Formplayer answered with an exception.

    `detail` is Formplayer's own exception text (an app or engine message, e.g. an XPath error).
    It never contains a token: Formplayer does not echo request headers. It is cut short and
    shown to the person, because it is usually the only clue to what is wrong with the app.
    """

    service = "The form engine"
    max_detail = 300

    def __init__(self, message: str = "", *, status: int | None = None, error_code: str = "", detail: str = ""):
        self.detail = (detail or "").strip()[: self.max_detail]
        super().__init__(message, status=status, error_code=error_code)

    @property
    def user_message(self) -> str:
        if self.error_code == ENGINE_ERROR and self.detail:
            return f"The form engine reported a problem: {self.detail}"
        if self.error_code in PLAIN_LANGUAGE:
            return PLAIN_LANGUAGE[self.error_code]
        if self.status is None:
            return "Couldn't reach the form engine. Check your connection and try again."
        if self.status >= 500 or self.status < 300:
            return f"The form engine had a problem (HTTP {self.status}). Please try again in a moment."
        return f"The form engine refused the request (HTTP {self.status})."


class FormSessionExpired(FormplayerError):
    """Formplayer no longer has the form session (submitted already, or cleaned up)."""

    def __init__(self):
        super().__init__("The form session has expired", status=None, error_code=SESSION_EXPIRED)


class InvalidInput(ConnectWebError):
    """The browser sent something the form engine API does not accept."""

    def __init__(self, message: str = "", *, error_code: str = ""):
        super().__init__(message, error_code=error_code or INVALID_INPUT)

    @property
    def user_message(self) -> str:
        return PLAIN_LANGUAGE.get(self.error_code) or str(self)


#: Our own code for connect-id's "200 with attempts_left" answer.
INCORRECT_BACKUP_CODE = "INCORRECT_BACKUP_CODE"
#: Our own code for a claim refused by the learn-complete guard.
LEARNING_INCOMPLETE = "LEARNING_INCOMPLETE"
#: Our own code for a failed start_learn_app (Connect answers a bare 500 when the access has no invite).
START_LEARN_FAILED = "START_LEARN_FAILED"

#: The form engine (formplayer.py).
ENGINE_NOT_CONFIGURED = "ENGINE_NOT_CONFIGURED"
ENGINE_AUTH_FAILED = "ENGINE_AUTH_FAILED"
ENGINE_BUSY = "ENGINE_BUSY"
ENGINE_ERROR = "ENGINE_ERROR"
SESSION_EXPIRED = "SESSION_EXPIRED"
SESSION_UNKNOWN = "SESSION_UNKNOWN"
NO_RELEASED_BUILD = "NO_RELEASED_BUILD"
INVALID_INPUT = "INVALID_INPUT"

#: Extra fields an error may carry: none of them is secret.
SAFE_EXTRA = {"attempts_left", "retry_after_seconds"}

PLAIN_LANGUAGE = {
    # PersonalID (connect-id users/const.py ErrorCodes)
    INCORRECT_BACKUP_CODE: (
        "PersonalID didn't accept this worker's backup code. Sign-in is paused for this worker so the "
        "account isn't locked; an admin needs to check the backup code saved for them."
    ),
    "LOCKED_ACCOUNT": "This worker's PersonalID account is locked. It needs to be unlocked before anyone can sign in.",
    "PHONE_NOT_VALIDATED": "PersonalID hasn't validated this worker's phone number (pool numbers must start +7426).",
    "NO_RECOVERY_PIN_SET": "This worker has no backup code set in PersonalID, so they can't be signed in here.",
    "UNSUPPORTED_COUNTRY": "PersonalID doesn't support this worker's phone number's country.",
    "RATE_LIMITED": "PersonalID is rate-limiting sign-ins. Wait a few minutes and try again.",
    "TOKEN_EXPIRED": "The PersonalID sign-in session expired. Please try again.",
    "INVALID_TOKEN": "The PersonalID sign-in session wasn't recognised. Please try again.",
    "LOGIN_FROM_DIFFERENT_DEVICE": "PersonalID says this worker signed in on another device. Please try again.",
    "invalid_grant": "PersonalID didn't accept this worker's saved password; they'll be signed in again.",
    "invalid_client": "PersonalID didn't recognise Connect Web's client id (CONNECT_WEB_PERSONALID_CLIENT_ID).",
    # Connect (commcare_connect/utils/error_codes.py)
    "OPPORTUNITY_FULL": "This opportunity is full: there's no budget left for another worker to claim it.",
    "OPPORTUNITY_ENDED": "This opportunity has ended, so it can't be claimed.",
    "NO_OPPORTUNITY_ACCESS": "This worker hasn't been invited to this opportunity.",
    "FAILED_USER_CREATE": "Connect couldn't create this worker's CommCare user. Please try again.",
    "INVALID_FLAG": "Connect didn't accept the payment confirmation.",
    START_LEARN_FAILED: (
        "Connect couldn't start learning for this worker. The usual cause is that the worker has no "
        "invite to this opportunity — check the opportunity's invites in Connect."
    ),
    LEARNING_INCOMPLETE: (
        "This worker can't claim yet: every learn module must be complete and an assessment passed first."
    ),
    "NOT_CLAIMED": "Claim the opportunity before delivering.",
    # The form engine
    ENGINE_NOT_CONFIGURED: (
        "The form engine isn't configured on this labs server yet, so forms can't be filled in here."
    ),
    ENGINE_AUTH_FAILED: (
        "The form engine didn't accept labs' request (check CONNECT_WEB_FORMPLAYER_KEY matches the engine's key)."
    ),
    ENGINE_BUSY: "The form engine is busy with another request for this worker. Try again in a moment.",
    SESSION_EXPIRED: "This form session has expired. Start the form again.",
    SESSION_UNKNOWN: "This form session isn't one labs opened for this worker. Start the form again.",
    NO_RELEASED_BUILD: "CommCare HQ has no released build of this app, so it can't be run yet.",
}


def user_message(exc: Exception) -> str:
    """Plain-language text for any exception a view catches."""
    if isinstance(exc, ConnectWebError):
        return exc.user_message
    return ConnectWebError.default_message
