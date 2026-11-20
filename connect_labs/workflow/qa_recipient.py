"""Where a person's own test coaching conversations go: their PersonalID username.

"Send to me" on a coaching action (``start_ocs_outreach`` with ``deliver_to``) needs the
sender's own PersonalID username. Typing it on every demo is a distraction, and few
people remember it, so Labs keeps it in the person's own settings layer (the ``profile``
namespace, user layer: nobody else reads or writes it), remembered from the last test
they sent to themselves and editable on their settings page.

``deliver_to: "me"`` resolves to it, so a page or an agent can say "send it to me"
without knowing the username.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

NAMESPACE = "profile"
KEY = "personalid_username"
ME = "me"
_VALID = re.compile(r"^[A-Za-z0-9._@+-]{3,150}$")


def clean(value) -> str:
    """A PersonalID username as stored, or "" when it is not one."""
    text = str(value or "").strip()
    return text if _VALID.match(text) and text.lower() != ME else ""


def saved(user) -> str:
    """The PersonalID username ``user`` keeps in Labs, or ""."""
    username = (user.get_username() or "") if user is not None and getattr(user, "pk", None) else ""
    if not username:
        return ""
    from connect_labs.scope_config.models import ScopeConfig

    row = ScopeConfig.objects.filter(scope_type="user", scope_key=username, namespace=NAMESPACE).first()
    return clean(((row.data if row else None) or {}).get(KEY))


def save(user, value, *, request=None, via: str = "workflow:qa_recipient") -> bool:
    """Keep ``value`` as ``user``'s PersonalID username. True when it changed. Never raises:
    remembering is a convenience, and a send must not fail on it."""
    value = clean(value)
    if not value or user is None or not getattr(user, "pk", None) or value == saved(user):
        return False
    from connect_labs.labs.access.scopes import Caller
    from connect_labs.scope_config import service
    from connect_labs.scope_config.scopes import Scope

    try:
        service.update(
            NAMESPACE, Scope.of("user", user.get_username()), {KEY: value}, Caller(user=user, request=request), via=via
        )
    except Exception:  # noqa: BLE001 -- a convenience; log and move on
        logger.warning("could not remember a PersonalID username for %s", user.get_username(), exc_info=True)
        return False
    return True


def resolve(user, value):
    """``deliver_to`` as given, with ``"me"`` replaced by the saved username. Raises
    ValueError (for the person) when they ask for "me" and Labs has none."""
    if not isinstance(value, str) or value.strip().lower() != ME:
        return value
    mine = saved(user)
    if not mine:
        raise ValueError(
            "Labs has no PersonalID username for you yet: send one test with your PersonalID username "
            "(Labs remembers it), or set it under Your profile in Labs settings."
        )
    return mine
