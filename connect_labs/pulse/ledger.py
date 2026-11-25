"""The invoices sheet: what Dimagi paid for Connect opportunities outside Connect, as invoices, and its import.

One Google Sheet, "Connect invoices to add", read by labs' service account. It holds only what labs
needs and what a person reads, never raw accounting lines:

* **Invoices** -- one row per reconstructed invoice: the opportunity, its organisation, the kind
  (``llo_payment``: paid to the organisation beyond what Connect recorded; ``direct_cost``: bought
  directly for the programme), the amount and dates, a one-line description naming only the
  organisation or supplier and the bill numbers, and whether it counts. This is what labs imports.
* **Per opportunity** -- the readable summary people keep beside it. Labs never reads or writes it.

The invoices are built outside labs, from a filtered Intacct pull (vendor payments to companies in
the Connect departments; never journal entries, payroll or a payment to a person), on the machine
that holds the Intacct login. Labs keeps no ledger line: the staff and contractor payments in an
accounting ledger must never reach a shared place.

``run_import`` replaces ``PulseReconstructedInvoice`` with the Invoices tab; ``create_sheet`` makes
the sheet (or adopts one a person made). Parsing is separated from fetching, as in
``marketplace.directory``, so it is tested on rows. Doc: ``docs/COSTS_LEDGER.md``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from connect_labs.marketplace import directory

logger = logging.getLogger(__name__)

#: Where the sheet's id is kept once ``create_sheet`` has made or adopted it (``PulseScalar``).
SHEET_KEY = "costs_ledger_sheet"
SHEET_TITLE = "Connect invoices to add"

INVOICES_TAB = "Invoices"
SUMMARY_TAB = "Per opportunity"

INVOICE_HEADERS = [
    "opportunity_id", "opportunity", "org", "kind", "amount_usd", "date", "start_date", "end_date", "title",
    "description", "count", "exclude_reason",
]  # fmt: skip
SUMMARY_HEADERS = [
    "opportunity_id", "opportunity", "org", "programme", "status", "connect_recorded_usd", "llo_invoice_number",
    "llo_invoice_usd", "llo_basis", "direct_invoice_number", "direct_invoice_usd", "direct_basis", "implied_usd",
    "held_usd", "people_reached", "per_person_before", "per_person_after", "note",
]  # fmt: skip

KIND_LLO, KIND_DIRECT = "llo_payment", "direct_cost"
KINDS = (KIND_LLO, KIND_DIRECT)
KIND_TITLES = {
    KIND_LLO: "Payments to the organisation Connect did not record",
    KIND_DIRECT: "Bought directly for the programme",
}
_YES, _NO = {"yes", "y", "true", "1", ""}, {"no", "n", "false", "0"}

LOCK_KEY = "pulse:costs-ledger-import"
LOCK_SECONDS = 600


class ImportFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def invoice_number(opportunity_id: int, kind: str) -> str:
    """Stable per opportunity and kind, so a re-import keeps each invoice's number (and what was pushed)."""
    digest = hashlib.sha1(f"{opportunity_id}:{kind}".encode()).hexdigest()[:8].upper()  # noqa: S324 -- a label
    return f"LABS-{digest}"


# --- parsing --------------------------------------------------------------------------


