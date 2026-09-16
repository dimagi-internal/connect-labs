"""Reading the Connect LLO Directory.

The directory is the master organisation list. ``pulse_partner_import`` read two
of its columns to put names on Connect slugs; this reads all of it, because the
rest — contacts, sectors, scale, application history — is the part nobody could
query, and is most of why the sheet exists.

Fetching and parsing are separated deliberately. Parsing is where every real bug
lives (a country name containing a comma, a count written "50+", a verdict with
no stated reason), and separating it means those are tested against rows in a
list rather than against a network and a service account.

Nothing here writes. Import is a read-only path over the master registry, so a
parsing or matching bug cannot reshape the sheet people maintain by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import quote

DIRECTORY_ID = "19sqU7xpSb_0VX6H_QZK2dcRz0RvXiQ1En9kvSZkEiY8"

ORGANIZATIONS_TAB = "Organizations"
CONTACTS_TAB = "Contacts"
MAPPING_TAB = "Connect Org Mapping"
DATES_TAB = "AI Enrichment - Connect Dates"

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0"}


@dataclass
class DirectoryOrg:
    name: str
    short_name: str = ""
    has_used_connect: bool | None = None
    year_established: int | None = None
    team_size: int | None = None
    flws_managed: int | None = None
    countries: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    sectors: list[str] = field(default_factory=list)
    website: str = ""
    office_address: str = ""
    notes: str = ""
    msa_link: str = ""
    work_order_link: str = ""
    source_row: int | None = None
    # Kept verbatim for the location resolver, which wants the raw cells.
    raw_countries: str = ""
    raw_regions: str = ""


@dataclass
class DirectoryContact:
    full_name: str
    org_name: str
    role_title: str = ""
    is_main_poc: bool = False
    email: str = ""
    phone: str = ""
    notes: str = ""
    source_row: int | None = None


def cell(row: list[str], index: int) -> str:
    return row[index].strip() if len(row) > index else ""


def read_tab(spreadsheet_id: str, tab: str) -> list[list[str]]:
    """One tab as rows, read as the labs service account.

    Talks to the Sheets REST API over httpx with a service-account bearer, the
    same shape ``labs.synthetic.gdrive`` uses for Drive — the Google API client
    library is deliberately not a dependency. Addressing the tab by name
    matters: a Drive export only ever yields the first one.
    """
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(tab)}"
    got = httpx.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=60)
    got.raise_for_status()
    return got.json().get("values", [])


def _int_or_none(raw: str) -> int | None:
    """People write "50+", "approx 200" and "circa 2010" in number columns.

    Coercing to None here is only safe because ``quality.audit`` reports every
    one of them with its row number. Silently dropping them would hide exactly
    the cells that need a person.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _bool_or_none(raw: str) -> bool | None:
    value = (raw or "").strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    return None


def _split_countries(raw: str) -> list[str]:
    """Split a country cell without breaking names that contain a comma.

    Several ISO names contain one — "Congo, the Democratic Republic of the" —
    and splitting it leaves "Congo", which is the OTHER Congo. The sheet quotes
    such names, so a quoted cell is taken whole.
    """
    value = (raw or "").strip()
    if not value:
        return []
    if value.startswith('"') and value.endswith('"'):
        return [value.strip('"').strip()]
    return [part.strip() for part in value.split(",") if part.strip()]


def _split_list(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", raw or "") if part.strip()]


def parse_organizations(rows: list[list[str]]) -> list[DirectoryOrg]:
    seen: set[str] = set()
    out: list[DirectoryOrg] = []
    for index, row in enumerate(rows[1:], start=2):
        name = cell(row, 0)
        if not name or name in seen:
            continue
        seen.add(name)
        out.append(
            DirectoryOrg(
                name=name,
                short_name=cell(row, 1),
                has_used_connect=_bool_or_none(cell(row, 2)),
                year_established=_int_or_none(cell(row, 3)),
                team_size=_int_or_none(cell(row, 4)),
                flws_managed=_int_or_none(cell(row, 5)),
                countries=_split_countries(cell(row, 6)),
                regions=_split_list(cell(row, 7)),
                sectors=_split_list(cell(row, 8)),
                website=cell(row, 9),
                office_address=cell(row, 10),
                notes=cell(row, 13),
                msa_link=cell(row, 14),
                work_order_link=cell(row, 15),
                source_row=index,
                raw_countries=cell(row, 6),
                raw_regions=cell(row, 7),
            )
        )
    return out


def parse_contacts(rows: list[list[str]]) -> list[DirectoryContact]:
    out: list[DirectoryContact] = []
    for index, row in enumerate(rows[1:], start=2):
        email = cell(row, 4).lower()
        org_name = cell(row, 1)
        # The email is the key: a contact without one cannot be deduplicated and
        # cannot be written to, so it is not yet a contact. ``quality.audit``
        # reports each one dropped here.
        if not email or not org_name:
            continue
        out.append(
            DirectoryContact(
                full_name=cell(row, 0),
                org_name=org_name,
                role_title=cell(row, 2),
                is_main_poc=_bool_or_none(cell(row, 3)) is True,
                email=email,
                phone=cell(row, 5),
                notes=cell(row, 6),
                source_row=index,
            )
        )
    return out


def parse_dates(rows: list[list[str]]) -> dict[str, tuple[str, str]]:
    """name -> (ISO join date, basis). Non-ISO cells are free text and ignored."""
    out: dict[str, tuple[str, str]] = {}
    for row in rows[1:]:
        name, joined, basis = cell(row, 0), cell(row, 1), cell(row, 2)
        if name and _ISO_DATE.fullmatch(joined):
            out[name] = (joined, basis[:200])
    return out


def parse_mapping(rows: list[list[str]], known_names: set[str]) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """slug -> (organisation name, reason), plus the rows that were refused.

    Refusals are returned rather than logged so the caller can print them: a
    verdict silently dropped is worse than one never recorded.
    """
    mapped: dict[str, tuple[str, str]] = {}
    skipped: list[str] = []
    for row in rows[1:]:
        slug, target, why = cell(row, 0), cell(row, 8), cell(row, 5)
        if not slug or not target:
            continue
        if target not in known_names:
            skipped.append(f"{slug} → {target!r} (not on the {ORGANIZATIONS_TAB} tab)")
            continue
        if not why:
            skipped.append(f"{slug} → {target!r} (no reason given)")
            continue
        mapped[slug] = (target, why)
    return mapped, skipped
