"""
Google Drive fetcher for the analysis pipeline.

Reads tabular files from Drive with the server's service account and normalizes
each row to the Connect visit-dict shape, so FieldComputation paths work as they
do for every other source. A row's cells sit under form_json["row"], so field
paths read "row.<column>"; the file it came from sits under form_json["file"]
("file.name", "file.id", "file.modified").

Supported files: CSV (any text/csv-ish file), Google Sheets (first sheet, exported
as CSV) and JSON (an array of objects, or an object holding one under "rows" or
"data"). A source names one file (file_id) or a folder (folder_id) plus an
optional fnmatch file_pattern; a folder's matching files are concatenated.

Authorization
-------------
The service account can read everything it has been shared on, which includes
labs-internal folders (synthetic fixtures cloned from production). An unchecked
source would let anyone who can edit a pipeline read those by id. So a source is
honoured only when it carries an `authorization` the server stamped when a Dimagi
staff member saved it (``authorize_gdrive_source``): an HMAC over the Drive ids,
the pattern and the opportunity, keyed by SECRET_KEY. The fetcher re-verifies it
on every read -- fresh or cached -- so a source smuggled in through any other save
path, copied to another opportunity, or edited after authorization is refused
rather than read.

A stamp names an opportunity; it is not a pass to it. Every read also requires
the caller (the Connect token the pipeline runs with) to be a member of that
opportunity, because Drive -- unlike Connect, HQ or OCS -- never checks who is
asking, and labs has paths that run a pipeline for an opportunity other than the
caller's own (clones, multi-opp fan-out).

A staff save only stamps a target that the save itself sets or changes (or one
the caller explicitly asks to authorize), so a staff member editing an unrelated
field cannot silently authorize a target someone else planted.
"""

from __future__ import annotations

import csv
import fnmatch
import hashlib
import hmac
import io
import json
import logging
from datetime import datetime

from django.conf import settings

from connect_labs.labs.analysis.config import DataSourceConfig

logger = logging.getLogger(__name__)

GOOGLE_SHEET = "application/vnd.google-apps.spreadsheet"
GOOGLE_FOLDER = "application/vnd.google-apps.folder"
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_FILES = 100
MAX_ROWS = 500_000

_SIGNED_FIELDS = ("file_id", "folder_id", "file_pattern")


class GDriveSourceError(ValueError):
    """A gdrive source that cannot be read: unauthorized, unreachable or unparseable.

    Every message starts with ``Google Drive source:`` so a caller that only sees
    the stringified error (a pipeline event, an MCP preview) can still tell it
    apart from an engine failure.
    """

    PREFIX = "Google Drive source: "

    def __init__(self, message: str):
        super().__init__(message if message.startswith(self.PREFIX) else self.PREFIX + message)


# --------------------------------------------------------------------------- authorization


def _signature(source: dict, opportunity_id: int, authorized_by: str) -> str:
    payload = json.dumps(
        {
            **{k: source.get(k) or "" for k in _SIGNED_FIELDS},
            "opportunity_id": int(opportunity_id),
            "authorized_by": authorized_by,
        },
        sort_keys=True,
    )
    key = hashlib.sha256(f"labs-gdrive-source:{settings.SECRET_KEY}".encode()).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def authorize_gdrive_source(source: dict, opportunity_id: int, user) -> dict:
    """Return `source` stamped with an authorization for `opportunity_id`.

    Raises GDriveSourceError unless `user` is Dimagi staff. Callers that save a
    pipeline schema run every gdrive data_source through this.
    """
    from connect_labs.utils.dimagi_user import is_dimagi_user

    if not is_dimagi_user(user):
        raise GDriveSourceError(
            "Only Dimagi staff can point a pipeline at Google Drive: the server's service account "
            "can read files the person editing the pipeline may not be allowed to see."
        )
    by = getattr(user, "email", "") or getattr(user, "username", "")
    clean = {k: v for k, v in source.items() if k != "authorization"}
    clean["authorization"] = {
        "opportunity_id": int(opportunity_id),
        "authorized_by": by,
        "signature": _signature(clean, opportunity_id, by),
    }
    return clean


