"""De-identified exports of a data space: the dictionary applied to the space's visits,
checked, and signed off before anyone can download it.

The whole life of one, and who may do each step:

1. **Ask** (``request``): anyone who can open the space (``spaces.open``, as them, now).
   The dictionary as it stands is frozen into the export, with the opportunities they
   hold -- the build reads exactly those, no more.
2. **Build** (``build``, a Celery task): reads the cached visits in pages through the Data
   page's locked reader (``engine``), so there is no row cap. First it refuses any field
   the dictionary does not know ("refuse untagged"): someone must scan and review it.
   Then every value goes through its entry's setting (``labs.deid.transforms``), and an
   entry nobody reviewed is dropped ("drop unless allowed").
3. **Check** (during the build): rows out equal visits in, a leak scan of every cell
   against the source's own names, usernames, phones, ids and GPS points, value ranges,
   duplicates, visits with no case, and how many rows sit in a small group of cases on
   the fields that can single someone out together. A failed check blocks the export.
4. **Sign off** (``sign_off``): a manager of the space who did not ask for it, with a
   note, acknowledging any warnings. Or ``reject``.
5. **Download** (``open_file``): the requester or a manager, while they can still open
   the space, for ``EXPIRY`` after sign-off; then the file is deleted (``sweep``).

The file is a zip: the data, the dictionary of what is in it (meanings, settings, form
paths and builds -- never a sample value), the caveats, and the checks.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import logging
import statistics
import tempfile
import zipfile
from collections import Counter, defaultdict

from django.core.files import File
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from connect_labs.labs.deid import classify, fieldscan, leaks, transforms

from . import dictionary as dictionary_mod
from . import engine, fields, spaces
from .models import SpaceExport
from .scope import Opportunity

logger = logging.getLogger(__name__)

EXPIRY = dt.timedelta(days=7)
#: An export nobody signed off is withdrawn after this.
UNSIGNED_EXPIRY = dt.timedelta(days=14)
#: A build still running after this died with its worker; ``sweep`` fails it.
STUCK = dt.timedelta(hours=3)
PAGE = 1000
#: A row whose fields that can single someone out are shared by fewer cases than this is
#: counted by the small-group check.
SMALL_GROUP = 5

PASS, WARN, FAIL = "pass", "warn", "fail"

#: Columns whose values identify someone, learned (in memory) to scan the output for.
_NAME_COLUMNS = ("entity_name", "username")
_ID_COLUMNS = ("entity_id", "visit_id", "user_visit_id", "username")
_NAME_SEGMENTS = frozenset({"name", "names", "surname", "guardian"})


class ExportError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# --- asking for one -------------------------------------------------------------------


def _date(raw, what: str) -> dt.date | None:
    if raw in (None, ""):
        return None
    try:
        return dt.date.fromisoformat(str(raw))
    except ValueError as e:
        raise ExportError("bad_date", f"The {what} date must be YYYY-MM-DD.") from e


def request(opened: spaces.OpenSpace, *, user, date_from=None, date_to=None) -> SpaceExport:
    """Ask for an export of the space (anyone who can open it). Queues the build."""
    dictionary = dictionary_mod.for_space(opened)
    start, end = _date(date_from, "start"), _date(date_to, "end")
    if start and end and start > end:
        raise ExportError("bad_dates", "The start date is after the end date.")
    entries = dictionary_mod.snapshot(dictionary)
    if not any(dictionary_mod.effective(e) != transforms.DROP_SETTING for e in entries):
        raise ExportError(
            "nothing_allowed",
            "No field of this dictionary has been reviewed and allowed yet, so an export would be empty.",
        )
    if dictionary.scanned_at is None:
        raise ExportError(
            "not_scanned", "Scan the space's fields first, so the dictionary knows what its forms carry."
        )
    opps = opened.selection.opportunities
    unloaded = [s["opportunity_id"] for s in fields.cache_status(opps) if not s["cached_visits"] or not s["complete"]]
    if unloaded:
        raise ExportError(
            "not_loaded",
            f"Some of the space's visits are not loaded yet (opportunities {unloaded}). Open the space to load them.",
            status=409,
        )
    export = SpaceExport.objects.create(
        space=opened.space,
        requested_by=user,
        date_from=start,
        date_to=end,
        opportunities=[{"id": o.id, "name": o.name, "llo": o.llo} for o in opps],
        entries=entries,
    )
    from .tasks import build_space_export

    transaction.on_commit(lambda: build_space_export.delay(export.pk))
    return export


def for_space(opened: spaces.OpenSpace):
    return SpaceExport.objects.filter(space=opened.space).select_related("requested_by", "signed_off_by")


def _get(opened: spaces.OpenSpace, export_id) -> SpaceExport:
    try:
        pk = int(export_id)
    except (TypeError, ValueError) as e:
        raise ExportError("not_found", "There is no such export.", status=404) from e
    export = for_space(opened).filter(pk=pk).first()
    if export is None:
        raise ExportError("not_found", "There is no such export.", status=404)
    return export


def describe(export: SpaceExport, opened: spaces.OpenSpace) -> dict:
    exported = [e for e in export.entries if dictionary_mod.effective(e) != transforms.DROP_SETTING]
    me = opened.viewer_id
    return {
        "id": export.pk,
        "status": export.status,
        "status_label": export.get_status_display(),
        "requested_by": getattr(export.requested_by, "username", None),
        "created_at": export.created_at.isoformat(),
        "date_from": export.date_from.isoformat() if export.date_from else None,
        "date_to": export.date_to.isoformat() if export.date_to else None,
        "opportunities": [o["id"] for o in export.opportunities],
        "fields": len(exported),
        "source_rows": export.source_rows,
        "rows": export.rows,
        "checks": export.checks,
        "error": export.error,
        "built_at": export.built_at.isoformat() if export.built_at else None,
        "signed_off_by": getattr(export.signed_off_by, "username", None),
        "signed_off_at": export.signed_off_at.isoformat() if export.signed_off_at else None,
        "signoff_note": export.signoff_note,
        "expires_at": export.expires_at.isoformat() if export.expires_at else None,
        "can_sign_off": (
            export.status == SpaceExport.READY
            and opened.is_manager
            and me is not None
            and me != export.requested_by_id
        ),
        "can_download": _may_download(export, opened),
    }


# --- building one -----------------------------------------------------------------------


def _pages(opps: list[Opportunity], date_from, date_to):
    """Every visit of ``opps`` in the window, whole, in pages, through the locked reader."""
    cte_sql, base_params = engine.visits_cte(opps)
    where, params = _window(date_from, date_to)
    after = (-1, "")
    grants = engine.visit_grants(opps)
    while True:
        sql = (
            f"WITH {cte_sql}\nSELECT * FROM visits WHERE {where} "
            "AND (opportunity_id, visit_id) > (%s, %s) ORDER BY opportunity_id, visit_id LIMIT %s"
        )
        columns, rows = engine._execute(sql, base_params + params + [after[0], after[1], PAGE], grants)
        if not rows:
            return
        page = [dict(zip(columns, row)) for row in rows]
        yield page
        last = page[-1]
        after = (int(last["opportunity_id"]), str(last["visit_id"]))
        if len(rows) < PAGE:
            return


def _count(opps: list[Opportunity], date_from, date_to) -> int:
    """The visits in the window, counted in one statement: what the pages must add up to."""
    cte_sql, params = engine.visits_cte(opps)
    where, extra = _window(date_from, date_to)
    _cols, rows = engine._execute(
        f"WITH {cte_sql}\nSELECT COUNT(*) FROM visits WHERE {where}", params + extra, engine.visit_grants(opps)
    )
    return int(rows[0][0])


def _window(date_from, date_to) -> tuple[str, list]:
    where, params = ["TRUE"], []
    if date_from:
        where.append("visit_date::date >= %s")
        params.append(date_from)
    if date_to:
        where.append("visit_date::date <= %s")
        params.append(date_to)
    return " AND ".join(where), params


def _source_values(entry: dict, row: dict) -> list:
    """The values one form entry has on one visit: its first spelling the visit filled."""
    du = row.get("deliver_unit") or ""
    form = row.get("form_json") or {}
    for source in entry["sources"]:
        if source["deliver_unit"] and source["deliver_unit"] != du:
            continue
        found = [v for v in fieldscan.values_at(form, fieldscan.split(source["path"])) if v not in (None, "")]
        if found:
            return found
    return []


def _learns_words(entry: dict) -> bool:
    return entry["kind"] == classify.IDENTIFIER and bool(classify.leaf_segments(entry["label"]) & _NAME_SEGMENTS)


def _learns_values(entry: dict) -> bool:
    return entry["kind"] in (classify.IDENTIFIER, classify.CASE_ID, classify.ID)


def _guard(cell: str) -> str:
    """A spreadsheet must not run a cell as a formula."""
    return "'" + cell if cell[:1] in ("=", "+", "-", "@", "\t", "\r") and not _is_number(cell) else cell


def _is_number(text: str) -> bool:
    try:
        float(text)
    except ValueError:
        return False
    return True


class _Checks:
    """What the build measures as it writes, without keeping a value it writes."""

    def __init__(self, exported: list[dict]):
        self.exported = exported
        self.leak_hits: dict[str, Counter] = defaultdict(Counter)
        self.unreadable: Counter = Counter()
        self.numbers: dict[str, list[float]] = defaultdict(list)
        self.blank: Counter = Counter()
        self.row_hashes: set[int] = set()
        self.duplicate_rows = 0
        self.ids_seen: set[str] = set()
        self.duplicate_ids = 0
        self.no_case = 0
        self.groups: dict[tuple, set] = defaultdict(set)
        self.group_rows: Counter = Counter()


def build(export_id: int) -> None:
    """The task: build, check and store one export.

    Claimed once: only a QUEUED export moves to BUILDING, under a row lock, so a message
    the broker delivers twice builds once. A build that dies part-way stays BUILDING until
    ``sweep`` fails it (``STUCK``); one that fails leaves no file behind."""
    with transaction.atomic():
        export = SpaceExport.objects.select_for_update().filter(pk=export_id).first()
        if export is None or export.status != SpaceExport.QUEUED:
            return
        export.status, export.error = SpaceExport.BUILDING, ""
        export.save(update_fields=["status", "error"])
    export = SpaceExport.objects.select_related("space__dictionary").get(pk=export_id)
    try:
        _build(export)
    except Exception as e:  # noqa: BLE001 -- recorded on the export, never lost
        logger.exception("labsdata export %s failed", export.pk)
        _delete_file(export)
        export.status, export.error = SpaceExport.FAILED, f"{type(e).__name__}: {str(e)[:500]}"
        export.save(update_fields=["status", "error", "file_name"])


def _build(export: SpaceExport) -> None:
    opps = [
        Opportunity(id=int(o["id"]), name=o.get("name") or "", llo=o.get("llo") or "") for o in export.opportunities
    ]
    entries = export.entries
    dictionary = export.space.dictionary
    key = bytes(dictionary.secret)
    exported = [e for e in entries if dictionary_mod.effective(e) != transforms.DROP_SETTING]

    # Pass 1: every field the visits carry is one the dictionary knows (refuse untagged),
    # and what identifies the people in them, held in memory to scan the output for.
    scanner = leaks.LeakScanner()
    seen_sources: set[tuple[str, str]] = set()
    source_rows = 0
    word_entries = [e for e in entries if not e["column"] and _learns_words(e)]
    value_entries = [e for e in entries if not e["column"] and _learns_values(e)]
    for page in _pages(opps, export.date_from, export.date_to):
        for row in page:
            source_rows += 1
            du = row.get("deliver_unit") or ""
            for path, value in fieldscan.walk(row.get("form_json") or {}):
                if value not in (None, ""):
                    seen_sources.add((fieldscan.dotted(path), du))
            for column in _NAME_COLUMNS:
                scanner.learn(row.get(column))
            for column in _ID_COLUMNS:
                scanner.learn_value(row.get(column))
            for e in word_entries:
                for v in _source_values(e, row):
                    scanner.learn(v)
            for e in value_entries:
                for v in _source_values(e, row):
                    scanner.learn_value(v)
    export.source_rows = _count(opps, export.date_from, export.date_to)
    if export.source_rows != source_rows:
        raise RuntimeError(f"read {source_rows} visits page by page but {export.source_rows} are in the window")
    missing = dictionary_mod.untagged(dictionary, seen_sources)
    if missing:
        export.status = SpaceExport.BLOCKED
        export.checks = {
            "checks": [
                {
                    "name": "untagged",
                    "status": FAIL,
                    "summary": f"{len(missing)} field(s) in these visits are not in the dictionary. Scan the space "
                    "and review them, then ask again.",
                    "detail": {"fields": missing[:200]},
                }
            ]
        }
        export.built_at = timezone.now()
        export.save(update_fields=["status", "checks", "source_rows", "built_at"])
        return

    # Pass 2: write the data, checking each cell as it goes.
    terms = frozenset(scanner.words)
    checks = _Checks(exported)
    header = [e["label"] for e in exported]
    quasi = [i for i, e in enumerate(exported) if e["quasi"]]
    case_out = next((i for i, e in enumerate(exported) if e["column"] == "entity_id"), None)
    id_out = next((i for i, e in enumerate(exported) if e["column"] in ("visit_id", "user_visit_id")), None)
    rows = 0
    with tempfile.TemporaryFile(mode="w+", newline="", encoding="utf-8") as data:
        writer = csv.writer(data)
        writer.writerow(header)
        for page in _pages(opps, export.date_from, export.date_to):
            for row in page:
                case = row.get("entity_id")
                out = []
                for e in exported:
                    raw = [row.get(e["column"])] if e["column"] else _source_values(e, row)
                    cells = []
                    for value in raw:
                        if value in (None, ""):
                            continue
                        if isinstance(value, dict | list):
                            value = json.dumps(value, sort_keys=True)
                        done = transforms.apply(e["setting"], e["params"], value, key=key, case=case, terms=terms)
                        if done is transforms.DROP:
                            checks.unreadable[e["label"]] += 1
                            continue
                        cells.append(str(done))
                    cell = " | ".join(cells)
                    out.append(cell)
                    _check_cell(checks, scanner, e, cells)
                _check_row(checks, out, case, rows, quasi, case_out, id_out)
                writer.writerow([_guard(c) for c in out])
                rows += 1
        export.rows = rows
        export.checks = {"checks": _results(checks, source_rows, rows, exported, case_out, id_out)}
        failed = any(c["status"] == FAIL for c in export.checks["checks"])
        export.status = SpaceExport.BLOCKED if failed else SpaceExport.READY
        export.built_at = timezone.now()
        if not failed:
            data.seek(0)
            export.file_name = _store(export, data, exported)
    export.save(update_fields=["status", "checks", "rows", "source_rows", "built_at", "file_name"])


def _check_cell(checks: _Checks, scanner: leaks.LeakScanner, entry: dict, cells: list[str]) -> None:
    """Scan every value of one cell (a repeat group has several), each on its own, so a
    learned id in a list is found as surely as one alone."""
    label = entry["label"]
    if not cells:
        checks.blank[label] += 1
        return
    hits = set().union(*(scanner.scan(c) for c in cells))
    if entry["setting"] == transforms.PSEUDONYMISE:
        hits.discard("known_name")  # a code is letters; it can spell a short name by chance
    for hit in hits:
        checks.leak_hits[label][hit] += 1
    if entry["kind"] == classify.NUMBER and entry["setting"] in (transforms.KEEP, transforms.ROUND):
        for part in cells:
            if _is_number(part):
                checks.numbers[label].append(float(part))


def _check_row(checks: _Checks, out: list[str], case, row_number: int, quasi: list[int], case_out, id_out) -> None:
    h = hash(tuple(out))
    if h in checks.row_hashes:
        checks.duplicate_rows += 1
    checks.row_hashes.add(h)
    if id_out is not None and out[id_out]:
        if out[id_out] in checks.ids_seen:
            checks.duplicate_ids += 1
        checks.ids_seen.add(out[id_out])
    if case_out is not None and not out[case_out]:
        checks.no_case += 1
    if quasi:
        group = tuple(_coarse(out[i]) for i in quasi)
        checks.groups[group].add(case if case not in (None, "") else f"row {row_number}")
        checks.group_rows[group] += 1


def _coarse(cell: str) -> str:
    """A date cut to its month, for grouping: the small-group check asks about months."""
    date = classify.parse_iso_date(cell)
    return date.strftime("%Y-%m") if date else cell


#: Leak kinds that fail the export wherever they appear; a known name word in a column of
#: categories or numbers is more likely a place or a common word, so it warns.
_ALWAYS_FAIL = {"phone", "email", "gps", "case_id", "known_value"}


def _results(checks: _Checks, source_rows: int, rows: int, exported, case_out, id_out) -> list[dict]:
    out = []
    out.append(
        {
            "name": "row_count",
            "status": PASS if rows == source_rows else FAIL,
            "summary": f"{rows} rows written for {source_rows} visits in the window.",
            "detail": {"rows": rows, "source_rows": source_rows},
        }
    )
    kinds = {e["label"]: e["kind"] for e in exported}
    failing, warning = {}, {}
    for label, hits in checks.leak_hits.items():
        hard = {k: n for k, n in hits.items() if k in _ALWAYS_FAIL}
        soft = {k: n for k, n in hits.items() if k not in _ALWAYS_FAIL}
        if soft and kinds.get(label) not in (classify.CATEGORICAL, classify.NUMBER, classify.DATE):
            hard.update(soft)
            soft = {}
        if hard:
            failing[label] = hard
        if soft:
            warning[label] = soft
    if failing:
        status, summary = FAIL, f"Identifying values found in {len(failing)} column(s). Change their settings."
    elif warning:
        status, summary = WARN, f"Words that match a person's name appear in {len(warning)} column(s) of categories."
    else:
        status, summary = PASS, "No name, username, phone, email, id or GPS point from the source found."
    out.append(
        {"name": "pii_scan", "status": status, "summary": summary, "detail": {"fail": failing, "warn": warning}}
    )

    ranges = {}
    odd = 0
    # Counts only: a minimum or maximum is a real value (an age of 103 names someone), and
    # the checks are shown to everyone who can open the space and through MCP.
    for label, values in checks.numbers.items():
        if len(values) < 4:
            ranges[label] = {"n": len(values)}
            continue
        q = statistics.quantiles(values, n=4)
        spread = q[2] - q[0]
        lo, hi = q[0] - 3 * spread, q[2] + 3 * spread
        far = sum(1 for v in values if v < lo or v > hi)
        odd += far
        ranges[label] = {"n": len(values), "far_out": far}
    out.append(
        {
            "name": "ranges",
            "status": WARN if odd else PASS,
            "summary": (
                f"{odd} number(s) lie far outside their column's usual range: look before sharing."
                if odd
                else "Every number lies within its column's usual range."
            ),
            "detail": {"columns": ranges, "unreadable": dict(checks.unreadable)},
        }
    )
    dup = checks.duplicate_ids if id_out is not None else checks.duplicate_rows
    out.append(
        {
            "name": "duplicates",
            "status": WARN if dup else PASS,
            "summary": (
                f"{dup} duplicate {'visit codes' if id_out is not None else 'rows'}." if dup else "No duplicates."
            ),
            "detail": {"duplicate_ids": checks.duplicate_ids, "duplicate_rows": checks.duplicate_rows},
        }
    )
    out.append(
        {
            "name": "orphans",
            "status": WARN if case_out is not None and checks.no_case else PASS,
            "summary": (
                "No case column exported, so visits cannot be grouped by case."
                if case_out is None
                else (f"{checks.no_case} visit(s) belong to no case." if checks.no_case else "Every visit has a case.")
            ),
            "detail": {"no_case": checks.no_case, "case_column": case_out is not None},
        }
    )
    small = sum(n for g, n in checks.group_rows.items() if len(checks.groups[g]) < SMALL_GROUP)
    quasi_cols = [e["label"] for e in exported if e["quasi"]]
    out.append(
        {
            "name": "small_groups",
            "status": WARN if small else PASS,
            "summary": (
                f"{small} of {rows} rows share their {', '.join(quasi_cols)} with fewer than {SMALL_GROUP} cases: "
                "together these fields could single someone out."
                if small
                else (
                    f"Every combination of {', '.join(quasi_cols)} covers {SMALL_GROUP} or more cases."
                    if quasi_cols
                    else "No field that can single someone out in combination is exported."
                )
            ),
            "detail": {"rows_in_small_groups": small, "fields": quasi_cols, "threshold": SMALL_GROUP},
        }
    )
    return out


# --- the file -------------------------------------------------------------------------------


def _dictionary_csv(exported: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["column", "meaning", "level", "setting", "params", "kind", "sources"])
    for e in exported:
        sources = "; ".join(
            f"{s['path']}{' @ ' + s['deliver_unit'] if s['deliver_unit'] else ''}"
            f"{' (builds ' + ','.join(str(b) for b in s['builds']) + ')' if s['builds'] else ''}"
            for s in e["sources"]
        )
        writer.writerow(
            [
                e["label"],
                e["meaning"],
                e["level"],
                transforms.SETTING_LABELS.get(e["setting"], e["setting"]),
                json.dumps(e["params"]) if e["params"] else "",
                e["kind"],
                sources or (f"visits column {e['column']}" if e["column"] else ""),
            ]
        )
    return buf.getvalue()


def _readme(export: SpaceExport, exported: list[dict]) -> str:
    dropped = len(export.entries) - len(exported)
    lines = [
        f"# De-identified export {export.pk} of data space {export.space_id}",
        "",
        f"- Visits from {export.date_from or 'the start'} to {export.date_to or 'the latest loaded'}, "
        f"opportunities {', '.join(str(o['id']) for o in export.opportunities)}.",
        f"- {export.rows} rows, {len(exported)} columns; {dropped} field(s) of the dictionary were dropped.",
        f"- Asked for by {getattr(export.requested_by, 'username', 'unknown')} on {export.created_at:%Y-%m-%d}.",
        "",
        "## What was done to the data",
        "",
        "- Every column is listed in dictionary.csv with its meaning and what was done to it.",
        "- Codes (pseudonyms) are keyed one-way: the same case has the same code in every export of this "
        "data space, and a code cannot be turned back into an id.",
        "- Dates shifted per case keep the intervals within a case and lose the calendar date.",
        "- Fields nobody reviewed were dropped, whatever the suggestion was.",
        "",
        "## Caveats",
        "",
        "- Free text scrubbing is a best guess: a note can still name a person in a way no list knows.",
        "- Removing names does not stop re-identification from a mix of fields such as dates, place and "
        "worker. checks.json says how many rows sit in a small group of cases on those fields.",
        "- A setting only protects fields someone mapped; this export refused to run on any field the "
        "dictionary did not know.",
        "- Do not share this file beyond the people the sign-off note names.",
    ]
    return "\n".join(lines) + "\n"


def _store(export: SpaceExport, data, exported: list[dict]) -> str:
    name = f"labsdata/exports/{export.pk}/{slugify(export.space.name)[:60] or 'space'}-export-{export.pk}.zip"
    with tempfile.TemporaryFile() as raw:
        with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as z:
            with z.open("data.csv", "w") as out:
                while chunk := data.read(1 << 20):
                    out.write(chunk.encode("utf-8"))
            z.writestr("dictionary.csv", _dictionary_csv(exported))
            z.writestr("README.md", _readme(export, exported))
            z.writestr("checks.json", json.dumps(export.checks, indent=2))
        raw.seek(0)
        return default_storage.save(name, File(raw, name=name.rsplit("/", 1)[-1]))


# --- sign-off and download ------------------------------------------------------------------


def sign_off(opened: spaces.OpenSpace, export_id, *, user, note: str, acknowledge_warnings: bool) -> SpaceExport:
    """A manager who did not ask for it signs it off, with a note saying who it is for."""
    spaces.require_manager(opened)
    export = _get(opened, export_id)
    if export.status != SpaceExport.READY:
        raise ExportError("not_ready", "Only a built export that passed its checks can be signed off.", status=409)
    if export.requested_by_id == opened.viewer_id:
        raise ExportError("own_export", "Someone other than the person who asked for an export must sign it off.", 403)
    note = (note or "").strip()
    if not note:
        raise ExportError("note_required", "Say who this export is for and why, in the sign-off note.")
    warned = [c["name"] for c in export.checks.get("checks", []) if c["status"] == WARN]
    if warned and not acknowledge_warnings:
        raise ExportError("warnings", f"Acknowledge the warnings first: {', '.join(warned)}.", status=409)
    export.status = SpaceExport.APPROVED
    export.signed_off_by, export.signed_off_at = user, timezone.now()
    export.signoff_note = note[:4000]
    export.expires_at = timezone.now() + EXPIRY
    export.save(update_fields=["status", "signed_off_by", "signed_off_at", "signoff_note", "expires_at"])
    return export


def reject(opened: spaces.OpenSpace, export_id, *, user, note: str) -> SpaceExport:
    spaces.require_manager(opened)
    export = _get(opened, export_id)
    if export.status not in (SpaceExport.READY, SpaceExport.BLOCKED, SpaceExport.APPROVED):
        raise ExportError("not_open", "This export cannot be rejected now.", status=409)
    _delete_file(export)
    export.status = SpaceExport.REJECTED
    export.signed_off_by, export.signed_off_at = user, timezone.now()
    export.signoff_note = (note or "").strip()[:4000]
    export.save(update_fields=["status", "signed_off_by", "signed_off_at", "signoff_note", "file_name"])
    return export


def _may_download(export: SpaceExport, opened: spaces.OpenSpace) -> bool:
    # Opening the space checks what it reads NOW; a manager may since have taken an
    # opportunity out of it. The file holds every opportunity it was built from, so each
    # must still be one this space reads for this person.
    held = set(opened.selection.ids)
    return (
        all(int(o["id"]) in held for o in export.opportunities)
        and export.status == SpaceExport.APPROVED
        and bool(export.file_name)
        and export.expires_at is not None
        and export.expires_at > timezone.now()
        and (opened.is_manager or (opened.viewer_id is not None and opened.viewer_id == export.requested_by_id))
    )


def open_file(opened: spaces.OpenSpace, export_id):
    """The signed-off file, for the requester or a manager, until it expires."""
    export = _get(opened, export_id)
    if not _may_download(export, opened):
        raise ExportError(
            "not_downloadable",
            "Only a signed-off export, before it expires, can be downloaded, by whoever asked for it or a manager.",
            status=403,
        )
    return export, default_storage.open(export.file_name, "rb")


def _delete_file(export: SpaceExport) -> None:
    if export.file_name:
        try:
            default_storage.delete(export.file_name)
        except Exception:  # noqa: BLE001 -- a missing file is already deleted
            logger.warning("labsdata export %s: could not delete %s", export.pk, export.file_name, exc_info=True)
        export.file_name = ""


def sweep(now=None) -> int:
    """Delete the files of expired exports, withdraw unsigned ones, fail builds that died,
    and delete any file an export that is not open still holds (hourly)."""
    now = now or timezone.now()
    n = 0
    stale = SpaceExport.objects.filter(status=SpaceExport.APPROVED, expires_at__lte=now) | SpaceExport.objects.filter(
        status=SpaceExport.READY, built_at__lte=now - UNSIGNED_EXPIRY
    )
    for export in stale:
        _delete_file(export)
        export.status = SpaceExport.EXPIRED
        export.save(update_fields=["status", "file_name"])
        n += 1
    for export in SpaceExport.objects.filter(status=SpaceExport.BUILDING, created_at__lte=now - STUCK):
        _delete_file(export)
        export.status, export.error = SpaceExport.FAILED, "The build stopped before it finished. Ask again."
        export.save(update_fields=["status", "error", "file_name"])
        n += 1
    for export in SpaceExport.objects.exclude(status__in=[SpaceExport.APPROVED, SpaceExport.READY]).exclude(
        file_name=""
    ):
        _delete_file(export)
        export.save(update_fields=["file_name"])
        n += 1
    return n
