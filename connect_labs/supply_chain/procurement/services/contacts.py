"""Who an email to a supplier goes to, read from the supplier's contacts.

A supplier's contacts are a list of dicts (name, email, phone, role). Two
optional keys drive the emails:

- ``use``: ``"to"`` (the one main contact), ``"cc"`` (copied on every email) or
  ``"history"`` (an old or unanswered address, kept on file but off new emails).
- With no ``use`` anywhere on the supplier, the first contact with an address is
  the main one and nothing is copied -- how every supplier read before the key
  existed.

Contacts imported from a spreadsheet are messy: one cell holding five names
joined with slashes or semicolons, an address written ``Jane <jane@x.com>`` or
with a note after it. The helpers here read what is safely there and never
invent a person.
"""

import re
from dataclasses import dataclass, field

USES = ("to", "cc", "history")

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-']+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+")
_NAME_SEPARATORS = re.compile(r"\s*(?:;|/|\||&|\n|,|\band\b)\s*", re.IGNORECASE)


def clean_email(raw) -> str:
    """The address inside a messy cell: "Jane Doe <jane@x.com>", "jane@x.com (Jane)",
    "jane@x.com - bounced". "" when no address is there (the text is never guessed at)."""
    match = _EMAIL.search(str(raw or ""))
    return match.group(0).rstrip(".").lower() if match else ""


def split_names(raw) -> list[str]:
    """The people in a name cell: "A / B; C" -> ["A", "B", "C"]."""
    return [n.strip(" -.") for n in _NAME_SEPARATORS.split(str(raw or "")) if n.strip(" -.")]


@dataclass(frozen=True)
class Recipients:
    to: str | None = None
    cc: list[str] = field(default_factory=list)
    # The names the greeting uses: the main contact's own name where it is one person.
    names: list[str] = field(default_factory=list)


def _dicts(supplier):
    return [c for c in (supplier.contacts or []) if isinstance(c, dict)]


def recipients(supplier) -> Recipients:
    contacts = [c for c in _dicts(supplier) if c.get("use") != "history"]
    marked = [c for c in contacts if c.get("use") in ("to", "cc")]
    if marked:
        mains = [c for c in marked if c.get("use") == "to" and clean_email(c.get("email"))]
        main = mains[0] if mains else None
        cc_pool = [c for c in marked if c.get("use") == "cc"]
    else:
        withs = [c for c in contacts if clean_email(c.get("email"))]
        # Prefer the contact that is one named person with an address of their own.
        main = next((c for c in withs if len(split_names(c.get("name"))) == 1), withs[0] if withs else None)
        cc_pool = []
    to = clean_email(main.get("email")) if main else None
    cc = []
    for contact in cc_pool:
        address = clean_email(contact.get("email"))
        if address and address != to and address not in cc:
            cc.append(address)
    names = split_names(main.get("name")) if main else []
    if not names:
        # No main contact with a name: a lone named person elsewhere, else whoever is named.
        listed = [split_names(c.get("name")) for c in contacts if split_names(c.get("name"))]
        names = next((n for n in listed if len(n) == 1), listed[0] if listed else [])
    return Recipients(to=to or None, cc=cc, names=names)


def greeting(supplier) -> str:
    """ "Dear Jane," for one person; "Dear Jane and Joy," for two or three; the
    company's team when a cell holds more, because five names in a row is not a greeting."""
    names = recipients(supplier).names
    if len(names) == 1:
        return f"Dear {names[0]},"
    if 2 <= len(names) <= 3:
        return f"Dear {', '.join(names[:-1])} and {names[-1]},"
    if not names:
        return f"Dear {supplier.name},"
    return f"Dear {supplier.name} team,"
