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

import json
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
    # safe="" matters: the EOI/RFP tab has a slash in its name, and an
    # unescaped one is read as a URL path separator, giving a 400.
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote(tab, safe='')}"
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


# ======================================================================
# The EOI/RFP tab — one row per FORM, every component an explicit link.
# ======================================================================

ROUNDS_TAB = "EOI/RFP"

_SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})")


@dataclass
class DirectoryRound:
    slug: str
    title: str
    solicitation_type: str = "eoi"
    status: str = "closed"
    published_on: str = ""
    application_deadline: str = ""
    decision_on: str = ""
    expected_start_date: str = ""
    expected_end_date: str = ""
    target_countries: str = ""
    announcement_url: str = ""
    form_url: str = ""
    response_spreadsheet_id: str = ""
    response_tab: str = ""
    column_map: dict = field(default_factory=dict)
    notes: str = ""
    source_row: int | None = None


def parse_rounds(rows: list[list[str]]) -> tuple[list[DirectoryRound], list[str]]:
    """The EOI/RFP tab as rounds, plus the rows that could not be used.

    One row is one *form*. A single announcement can run several — the Malaria
    RFI ran four and the 2026 Readers round ran an English and a French one —
    and modelling the announcement as the round made those invisible, because a
    round with no responses of its own is not a round.
    """
    out: list[DirectoryRound] = []
    skipped: list[str] = []
    seen: set[str] = set()

    for index, row in enumerate(rows[1:], start=2):
        slug = cell(row, 0)
        title = cell(row, 1)
        if not slug and not title:
            continue
        if not slug:
            skipped.append(f"row {index} ({title!r}) has no slug — it cannot be referred to, so it is not imported")
            continue
        if slug in seen:
            skipped.append(f"row {index}: slug {slug!r} is already used above — slugs must be unique")
            continue
        seen.add(slug)

        raw_map = cell(row, 14)
        column_map: dict = {}
        if raw_map:
            try:
                column_map = json.loads(raw_map)
            except ValueError:
                skipped.append(f"row {index} ({slug}): Column Map is not valid JSON — falling back to auto-detect")

        kind = cell(row, 2).strip().lower()
        status = cell(row, 3).strip().lower()
        sheet_link = cell(row, 12)
        found = _SHEET_ID.search(sheet_link)

        out.append(
            DirectoryRound(
                slug=slug,
                title=title or slug,
                solicitation_type="rfp" if kind.startswith("rfp") else "eoi",
                # "Published" means the call is live; anything else we treat as
                # closed rather than guessing at a third state.
                status="active" if status.startswith("publish") else "closed",
                published_on=cell(row, 4),
                application_deadline=cell(row, 5),
                decision_on=cell(row, 6),
                expected_start_date=cell(row, 7),
                expected_end_date=cell(row, 8),
                target_countries=cell(row, 9),
                announcement_url=cell(row, 10),
                form_url=cell(row, 11),
                response_spreadsheet_id=found.group(1) if found else "",
                response_tab=cell(row, 13),
                column_map=column_map,
                notes=cell(row, 17),
                source_row=index,
            )
        )
    return out, skipped


# ======================================================================
# Human verdicts on submissions labs refused to attribute.
# ======================================================================

RESPONSE_MAPPING_TAB = "EOI Response Mapping"
FINDINGS_TAB = "Labs Findings"

RESPONSE_MAPPING_HEADER = [
    "Round Slug",
    "Response Row",
    "Organisation (exact name from the Organizations tab)",
    "Verdict (link | not an LLO)",
    "Why",
    "Decided by",
]

VERDICT_LINK = "link"
VERDICT_NOT_LLO = "not_an_llo"


def parse_response_mapping(rows, known_names: set[str]) -> tuple[dict, list[str]]:
    """(round slug, response row) -> (organisation name | None, verdict, why).

    The counterpart to ``Connect Org Mapping``, for submissions rather than
    slugs, and it follows the same rule: a verdict without a stated reason is
    refused. An attribution nobody justified is a guess someone will later
    trust, and here the cost is one organisation's application filed under
    another organisation's name.

    ``not an LLO`` is a first-class verdict, not an absence. Some submissions
    are not organisations at all, and without a way to say so they would sit in
    the review queue for ever, indistinguishable from work not yet done.
    """
    mapped: dict = {}
    skipped: list[str] = []
    for index, row in enumerate(rows[1:], start=2):
        slug, raw_row = cell(row, 0), cell(row, 1)
        target, raw_verdict, why = cell(row, 2), cell(row, 3), cell(row, 4)
        if not slug and not raw_row:
            continue
        try:
            source_row = int(raw_row)
        except (TypeError, ValueError):
            skipped.append(f"{RESPONSE_MAPPING_TAB} row {index}: {raw_row!r} is not a response row number")
            continue

        verdict = VERDICT_NOT_LLO if "not" in raw_verdict.strip().lower() else VERDICT_LINK
        if not why:
            skipped.append(f"{RESPONSE_MAPPING_TAB} row {index}: {slug}:{source_row} has no stated reason — refused")
            continue
        if verdict == VERDICT_LINK:
            if not target:
                skipped.append(f"{RESPONSE_MAPPING_TAB} row {index}: {slug}:{source_row} names no organisation")
                continue
            if target not in known_names:
                skipped.append(f"{RESPONSE_MAPPING_TAB} row {index}: {target!r} is not on the {ORGANIZATIONS_TAB} tab")
                continue
        mapped[f"{slug}:{source_row}"] = (target if verdict == VERDICT_LINK else None, verdict, why)
    return mapped, skipped


def write_tab(spreadsheet_id: str, tab: str, values: list[list[str]]) -> None:
    """Replace a tab's contents, creating the tab if it does not exist.

    Only ever called for tabs labs owns outright (``Labs Findings``). A tab a
    person maintains is never cleared — see ``update_cells`` for the narrow
    in-place write used on the rounds tab.
    """
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}"

    meta = httpx.get(base, params={"fields": "sheets(properties(title))"}, headers=headers, timeout=60)
    meta.raise_for_status()
    titles = {s["properties"]["title"] for s in meta.json().get("sheets", [])}
    if tab not in titles:
        httpx.post(
            f"{base}:batchUpdate",
            headers=headers,
            timeout=60,
            json={"requests": [{"addSheet": {"properties": {"title": tab}}}]},
        ).raise_for_status()

    quoted = quote(tab, safe="")
    httpx.post(f"{base}/values/{quoted}:clear", headers=headers, timeout=60, json={}).raise_for_status()
    httpx.put(
        f"{base}/values/{quoted}",
        params={"valueInputOption": "RAW"},
        headers=headers,
        timeout=90,
        json={"values": values},
    ).raise_for_status()


def update_cells(spreadsheet_id: str, updates: list[tuple[str, list[list[str]]]]) -> None:
    """Write specific A1 ranges and nothing else.

    The rounds tab is maintained by people. Labs writes exactly two columns on
    it — the verified access state and when it was checked — so this takes
    explicit ranges rather than replacing the tab, and a bug here can damage at
    most the cells it was handed.
    """
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    if not updates:
        return
    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    httpx.post(
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values:batchUpdate",
        headers={"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"},
        timeout=90,
        json={
            "valueInputOption": "RAW",
            "data": [{"range": rng, "values": vals} for rng, vals in updates],
        },
    ).raise_for_status()
