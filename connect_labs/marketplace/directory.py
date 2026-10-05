"""Reading the Connect LLO Directory.

The directory is the master organisation list. ``pulse_partner_import`` read two
of its columns to put names on Connect slugs; this reads all of it, because the
rest — contacts, scale, application history — is the part nobody could
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

from connect_labs.marketplace.countries import parse_cell as parse_countries

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
    countries: list[str] = field(default_factory=list)
    unresolved_countries: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    website: str = ""
    office_address: str = ""
    notes: str = ""
    msa_link: str = ""
    work_order_link: str = ""
    source_row: int | None = None
    # Kept verbatim for the location resolver, which wants the raw cells.
    raw_countries: str = ""
    raw_regions: str = ""
    # The head office's town, when someone has written one down. Preferred
    # over parsing Office Address, which is often an email or a PO box.
    hq_city: str = ""


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


def _split_list(raw: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,]", raw or "") if part.strip()]


class DirectoryFormatError(ValueError):
    """A tab is missing a column labs reads. Raised, never defaulted: a renamed
    header read as blank would silently empty that field for every row."""


def _header_key(title: str) -> str:
    """A header as people retitle it: case, spacing, a parenthetical note and
    trailing punctuation are not part of its name, so "Verdict (link | not an
    LLO)" and "verdict" are one column. "Organisation" and "Organization" are
    the same word on these tabs."""
    text = re.sub(r"\([^)]*\)", " ", (title or "").lower())
    text = re.sub(r"[?:*]+", " ", text).replace("organisation", "organization")
    return re.sub(r"\s+", " ", text).strip()


def column_letter(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class Columns:
    """A tab's columns, looked up by header text rather than by position.

    The sheet is maintained by hand. A column inserted, moved or added at the
    end must not shift which field labs reads, so every read goes through the
    header row. A header the parser needs that is missing raises, naming it.
    """

    def __init__(self, rows: list[list[str]], tab: str, required=(), optional=()):
        self.tab = tab
        self._index: dict[str, int] = {}
        for position, title in enumerate(rows[0] if rows else []):
            key = _header_key(title)
            if key and key not in self._index:
                self._index[key] = position
        missing = [h for h in required if _header_key(h) not in self._index]
        if missing and rows:
            raise DirectoryFormatError(
                f"The '{tab}' tab has no column headed "
                + ", ".join(repr(h) for h in missing)
                + ". Labs finds columns by their header text; restore the header (a note in brackets "
                "after it is fine) rather than letting every row read as blank."
            )

    def index(self, header: str) -> int | None:
        return self._index.get(_header_key(header))

    def get(self, row: list[str], header: str) -> str:
        position = self.index(header)
        return cell(row, position) if position is not None else ""

    def letter(self, header: str) -> str | None:
        position = self.index(header)
        return column_letter(position) if position is not None else None


ORG_NAME = "Organization Name"
ORG_COLUMNS = (
    ORG_NAME,
    "Short Name",
    "Has Used Connect",
    "Year of Establishment",
    "Org Team Size",
    "Countries of Operation",
    "Regions/States of Operation",
    "Website",
    "Office Address",
    "Organization Notes",
    "Latest MSA Link",
    "Latest Work Order Link",
)
# Optional: the head office's town, when someone has written one down.
HQ_CITY = "HQ City"


def parse_organizations(rows: list[list[str]]) -> list[DirectoryOrg]:
    cols = Columns(rows, ORGANIZATIONS_TAB, required=ORG_COLUMNS, optional=(HQ_CITY,))
    seen: set[str] = set()
    out: list[DirectoryOrg] = []
    for index, row in enumerate(rows[1:], start=2):
        name = cols.get(row, ORG_NAME)
        if not name or name in seen:
            continue
        seen.add(name)
        raw_countries = cols.get(row, "Countries of Operation")
        raw_regions = cols.get(row, "Regions/States of Operation")
        countries, unresolved = parse_countries(raw_countries)
        out.append(
            DirectoryOrg(
                name=name,
                short_name=cols.get(row, "Short Name"),
                has_used_connect=_bool_or_none(cols.get(row, "Has Used Connect")),
                year_established=_int_or_none(cols.get(row, "Year of Establishment")),
                team_size=_int_or_none(cols.get(row, "Org Team Size")),
                countries=countries,
                unresolved_countries=unresolved,
                regions=_split_list(raw_regions),
                website=cols.get(row, "Website"),
                office_address=cols.get(row, "Office Address"),
                notes=cols.get(row, "Organization Notes"),
                msa_link=cols.get(row, "Latest MSA Link"),
                work_order_link=cols.get(row, "Latest Work Order Link"),
                source_row=index,
                raw_countries=raw_countries,
                raw_regions=raw_regions,
                hq_city=cols.get(row, HQ_CITY),
            )
        )
    return out


CONTACT_COLUMNS = (
    "Contact Full Name",
    ORG_NAME,
    "Role / Title",
    "Main POC",
    "Email Address",
    "Phone Number",
    "Contact Notes",
)


def parse_contacts(rows: list[list[str]]) -> list[DirectoryContact]:
    cols = Columns(rows, CONTACTS_TAB, required=CONTACT_COLUMNS)
    out: list[DirectoryContact] = []
    for index, row in enumerate(rows[1:], start=2):
        email = cols.get(row, "Email Address").lower()
        org_name = cols.get(row, ORG_NAME)
        # The email is the key: a contact without one cannot be deduplicated and
        # cannot be written to, so it is not yet a contact. ``quality.audit``
        # reports each one dropped here.
        if not email or not org_name:
            continue
        out.append(
            DirectoryContact(
                full_name=cols.get(row, "Contact Full Name"),
                org_name=org_name,
                role_title=cols.get(row, "Role / Title"),
                is_main_poc=_bool_or_none(cols.get(row, "Main POC")) is True,
                email=email,
                phone=cols.get(row, "Phone Number"),
                notes=cols.get(row, "Contact Notes"),
                source_row=index,
            )
        )
    return out


def parse_dates(rows: list[list[str]]) -> dict[str, tuple[str, str]]:
    """name -> (ISO join date, basis). Non-ISO cells are free text and ignored."""
    cols = Columns(rows, DATES_TAB, required=(ORG_NAME, "Joined Connect Network", "Joined — basis"))
    out: dict[str, tuple[str, str]] = {}
    for row in rows[1:]:
        name = cols.get(row, ORG_NAME)
        joined, basis = cols.get(row, "Joined Connect Network"), cols.get(row, "Joined — basis")
        if name and _ISO_DATE.fullmatch(joined):
            out[name] = (joined, basis[:200])
    return out


def parse_mapping(rows: list[list[str]], known_names: set[str]) -> tuple[dict[str, tuple[str, str]], list[str]]:
    """slug -> (organisation name, reason), plus the rows that were refused.

    Refusals are returned rather than logged so the caller can print them: a
    verdict silently dropped is worse than one never recorded.
    """
    cols = Columns(rows, MAPPING_TAB, required=("Connect Org Slug", "Why", "Corrected Partner"))
    mapped: dict[str, tuple[str, str]] = {}
    skipped: list[str] = []
    for row in rows[1:]:
        slug = cols.get(row, "Connect Org Slug")
        target, why = cols.get(row, "Corrected Partner"), cols.get(row, "Why")
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
    delivery_type: str = ""
    source_row: int | None = None
    # Where labs' own columns are on this sheet, by header: {header: letter}.
    # Written to by ``eoi._write_access_columns``; a header that is absent has
    # no entry and is never written, rather than written to a guessed column.
    owned_columns: dict = field(default_factory=dict)


ROUND_COLUMNS = (
    "Slug",
    "Program / Initiative Name",
    "Announcement Type",
    "Status",
    "Published Date",
    "Application Deadline",
    "Selection Decision Date",
    "Program Start Date",
    "Program End Date",
    "Target Countries / Regions",
    "Announcement Link",
    "Form Link",
    "Response Sheet Link",
    "Response Tab",
    "Column Map",
    "Notes",
    "Connect Programme",
)
# The columns labs writes. Optional to read: a missing one is simply not written.
LABS_ACCESS = "Labs Access"
LABS_ACCESS_CHECKED = "Labs Access Checked"
NEXT_STEP = "Next Step"


def parse_rounds(rows: list[list[str]]) -> tuple[list[DirectoryRound], list[str]]:
    """The EOI/RFP tab as rounds, plus the rows that could not be used.

    One row is one *form*. A single announcement can run several — the Malaria
    RFI ran four and the 2026 Readers round ran an English and a French one —
    and modelling the announcement as the round made those invisible, because a
    round with no responses of its own is not a round.
    """
    cols = Columns(rows, ROUNDS_TAB, required=ROUND_COLUMNS, optional=(LABS_ACCESS, LABS_ACCESS_CHECKED, NEXT_STEP))
    owned = {h: cols.letter(h) for h in (LABS_ACCESS, LABS_ACCESS_CHECKED, NEXT_STEP) if cols.letter(h)}
    out: list[DirectoryRound] = []
    skipped: list[str] = []
    seen: set[str] = set()

    for index, row in enumerate(rows[1:], start=2):
        slug = cols.get(row, "Slug")
        title = cols.get(row, "Program / Initiative Name")
        if not slug and not title:
            continue
        if not slug:
            skipped.append(f"row {index} ({title!r}) has no slug — it cannot be referred to, so it is not imported")
            continue
        if slug in seen:
            skipped.append(f"row {index}: slug {slug!r} is already used above — slugs must be unique")
            continue
        seen.add(slug)

        raw_map = cols.get(row, "Column Map")
        column_map: dict = {}
        if raw_map:
            try:
                column_map = json.loads(raw_map)
            except ValueError:
                skipped.append(f"row {index} ({slug}): Column Map is not valid JSON — falling back to auto-detect")

        kind = cols.get(row, "Announcement Type").strip().lower()
        status = cols.get(row, "Status").strip().lower()
        sheet_link = cols.get(row, "Response Sheet Link")
        found = _SHEET_ID.search(sheet_link)

        out.append(
            DirectoryRound(
                slug=slug,
                title=title or slug,
                solicitation_type="rfp" if kind.startswith("rfp") else "eoi",
                # "Published" means the call is live; anything else we treat as
                # closed rather than guessing at a third state.
                status="active" if status.startswith("publish") else "closed",
                published_on=cols.get(row, "Published Date"),
                application_deadline=cols.get(row, "Application Deadline"),
                decision_on=cols.get(row, "Selection Decision Date"),
                expected_start_date=cols.get(row, "Program Start Date"),
                expected_end_date=cols.get(row, "Program End Date"),
                target_countries=cols.get(row, "Target Countries / Regions"),
                announcement_url=cols.get(row, "Announcement Link"),
                form_url=cols.get(row, "Form Link"),
                response_spreadsheet_id=found.group(1) if found else "",
                response_tab=cols.get(row, "Response Tab"),
                column_map=column_map,
                notes=cols.get(row, "Notes"),
                # Connect's own delivery_type, decided by a human — the sheet is
                # the master, so labs reads this rather than guessing a program
                # from the round's title.
                delivery_type=cols.get(row, "Connect Programme").strip().lower(),
                source_row=index,
                owned_columns=dict(owned),
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
    cols = Columns(
        rows, RESPONSE_MAPPING_TAB, required=("Round Slug", "Response Row", "Organisation", "Verdict", "Why")
    )
    mapped: dict = {}
    skipped: list[str] = []
    for index, row in enumerate(rows[1:], start=2):
        slug, raw_row = cols.get(row, "Round Slug"), cols.get(row, "Response Row")
        target, raw_verdict = cols.get(row, "Organisation"), cols.get(row, "Verdict")
        why = cols.get(row, "Why")
        if not slug and not raw_row:
            continue
        # A tab people work in carries guidance for them. A '#' row is a note to
        # the reader, not a verdict, and warning about one every single run
        # would train people to ignore the warnings that matter.
        if slug.startswith("#"):
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


# ======================================================================
# Opportunities Connect recorded separately that were really one engagement.
# ======================================================================

OPPORTUNITY_GROUPS_TAB = "Connect Opportunity Groups"

OPPORTUNITY_GROUPS_HEADER = [
    "Group Slug",
    "Group Name",
    "Connect Org Slug",
    "Opportunity ID",
    "Opportunity Name (for the reader; labs uses the ID)",
    "Why",
    "Decided by",
]


@dataclass
class DirectoryOppGroup:
    slug: str
    name: str
    org_slug: str
    why: str
    members: list[int] = field(default_factory=list)


def parse_opportunity_groups(rows) -> tuple[dict, list[str]]:
    """group slug -> the engagement, plus the rows that could not be used.

    One row per opportunity, like ``EOI Response Mapping`` is one row per
    submission: membership has to be readable and correctable a line at a time,
    and each line carries the reason it is there.

    The same rule as every other verdict tab -- no stated reason, no row. A
    grouping says several engagements were really one, and unexplained it is a
    guess a later reader trusts. Labs never writes here: this is a decision
    people make, and the sheet is where it lives.
    """
    cols = Columns(
        rows,
        OPPORTUNITY_GROUPS_TAB,
        required=("Group Slug", "Group Name", "Connect Org Slug", "Opportunity ID", "Why"),
    )
    groups: dict = {}
    skipped: list[str] = []
    for index, row in enumerate(rows[1:], start=2):
        slug, name = cols.get(row, "Group Slug"), cols.get(row, "Group Name")
        org = cols.get(row, "Connect Org Slug")
        raw_id, why = cols.get(row, "Opportunity ID"), cols.get(row, "Why")
        if not slug and not raw_id:
            continue
        # A '#' row is guidance for whoever maintains the tab, not a verdict.
        if slug.startswith("#"):
            continue
        try:
            opportunity_id = int(raw_id)
        except (TypeError, ValueError):
            skipped.append(f"{OPPORTUNITY_GROUPS_TAB} row {index}: {raw_id!r} is not an opportunity id")
            continue
        if not why:
            skipped.append(f"{OPPORTUNITY_GROUPS_TAB} row {index}: {slug}:{opportunity_id} has no stated reason")
            continue

        group = groups.get(slug)
        if group is None:
            group = DirectoryOppGroup(slug=slug, name=name or slug, org_slug=org, why=why)
            groups[slug] = group
        elif org and group.org_slug and org != group.org_slug:
            # An engagement belongs to one partner. Spanning two, it would have
            # to appear under both or neither.
            skipped.append(
                f"{OPPORTUNITY_GROUPS_TAB} row {index}: {slug} already belongs to {group.org_slug!r}, "
                f"so {org!r} cannot be added to it"
            )
            continue
        if opportunity_id in group.members:
            skipped.append(f"{OPPORTUNITY_GROUPS_TAB} row {index}: {opportunity_id} is already listed above")
            continue
        group.members.append(opportunity_id)
    return groups, skipped


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