def _target(source: dict | None) -> tuple | None:
    if not source or source.get("type") != "gdrive":
        return None
    return tuple(source.get(k) or "" for k in _SIGNED_FIELDS)


def _stamp_is_valid(source: dict, opportunity_id: int) -> bool:
    try:
        verify_gdrive_authorization(
            DataSourceConfig(
                type="gdrive",
                **{k: source.get(k) or "" for k in _SIGNED_FIELDS},
                authorization=source.get("authorization") or {},
            ),
            opportunity_id,
        )
        return True
    except (GDriveSourceError, ValueError):
        return False


def authorize_schema_drive_source(
    schema: dict, opportunity_id: int, user, previous_schema: dict | None = None, force: bool = False
) -> dict:
    """Settle a pipeline schema's gdrive authorization before it is saved.

    - No gdrive source: returned untouched.
    - A stamp that still verifies for this opportunity: kept.
    - Same target as ``previous_schema`` (the stored schema): the stored stamp is
      carried over when it verifies, and otherwise NOT created -- a staff member
      re-saving to edit a field must not authorize a target someone else stored.
    - A new or changed target, or ``force``: Dimagi staff only, stamped.
    """
    if not isinstance(schema, dict):
        return schema
    source = schema.get("data_source") or {}
    if source.get("type") != "gdrive":
        return schema
    # Shape first (raises ValueError): a malformed source is rejected, never stamped.
    DataSourceConfig(**{k: v for k, v in source.items() if k in DataSourceConfig.__dataclass_fields__})
    if _stamp_is_valid(source, opportunity_id):
        return schema
    previous = (previous_schema or {}).get("data_source") if isinstance(previous_schema, dict) else None
    if not force and previous and _target(previous) == _target(source):
        if _stamp_is_valid(previous, opportunity_id):
            return {**schema, "data_source": {**source, "authorization": previous["authorization"]}}
        return schema
    return {**schema, "data_source": authorize_gdrive_source(source, opportunity_id, user)}


def _caller_opportunity_ids(request, access_token: str | None) -> set[int] | None:
    """Opportunity ids the caller is a member of, or None when no caller can be resolved."""
    ids: set = set()
    user = getattr(request, "user", None) if request is not None else None
    session = getattr(request, "session", None) if request is not None else None
    if session is not None and session.get("labs_oauth"):
        from connect_labs.labs.context import get_org_data

        ids = {o.get("id") for o in get_org_data(request).get("opportunities") or []}
    elif access_token:
        from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

        data = fetch_user_organization_data(access_token)
        if data is None:
            return None
        ids = {o.get("id") for o in data.get("opportunities") or []}
        if user is None:
            from connect_labs.labs.integrations.ocs.ocs_tokens import current_mcp_caller

            user = current_mcp_caller()
        if user is not None:
            from connect_labs.labs.access.scopes import labs_only_opportunity_ids

            ids |= labs_only_opportunity_ids(user)
    else:
        return None
    out = set()
    for i in ids:
        try:
            out.add(int(i))
        except (TypeError, ValueError):
            continue
    return out


def check_gdrive_access(data_source: DataSourceConfig, opportunity_id: int, request=None, access_token=None) -> None:
    """The gate every read of a gdrive pipeline passes: a valid stamp for this
    opportunity AND a caller who is a member of it. Raises GDriveSourceError."""
    verify_gdrive_authorization(data_source, opportunity_id)
    ids = _caller_opportunity_ids(request, access_token)
    if ids is None:
        raise GDriveSourceError("cannot confirm who is reading it, so it is not read. Sign in and retry.")
    if int(opportunity_id) not in ids:
        raise GDriveSourceError(f"you are not a member of opportunity {opportunity_id}, which this source belongs to.")


