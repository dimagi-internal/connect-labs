"""The credentials a buyer usually checks, and who is missing them.

A supplier's ``qualifications`` is a list of ``{"name", "expires_on"?, "note"?}``
(older rows may be bare strings). The fields existed but nothing said what to
record, so every supplier's list was empty (Sophie's RUTF feedback, Suppliers:
Qualifications). This module names the usual ones per kind of product and works
out who lacks them, so the answer can sit next to "ask for a quote" and "award".

A usual qualification is held when a stored qualification's name mentions one of
its words and it has not run out. Nothing is guessed beyond that: a name that
matches nothing is simply an extra qualification the supplier has.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Usual:
    name: str
    why: str
    words: tuple[str, ...]


_UNICEF = Usual(
    "UNICEF supplier approval",
    "UNICEF approves RUTF makers; many funders and governments accept only approved ones.",
    ("unicef",),
)
_REGISTRATION = Usual(
    "National product registration",
    "The product must be registered with the health regulator of the country it is delivered to.",
    ("registration", "registered", "nafdac", "regulator"),
)
_CERTIFICATE = Usual(
    "Certificate of analysis for each batch",
    "A lab certificate that comes with every batch, so quality can be checked on arrival.",
    ("certificate", "analysis", "coa"),
)

USUAL_BY_CATEGORY: dict[str, tuple[Usual, ...]] = {
    "therapeutic_food": (_UNICEF, _REGISTRATION, _CERTIFICATE),
    "supplementary_food": (_UNICEF, _REGISTRATION, _CERTIFICATE),
}


def usual_for(categories) -> list[Usual]:
    """The usual qualifications for these product categories, each once, in order."""
    seen, out = set(), []
    for category in categories:
        for usual in USUAL_BY_CATEGORY.get(category, ()):
            if usual.name not in seen:
                seen.add(usual.name)
                out.append(usual)
    return out


def _entries(qualifications) -> list[dict]:
    out = []
    for q in qualifications or []:
        if isinstance(q, str) and q.strip():
            out.append({"name": q.strip()})
        elif isinstance(q, dict) and (q.get("name") or "").strip():
            out.append(q)
    return out


def _expired(entry, today) -> bool:
    raw = entry.get("expires_on")
    if not raw:
        return False
    try:
        return date.fromisoformat(str(raw)[:10]) < today
    except ValueError:
        return False


def missing(qualifications, usual, today=None) -> list[tuple[Usual, str]]:
    """The usual ones this supplier lacks, each as (usual, "missing" | "expired")."""
    today = today or date.today()
    entries = _entries(qualifications)
    out = []
    for u in usual:
        matches = [e for e in entries if any(w in e["name"].lower() for w in u.words)]
        if not matches:
            out.append((u, "missing"))
        elif all(_expired(e, today) for e in matches):
            out.append((u, "expired"))
    return out


def gaps(suppliers, categories, today=None) -> list[dict]:
    """Suppliers (dicts with id, name, qualifications) lacking any usual qualification.

    ``[{"supplier_id", "name", "lacks": ["UNICEF supplier approval", ...]}]``; a supplier
    that holds them all is not listed, and with no usual list for the products, nobody is.
    """
    usual = usual_for(categories)
    if not usual:
        return []
    out = []
    for supplier in suppliers:
        lacking = missing(supplier.get("qualifications"), usual, today)
        if lacking:
            out.append(
                {
                    "supplier_id": supplier.get("id"),
                    "name": supplier.get("name"),
                    "lacks": [u.name if state == "missing" else f"{u.name} (expired)" for u, state in lacking],
                }
            )
    return out


def categories_of(commodities, slugs=None) -> list[str]:
    """Product categories (commodity dicts), optionally only those with these slugs."""
    wanted = None if slugs is None else set(slugs)
    return list(
        dict.fromkeys(
            c["category"]
            for c in commodities
            if isinstance(c, dict) and c.get("category") and (wanted is None or c.get("slug") in wanted)
        )
    )


def gaps_among(list_suppliers, supplier_ids, commodities, slugs, today=None) -> list[dict]:
    """`gaps` for the suppliers with these ids, asking for the supplier list only if
    the products have a usual list at all (most do not)."""
    categories = categories_of(commodities, slugs)
    if not usual_for(categories):
        return []
    wanted = set(supplier_ids)
    return gaps([s for s in list_suppliers() if s["id"] in wanted], categories, today)
