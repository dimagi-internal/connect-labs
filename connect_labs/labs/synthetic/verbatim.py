"""Copy named form fields VERBATIM from a real opportunity into its clone (connect-labs#2150).

Every verbatim copy is a re-export of real data, whatever the caller says about the
fields: a borehole's name is harmless in one programme and a household's GPS is
identifying in another. So the rules are about WHO may copy, never WHICH fields are
"safe" -- there is no caller-asserted non-PII flag, by design:

1. The copy happens only if the caller can read the source's raw
   ``/export/opportunity/<id>/user_visits/`` rows with their OWN token, at copy time.
   The fetch is the check; Connect enforces it, and labs cannot be argued around it.
   No access refuses the verbatim part only -- the statistical clone still runs.
2. The copy is never more visible than the source: its creator only by default
   (``SyntheticOpportunity.verbatim_paths`` switches the access model), and each
   person added later must pass the same raw-visit check (``add_viewers_denied_reason``).
   Widening by domain is refused.
3. Each copy is audited as the bulk-PHI EXPORT it is (``record_copy``).
4. The opp is never marked generated, and reports say it holds real values
   (``real_value_sources``).
5. The values go into the generated fixture only. The source rows are held in memory
   between the gate and the fixture write; the profile bundle never sees them.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .dump import _fetch_endpoint

#: A dotted path into a visit's ``form_json`` (``form.waterpoint_name``).
_PATH_RE = re.compile(r"^[A-Za-z0-9_\-]+(\.[A-Za-z0-9_\-]+)*$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


@dataclass
class VerbatimSource:
    """One source's rows, read through the gate, waiting to be copied into its clone.

    In memory only: nothing here is ever written to a profile bundle.
    """

    source_opportunity_id: int
    paths: list[str]
    rows: list[dict] = field(repr=False)
    caller: Any = None


def normalise_paths(raw) -> list[str]:
    """Validate and de-duplicate the requested ``form_json`` paths. Raises ValueError."""
    out: list[str] = []
    for value in raw or []:
        path = (value or "").strip() if isinstance(value, str) else ""
        if not _PATH_RE.match(path):
            raise ValueError(f"not a form_json path: {value!r} (use dotted keys, e.g. 'form.village')")
        if path not in out:
            out.append(path)
    return out


def _user_visits_endpoint(opportunity_id: int) -> str:
    return f"/export/opportunity/{int(opportunity_id)}/user_visits/"


def read_source_rows(
    source_opportunity_id: int,
    *,
    paths: list[str],
    base_url: str,
    oauth_token: str,
    caller,
    restricted: bool = False,
) -> tuple[VerbatimSource | None, str | None]:
    """The gate: read the source's raw visit rows with the CALLER's token.

    Returns ``(source, None)`` when the caller can read them, else ``(None, reason)``.
    A refused read is recorded as an attempted export, as any errored export is.
    """
    if restricted:
        return None, (
            "this session may not see user visit data, so it may not copy real values; "
            "the statistical clone still ran"
        )
    try:
        rows = _fetch_endpoint(base_url, source_opportunity_id, "user_visits", oauth_token)
    except Exception as exc:  # noqa: BLE001 -- any failure to read is a refusal, never a crash
        _audit(caller, source_opportunity_id, paths, 0, error=type(exc).__name__)
        return None, (
            f"your Connect account could not read the raw visits of opportunity {source_opportunity_id} "
            f"({type(exc).__name__}); real values are copied only for people who can read them "
            "there. The statistical clone still ran."
        )
    if not isinstance(rows, list) or not rows:
        return None, f"opportunity {source_opportunity_id} returned no visit rows to copy from"
    return VerbatimSource(source_opportunity_id, list(paths), rows, caller), None


_MISSING = object()


def _get(obj, path: str):
    for key in path.split("."):
        if not isinstance(obj, dict) or key not in obj:
            return _MISSING
        obj = obj[key]
    return obj


def _set(obj: dict, path: str, value) -> None:
    keys = path.split(".")
    for key in keys[:-1]:
        nxt = obj.get(key)
        if not isinstance(nxt, dict):
            nxt = obj[key] = {}
        obj = nxt
    obj[keys[-1]] = value


def _by_worker(visits: list[dict], key) -> dict[str, list[dict]]:
    """Group visits by worker, each worker's visits in date order (ties keep list order)."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for visit in visits:
        worker = key(visit)
        if worker:
            grouped[worker].append(visit)
    for worker, items in grouped.items():
        grouped[worker] = sorted(items, key=lambda v: str(v.get("visit_date") or ""))
    return grouped


def overlay(generated_visits: list[dict], source_rows: list[dict], paths: list[str]) -> int:
    """Copy each path's source value onto its paired generated visit. Returns visits written.

    Pairing is deterministic: a source worker maps to the clone persona the profiler
    gave it (``persona_ids_by_username``: same volume ranking), and within a worker
    the n-th source visit by date pairs with the n-th generated visit by date. A path
    the source row does not carry is left as generated.
    """
    from .generator.fixtures.profiler import persona_ids_by_username

    source_by_user: dict[str, list[dict]] = defaultdict(list)
    for row in source_rows:
        if row.get("username"):
            source_by_user[row["username"]].append(row)
    persona_of = persona_ids_by_username(source_by_user)
    source = _by_worker(source_rows, lambda r: persona_of.get(r.get("username")))
    generated = _by_worker(generated_visits, lambda v: v.get("username"))

    written = 0
    for persona, clone_visits in generated.items():
        for src, visit in zip(source.get(persona) or [], clone_visits):
            copied = False
            form_json = visit.setdefault("form_json", {})
            for path in paths:
                value = _get(src.get("form_json") or {}, path)
                if value is _MISSING:
                    continue
                _set(form_json, path, value)
                copied = True
            written += copied
    return written