def verify_gdrive_authorization(data_source: DataSourceConfig, opportunity_id: int) -> None:
    """Raise GDriveSourceError unless the source carries a valid authorization for this opportunity."""
    auth = data_source.authorization if isinstance(data_source.authorization, dict) else {}
    sig, by, auth_opp = auth.get("signature"), auth.get("authorized_by", ""), auth.get("opportunity_id")
    if not isinstance(sig, str) or not sig or not isinstance(by, str):
        raise GDriveSourceError(
            "This Google Drive source has not been authorized. A Dimagi staff member must save the "
            "pipeline schema (e.g. with the pipeline_update_schema MCP tool) to authorize it."
        )
    source = {k: getattr(data_source, k) for k in _SIGNED_FIELDS}
    expected = _signature(source, opportunity_id, by)
    if auth_opp != int(opportunity_id) or not hmac.compare_digest(sig.encode(), expected.encode()):
        raise GDriveSourceError(
            f"This Google Drive source is not authorized for opportunity {opportunity_id}, or it was "
            "changed after it was authorized. Re-save the schema as Dimagi staff to authorize it."
        )


# --------------------------------------------------------------------------- reading


def _drive():
    from connect_labs.labs.synthetic.gdrive import READONLY_SCOPES, DriveAuthError, DriveClient, _load_credentials

    creds = _load_credentials(scopes=READONLY_SCOPES)
    if creds is None:
        raise DriveAuthError("LABS_SYNTHETIC_GDRIVE_SA_KEY is not set, so Google Drive sources cannot be read.")
    return DriveClient(credentials=creds)


def _service_account_email() -> str:
    try:
        return _drive().credentials.service_account_email
    except Exception:  # noqa: BLE001 - only used to make an error message helpful
        return "the labs service account"


def _kind(meta: dict) -> str | None:
    """'json', 'csv', 'tsv' or 'sheet' for a file this source can read, else None."""
    mime, lower = meta.get("mimeType", ""), (meta.get("name") or "").lower()
    if mime == GOOGLE_SHEET:
        return "sheet"
    if mime.startswith("application/vnd.google-apps."):
        return None  # Docs, Slides, Forms...: not tabular, and not downloadable as bytes
    if lower.endswith(".json") or mime == "application/json":
        return "json"
    if lower.endswith(".tsv") or mime == "text/tab-separated-values":
        return "tsv"
    if lower.endswith((".csv", ".txt")) or mime.startswith("text/"):
        return "csv"
    return None


def _cell(v, null_values: set):
    # Only scalars can equal a null marker; a nested JSON list/object is kept as is.
    if v is None or (isinstance(v, (str, int, float, bool)) and v in null_values):
        return None
    return v


