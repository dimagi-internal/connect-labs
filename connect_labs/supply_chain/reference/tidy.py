"""Tidy the messy parts of imported suppliers, only where it is safe.

The tracker import left two kinds of mess (Sophie's RUTF feedback, Suppliers:
imported data): a supplier's "City" that holds a country, and notes written
inside an email cell. This module works out what to change, as a list of
visible changes, and never changes anything itself. It only moves text to the
place it belongs:

- A city that is exactly a country name (or "City, Country") moves to the country,
  when the supplier has none or the same one. A different country is left alone and reported.
- An email cell with exactly one address keeps the address; the words around it become the
  contact's name (when it has none and the cell was "Name <address>") or its note. A cell
  with no address, or several, is left alone and reported: splitting it would invent people.

Nothing is deleted outright: removed text always lands in a field. Running it twice
changes nothing the second time. The management command `supply_tidy_suppliers` shows
the changes first and applies them only with --apply.
"""

import re
from dataclasses import dataclass, field

from connect_labs.microplans.core import iso
from connect_labs.supply_chain.procurement.services.contacts import _EMAIL

_ALIASES = {"usa": "US", "uk": "GB", "ivory coast": "CI", "drc": "CD", "dr congo": "CD"}


def _country_codes() -> dict[str, str]:
    codes = {c["name"].lower(): c["alpha2"] for c in iso.all_countries()}
    codes.update(_ALIASES)
    return codes


@dataclass
class Tidied:
    data: dict = field(default_factory=dict)  # the supplier_update patch; empty when nothing to do
    changes: list[str] = field(default_factory=list)  # plain-language lines
    left_alone: list[str] = field(default_factory=list)  # messy, but not safe to fix by rule

    @property
    def any(self) -> bool:
        return bool(self.data)


def tidy_city(city, country) -> tuple[dict, list[str], list[str]]:
    text = (city or "").strip()
    if not text:
        return {}, [], []
    codes = _country_codes()
    code, place = codes.get(text.lower()), ""
    if code is None and "," in text:
        head, _, tail = text.rpartition(",")
        if tail.strip().lower() in codes and head.strip():
            code, place = codes[tail.strip().lower()], head.strip()
    if code is None:
        return {}, [], []
    have = (country or "").strip().upper()
    if have and have != code:
        return {}, [], [f"city “{text}” is a country, but the supplier's country is {have}"]
    patch = {"city": place, "country": code}
    where = f"“{place}” stays as the city and " if place else ""
    return patch, [f"city “{text}”: {where}the country is set to {code}"], []


def _words_around(cell: str, address: str) -> str:
    rest = cell.replace(address, " ")
    # the address may have been written in another case than the one found
    rest = re.sub(re.escape(address), " ", rest, flags=re.IGNORECASE)
    rest = re.sub(r"[<>()\[\]]", " ", rest)
    rest = re.sub(r"\s+", " ", rest).strip(" -–—:;,.|/")
    return rest if re.search(r"[A-Za-z]", rest) else ""


def tidy_contact(contact: dict) -> tuple[dict, list[str], list[str]]:
    cell = str(contact.get("email") or "").strip()
    if not cell:
        return contact, [], []
    found = _EMAIL.findall(cell)
    label = contact.get("name") or cell[:40]
    if len(found) == 0:
        return contact, [], [f"{label}: the email cell holds no address (“{cell[:60]}”)"]
    if len(found) > 1:
        return contact, [], [f"{label}: the email cell holds {len(found)} addresses, so it is not split by rule"]
    address = found[0].rstrip(".").lower()
    extra = _words_around(cell, found[0])
    if cell == address or not extra:
        if cell == address:
            return contact, [], []
        return {**contact, "email": address}, [f"{label}: email cleaned to {address}"], []
    fixed = dict(contact)
    fixed["email"] = address
    named = re.fullmatch(r"([^<>@]+?)\s*<[^<>]*>\s*", cell)
    if not contact.get("name") and named and named.group(1).strip() == extra:
        fixed["name"] = extra
        return fixed, [f"{address}: “{extra}” becomes the contact's name"], []
    note = contact.get("note")
    fixed["note"] = f"{note} · {extra}" if note else extra
    return fixed, [f"{address}: “{extra}” moved from the email cell to the note"], []


def tidy_supplier(city, country, contacts) -> Tidied:
    out = Tidied()
    patch, changes, left = tidy_city(city, country)
    out.data.update(patch)
    out.changes += changes
    out.left_alone += left
    new_contacts, touched = [], False
    for contact in contacts or []:
        if not isinstance(contact, dict):
            new_contacts.append(contact)
            continue
        fixed, changes, left = tidy_contact(contact)
        out.changes += changes
        out.left_alone += left
        touched = touched or fixed != contact
        new_contacts.append(fixed)
    if touched:
        out.data["contacts"] = new_contacts
    return out
