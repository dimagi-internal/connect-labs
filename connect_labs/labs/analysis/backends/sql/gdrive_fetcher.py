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

Containment comes first: a source's file or folder must sit under one of the
folders in ``settings.LABS_WORKFLOW_GDRIVE_ROOT_IDS`` (checked by walking its
Drive parents on every fetch, and when it is stamped). Everything else the
service account can see -- synthetic fixtures, trackers, exports -- is out of
reach whoever saves the pipeline; data a workflow should read is copied under a
root. No root configured = Drive sources are off.

A stamp names an opportunity; it is not a pass to it. Every read also requires
the caller (the Connect token the pipeline runs with) to be a member of that
opportunity, because Drive -- unlike Connect, HQ or OCS -- never checks who is
asking, and labs has paths that run a pipeline for an opportunity other than the
caller's own (clones, multi-opp fan-out).

A staff save only stamps a target that the save itself sets or changes (or one
the caller explicitly asks to authorize), so a staff member editing an unrelated
field cannot silently authorize a target someone else planted.

Program scope
-------------
A source can instead be stamped for a PROGRAM (``program_id`` in place of
``opportunity_id``; the signature names which, so neither can pass for the other).
That is for Drive files that cover a whole program -- one interview export across
every cohort -- rather than one opportunity. The read rule is different and
stricter: a caller must be a member of the organization that OWNS the program (the
program's managing organization), and membership in one of the program's
opportunities does NOT count, because those are partner network organizations who
must not see other cohorts' raw rows. Program ownership comes from the caller's own
Connect org tree (``/export/opp_org_program_list/``), whose ``programs`` lists only
programs whose owning organization the caller belongs to, each naming that
organization's slug; an unresolvable tree fails closed.

A program-scoped source is read ONCE for the program, not once per opportunity:
its rows are cached under ``program_cache_scope(program_id)`` (a negative
pseudo-opportunity id that no real or labs-only opportunity can take), so a
program workflow that spans N opportunities does not multiply every count by N.
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


def _scope(opportunity_id, program_id) -> dict:
    """The scope a stamp names: exactly one of opportunity_id / program_id."""
    if (opportunity_id is None) == (program_id is None):
        raise GDriveSourceError("a Drive source is authorized for exactly one of an opportunity or a program.")
    if program_id is not None:
        return {"program_id": int(program_id)}
    return {"opportunity_id": int(opportunity_id)}


def _signature(source: dict, scope: dict, authorized_by: str, pipeline_id: int | None) -> str:
    payload = json.dumps(
        {
            **{k: source.get(k) or "" for k in _SIGNED_FIELDS},
            # {"opportunity_id": N} or {"program_id": N}. The KEY is signed, so an
            # opportunity stamp can never verify as a program stamp of the same number.
            **scope,
            "authorized_by": authorized_by,
            # Bound to ONE pipeline: a stamp copied into another pipeline (any member can
            # read one) does not verify there, and re-pointing a pipeline retires it.
            "pipeline_id": int(pipeline_id) if pipeline_id is not None else None,
        },
        sort_keys=True,
    )
    key = hashlib.sha256(f"labs-gdrive-source:{settings.SECRET_KEY}".encode()).digest()
    return hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()


def authorize_gdrive_source(
    source: dict,
    opportunity_id: int | None,
    user,
    pipeline_id: int | None = None,
    *,
    program_id: int | None = None,
) -> dict:
    """Return `source` stamped with an authorization for `pipeline_id` in ONE scope:
    `opportunity_id`, or (pass opportunity_id=None) `program_id`.

    Raises GDriveSourceError unless `user` is Dimagi staff. Callers that save a
    pipeline schema run every gdrive data_source through this.
    """
    from connect_labs.utils.dimagi_user import is_dimagi_user

    scope = _scope(opportunity_id, program_id)
    if not is_dimagi_user(user):
        raise GDriveSourceError(
            "Only Dimagi staff can point a pipeline at Google Drive: the server's service account "
            "can read files the person editing the pipeline may not be allowed to see."
        )
    # Containment at save time too, so a target outside the workflow-data tree is
    # refused when it is set rather than on the first read.
    from connect_labs.labs.synthetic.gdrive import DriveAPIError, DriveAuthError

    try:
        assert_under_allowed_root(_drive(), source.get("file_id") or source.get("folder_id"))
    except (DriveAPIError, DriveAuthError) as e:
        raise GDriveSourceError(f"could not check where it lives in Drive: {e}") from e
    by = getattr(user, "email", "") or getattr(user, "username", "")
    clean = {k: v for k, v in source.items() if k != "authorization"}
    clean["authorization"] = {
        **scope,
        "authorized_by": by,
        "pipeline_id": pipeline_id,
        "signature": _signature(clean, scope, by, pipeline_id),
    }
    return clean


def gdrive_program_id(data_source) -> int | None:
    """The program a Drive source's stamp NAMES, or None for an opportunity stamp / no stamp.

    A routing hint only (which gate, which cache scope): it is not verified here.
    Every read still passes ``check_gdrive_access``, which verifies the signature
    for exactly this program, so a forged ``program_id`` is refused there.
    """
    if data_source is None or getattr(data_source, "type", None) != "gdrive":
        return None
    auth = getattr(data_source, "authorization", None)
    if not isinstance(auth, dict) or auth.get("program_id") is None or auth.get("opportunity_id") is not None:
        return None
    try:
        return int(auth["program_id"])
    except (TypeError, ValueError):
        return None


def program_cache_scope(program_id: int) -> int:
    """The cache `opportunity_id` a program-scoped Drive source's rows live under.

    Negative, so it can meet neither a real Connect opportunity nor a labs-only one
    (>= 10,000). Every reader of a program's source -- whichever opportunity, if
    any, it was asked for -- lands on this one key, which is what makes the source
    read and aggregated ONCE per program.
    """
    return -int(program_id)


def drive_cache_scope(data_source, opportunity_id):
    """The cache `opportunity_id` to read/write a source under: the program's key for a
    program-scoped Drive source, else ``opportunity_id`` unchanged. Idempotent."""
    program_id = gdrive_program_id(data_source)
    return program_cache_scope(program_id) if program_id is not None else opportunity_id


def _target(source: dict | None) -> tuple | None:
    if not source or source.get("type") != "gdrive":
        return None
    return tuple(source.get(k) or "" for k in _SIGNED_FIELDS)


def _stamp_is_valid(source: dict, opportunity_id, pipeline_id: int | None, program_id=None) -> bool:
    try:
        verify_gdrive_authorization(
            DataSourceConfig(
                type="gdrive",
                **{k: source.get(k) or "" for k in _SIGNED_FIELDS},
                authorization=source.get("authorization") or {},
            ),
            opportunity_id,
            pipeline_id,
            program_id=program_id,
        )
        return True
    except (GDriveSourceError, ValueError):
        return False


def authorize_schema_drive_source(
    schema: dict,
    opportunity_id: int | None,
    user,
    previous_schema: dict | None = None,
    force: bool = False,
    *,
    pipeline_id: int | None = None,
    program_id: int | None = None,
) -> dict:
    """Settle a pipeline schema's gdrive authorization before it is saved.

    The pipeline's scope is ``opportunity_id`` or, for a program-owned pipeline
    (pass opportunity_id=None), ``program_id``; a stamp is only ever kept or
    created for that scope.

    - No gdrive source: returned untouched.
    - A stamp that still verifies for this scope: kept.
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
    _scope(opportunity_id, program_id)  # exactly one scope, before anything is kept or stamped
    if _stamp_is_valid(source, opportunity_id, pipeline_id, program_id):
        return schema
    previous = (previous_schema or {}).get("data_source") if isinstance(previous_schema, dict) else None
    if not force and previous and _target(previous) == _target(source):
        if _stamp_is_valid(previous, opportunity_id, pipeline_id, program_id):
            return {**schema, "data_source": {**source, "authorization": previous["authorization"]}}
        return schema
    return {
        **schema,
        "data_source": authorize_gdrive_source(source, opportunity_id, user, pipeline_id, program_id=program_id),
    }


def _caller_org_tree(request, access_token: str | None) -> dict | None:
    """The caller's Connect org tree ({organizations, programs, opportunities}), with
    the labs-only (synthetic) shells they can see folded in; None when no caller can
    be resolved.

    A web session carries the tree from login. A Celery job's mock request carries
    only a token (workflow/tasks.py _create_mock_request): the tree is read from
    Connect with that token instead of reading an empty one as "no access".
    """
    user = getattr(request, "user", None) if request is not None else None
    session = getattr(request, "session", None) if request is not None else None
    labs_oauth = (session.get("labs_oauth") or {}) if session is not None else {}
    access_token = access_token or labs_oauth.get("access_token")
    if labs_oauth.get("organization_data"):
        from connect_labs.labs.context import get_org_data

        return get_org_data(request)
    if not access_token:
        return None
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    data = fetch_user_organization_data(access_token)
    if data is None:
        return None
    tree = {k: list(data.get(k) or []) for k in ("organizations", "programs", "opportunities")}
    if user is None:
        from connect_labs.labs.integrations.ocs.ocs_tokens import current_mcp_caller

        user = current_mcp_caller()
    if user is not None:
        from connect_labs.labs.access.scopes import labs_only_opportunity_ids, labs_only_program_ids

        tree["opportunities"] += [{"id": i, "labs_only": True} for i in labs_only_opportunity_ids(user)]
        tree["programs"] += [{"id": i, "labs_only": True} for i in labs_only_program_ids(user)]
    return tree


def _int_ids(values) -> set[int]:
    out = set()
    for i in values:
        try:
            out.add(int(i))
        except (TypeError, ValueError):
            continue
    return out


def _caller_opportunity_ids(request, access_token: str | None) -> set[int] | None:
    """Opportunity ids the caller is a member of, or None when no caller can be resolved."""
    tree = _caller_org_tree(request, access_token)
    if tree is None:
        return None
    return _int_ids(o.get("id") for o in tree.get("opportunities") or [])


def _caller_owns_program(request, access_token: str | None, program_id: int) -> bool | None:
    """Whether the caller is a member of the organization that OWNS ``program_id``;
    None when no caller can be resolved.

    Connect's org tree lists, under ``programs``, only programs whose owning
    organization the caller belongs to, each naming that organization's slug. Both
    halves are checked: the program is listed AND its owning organization is one of
    the caller's own. Seeing the program's OPPORTUNITIES is deliberately not enough
    -- a partner network organization sees its own opportunities in a program it
    does not own. A labs-only (synthetic) program the caller can see counts as owned:
    its organization exists only as the caller's own labs shell.
    """
    tree = _caller_org_tree(request, access_token)
    if tree is None:
        return None
    program_id = int(program_id)
    my_orgs = {o.get("slug") for o in tree.get("organizations") or [] if o.get("slug")}
    for program in tree.get("programs") or []:
        if _int_ids([program.get("id")]) != {program_id}:
            continue
        if program.get("labs_only"):
            return True
        owner = program.get("organization")
        if isinstance(owner, dict):
            owner = owner.get("slug")
        if owner and owner in my_orgs:
            return True
    return False


def check_gdrive_access(
    data_source: DataSourceConfig, opportunity_id: int | None, request=None, access_token=None, pipeline_id=None
) -> None:
    """The gate every read of a gdrive pipeline passes.

    - Opportunity-stamped: a valid stamp for this opportunity and pipeline AND a
      caller who is a member of the opportunity.
    - Program-stamped: a valid stamp for that program and pipeline AND a caller who
      is a member of the program's OWNING organization. ``opportunity_id`` plays no
      part: the rows are the program's, and opportunity membership grants nothing.
    """
    program_id = gdrive_program_id(data_source)
    if program_id is not None:
        verify_gdrive_authorization(data_source, None, pipeline_id, program_id=program_id)
        owns = _caller_owns_program(request, access_token, program_id)
        if owns is None:
            raise GDriveSourceError(
                "cannot confirm who is reading it or which organization manages program "
                f"{program_id}, so it is not read. Sign in and retry."
            )
        if not owns:
            raise GDriveSourceError(
                f"only members of the organization that manages program {program_id} can read this source; "
                "membership in one of the program's opportunities is not enough."
            )
        return
    if opportunity_id is None or int(opportunity_id) <= 0:
        raise GDriveSourceError("this source is authorized for an opportunity, and no opportunity was given.")
    verify_gdrive_authorization(data_source, opportunity_id, pipeline_id)
    ids = _caller_opportunity_ids(request, access_token)
    if ids is None:
        raise GDriveSourceError("cannot confirm who is reading it, so it is not read. Sign in and retry.")
    if int(opportunity_id) not in ids:
        raise GDriveSourceError(f"you are not a member of opportunity {opportunity_id}, which this source belongs to.")


def verify_gdrive_authorization(
    data_source: DataSourceConfig,
    opportunity_id: int | None,
    pipeline_id: int | None = None,
    *,
    program_id: int | None = None,
) -> None:
    """Raise GDriveSourceError unless the source carries a valid authorization for this
    pipeline in this scope: ``opportunity_id``, or (opportunity_id=None) ``program_id``."""
    scope = _scope(opportunity_id, program_id)
    ((scope_key, scope_id),) = scope.items()
    other_key = "opportunity_id" if scope_key == "program_id" else "program_id"
    where = f"{'program' if scope_key == 'program_id' else 'opportunity'} {scope_id}"
    auth = data_source.authorization if isinstance(data_source.authorization, dict) else {}
    sig, by = auth.get("signature"), auth.get("authorized_by", "")
    if not isinstance(sig, str) or not sig or not isinstance(by, str):
        raise GDriveSourceError(
            "This Google Drive source has not been authorized. A Dimagi staff member must save the "
            "pipeline schema (e.g. with the pipeline_update_schema MCP tool) to authorize it."
        )
    source = {k: getattr(data_source, k) for k in _SIGNED_FIELDS}
    expected = _signature(source, scope, by, pipeline_id)
    if (
        auth.get(scope_key) != scope_id
        or auth.get(other_key) is not None
        or not hmac.compare_digest(sig.encode(), expected.encode())
    ):
        raise GDriveSourceError(
            f"This Google Drive source is not authorized for this pipeline in {where}, "
            "or it was changed after it was authorized. Re-save the schema as Dimagi staff to authorize it."
        )


# --------------------------------------------------------------------------- containment

MAX_ANCESTRY_DEPTH = 40


def _allowed_roots() -> set[str]:
    return {r.strip() for r in getattr(settings, "LABS_WORKFLOW_GDRIVE_ROOT_IDS", []) or [] if r and r.strip()}


def assert_under_allowed_root(drive, target_id: str) -> None:
    """Raise GDriveSourceError unless ``target_id`` is, or sits under, an allowed root.

    Walks every parent chain (a legacy My Drive item can have several) breadth-first
    up to MAX_ANCESTRY_DEPTH levels; one chain reaching a root suffices -- the item
    is in the allowed tree, whoever else it is filed under.
    """
    roots = _allowed_roots()
    if not roots:
        raise GDriveSourceError(
            "Drive pipeline sources are not enabled here (no LABS_WORKFLOW_GDRIVE_ROOT_IDS configured)."
        )
    frontier, seen = [target_id], set()
    for _ in range(MAX_ANCESTRY_DEPTH):
        if any(node in roots for node in frontier):
            return
        nxt = []
        for node in frontier:
            if node in seen:
                continue
            seen.add(node)
            nxt.extend(drive.get_parents(node))
        if not nxt:
            break
        frontier = nxt
    raise GDriveSourceError(
        f"{target_id} is not inside an allowed workflow-data folder. Copy the data under "
        "the shared workflow-data folder and point the source there."
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
    """Rows of one file, refusing more than ``budget`` rows as they are read. Any
    decode/parse failure (bad JSON, non-UTF-8 CSV, oversized cell) is reported as a
    GDriveSourceError naming the file."""
    try:
        return _parse_rows(name, kind, raw, null_values, budget)
    except GDriveSourceError:
        raise
    except (ValueError, csv.Error) as e:  # JSONDecodeError and UnicodeDecodeError are ValueErrors
        raise GDriveSourceError(f"{name} could not be read as {kind.upper()}: {e}") from e


def _parse_rows(name: str, kind: str, raw: bytes, null_values: set, budget: int) -> list[dict]:

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


def _source_metas(drive, data_source: DataSourceConfig) -> list[dict]:
    """The Drive metadata of every file this source reads, in read order.

    One listing call for a folder source (one metadata call for a file source), and
    no content: this is both what a fetch reads and what ``source_fingerprint`` hashes,
    so the two can never disagree about WHICH files make up the source.
    """
    if data_source.file_id:
        metas = [drive.get_metadata(data_source.file_id)]
        if metas[0].get("mimeType") == GOOGLE_FOLDER:
            raise GDriveSourceError(f"{data_source.file_id} is a folder; set folder_id instead of file_id")
        return metas
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
        raise GDriveSourceError(
            f"no CSV / Sheet / JSON files matching {pattern!r} in Drive folder {data_source.folder_id}"
        )
    if len(metas) > MAX_FILES:
        raise GDriveSourceError(f"{len(metas)} files match {pattern!r}; the limit is {MAX_FILES}")
    return metas


def fingerprint_metas(metas: list[dict]) -> str:
    """A digest of WHAT the source's files are: each file's id, name, size, content
    checksum and modification time. Any edit, upload, rename, removal or addition of
    a matching file changes it; nothing else does."""
    entries = sorted(
        (
            str(m.get("id") or ""),
            str(m.get("name") or ""),
            str(m.get("size") or ""),
            str(m.get("md5Checksum") or ""),
            str(m.get("modifiedTime") or ""),
        )
        for m in metas
    )
    return hashlib.sha256(json.dumps(entries).encode()).hexdigest()


def source_fingerprint(data_source: DataSourceConfig) -> str:
    """The current fingerprint of the source's files, from one Drive listing.

    Reads metadata only -- never file content, and never on a caller's behalf: it
    says nothing about who may read the rows, so callers still gate every read with
    ``check_gdrive_access``. Raises GDriveSourceError only when Drive cannot be
    listed at all.

    A listing that SUCCEEDS but selects nothing readable (the last matching file
    removed, too many files, a file_id that became a folder) is a change of source,
    not an outage: it returns an ``invalid:`` fingerprint no cache was built from, so
    the next read rebuilds and reports the problem instead of serving the old rows.
    """
    from connect_labs.labs.synthetic.gdrive import DriveAPIError, DriveAuthError

    try:
        drive = _drive()
        try:
            metas = _source_metas(drive, data_source)
        except GDriveSourceError as e:
            return "invalid:" + hashlib.sha256(str(e).encode()).hexdigest()
        return fingerprint_metas(metas)
    except (DriveAPIError, DriveAuthError) as e:
        raise GDriveSourceError(f"could not list {data_source.file_id or data_source.folder_id}: {e}") from e


def fetch_gdrive_rows_as_visit_dicts(
    data_source: DataSourceConfig,
    opportunity_id: int | None,
    request=None,
    access_token: str | None = None,
    pipeline_id: int | None = None,
) -> list[dict]:
    """Read the source's file(s) from Drive and return visit-shaped dicts.

    See ``fetch_gdrive_rows_with_fingerprint``, which this wraps.
    """
    visits, _fingerprint = fetch_gdrive_rows_with_fingerprint(
        data_source, opportunity_id, request=request, access_token=access_token, pipeline_id=pipeline_id
    )
    return visits


def fetch_gdrive_rows_with_fingerprint(
    data_source: DataSourceConfig,
    opportunity_id: int | None,
    request=None,
    access_token: str | None = None,
    pipeline_id: int | None = None,
) -> tuple[list[dict], str]:
    """Read the source's file(s) from Drive; return (visit-shaped dicts, fingerprint).

    The fingerprint (``fingerprint_metas``) is of the exact files that were read, so a
    cache filled from these rows can later be checked against the folder with one
    listing call (``source_fingerprint``) instead of being thrown away on a clock.

    Raises GDriveSourceError when the source is unauthorized, the caller may not
    read it (``check_gdrive_access``), or the files are unshared, absent, too
    large, or not parseable. A folder source reads only its tabular files.
    """
    from connect_labs.labs.synthetic.gdrive import DriveAPIError

    check_gdrive_access(
        data_source, opportunity_id, request=request, access_token=access_token, pipeline_id=pipeline_id
    )
    drive = _drive()
    null_values = set(data_source.null_values or [""])
    try:
        assert_under_allowed_root(drive, data_source.file_id or data_source.folder_id)
        metas = _source_metas(drive, data_source)
        visits: list[dict] = []
        for meta in metas:
            rows = _read_file(drive, meta, null_values, MAX_ROWS - len(visits))
            visits.extend(normalize_row_to_visit_dict(r, meta, i, data_source) for i, r in enumerate(rows))
    except DriveAPIError as e:
        target = data_source.file_id or data_source.folder_id
        raise GDriveSourceError(
            f"Could not read Drive {target}: {e}. Share it (Viewer is enough) with {_service_account_email()}."
        ) from e
    program_id = gdrive_program_id(data_source)
    scope = f"program {program_id}" if program_id is not None else f"opp {opportunity_id}"
    logger.info("[GDrive Fetcher] %d rows from %d file(s) for %s", len(visits), len(metas), scope)
    return visits, fingerprint_metas(metas)
