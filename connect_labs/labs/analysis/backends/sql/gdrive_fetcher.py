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
on every fetch, so a source smuggled in through any other save path, copied to
another opportunity, or edited after authorization is refused rather than read.
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
    """A gdrive source that cannot be read: unauthorized, unreachable or unparseable."""


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


def authorize_schema_drive_source(schema: dict, opportunity_id: int, user) -> dict:
    """Stamp a pipeline schema's gdrive data_source, if it has one, before it is saved.

    A source whose existing authorization still verifies for this opportunity is
    kept as is, so anyone may re-save a staff-authorized pipeline (to edit its
    fields) as long as the Drive target is unchanged. A new or changed target
    needs Dimagi staff. Non-gdrive schemas pass through untouched.
    """
    if not isinstance(schema, dict):
        return schema
    source = schema.get("data_source") or {}
    if source.get("type") != "gdrive":
        return schema
    try:
        verify_gdrive_authorization(
            DataSourceConfig(
                type="gdrive",
                **{k: source.get(k) or "" for k in _SIGNED_FIELDS},
                authorization=source.get("authorization") or {},
            ),
            opportunity_id,
        )
        return schema
    except GDriveSourceError:
        pass
    return {**schema, "data_source": authorize_gdrive_source(source, opportunity_id, user)}


def verify_gdrive_authorization(data_source: DataSourceConfig, opportunity_id: int) -> None:
    """Raise GDriveSourceError unless the source carries a valid authorization for this opportunity."""
    auth = data_source.authorization or {}
    sig, by, auth_opp = auth.get("signature"), auth.get("authorized_by", ""), auth.get("opportunity_id")
    if not sig:
        raise GDriveSourceError(
            "This Google Drive source has not been authorized. A Dimagi staff member must save the "
            "pipeline schema (e.g. with the pipeline_update_schema MCP tool) to authorize it."
        )
    source = {k: getattr(data_source, k) for k in _SIGNED_FIELDS}
    if auth_opp != int(opportunity_id) or not hmac.compare_digest(sig, _signature(source, opportunity_id, by)):
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


def _parse(name: str, mime: str, raw: bytes, null_values: set) -> list[dict]:
    lower = name.lower()
    if lower.endswith(".json") or mime == "application/json":
        data = json.loads(raw.decode("utf-8-sig"))
        if isinstance(data, dict):
            data = data.get("rows", data.get("data"))
        if not isinstance(data, list) or not all(isinstance(r, dict) for r in data):
            raise GDriveSourceError(f"{name}: JSON must be an array of objects (or hold one under 'rows'/'data')")
        return [{k: (None if v in null_values else v) for k, v in r.items()} for r in data]
    if mime == GOOGLE_SHEET or lower.endswith((".csv", ".tsv", ".txt")) or mime.startswith("text/"):
        text = raw.decode("utf-8-sig")
        dialect = "excel-tab" if lower.endswith(".tsv") else "excel"
        return [
            {k: (None if v in null_values else v) for k, v in row.items() if k is not None}
            for row in csv.DictReader(io.StringIO(text, newline=""), dialect=dialect)
        ]
    raise GDriveSourceError(f"{name}: unsupported file type {mime!r} (use CSV, a Google Sheet, or JSON)")


def _read_file(drive, meta: dict, null_values: set) -> list[dict]:
    mime, name = meta.get("mimeType", ""), meta.get("name", meta.get("id"))
    if mime == GOOGLE_SHEET:
        raw = drive.export_file(meta["id"], "text/csv")
    else:
        size = int(meta.get("size") or 0)
        if size > MAX_FILE_BYTES:
            raise GDriveSourceError(f"{name} is {size:,} bytes; the limit is {MAX_FILE_BYTES:,}")
        raw = drive.download_file(meta["id"])
    return _parse(name, mime, raw, null_values)


def _visit_date(value) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return text[:10] if len(text) >= 10 else None


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


def fetch_gdrive_rows_as_visit_dicts(data_source: DataSourceConfig, opportunity_id: int) -> list[dict]:
    """Read the source's file(s) from Drive and return visit-shaped dicts.

    Raises GDriveSourceError when the source is unauthorized, unshared, empty
    of matching files, too large, or not parseable.
    """
    from connect_labs.labs.synthetic.gdrive import DriveAPIError

    verify_gdrive_authorization(data_source, opportunity_id)
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
                (m for m in drive.list_folder_files(data_source.folder_id) if fnmatch.fnmatch(m["name"], pattern)),
                key=lambda m: m["name"],
            )
            if not metas:
                raise GDriveSourceError(f"No files matching {pattern!r} in Drive folder {data_source.folder_id}")
            if len(metas) > MAX_FILES:
                raise GDriveSourceError(f"{len(metas)} files match {pattern!r}; the limit is {MAX_FILES}")
        visits: list[dict] = []
        for meta in metas:
            rows = _read_file(drive, meta, null_values)
            visits.extend(normalize_row_to_visit_dict(r, meta, i, data_source) for i, r in enumerate(rows))
            if len(visits) > MAX_ROWS:
                raise GDriveSourceError(f"More than {MAX_ROWS:,} rows; narrow file_pattern")
    except DriveAPIError as e:
        target = data_source.file_id or data_source.folder_id
        raise GDriveSourceError(
            f"Could not read Drive {target}: {e}. Share it (Viewer is enough) with {_service_account_email()}."
        ) from e
    logger.info("[GDrive Fetcher] %d rows from %d file(s) for opp %s", len(visits), len(metas), opportunity_id)
    return visits
