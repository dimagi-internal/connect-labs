"""Staff-only: bring prod's solicitation LabsRecords into the labs tables, once.

See ``connect_labs/solicitations/legacy_import.py`` for what is imported and how
owners, listing and respondent organisations are decided.
"""

from __future__ import annotations

from connect_labs.labs.progress import NULL_PROGRESS

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register


@register(
    name="solicitations_import_records",
    description=(
        "STAFF ONLY. Import prod's solicitation, solicitation_response and solicitation_review "
        "LabsRecords into the labs solicitation tables, read as you. Upserts by the prod record id "
        "(legacy_record_id), so re-running is safe; prod is never written. Each call is owned by its "
        "programme's running organisation, else default_owner_org_slug, else nobody (staff-only). "
        "Calls whose title or programme marks them smoke/e2e/test run are imported unlisted. "
        "Respondent organisations are matched exactly (directory contact email or exact name); "
        "anything else keeps the typed name with no organisation. dry_run=true returns the counts "
        "a real run would produce without writing anything."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "default_owner_org_slug": {
                "type": "string",
                "description": "Owner for calls whose programme has no running organisation (a labs org slug).",
            },
            "program_ids": {
                "type": "array",
                "items": {"type": "integer"},
                "description": (
                    "Programmes whose NON-public solicitations should also be read (prod returns only "
                    "public records unscoped). You must be a member of each on Connect."
                ),
            },
            "dry_run": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    is_write=True,
    wants_progress=True,
    # Upserts by legacy_record_id: a run a deploy interrupted is safely run again from the start.
    long_running=True,
)
def solicitations_import_records(
    user,
    default_owner_org_slug: str | None = None,
    program_ids: list | None = None,
    dry_run: bool = False,
    progress=NULL_PROGRESS,
) -> dict:
    from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient
    from connect_labs.solicitations.access import resolve_org
    from connect_labs.solicitations.legacy_import import fetch_records, import_records

    if not (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)):
        raise MCPToolError("PERMISSION_DENIED", "solicitations_import_records is for platform staff only.")
    default_owner = None
    if default_owner_org_slug:
        default_owner = resolve_org(default_owner_org_slug)
        if default_owner is None:
            raise MCPToolError("INVALID_SCHEMA", f"No organisation {default_owner_org_slug!r} in labs.")

    token = require_connect_token(user)
    client = LabsRecordAPIClient(access_token=token)
    try:
        records = fetch_records(client, program_ids or ())
    finally:
        client.close()
    stats = import_records(records, default_owner=default_owner, dry_run=dry_run, progress=progress)
    stats["read"] = {kind: len(rows) for kind, rows in records.items()}
    return stats
