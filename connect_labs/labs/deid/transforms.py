"""The settings a field can carry, and what each does to one value.

Every setting is a pure function of the value, the setting's params and a KEY: a
secret that belongs to the dictionary (``labsdata.models.DataDictionary.secret``),
never shown. The same input under the same key always gives the same output, so a
case pseudonymised in one export is the same pseudonym in the next, and visits of one
case still link up -- but nobody without the key can go back from a pseudonym, or
build the table that would.

``apply`` returns ``DROP`` for a value that must not appear at all.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import math
import re
from decimal import ROUND_HALF_UP, Decimal, DecimalException, InvalidOperation

from .classify import parse_iso_date

KEEP = "keep"
DROP_SETTING = "drop"
ROUND = "round"  # to a step: params {"step": 25}
PSEUDONYMISE = "pseudonymise"  # keyed token: params {"namespace": "case"}
MONTH = "month"  # a date cut to YYYY-MM
SHIFT = "shift"  # a date moved by a per-case number of days: params {"max_days": 30}
COARSEN = "coarsen"  # a GPS point rounded: params {"decimals": 2}
SCRUB = "scrub"  # free text with names, numbers and emails removed (best effort)

SETTINGS = (KEEP, DROP_SETTING, ROUND, PSEUDONYMISE, MONTH, SHIFT, COARSEN, SCRUB)
SETTING_LABELS = {
    KEEP: "Keep as is",
    DROP_SETTING: "Drop",
    ROUND: "Round",
    PSEUDONYMISE: "Replace with a code",
    MONTH: "Month only",
    SHIFT: "Shift dates per case",
    COARSEN: "Coarsen location",
    SCRUB: "Scrub free text (best effort)",
}

#: Strictest first: when a value inherits from several sources, the earliest wins.
STRICTNESS = (DROP_SETTING, SCRUB, PSEUDONYMISE, COARSEN, MONTH, SHIFT, ROUND, KEEP)


class _Drop:
    def __repr__(self) -> str:
        return "DROP"


#: What ``apply`` returns for a value that must not appear.
DROP = _Drop()

# A pseudonym is letters only, so it can never look like a phone number, an id or a
# date to the leak scan, and never collides with a real numeric value.
_ALPHABET = "abcdefghjkmnpqrstuvwxyz"


def pseudonym(value, key: bytes, namespace: str = "", length: int = 12) -> str:
    """A stable, keyed, one-way code for ``value`` (HMAC-SHA256, letters only).

    ``namespace`` keeps different kinds of id apart (a worker and a case that happen
    to share a string get different codes), and the same namespace on two fields keeps
    them joinable -- every case id field uses ``case``."""
    digest = hmac.new(key, f"{namespace}\x00{value}".encode(), hashlib.sha256).digest()
    n = int.from_bytes(digest, "big")
    out = []
    for _ in range(length):
        n, i = divmod(n, len(_ALPHABET))
        out.append(_ALPHABET[i])
    return "".join(out)


def offset_days(key: bytes, case, max_days: int) -> int:
    """A keyed shift in ``[-max_days, max_days - 1]``, the same for every date of one
    case: intervals within a case survive, its calendar position does not."""
    if max_days < 1:
        return 0
    digest = hmac.new(key, f"shift\x00{case}".encode(), hashlib.sha256).digest()
    return int.from_bytes(digest[:8], "big") % (2 * max_days) - max_days


def round_to(value, step) -> str | _Drop:
    try:
        number = Decimal(str(value).strip())
        size = Decimal(str(step))
    except (InvalidOperation, ValueError):
        return DROP
    if not (number.is_finite() and size.is_finite()) or size <= 0:
        return DROP
    try:
        rounded = (number / size).quantize(Decimal(1), rounding=ROUND_HALF_UP) * size
    except (DecimalException, ArithmeticError):
        return DROP  # too large to round: dropped, and counted as unreadable
    return format(rounded.normalize(), "f")


def month(value) -> str | _Drop:
    date = parse_iso_date(str(value))
    return date.strftime("%Y-%m") if date else DROP


def shift(value, days: int) -> str | _Drop:
    date = parse_iso_date(str(value))
    return (date + dt.timedelta(days=days)).isoformat() if date else DROP


def coarsen(value, decimals: int = 2) -> str | _Drop:
    """``lat lon [alt acc]`` to ``lat lon`` at ``decimals`` places (2 is about 1 km);
    altitude and accuracy are dropped."""
    parts = str(value).split()
    if len(parts) < 2:
        return DROP
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except ValueError:
        return DROP
    return f"{lat:.{decimals}f} {lon:.{decimals}f}"


_EMAIL = re.compile(r"\S+@\S+")
_LONG_NUMBER = re.compile(r"\+?\d[\d\s-]{4,}\d")


def scrub(text, terms: frozenset[str] = frozenset()) -> str:
    """Free text with emails, runs of five or more digits and every known identifying
    word (``terms``: lower-case) replaced. Best effort: a note can still name someone
    in a way no list knows."""
    out = _EMAIL.sub("[email]", str(text))
    out = _LONG_NUMBER.sub("[number]", out)
    if terms:
        # Letters only, so "Amina's" and "Amina-Wanjiru" are matched part by part.
        out = re.sub(r"[^\W\d_]+", lambda m: "[name]" if m.group(0).lower() in terms else m.group(0), out)
    return out


def apply(setting: str, params: dict | None, value, *, key: bytes, case=None, terms: frozenset[str] = frozenset()):
    """One value through one setting. ``case`` is the value's case (for ``shift``);
    ``terms`` the identifying words known so far (for ``scrub``)."""
    params = params or {}
    if value is None or value == "":
        return ""
    if setting == KEEP:
        return value
    if setting == ROUND:
        return round_to(value, params.get("step", 1))
    if setting == PSEUDONYMISE:
        return pseudonym(value, key, params.get("namespace", ""))
    if setting == MONTH:
        return month(value)
    if setting == SHIFT:
        if case in (None, ""):
            return DROP
        return shift(value, offset_days(key, case, int(params.get("max_days", 30))))
    if setting == COARSEN:
        return coarsen(value, int(params.get("decimals", 2)))
    if setting == SCRUB:
        return scrub(value, terms)
    return DROP


def stricter(a: str, b: str) -> str:
    """The stricter of two settings (unknown settings count as drop)."""
    rank = {s: i for i, s in enumerate(STRICTNESS)}
    return a if rank.get(a, 0) <= rank.get(b, 0) else b


def _int(value, name: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be a whole number") from e


def validate_params(setting: str, params: dict | None) -> dict:
    """The params a setting takes, checked; raises ValueError naming what is wrong."""
    params = dict(params or {})
    if setting not in SETTINGS:
        raise ValueError(f"unknown setting {setting!r}")
    if setting == ROUND:
        try:
            step = float(params.get("step"))
        except (TypeError, ValueError) as e:
            raise ValueError("round needs a step, e.g. 25") from e
        if not math.isfinite(step) or step <= 0:
            raise ValueError("the rounding step must be a positive number")
        return {"step": params["step"]}
    if setting == PSEUDONYMISE:
        ns = str(params.get("namespace", "") or "")
        if not re.fullmatch(r"[a-z0-9_]{0,40}", ns):
            raise ValueError("a namespace is lower-case letters, digits and _")
        return {"namespace": ns} if ns else {}
    if setting == SHIFT:
        days = _int(params.get("max_days", 30), "max_days")
        if not 1 <= days <= 365:
            raise ValueError("max_days is between 1 and 365")
        return {"max_days": days}
    if setting == COARSEN:
        decimals = _int(params.get("decimals", 2), "decimals")
        # Three decimals is ~100 m: a home, not an area. The leak scan fails such a point.
        if not 0 <= decimals <= 2:
            raise ValueError("decimals is between 0 and 2 (2 is about 1 km)")
        return {"decimals": decimals}
    return {}