def _audit(caller, source_opportunity_id: int, paths: list[str], rows: int, *, error=None, clone_id=None) -> None:
    from connect_labs.labs.integrations.connect.export_client import _record_export_audit

    metadata = {"verbatim_copy": True, "verbatim_paths": list(paths)}
    if clone_id is not None:
        metadata["copied_into_opportunity_id"] = int(clone_id)
    _record_export_audit(
        _user_visits_endpoint(source_opportunity_id), rows, error, user=caller, extra_metadata=metadata
    )


def record_copy(verbatim: VerbatimSource, *, clone_opportunity_id: int, rows_copied: int) -> None:
    """Audit one verbatim copy as the bulk-PHI EXPORT it is: caller, source, fields, rows."""
    _audit(
        verbatim.caller,
        verbatim.source_opportunity_id,
        verbatim.paths,
        rows_copied,
        clone_id=clone_opportunity_id,
    )


def raw_visits_readable_reason(user, source_opportunity_id: int, *, base_url: str) -> str | None:
    """Why ``user`` cannot read the source's raw visit rows with their own token, or None.

    Reads one row with that person's stored Connect token -- the same check the copy
    itself passed. The read goes through the audited export client.
    """
    from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
    from connect_labs.labs.integrations.connect.export_client import ExportAPIClient

    try:
        token = get_valid_access_token(user)
    except ConnectTokenError:
        return "has no Connect sign-in in labs to check with"
    try:
        with ExportAPIClient(base_url, token) as client:
            for _page in client.paginate(
                _user_visits_endpoint(source_opportunity_id), params={"page_size": 1}, partial_ok=True
            ):
                break
    except Exception as exc:  # noqa: BLE001 -- any failure to read is a refusal
        return f"cannot read the raw visits of opportunity {source_opportunity_id} ({type(exc).__name__})"
    return None


def add_viewers_denied_reason(row, caller, entries: list[str], *, base_url: str) -> tuple[list[str], str | None]:
    """Check a request to widen an opp carrying verbatim values. Returns ``(emails, reason)``.

    Only the creator may widen it, only by individual address, and every person named
    must be a labs user who can read the source's raw visits themselves. All or nothing.
    """
    from connect_labs.users.models import User

    if not (row.created_by_id and row.created_by_id == getattr(caller, "id", None)):
        return [], "only the person who copied real values into this opp may change who can see it"
    emails: list[str] = []
    for value in entries:
        entry = (value or "").strip().lower()
        if entry.startswith("@") or "@" not in entry:
            return [], (
                f"{value!r} is a domain; this opp holds real values copied from opportunity "
                f"{row.cloned_from_opportunity_id}, so it can be widened only by individual address, "
                "each of whom must be able to read that opportunity's raw visits"
            )
        if not _EMAIL_RE.match(entry):
            return [], f"not an email address: {value!r}"
        if entry not in emails:
            emails.append(entry)
    for email in emails:
        person = User.objects.filter(email__iexact=email).first()
        if person is None:
            return [], f"{email} has no labs account, so their access to the source cannot be checked"
        reason = raw_visits_readable_reason(person, row.cloned_from_opportunity_id, base_url=base_url)
        if reason:
            return [], f"{email} {reason}; only people who can read the source's raw visits may see this copy"
    return emails, None


def folder_denied_reason(gdrive_folder_id) -> str | None:
    """Why a folder may not be (re)registered under another opp, or None.

    A folder holding verbatim values is served only by the opp the copy made; pointing
    a second opp at it would hand the real values to that opp's audience.
    """
    from .models import SyntheticOpportunity

    if not gdrive_folder_id:
        return None
    holder = (
        SyntheticOpportunity.objects.filter(gdrive_folder_id=gdrive_folder_id)
        .exclude(verbatim_paths=[])
        .values_list("opportunity_id", flat=True)
        .first()
    )
    if holder is None:
        return None
    return (
        f"that folder holds real values copied into opportunity {holder}; it cannot be served "
        "by another opportunity"
    )


def real_value_sources(opportunity_ids) -> list[dict]:
    """Every opp in scope that carries verbatim values, with its source and fields.

    What a report shows instead of claiming the data is synthetic.
    """
    from .models import SyntheticOpportunity

    ids = set()
    for value in opportunity_ids or []:
        try:
            ids.add(int(value))
        except (TypeError, ValueError):
            continue
    rows = SyntheticOpportunity.objects.filter(opportunity_id__in=ids, enabled=True).exclude(verbatim_paths=[])
    return [
        {
            "opportunity_id": row.opportunity_id,
            "source_opportunity_id": row.cloned_from_opportunity_id,
            "fields": list(row.verbatim_paths),
        }
        for row in sorted(rows, key=lambda r: r.opportunity_id)
    ]
