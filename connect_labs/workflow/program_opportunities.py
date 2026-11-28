"""A multi-opp workflow whose opportunities are its program's, as the viewer sees it.

A workflow's ``opportunity_ids`` is a list someone wrote down, so a program that adds
opportunities (a revisit round, a new partner) leaves every report on it short until
each list is edited by hand. ``opportunity_source: {"program_id": <id>}`` on the
definition says "every opportunity under this program" instead: the list is resolved
on each read from the viewer's own organisation tree, so it holds exactly what the
viewer may read, and a new opportunity appears the next time the report is opened.
``exclude_opportunity_ids`` takes some out.

Resolved into ``data["opportunity_ids"]`` at the definition read, so every reader of
the list -- pipelines, workers, the visit cache, actions, snapshots -- sees the same
thing without knowing this exists. When the tree cannot be had, or names nothing
under the program, the stored list stands: a blip must not empty a report.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SOURCE_KEY = "opportunity_source"


def source_problem(value) -> str | None:
    """Why ``value`` is not a valid ``opportunity_source`` (None when it is, or is null)."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return 'opportunity_source must be {"program_id": <int>} (optionally with exclude_opportunity_ids), or null'
    unknown = set(value) - {"program_id", "exclude_opportunity_ids"}
    if unknown:
        return f"Unknown opportunity_source keys: {sorted(unknown)}"
    program_id = value.get("program_id")
    if isinstance(program_id, bool) or not isinstance(program_id, int):
        return "opportunity_source.program_id must be an integer"
    exclude = value.get("exclude_opportunity_ids", [])
    if not isinstance(exclude, list) or any(isinstance(o, bool) or not isinstance(o, int) for o in exclude):
        return "opportunity_source.exclude_opportunity_ids must be a list of integers"
    return None


def program_opportunity_ids(org_data: dict | None, program_id, exclude=()) -> list[int]:
    """The opportunities filed under ``program_id`` in ``org_data``, ascending."""
    excluded = {int(o) for o in exclude or ()}
    ids = set()
    for opp in (org_data or {}).get("opportunities") or []:
        if str(opp.get("program")) != str(program_id):
            continue
        try:
            oid = int(opp.get("id"))
        except (TypeError, ValueError):
            continue
        if oid not in excluded:
            ids.add(oid)
    return sorted(ids)


def viewer_org_data(request=None, access_token: str | None = None) -> dict | None:
    """The viewer's organisation tree: the session's copy on the web, else a cached fetch."""
    if request is not None and hasattr(request, "session") and "labs_oauth" in request.session:
        from connect_labs.labs.context import get_org_data

        data = get_org_data(request)
        if data and data.get("opportunities"):
            return data
    if not access_token:
        return None
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    return fetch_user_organization_data(access_token)


def _source(record) -> dict | None:
    data = getattr(record, "data", None)
    source = data.get(SOURCE_KEY) if isinstance(data, dict) else None
    return source if source and not source_problem(source) else None


def resolve_all(records, request=None, access_token: str | None = None):
    """Fill each record's ``data["opportunity_ids"]`` from its ``opportunity_source``, if it has one.

    The tree is read once, and only when some record asks for it."""
    sourced = [(r, s) for r in records or [] if (s := _source(r))]
    if not sourced:
        return records
    try:
        org_data = viewer_org_data(request, access_token)
    except Exception:  # noqa: BLE001 -- an unreadable tree keeps the stored list
        logger.exception("opportunity_source: could not read the viewer's organisation tree")
        return records
    for record, source in sourced:
        ids = program_opportunity_ids(org_data, source["program_id"], source.get("exclude_opportunity_ids"))
        if ids:
            record.data = {**record.data, "opportunity_ids": ids}
    return records


def resolve(record, request=None, access_token: str | None = None):
    if record is not None:
        resolve_all([record], request, access_token)
    return record
