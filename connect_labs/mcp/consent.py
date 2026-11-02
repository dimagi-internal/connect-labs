"""What a person is told before an app gets access to labs as them.

There are two ways in: an MCP app signing in (``oauth.MCPAuthorizationView``) and the
``labs-token-setup`` page making a token (``admin_views``). Both show the same four
answers, built here so they cannot drift: who is asking, what it can do, for how long,
and what happens next.

The app's name never goes in the headline. An app chooses its own name when it
registers and nothing checks it, and a name is not a limit: an app that called itself
"read one saved run" still asked for everything its person can do.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

from . import token_scopes
from .models import MCPAccessToken

#: A sign-in's refresh token has no expiry of its own (``token_views._connected_mcp_clients``).
SIGN_IN_LASTS = "Until you disconnect it. It renews itself, so it does not expire on its own."
TEMPORARY_LASTS = "1 hour, then it stops working on its own."
PASSKEY_STEP = "You approve this with your passkey in canopy."


@dataclass(frozen=True)
class Consent:
    heading: str
    #: What the app calls itself, shown as its claim. Empty when it named nothing.
    claimed_name: str
    scope: str
    access_label: str
    access_text: str
    lasts: str
    undo: str
    next_step: str
    button: str
    #: Where the approval is sent, and whether that is the person's own computer.
    address: str
    address_is_local: bool

    @property
    def is_full(self) -> bool:
        return self.scope == token_scopes.FULL


def _consent(*, heading, claimed_name, scope, lasts, undo, needs_passkey, address, address_is_local) -> Consent:
    return Consent(
        heading=heading,
        claimed_name=claimed_name,
        scope=scope,
        access_label=dict(token_scopes.SCOPE_CHOICES)[scope],
        access_text=token_scopes.ACCESS_TEXT[scope],
        lasts=lasts,
        undo=undo,
        next_step=PASSKEY_STEP if needs_passkey else "",
        button="Continue to passkey" if needs_passkey else "Approve",
        address=address,
        address_is_local=address_is_local,
    )


def for_sign_in(
    *, claimed_name: str, restricted: bool, needs_passkey: bool, address: str, is_loopback: bool
) -> Consent:
    """An MCP app signing in. ``is_loopback``: its redirect comes back to this computer."""
    return _consent(
        heading=(
            "An app on this computer wants to use Connect Labs as you"
            if is_loopback
            else "An app wants to use Connect Labs as you"
        ),
        claimed_name=claimed_name,
        scope=token_scopes.NO_USERVISIT_DATA if restricted else token_scopes.FULL,
        lasts=SIGN_IN_LASTS,
        undo="Disconnect it",
        needs_passkey=needs_passkey,
        address=address,
        address_is_local=is_loopback,
    )


def for_token(*, scope: str, temporary: bool, needs_passkey: bool, callback: str) -> Consent:
    """The ``labs-token-setup`` page. Its callback is always on this computer
    (``admin_views._validate_callback``), and nothing tells labs which app opened it."""
    parsed = urlparse(callback)
    return _consent(
        heading="An app on this computer is asking for a Connect Labs token",
        claimed_name="",
        scope=scope,
        lasts=TEMPORARY_LASTS if temporary else f"{MCPAccessToken.DEFAULT_TTL_DAYS} days.",
        undo="Revoke it",
        needs_passkey=needs_passkey,
        address=f"{parsed.scheme}://{parsed.netloc}",
        address_is_local=True,
    )
