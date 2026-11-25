"""The costs ledger sheet: what Dimagi paid for Connect opportunities outside Connect, and its import.

One Google Sheet, kept next to the LLO Directory (``marketplace.directory.DIRECTORY_ID``) and read
by the same labs service account. It is the record people and agents maintain, as the directory is:

* **Ledger** -- one row per accounting line (Intacct bills and payments): when, what, how much, its
  class (``PulseLedgerLine.CLASS_CHOICES``), and the Connect organisation and programme it paid for.
  Whoever pulls the ledger from Intacct writes this tab and does the matching; labs does not guess.
* **Programmes** -- which Connect programmes (ids) each programme name on the Ledger tab covers. The
  ledger books by project; Connect splits a project into programmes (batches, pilots, rounds).
* **Exclusions** -- reconstructed invoices a person has decided not to count, with why.

``run_import`` mirrors those tabs into labs (``PulseLedgerLine``), rebuilds the reconstructed invoices
(``pulse.reconstruct``) against what Connect recorded and the people each opportunity reached, and
writes labs' own tabs back so the sheet shows the result: Labs Pairs, Labs Programmes, Labs Invoices,
Labs Unattributed. A tab a person maintains is never written. ``create_sheet`` makes the sheet in the
directory's folder with those tabs and their headers.

Parsing is separated from fetching, as in ``marketplace.directory``, so it is tested on rows.
Doc: ``docs/COSTS_LEDGER.md``.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from connect_labs.marketplace import directory
from connect_labs.pulse import reconstruct

logger = logging.getLogger(__name__)

#: Where the sheet's id is kept once ``create_sheet`` has made it (``PulseScalar``).
SHEET_KEY = "costs_ledger_sheet"
SHEET_TITLE = "Connect costs ledger (outside Connect)"

LEDGER_TAB = "Ledger"
PROGRAMMES_TAB = "Programmes"
EXCLUSIONS_TAB = "Exclusions"
LABS_TABS = ("Labs Opportunities", "Labs Pairs", "Labs Programmes", "Labs Invoices", "Labs Unattributed")

LEDGER_HEADERS = [
    "line_key", "source", "date", "entity", "vendor", "document", "memo", "department", "project",
    "account", "usd", "class", "org_slug", "programme", "match_rule",
]  # fmt: skip
PROGRAMMES_HEADERS = ["programme", "connect_program_ids", "notes"]
EXCLUSIONS_HEADERS = ["opportunity_id", "kind", "reason"]

CLASSES = {"llo_service", "llo_fixed", "direct_cost", "excluded", "unclear"}

LOCK_KEY = "pulse:costs-ledger-import"
LOCK_SECONDS = 600


class ImportFailed(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# --- parsing --------------------------------------------------------------------------


@dataclass
class Parsed:
    lines: list[dict] = field(default_factory=list)
    programmes: dict[str, list[int]] = field(default_factory=dict)
    exclusions: dict[tuple[int, str], str] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def _columns(header: list[str], expected: list[str], tab: str, problems: list[str]) -> dict[str, int] | None:
    found = {str(h).strip().lower(): i for i, h in enumerate(header)}
    missing = [h for h in expected if h not in found]
    if missing:
        problems.append(f"{tab}: missing column(s) {', '.join(missing)}")
        return None
    return {h: found[h] for h in expected}


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


def parse(ledger_rows, programme_rows, exclusion_rows) -> Parsed:
    """The three tabs a person maintains, as values; every row that cannot be read is a problem, not a guess."""
    out = Parsed()
    if ledger_rows:
        cols = _columns(ledger_rows[0], LEDGER_HEADERS, LEDGER_TAB, out.problems)
        seen: set[str] = set()
        for n, row in enumerate(ledger_rows[1:] if cols else [], start=2):
            values = {h: directory.cell(row, i).strip() for h, i in cols.items()}
            get = values.__getitem__
            key = get("line_key")
            if not key:
                continue
            usd, cls = _usd(get("usd")), get("class").lower()
            if key in seen:
                out.problems.append(f"{LEDGER_TAB} row {n}: line_key {key} appears twice")
                continue
            if usd is None:
                out.problems.append(f"{LEDGER_TAB} row {n}: usd {get('usd')!r} is not a number")
                continue
            if cls not in CLASSES:
                out.problems.append(f"{LEDGER_TAB} row {n}: class {cls!r} is not one of {', '.join(sorted(CLASSES))}")
                continue
            seen.add(key)
            out.lines.append(
                {"line_key": key[:120], "source": get("source") or "intacct", "date": _date(get("date")),
                 "entity": get("entity")[:32], "vendor": get("vendor")[:200], "document": get("document")[:120],
                 "memo": get("memo"), "department": get("department")[:120], "project": get("project")[:200],
                 "account": get("account")[:120], "usd": usd, "line_class": cls, "org_slug": get("org_slug")[:120],
                 "programme": get("programme")[:200], "match_rule": get("match_rule")[:200]}
            )  # fmt: skip
    if programme_rows:
        cols = _columns(programme_rows[0], PROGRAMMES_HEADERS[:2], PROGRAMMES_TAB, out.problems)
        for n, row in enumerate(programme_rows[1:] if cols else [], start=2):
            name = directory.cell(row, cols["programme"]).strip()
            if not name:
                continue
            ids, bad = [], []
            for part in directory.cell(row, cols["connect_program_ids"]).replace(";", ",").split(","):
                part = part.strip()
                if part:
                    (ids.append(int(part)) if part.isdigit() else bad.append(part))
            if bad:
                out.problems.append(f"{PROGRAMMES_TAB} row {n}: {', '.join(bad)} is not a Connect program id")
            if name in out.programmes:
                out.problems.append(f"{PROGRAMMES_TAB} row {n}: {name} is listed twice")
            out.programmes[name] = ids
    if exclusion_rows:
        cols = _columns(exclusion_rows[0], EXCLUSIONS_HEADERS, EXCLUSIONS_TAB, out.problems)
        for n, row in enumerate(exclusion_rows[1:] if cols else [], start=2):
            opp, kind = directory.cell(row, cols["opportunity_id"]).strip(), directory.cell(row, cols["kind"]).strip()
            reason = directory.cell(row, cols["reason"]).strip()
            if not opp:
                continue
            if not opp.isdigit() or kind not in (reconstruct.KIND_LLO, reconstruct.KIND_DIRECT) or not reason:
                out.problems.append(
                    f"{EXCLUSIONS_TAB} row {n}: needs an opportunity id, a kind "
                    f"({reconstruct.KIND_LLO} or {reconstruct.KIND_DIRECT}) and a reason"
                )
                continue
            out.exclusions[(int(opp), kind)] = reason
    return out


# --- the sheet ----------------------------------------------------------------------------


def sheet_id() -> str | None:
    from connect_labs.pulse.models import PulseScalar

    row = PulseScalar.objects.filter(key=SHEET_KEY).first()
    return (row.value or {}).get("id") if row else None


def sheet_url(sid: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{sid}/edit"


def create_sheet(share_with: list[str]) -> dict:
    """Make the ledger sheet in the LLO Directory's folder, with its tabs and headers, and share it.

    Refused when one already exists (``SHEET_EXISTS``): there is one ledger. ``share_with`` gets
    edit access (people and agents who maintain the Ledger tab)."""
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials
    from connect_labs.pulse.models import PulseScalar

    existing = sheet_id()
    if existing:
        raise ImportFailed("SHEET_EXISTS", f"The costs ledger already exists: {sheet_url(existing)}")
    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    headers = {"Authorization": f"Bearer {creds.token}"}
    drive = "https://www.googleapis.com/drive/v3/files"
    meta = httpx.get(f"{drive}/{directory.DIRECTORY_ID}", params={"fields": "parents", "supportsAllDrives": "true"},
                     headers=headers, timeout=60)  # fmt: skip
    meta.raise_for_status()
    parents = meta.json().get("parents") or []
    made = httpx.post(
        drive,
        params={"supportsAllDrives": "true", "fields": "id"},
        headers=headers,
        timeout=60,
        json={"name": SHEET_TITLE, "mimeType": "application/vnd.google-apps.spreadsheet", "parents": parents},
    )
    made.raise_for_status()
    sid = made.json()["id"]
    for tab, header in ((LEDGER_TAB, LEDGER_HEADERS), (PROGRAMMES_TAB, PROGRAMMES_HEADERS),
                        (EXCLUSIONS_TAB, EXCLUSIONS_HEADERS)):  # fmt: skip
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
    return {"spreadsheet_id": sid, "url": sheet_url(sid), "folder": parents, "shared_with": share_with}


# --- the import --------------------------------------------------------------------------


def _opps(people_reached):
    """Every real opportunity as `reconstruct` sees it: what Connect recorded, and who it reached.

    ``people_reached`` is passed in (``effectiveness.implementation.reach.people_reached``): who an
    opportunity reached is decided above Pulse, and Pulse never imports that layer."""
    from connect_labs.pulse import costs
    from connect_labs.pulse.models import PulseOpportunity

    rows = list(PulseOpportunity.objects.filter(is_test=False))
    ids = [o.opportunity_id for o in rows]
    cost_of = costs.opportunity_costs(ids)
    reached = people_reached(ids)
    return [
        reconstruct.Opp(
            id=o.opportunity_id,
            org_slug=o.org_slug,
            program_id=o.program_id,
            active=o.is_active,
            recorded_usd=cost_of[o.opportunity_id].recorded_usd if o.opportunity_id in cost_of else Decimal("0"),
            reached=int((reached.get(o.opportunity_id) or {}).get("reached") or 0),
        )
        for o in rows
    ]


def _save(parsed: Parsed, result: reconstruct.Result, opps: list[reconstruct.Opp]) -> dict:
    from connect_labs.pulse import costs
    from connect_labs.pulse.models import PulseLedgerLine, PulseReconstructedInvoice, PulseReconstructedInvoiceSource

    with transaction.atomic():
        pushed = dict(PulseReconstructedInvoice.objects.exclude(pushed_invoice_number="")
                      .values_list("invoice_number", "pushed_invoice_number"))  # fmt: skip
        PulseReconstructedInvoice.objects.all().delete()
        PulseLedgerLine.objects.all().delete()
        lines = {
            ln.line_key: ln
            for ln in PulseLedgerLine.objects.bulk_create(
                [PulseLedgerLine(**row) for row in parsed.lines], batch_size=1000
            )
        }
        sources = []
        by_id = {o.id: o for o in opps}
        for inv in result.invoices:
            opp = by_id.get(inv.opportunity_id)
            if opp is not None:
                # What the opportunity looked like when this was built, for the page's before/after.
                inv.basis.update({"recorded_usd": str(opp.recorded_usd), "reached": opp.reached})
            excluded = parsed.exclusions.get((inv.opportunity_id, inv.kind), "")
            kind_label = (
                "Payments to the organisation Connect did not record"
                if inv.kind == reconstruct.KIND_LLO
                else "Bought directly for the programme"
            )
            row = PulseReconstructedInvoice.objects.create(
                opportunity_id=inv.opportunity_id,
                kind=inv.kind,
                invoice_number=inv.invoice_number,
                amount_usd=inv.amount_usd,
                date=inv.end_date,
                start_date=inv.start_date,
                end_date=inv.end_date,
                title=kind_label,
                description=_describe(inv),
                basis=inv.basis,
                excluded_reason=excluded,
                pushed_invoice_number=pushed.get(inv.invoice_number, ""),
            )
            sources += [PulseReconstructedInvoiceSource(invoice=row, line=lines[k], usd=v)
                        for k, v in inv.sources.items() if k in lines]  # fmt: skip
        PulseReconstructedInvoiceSource.objects.bulk_create(sources, batch_size=2000)
    costs.invalidate()
    counted = [i for i in result.invoices if (i.opportunity_id, i.kind) not in parsed.exclusions]
    return {
        "ledger_lines": len(parsed.lines),
        "invoices": len(result.invoices),
        "invoices_counted": len(counted),
        "invoiced_usd": float(sum((i.amount_usd for i in counted), Decimal("0"))),
        "excluded": len(result.invoices) - len(counted),
    }


def _describe(inv: reconstruct.Invoice) -> str:
    parts = inv.basis.get("parts", [])
    if inv.kind == reconstruct.KIND_LLO:
        return "; ".join(
            f"{p['programme']} · {p['org_slug']}: ledger paid ${p['paid_usd']}, "
            f"Connect recorded ${p['recorded_usd']}, "
            f"{p['share']} of the ${p['gap_usd']} missed (split by {p['split_by']})"
            for p in parts
        )
    return "; ".join(
        f"{p['programme']}: {p['share']} of ${p['total_usd']} bought directly (split by {p['split_by']})"
        for p in parts
    )


def _money(value) -> str:
    return f"{Decimal(value):.2f}"


def compare(opps: list[reconstruct.Opp], result: reconstruct.Result, exclusions: dict) -> list[dict]:
    """Per opportunity: what Connect recorded, the invoices to add, the implied total and the change.

    Every real opportunity, the ones with nothing to add included, so the totals are the portfolio's.
    An excluded invoice is shown and not added."""
    added: dict[int, dict[str, Decimal]] = {}
    for inv in result.invoices:
        if (inv.opportunity_id, inv.kind) in exclusions:
            continue
        added.setdefault(inv.opportunity_id, {})[inv.kind] = inv.amount_usd
    rows = []
    for o in sorted(opps, key=lambda o: o.id):
        llo = added.get(o.id, {}).get(reconstruct.KIND_LLO, Decimal("0"))
        direct = added.get(o.id, {}).get(reconstruct.KIND_DIRECT, Decimal("0"))
        implied = o.recorded_usd + llo + direct
        rows.append({
            "opportunity_id": o.id,
            "org_slug": o.org_slug,
            "program_id": o.program_id,
            "closed": not o.active,
            "recorded_usd": o.recorded_usd,
            "llo_payment_usd": llo,
            "direct_cost_usd": direct,
            "implied_usd": implied,
            "change_usd": implied - o.recorded_usd,
            "change_pct": float((implied - o.recorded_usd) / o.recorded_usd) if o.recorded_usd else None,
            "reached": o.reached,
            "per_person_recorded": float(o.recorded_usd / o.reached) if o.reached else None,
            "per_person_implied": float(implied / o.reached) if o.reached else None,
        })  # fmt: skip
    return rows


def _write_back(sid: str, result: reconstruct.Result, exclusions: dict, comparison: list[dict]) -> None:
    opps = [["opportunity_id", "org_slug", "program_id", "closed", "recorded_usd", "llo_payment_usd",
             "direct_cost_usd", "implied_usd", "change_usd", "change_pct", "reached", "per_person_recorded",
             "per_person_implied"]]  # fmt: skip
    for c in comparison:
        opps.append([c["opportunity_id"], c["org_slug"], c["program_id"] or "", "yes" if c["closed"] else "no",
                     _money(c["recorded_usd"]), _money(c["llo_payment_usd"]), _money(c["direct_cost_usd"]),
                     _money(c["implied_usd"]), _money(c["change_usd"]),
                     "" if c["change_pct"] is None else f"{c['change_pct']:.4f}", c["reached"],
                     "" if c["per_person_recorded"] is None else f"{c['per_person_recorded']:.2f}",
                     "" if c["per_person_implied"] is None else f"{c['per_person_implied']:.2f}"])  # fmt: skip
    pairs = [["programme", "org_slug", "opportunities", "closed", "ledger_service_usd", "ledger_fixed_usd",
              "ledger_usd", "connect_usd", "gap_usd", "invoiced_usd", "status"]]  # fmt: skip
    pairs += [[p["programme"], p["org_slug"], p["opportunities"], p["closed"], _money(p["ledger_service_usd"]),
               _money(p["ledger_fixed_usd"]), _money(p["ledger_usd"]), _money(p["connect_usd"]),
               _money(p["gap_usd"]), _money(p["invoiced_usd"]), p["status"]] for p in result.pairs]  # fmt: skip
    progs = [["programme", "opportunities", "direct_cost_usd", "invoiced_usd", "held_for_active_usd"]]
    progs += [[p["programme"], p["opportunities"], _money(p["direct_cost_usd"]), _money(p["invoiced_usd"]),
               _money(p["held_for_active_usd"])] for p in result.programmes]  # fmt: skip
    invoices = [["invoice_number", "opportunity_id", "kind", "amount_usd", "start_date", "end_date", "counted",
                 "excluded_reason", "sources", "description"]]  # fmt: skip
    for inv in result.invoices:
        reason = exclusions.get((inv.opportunity_id, inv.kind), "")
        invoices.append([inv.invoice_number, inv.opportunity_id, inv.kind, _money(inv.amount_usd),
                         str(inv.start_date or ""), str(inv.end_date or ""), "no" if reason else "yes", reason,
                         len(inv.sources), _describe(inv)])  # fmt: skip
    unplaced = [["line_key", "usd", "class", "org_slug", "programme", "why"]]
    unplaced += [[u["line_key"], _money(u["usd"]), u["class"], u["org_slug"], u["programme"], u["why"]]
                 for u in result.unattributed]  # fmt: skip
    for tab, values in zip(LABS_TABS, (opps, pairs, progs, invoices, unplaced), strict=True):
        directory.write_tab(sid, tab, values)


def run_import(
    *, people_reached, spreadsheet_id: str | None = None, dry_run: bool = False, write_back: bool = True
) -> dict:
    """Read the ledger sheet, rebuild the reconstructed invoices, and (unless ``dry_run``) save them.

    ``people_reached(opp_ids) -> {id: {"reached": n}}`` splits each pair and programme by who was reached.

    Refused (``ImportFailed``): no sheet yet (``NO_SHEET``), it cannot be read (``SHEET_UNREADABLE``),
    a tab is malformed so nothing could be read from it (``SHEET_MALFORMED``), or another import is
    running (``IMPORT_IN_PROGRESS``). Rows that cannot be read are returned as ``problems`` and left
    out; they never stop the rest."""
    sid = spreadsheet_id or sheet_id()
    if not sid:
        raise ImportFailed("NO_SHEET", "There is no costs ledger sheet yet: create it with costs_ledger_sheet_create.")
    try:
        ledger_rows = directory.read_tab(sid, LEDGER_TAB)
        programme_rows = directory.read_tab(sid, PROGRAMMES_TAB)
        exclusion_rows = directory.read_tab(sid, EXCLUSIONS_TAB)
    except Exception as exc:  # noqa: BLE001 -- surfaced verbatim, the causes are many
        raise ImportFailed("SHEET_UNREADABLE", f"Could not read the costs ledger sheet: {exc}") from exc
    parsed = parse(ledger_rows, programme_rows, exclusion_rows)
    if ledger_rows and not parsed.lines and parsed.problems:
        raise ImportFailed("SHEET_MALFORMED", "; ".join(parsed.problems[:10]))
    lines = [reconstruct.Line(key=r["line_key"], usd=r["usd"], line_class=r["line_class"], org_slug=r["org_slug"],
                              programme=r["programme"], date=r["date"]) for r in parsed.lines]  # fmt: skip
    opps = _opps(people_reached)
    result = reconstruct.build(lines, parsed.programmes, opps)
    comparison = compare(opps, result, parsed.exclusions)
    recorded = sum((c["recorded_usd"] for c in comparison), Decimal("0"))
    implied = sum((c["implied_usd"] for c in comparison), Decimal("0"))
    summary = {
        "sheet": sheet_url(sid),
        "dry_run": dry_run,
        "problems": parsed.problems,
        "pairs": len(result.pairs),
        "pairs_invoiced": sum(1 for p in result.pairs if p["status"] == "invoiced"),
        "unattributed_lines": len(result.unattributed),
        "unattributed_usd": float(sum((u["usd"] for u in result.unattributed), Decimal("0"))),
        "held_for_active_usd": float(sum((p["held_for_active_usd"] for p in result.programmes), Decimal("0"))),
        "recorded_usd": float(recorded),
        "implied_usd": float(implied),
        "opportunities_changed": sum(1 for c in comparison if c["change_usd"]),
    }
    if dry_run:
        invoiced = sum((i.amount_usd for i in result.invoices), Decimal("0"))
        summary.update({"ledger_lines": len(parsed.lines), "invoices": len(result.invoices),
                        "invoiced_usd": float(invoiced)})  # fmt: skip
        return summary
    if cache.add(LOCK_KEY, timezone.now().isoformat(), LOCK_SECONDS) is False:
        raise ImportFailed("IMPORT_IN_PROGRESS", "Another costs ledger import is running; call again in a minute.")
    try:
        summary.update(_save(parsed, result, opps))
        if write_back:
            _write_back(sid, result, parsed.exclusions, comparison)
    finally:
        cache.delete(LOCK_KEY)
    return summary
