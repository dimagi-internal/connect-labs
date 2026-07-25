"""Session-timeline assembly for the audit trail.

Pure functions that turn a user's ordered audit events into a navigable
"what exactly did they do" structure:

    sessions (split on >30 min idle gaps)
      └─ steps (one per request_id — a page navigation, or a background/API
                action with no page render)
           └─ data events (reads/exports with row counts, writes, denials)

No DB access here — the view fetches, this shapes. Kept separate for
unit-testability.
"""
from __future__ import annotations

from datetime import timedelta

SESSION_GAP = timedelta(minutes=30)

# Actions that represent data effects (nested under a step), as opposed to
# the page_view that anchors the step itself.
_WRITE_ACTIONS = {"create", "update", "delete"}


def build_session_timeline(events) -> list[dict]:
    """Group ascending-ordered AuditEvents into session dicts.

    Returns a list of sessions:
        {started_at, ended_at, duration, ip_addresses, sources,
         pages, rows_read, rows_exported, writes, denials, steps: [...]}
    Each step:
        {at, kind: "page"|"background"|"auth", title, query_string,
         source, page_event, events: [AuditEvent, ...]}
    """
    sessions: list[dict] = []
    current: dict | None = None
    steps_by_request: dict[str, dict] = {}

    for event in events:
        if current is None or (event.occurred_at - current["ended_at"]) > SESSION_GAP:
            current = {
                "started_at": event.occurred_at,
                "ended_at": event.occurred_at,
                "ip_addresses": [],
                "sources": [],
                "pages": 0,
                "rows_read": 0,
                "rows_exported": 0,
                "writes": 0,
                "denials": 0,
                "steps": [],
            }
            sessions.append(current)
            steps_by_request = {}
        current["ended_at"] = event.occurred_at
        if event.ip_address and event.ip_address not in current["ip_addresses"]:
            current["ip_addresses"].append(event.ip_address)
        if event.source and event.source not in current["sources"]:
            current["sources"].append(event.source)

        step = steps_by_request.get(event.request_id) if event.request_id else None
        if step is None:
            step = {
                "at": event.occurred_at,
                "kind": "background",
                "title": event.path or event.resource_type or event.action,
                "query_string": event.query_string,
                "source": event.source,
                "page_event": None,
                "events": [],
            }
            if event.request_id:
                steps_by_request[event.request_id] = step
            current["steps"].append(step)

        if event.action == "page_view":
            step["kind"] = "page"
            step["page_event"] = event
            step["title"] = event.path
            step["query_string"] = event.query_string
            current["pages"] += 1
        elif event.action in ("login", "logout", "login_failed"):
            step["kind"] = "auth"
            step["title"] = event.get_action_display()
            step["events"].append(event)
        else:
            step["events"].append(event)
            if event.action == "export":
                current["rows_exported"] += event.record_count or 0
            elif event.action in ("read", "list"):
                current["rows_read"] += event.record_count or 0
            elif event.action in _WRITE_ACTIONS:
                current["writes"] += 1
            elif event.action == "access_denied":
                current["denials"] += 1

    for session in sessions:
        session["duration"] = session["ended_at"] - session["started_at"]
    return sessions