@dataclass
class Parsed:
    invoices: list[dict] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _date(raw: str) -> dt.date | None:
    raw = (raw or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _usd(raw: str) -> Decimal | None:
    raw = (raw or "").replace(",", "").replace("$", "").strip()
    if raw.startswith("(") and raw.endswith(")"):
        raw = "-" + raw[1:-1]
    try:
        return Decimal(raw).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        return None


def parse(rows) -> Parsed:
    """The Invoices tab, as values; every row that cannot be read is a problem, not a guess."""
    out = Parsed()
    if not rows:
        return out
    found = {str(h).strip().lower(): i for i, h in enumerate(rows[0])}
    missing = [h for h in INVOICE_HEADERS if h not in found]
    if missing:
        out.problems.append(f"{INVOICES_TAB}: missing column(s) {', '.join(missing)}")
        return out
    seen: set[tuple[int, str]] = set()
    for n, row in enumerate(rows[1:], start=2):
        get = {h: directory.cell(row, found[h]).strip() for h in INVOICE_HEADERS}.__getitem__
        opp = get("opportunity_id")
        if not opp:
            continue
        kind, amount, count = get("kind").lower(), _usd(get("amount_usd")), get("count").lower()
        why = []
        if not opp.isdigit():
            why.append(f"opportunity_id {opp!r} is not a number")
        if kind not in KINDS:
            why.append(f"kind {kind!r} is not one of {', '.join(KINDS)}")
        if amount is None:
            why.append(f"amount_usd {get('amount_usd')!r} is not a number")
        if count not in _YES | _NO:
            why.append(f"count {count!r} is not yes or no")
        elif count in _NO and not get("exclude_reason"):
            why.append("an invoice that does not count needs an exclude_reason")
        if why:
            out.problems.append(f"{INVOICES_TAB} row {n}: " + "; ".join(why))
            continue
        key = (int(opp), kind)
        if key in seen:
            out.problems.append(f"{INVOICES_TAB} row {n}: opportunity {opp} has a second {kind} invoice")
            continue
        seen.add(key)
        out.invoices.append({
            "opportunity_id": int(opp), "kind": kind, "amount_usd": amount,
            "date": _date(get("date")), "start_date": _date(get("start_date")), "end_date": _date(get("end_date")),
            "title": (get("title") or KIND_TITLES[kind])[:255], "description": get("description"),
            "excluded_reason": get("exclude_reason") if count in _NO else "",
            "opportunity": get("opportunity")[:255], "org": get("org")[:255],
        })  # fmt: skip
    return out


# --- the sheet ----------------------------------------------------------------------------


def sheet_id() -> str | None:
    from connect_labs.pulse.models import PulseScalar

    row = PulseScalar.objects.filter(key=SHEET_KEY).first()
    return (row.value or {}).get("id") if row else None


def sheet_url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def _google_refusal(resp) -> str:
    """Google's own reason for a refused Drive call, for the person reading the error."""
    try:
        err = resp.json().get("error") or {}
        reasons = ", ".join(e.get("reason", "") for e in err.get("errors") or [] if e.get("reason"))
        return f"{resp.status_code} {err.get('message', '')}" + (f" ({reasons})" if reasons else "")
    except ValueError:
        return f"{resp.status_code} {resp.text[:200]}"


def create_sheet(share_with: list[str], spreadsheet_id: str | None = None) -> dict:
    """Make the invoices sheet in the LLO Directory's folder, with its two tabs and headers, and share it.

    A service account can be refused a new file in a folder it may read (no storage of its own, or
    only read access there). Then a person makes the sheet in that folder, gives the labs service
    account edit access, and passes its id as ``spreadsheet_id``: labs adopts it, adds whichever kept
    tabs are missing or empty with their headers (never clearing a tab that has rows) and shares it.

    There is one sheet: making a second is refused (``SHEET_EXISTS``), while adopting one by id replaces
    the one labs used before. ``share_with`` gets
    edit access (people and agents who keep the Invoices tab)."""
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials
    from connect_labs.pulse.models import PulseScalar

    existing = sheet_id()
    if existing and not spreadsheet_id:
        raise ImportFailed("SHEET_EXISTS", f"The invoices sheet already exists: {sheet_url(existing)}")
    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    drive = "https://www.googleapis.com/drive/v3/files"
    if spreadsheet_id:
        meta = httpx.get(f"{drive}/{spreadsheet_id}",
                         params={"fields": "parents,mimeType,capabilities(canEdit)", "supportsAllDrives": "true"},
                         headers=headers, timeout=60)  # fmt: skip
        if meta.status_code in (403, 404):
            raise ImportFailed(
                "NO_ACCESS",
                f"Labs' service account ({getattr(creds, 'service_account_email', 'the service account')}) cannot "
                f"open that sheet: give it edit access. Google said: {_google_refusal(meta)}",
            )
        meta.raise_for_status()
        info = meta.json()
        if info.get("mimeType") != "application/vnd.google-apps.spreadsheet":
            raise ImportFailed("NOT_A_SHEET", "That file is not a Google Sheet.")
        if not (info.get("capabilities") or {}).get("canEdit"):
            raise ImportFailed(
                "NO_ACCESS",
                f"Labs' service account ({getattr(creds, 'service_account_email', 'the service account')}) can "
                "read that sheet but not edit it: give it edit access.",
            )
        sid, parents = spreadsheet_id, info.get("parents") or []
    else:
        meta = httpx.get(
            f"{drive}/{directory.DIRECTORY_ID}",
            params={"fields": "parents", "supportsAllDrives": "true"},
            headers=headers,
            timeout=60,
        )
        meta.raise_for_status()
        parents = meta.json().get("parents") or []
        made = httpx.post(
            drive,
            params={"supportsAllDrives": "true", "fields": "id"},
            headers=headers,
            timeout=60,
            json={"name": SHEET_TITLE, "mimeType": "application/vnd.google-apps.spreadsheet", "parents": parents},
        )
        if made.status_code == 403:
            raise ImportFailed(
                "CANNOT_CREATE",
                "Google refused labs' service account "
                f"({getattr(creds, 'service_account_email', 'the service account')}) a new file in the LLO "
                f"Directory's folder: {_google_refusal(made)}. Make a Google Sheet named '{SHEET_TITLE}' in that "
                "folder yourself, give the service account edit access, and call this again with its "
                "spreadsheet_id.",
            )
        made.raise_for_status()
        sid = made.json()["id"]
    for tab, header in ((INVOICES_TAB, INVOICE_HEADERS), (SUMMARY_TAB, SUMMARY_HEADERS)):
        try:
            has_rows = any(any(str(c).strip() for c in row) for row in directory.read_tab(sid, tab))
        except httpx.HTTPError:  # a missing tab reads as an error; write_tab adds it
            has_rows = False
        if not has_rows:
            directory.write_tab(sid, tab, [header])
    for email in share_with:
        httpx.post(
            f"{drive}/{sid}/permissions",
            params={"supportsAllDrives": "true", "sendNotificationEmail": "false"},
            headers=headers,
            timeout=60,
            json={"type": "user", "role": "writer", "emailAddress": email},
        ).raise_for_status()
    PulseScalar.objects.update_or_create(key=SHEET_KEY, defaults={"value": {"id": sid, "parents": parents}})
    return {"spreadsheet_id": sid, "url": sheet_url(sid), "folder": parents, "shared_with": share_with,
            "adopted": bool(spreadsheet_id)}  # fmt: skip


# --- the import --------------------------------------------------------------------------


def _save(parsed: Parsed, reached: dict[int, int]) -> dict:
    from connect_labs.pulse import costs
    from connect_labs.pulse.models import PulseReconstructedInvoice

    with transaction.atomic():
        pushed = dict(PulseReconstructedInvoice.objects.exclude(pushed_invoice_number="")
                      .values_list("invoice_number", "pushed_invoice_number"))  # fmt: skip
        PulseReconstructedInvoice.objects.all().delete()
        rows = []
        for inv in parsed.invoices:
            number = invoice_number(inv["opportunity_id"], inv["kind"])
            rows.append(PulseReconstructedInvoice(
                opportunity_id=inv["opportunity_id"], kind=inv["kind"], invoice_number=number,
                amount_usd=inv["amount_usd"], date=inv["date"] or inv["end_date"], start_date=inv["start_date"],
                end_date=inv["end_date"], title=inv["title"], description=inv["description"],
                # What the page needs for before/after per person; nothing about any payee.
                basis={"source": "invoices sheet", "opportunity": inv["opportunity"], "org": inv["org"],
                       "reached": reached.get(inv["opportunity_id"], 0)},
                excluded_reason=inv["excluded_reason"], pushed_invoice_number=pushed.get(number, ""),
            ))  # fmt: skip
        PulseReconstructedInvoice.objects.bulk_create(rows)
    costs.invalidate()
    return {"invoices": len(rows), "invoices_counted": sum(1 for r in rows if r.counted)}


def run_import(*, people_reached, spreadsheet_id: str | None = None, dry_run: bool = False) -> dict:
    """Read the Invoices tab and (unless ``dry_run``) replace the reconstructed invoices with it.

    ``people_reached(opp_ids) -> {id: {"reached": n}}`` is kept on each invoice for the page's cost per
    person. An invoice for an opportunity labs does not know is a problem and is left out.

    Refused (``ImportFailed``): no sheet yet (``NO_SHEET``), it cannot be read (``SHEET_UNREADABLE``),
    the tab is malformed so nothing could be read (``SHEET_MALFORMED``), or another import is running
    (``IMPORT_IN_PROGRESS``). Rows that cannot be read are returned as ``problems``; they never stop
    the rest. Labs writes nothing back to the sheet."""
    from connect_labs.pulse.models import PulseOpportunity

    sid = spreadsheet_id or sheet_id()
    if not sid:
        raise ImportFailed(
            "NO_SHEET", "There is no invoices sheet yet: create or adopt it with costs_ledger_sheet_create."
        )
    try:
        rows = directory.read_tab(sid, INVOICES_TAB)
    except Exception as exc:  # noqa: BLE001 -- surfaced verbatim, the causes are many
        raise ImportFailed("SHEET_UNREADABLE", f"Could not read the invoices sheet: {exc}") from exc
    parsed = parse(rows)
    if len(rows) > 1 and not parsed.invoices and parsed.problems:
        raise ImportFailed("SHEET_MALFORMED", "; ".join(parsed.problems[:10]))
    wanted = {i["opportunity_id"] for i in parsed.invoices}
    known = set(PulseOpportunity.objects.filter(opportunity_id__in=wanted).values_list("opportunity_id", flat=True))
    for inv in [i for i in parsed.invoices if i["opportunity_id"] not in known]:
        parsed.problems.append(f"{INVOICES_TAB}: opportunity {inv['opportunity_id']} is not one labs knows")
    parsed.invoices = [i for i in parsed.invoices if i["opportunity_id"] in known]
    counted = [i for i in parsed.invoices if not i["excluded_reason"]]

    def total(kind):
        return float(sum((i["amount_usd"] for i in counted if i["kind"] == kind), Decimal("0")))

    summary = {
        "sheet": sheet_url(sid),
        "dry_run": dry_run,
        "problems": parsed.problems,
        "invoices": len(parsed.invoices),
        "invoices_counted": len(counted),
        "llo_payment_usd": total(KIND_LLO),
        "direct_cost_usd": total(KIND_DIRECT),
        "invoiced_usd": total(KIND_LLO) + total(KIND_DIRECT),
        "opportunities": len({i["opportunity_id"] for i in parsed.invoices}),
    }
    if dry_run:
        return summary
    if cache.add(LOCK_KEY, timezone.now().isoformat(), LOCK_SECONDS) is False:
        raise ImportFailed("IMPORT_IN_PROGRESS", "Another invoices import is running; call again in a minute.")
    try:
        ids = sorted({i["opportunity_id"] for i in parsed.invoices})
        reached = {k: int((v or {}).get("reached") or 0) for k, v in people_reached(ids).items()} if ids else {}
        summary.update(_save(parsed, reached))
    finally:
        cache.delete(LOCK_KEY)
    return summary