def _parse(name: str, kind: str, raw: bytes, null_values: set, budget: int) -> list[dict]:
    """Rows of one file, refusing more than ``budget`` rows as they are read."""

    def capped(rows):
        out = []
        for row in rows:
            if len(out) >= budget:
                raise GDriveSourceError(f"more than {MAX_ROWS:,} rows; narrow file_pattern")
            out.append(row)
        return out

    if kind == "json":
        data = json.loads(raw.decode("utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("rows", data.get("data"))
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise GDriveSourceError(f"{name}: JSON must be an array of objects (or hold one under 'rows'/'data')")
        return capped({k: _cell(v, null_values) for k, v in r.items()} for r in data)
    reader = csv.DictReader(
        io.StringIO(raw.decode("utf-8-sig"), newline=""), dialect="excel-tab" if kind == "tsv" else "excel"
    )
    return capped({k: _cell(v, null_values) for k, v in row.items() if k is not None} for row in reader)


def _read_file(drive, meta: dict, null_values: set, budget: int) -> list[dict]:
    name, kind = meta.get("name", meta.get("id")), _kind(meta)
    if kind is None:
        raise GDriveSourceError(f"{name}: unsupported file type {meta.get('mimeType')!r} (use CSV, a Sheet, or JSON)")
    if kind == "sheet":
        raw = drive.export_file(meta["id"], "text/csv")
    else:
        size = int(meta.get("size") or 0)
        if size > MAX_FILE_BYTES:
            raise GDriveSourceError(f"{name} is {size:,} bytes; the limit is {MAX_FILE_BYTES:,}")
        raw = drive.download_file(meta["id"])
    return _parse(name, kind, raw, null_values, budget)


def _visit_date(value) -> str | None:
    """ISO date for a cell, or None when it is not a real date (never a guess the
    cache would later reject, e.g. '2025-02-30')."""
    if not value:
        return None
    text = str(value).strip()
    for candidate in (text.replace("Z", "+00:00"), text[:10]):
        try:
            return datetime.fromisoformat(candidate).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_row_to_visit_dict(row: dict, meta: dict, index: int, data_source: DataSourceConfig) -> dict:
    """Shape one row like a Connect visit dict (cells under form_json["row"])."""
    username = row.get(data_source.username_column) if data_source.username_column else ""
    return {
        "id": f"{meta['id']}:{index}",
        "opportunity_id": 0,
        "username": "" if username is None else str(username),
        "visit_date": _visit_date(row.get(data_source.date_column)) if data_source.date_column else None,
        "status": "approved",
        "entity_id": "",
        "entity_name": "",
        "deliver_unit": "",
        "deliver_unit_id": None,
        "location": "",
        "flagged": False,
        "flag_reason": "",
        "reason": "",
        "form_json": {
            "row": row,
            "file": {"id": meta["id"], "name": meta.get("name", ""), "modified": meta.get("modifiedTime", "")},
        },
        "completed_work": "",
        "status_modified_date": None,
        "review_status": "",
        "review_created_on": None,
        "justification": "",
        "date_created": meta.get("modifiedTime", ""),
        "completed_work_id": None,
        "images": [],
    }


def fetch_gdrive_rows_as_visit_dicts(
    data_source: DataSourceConfig, opportunity_id: int, request=None, access_token: str | None = None
) -> list[dict]:
    """Read the source's file(s) from Drive and return visit-shaped dicts.

    Raises GDriveSourceError when the source is unauthorized, the caller is not
    a member of the opportunity, or the files are unshared, absent, too large,
    or not parseable. A folder source reads only its tabular files.
    """
    from connect_labs.labs.synthetic.gdrive import DriveAPIError

    check_gdrive_access(data_source, opportunity_id, request=request, access_token=access_token)
    drive = _drive()
    null_values = set(data_source.null_values or [""])
    try:
        if data_source.file_id:
            metas = [drive.get_metadata(data_source.file_id)]
            if metas[0].get("mimeType") == GOOGLE_FOLDER:
                raise GDriveSourceError(f"{data_source.file_id} is a folder; set folder_id instead of file_id")
        else:
            pattern = data_source.file_pattern or "*"
            metas = sorted(
                (
                    m
                    for m in drive.list_folder_files(data_source.folder_id)
                    if fnmatch.fnmatch(m["name"], pattern) and _kind(m) is not None
                ),
                key=lambda m: m["name"],
            )
            if not metas:
                raise GDriveSourceError(f"no CSV / Sheet / JSON files matching {pattern!r} in Drive folder {data_source.folder_id}")
            if len(metas) > MAX_FILES:
                raise GDriveSourceError(f"{len(metas)} files match {pattern!r}; the limit is {MAX_FILES}")
        visits: list[dict] = []
        for meta in metas:
            rows = _read_file(drive, meta, null_values, MAX_ROWS - len(visits))
            visits.extend(normalize_row_to_visit_dict(r, meta, i, data_source) for i, r in enumerate(rows))
    except DriveAPIError as e:
        target = data_source.file_id or data_source.folder_id
        raise GDriveSourceError(
            f"Could not read Drive {target}: {e}. Share it (Viewer is enough) with {_service_account_email()}."
        ) from e
    logger.info("[GDrive Fetcher] %d rows from %d file(s) for opp %s", len(visits), len(metas), opportunity_id)
    return visits
