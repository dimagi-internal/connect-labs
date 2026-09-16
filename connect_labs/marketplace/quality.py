"""What the import could not read cleanly, and where.

The directory is maintained by hand, over years, by several people, and nobody
is fully confident in all of it. An importer that silently coerces is actively
harmful under that condition: ``"50+"`` becoming ``None`` and a contact row with
no email vanishing are both facts about the sheet, and both are invisible if the
only output is a success count.

Every finding carries a row number. "Some numbers did not parse" is not
actionable; "row 14, Org Team Size, '50+'" is.

This module reports. It never edits a person's cell, and never changes what the
import stores — a finding is an observation, not a correction.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from connect_labs.marketplace.directory import CONTACTS_TAB, ORGANIZATIONS_TAB, cell

# Columns that should hold a number, by index, with the header a person reads.
NUMBER_COLUMNS = {
    3: "Year of Establishment",
    4: "Org Team Size",
    5: "No. of FLWs Managed",
}

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True)
class Finding:
    kind: str
    row: int | None
    tab: str
    detail: str

    def __str__(self) -> str:
        where = f"row {self.row}" if self.row else "—"
        return f"[{self.tab}] {where}: {self.detail}"


def _fold(name: str) -> str:
    """Case, accent and whitespace folded away, for near-duplicate detection."""
    stripped = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped).strip().lower()


def audit(org_rows, contact_rows, orgs, contacts, skipped) -> list[Finding]:
    """Everything about these tabs that a person should look at."""
    findings: list[Finding] = []

    # Number columns that did not parse.
    for index, row in enumerate(org_rows[1:], start=2):
        for column, header in NUMBER_COLUMNS.items():
            raw = cell(row, column)
            if not raw:
                continue
            try:
                int(raw)
            except ValueError:
                findings.append(
                    Finding(
                        "unparsed_number",
                        index,
                        ORGANIZATIONS_TAB,
                        f"{header} reads {raw!r}, which is not a number — stored as blank",
                    )
                )

    # Organisation names that differ only incidentally.
    first_seen: dict[str, int] = {}
    for index, row in enumerate(org_rows[1:], start=2):
        name = cell(row, 0)
        if not name:
            continue
        key = _fold(name)
        if key in first_seen:
            findings.append(
                Finding(
                    "near_duplicate_org",
                    index,
                    ORGANIZATIONS_TAB,
                    f"{name!r} differs from row {first_seen[key]} only by case, spacing or accents",
                )
            )
        else:
            first_seen[key] = index

    # Organisations missing the things the directory exists to carry.
    with_contacts = {c.org_name for c in contacts}
    for org in orgs:
        if not org.countries:
            findings.append(
                Finding(
                    "missing_country",
                    org.source_row,
                    ORGANIZATIONS_TAB,
                    f"{org.name!r} has no country of operation",
                )
            )
        if org.name not in with_contacts:
            findings.append(
                Finding(
                    "org_without_contact",
                    org.source_row,
                    ORGANIZATIONS_TAB,
                    f"{org.name!r} has no contact on the {CONTACTS_TAB} tab — it cannot be written to",
                )
            )

    # Contact rows that cannot become contacts.
    for index, row in enumerate(contact_rows[1:], start=2):
        email, org_name = cell(row, 4), cell(row, 1)
        if not org_name and not email:
            continue
        if not email:
            findings.append(
                Finding(
                    "contact_without_email",
                    index,
                    CONTACTS_TAB,
                    f"{cell(row, 0) or 'a contact'} at {org_name!r} has no email address — not imported",
                )
            )
        elif not _EMAIL.match(email):
            findings.append(
                Finding(
                    "malformed_email",
                    index,
                    CONTACTS_TAB,
                    f"{email!r} is not a valid email address — not imported",
                )
            )

    # Whatever the parsers already refused, restated as findings.
    findings.extend(Finding("refused", None, ORGANIZATIONS_TAB, line) for line in skipped)

    return findings
