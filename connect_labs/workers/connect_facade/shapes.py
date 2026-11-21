"""Row shapes the facade returns, one function per entity kind.

Each takes one Connect export row and returns the dict labs keeps: the fields a
profile needs, a stable ``key``, and nothing identifying a beneficiary (a visit's or
a work unit's ``entity_id`` / ``entity_name`` are a beneficiary's name and phone
number; they are hashed or dropped here, as Pulse does). ``user_data`` keeps the
worker's own name and phone: the sync stores the phone encrypted on ConnectWorker
and strips it from every snapshot.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from datetime import timezone as dt_timezone


def parse_ts(raw) -> datetime | None:
    """An ISO timestamp or date from an export row, as an aware datetime (UTC if naive)."""
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def as_bool(raw) -> bool:
    # Some exports serialise booleans as the strings "True"/"False".
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() == "true"


def synthetic_key(*parts) -> str:
    """A stable key for a row Connect exports without an id (docs/connect-asks.md #6).

    Built only from fields that do not change over the row's life (who, what, when
    created), so a later status or payment change keeps the key. One-way: the inputs
    are not recoverable from it.
    """
    joined = "\x1f".join("" if p is None else str(p) for p in parts)
    return "s:" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:40]


def _pick(row: dict, fields) -> dict:
    return {f: row.get(f) for f in fields}


def user_data(row: dict, opp_id: int) -> dict:
    out = _pick(
        row,
        (
            "username",
            "name",
            "phone",
            "date_learn_started",
            "user_invite_status",
            "payment_accrued",
            "suspended",
            "suspension_date",
            "suspension_reason",
            "invited_date",
            "completed_learn_date",
            "last_active",
            "date_claimed",
        ),
    )
    out["suspended"] = as_bool(out.get("suspended"))
    out["claim_limits"] = [
        {
            "payment_unit": c.get("payment_unit"),
            "payment_unit_id": c.get("payment_unit_id"),
            "max_visits": c.get("max_visits"),
        }
        for c in (row.get("claim_limits") or [])
    ]
    out["opportunity_id"] = opp_id
    out["key"] = row.get("username") or ""
    return out


def completed_module(row: dict, opp_id: int) -> dict:
    out = _pick(row, ("username", "module", "date", "duration"))
    out["opportunity_id"] = opp_id
    out["key"] = synthetic_key(opp_id, row.get("username"), row.get("module"), row.get("date"))
    return out


def assessment(row: dict, opp_id: int) -> dict:
    out = _pick(row, ("username", "app", "date", "score", "passing_score", "passed"))
    out["passed"] = as_bool(out.get("passed"))
    out["opportunity_id"] = opp_id
    out["key"] = synthetic_key(opp_id, row.get("username"), row.get("app"), row.get("date"), row.get("score"))
    return out


def completed_works(row: dict, opp_id: int) -> dict:
    from connect_labs.pulse.normalize import work_key_for

    out = _pick(
        row,
        (
            "username",
            "payment_unit_id",
            "status",
            "last_modified",
            "reason",
            "status_modified_date",
            "payment_date",
            "date_created",
            "saved_completed_count",
            "saved_approved_count",
            "saved_payment_accrued",
            "saved_payment_accrued_usd",
        ),
    )
    out["opportunity_id"] = opp_id
    # Pulse's key for the same row (opportunity, worker, entity, payment unit), hashed so the
    # beneficiary it names is never stored.
    out["key"] = "w:" + work_key_for({**row, "opportunity_id": opp_id})[:40]
    return out


def payment(row: dict, opp_id: int) -> dict:
    out = _pick(
        row,
        (
            "username",
            "created_at",
            "amount",
            "amount_usd",
            "date_paid",
            "payment_unit",
            "confirmed",
            "confirmation_date",
            "organization",
            "invoice_id",
            "payment_method",
            "payment_operator",
        ),
    )
    out["confirmed"] = as_bool(out.get("confirmed"))
    out["opportunity_id"] = opp_id
    out["key"] = synthetic_key(
        opp_id, row.get("username"), row.get("created_at"), row.get("amount"), row.get("payment_unit")
    )
    return out


def task_type(row: dict, opp_id: int) -> dict:
    out = _pick(
        row,
        (
            "id",
            "task_type_id",
            "name",
            "slug",
            "description",
            "unit_name",
            "is_active",
            "archived",
            "duration",
            "mode",
        ),
    )
    out["opportunity_id"] = opp_id
    out["key"] = str(row.get("task_type_id") or row.get("id") or "")
    return out


def assigned_task(row: dict, opp_id: int, task_types_by_id: dict | None = None) -> dict:
    out = _pick(
        row,
        (
            "id",
            "assigned_task_id",
            "task_type",
            "task_type_name",
            "username",
            "completed_at",
            "duration",
            "status",
            "due_date",
            "date_created",
        ),
    )
    ttype = (task_types_by_id or {}).get(row.get("task_type")) or {}
    # The export gives the type's FK id only; the mode (relearn | ocs) lives on the type.
    out["mode"] = ttype.get("mode") or ""
    out["task_type_slug"] = ttype.get("slug") or ""
    out["opportunity_id"] = opp_id
    out["key"] = str(row.get("assigned_task_id") or row.get("id") or "")
    return out


def audit_report_entry(row: dict, opp_id: int) -> dict:
    out = _pick(
        row,
        (
            "id",
            "audit_report_entry_id",
            "audit_report_uuid",
            "username",
            "results",
            "flagged",
            "reviewed",
            "review_action",
            "date_created",
            "date_modified",
        ),
    )
    out["flagged"] = as_bool(out.get("flagged"))
    out["reviewed"] = as_bool(out.get("reviewed"))
    out["opportunity_id"] = opp_id
    out["key"] = str(row.get("audit_report_entry_id") or row.get("id") or "")
    return out


def work_area(row: dict, opp_id: int) -> dict:
    # No boundary (large geometry) and no case_properties (free-form case data).
    out = _pick(
        row,
        (
            "id",
            "slug",
            "ward",
            "status",
            "building_count",
            "expected_visit_count",
            "work_area_group_name",
            "username",
            "centroid",
        ),
    )
    out["opportunity_id"] = opp_id
    out["key"] = str(row.get("id") or row.get("slug") or "")
    return out


def _flag_type(raw) -> str:
    from connect_labs.pulse.normalize import flag_type_for

    try:
        return flag_type_for(raw) or ""
    except Exception:  # noqa: BLE001 -- a flag reason we cannot classify is still a flag
        return "other" if raw else ""


def entity_key(opp_id, entity_id) -> str:
    """Keyed hash of a beneficiary id: tells visits to the same case apart, names no one."""
    if not entity_id:
        return ""
    from django.utils.crypto import salted_hmac

    return salted_hmac("workers.entity", f"{opp_id}\x1f{entity_id}", algorithm="sha256").hexdigest()[:24]


def visit_thin(row: dict, opp_id: int) -> dict:
    """A visit without its form: who, when, status, review. Never form_json, GPS or entity names."""
    vid = row.get("id") or row.get("visit_id")
    return {
        "key": str(vid or ""),
        "visit_id": str(vid or ""),
        "opportunity_id": opp_id,
        "username": row.get("username") or "",
        "visit_date": _iso(row.get("visit_date")),
        "date_created": _iso(row.get("date_created")),
        "status": (row.get("status") or "") or "",
        "status_modified_date": _iso(row.get("status_modified_date")),
        "flagged": as_bool(row.get("flagged")),
        "flag_type": _flag_type(row.get("flag_reason")) if as_bool(row.get("flagged")) else "",
        "review_status": row.get("review_status") or "",
        "review_created_on": _iso(row.get("review_created_on")),
        "deliver_unit": row.get("deliver_unit") or "",
        "deliver_unit_id": row.get("deliver_unit_id"),
        "completed_work_id": row.get("completed_work_id"),
        "entity_key": entity_key(opp_id, row.get("entity_id")),
    }


def _iso(value):
    if value is None or value == "":
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


NORMALIZERS = {
    "user_data": user_data,
    "completed_module": completed_module,
    "assessment": assessment,
    "completed_works": completed_works,
    "payment": payment,
    "task_types": task_type,
    "audit_report_entries": audit_report_entry,
    "work_areas": work_area,
}

# The field a row's change time is read from, for emulating `since` by filtering a full pull.
CHANGED_AT = {
    "user_data": ("last_active", "invited_date"),
    "completed_module": ("date",),
    "assessment": ("date",),
    "completed_works": ("last_modified", "status_modified_date", "date_created"),
    "payment": ("confirmation_date", "date_paid", "created_at"),
    "assigned_tasks": ("completed_at", "date_created"),
    "audit_report_entries": ("date_modified", "date_created"),
    "visits_thin": ("status_modified_date", "review_created_on", "date_created", "visit_date"),
}


def changed_at(kind: str, row: dict) -> datetime | None:
    stamps = [parse_ts(row.get(f)) for f in CHANGED_AT.get(kind, ())]
    stamps = [s for s in stamps if s is not None]
    return max(stamps) if stamps else None
