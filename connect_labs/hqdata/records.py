"""One shape for what a source yields, whichever backend produced it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from django.utils.dateparse import parse_datetime


def parse_ts(value) -> datetime | None:
    """ISO-8601 from HQ (with or without a zone, with or without the trailing Z) as an aware UTC datetime."""
    from datetime import timezone as dt_timezone

    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value).replace("Z", "+00:00"))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


@dataclass
class FormRecord:
    form_id: str
    xmlns: str = ""
    app_id: str = ""
    build_id: str = ""
    received_on: datetime | None = None
    server_modified_on: datetime | None = None
    indexed_on: datetime | None = None
    username: str = ""
    user_id: str = ""
    archived: bool = False
    form_json: dict = field(default_factory=dict)

    @classmethod
    def from_hq(cls, obj: dict) -> FormRecord:
        form = obj.get("form") or {}
        meta = obj.get("metadata") or form.get("meta") or {}
        return cls(
            form_id=str(obj["id"]),
            xmlns=str(form.get("@xmlns") or obj.get("xmlns") or ""),
            app_id=str(obj.get("app_id") or ""),
            build_id=str(obj.get("build_id") or ""),
            received_on=parse_ts(obj.get("received_on")),
            server_modified_on=parse_ts(obj.get("server_modified_on")),
            indexed_on=parse_ts(obj.get("indexed_on")),
            username=str(meta.get("username") or ""),
            user_id=str(meta.get("userID") or ""),
            archived=bool(obj.get("archived", False)),
            form_json=obj,
        )


@dataclass
class CaseRecord:
    case_id: str
    case_type: str = ""
    case_name: str = ""
    owner_id: str = ""
    external_id: str = ""
    closed: bool = False
    date_opened: datetime | None = None
    last_modified: datetime | None = None
    server_last_modified: datetime | None = None
    indexed_on: datetime | None = None
    properties: dict = field(default_factory=dict)
    indices: dict = field(default_factory=dict)

    @classmethod
    def from_hq(cls, obj: dict) -> CaseRecord:
        return cls(
            case_id=str(obj["case_id"]),
            case_type=str(obj.get("case_type") or ""),
            case_name=str(obj.get("case_name") or ""),
            owner_id=str(obj.get("owner_id") or ""),
            external_id=str(obj.get("external_id") or ""),
            closed=bool(obj.get("closed", False)),
            date_opened=parse_ts(obj.get("date_opened")),
            last_modified=parse_ts(obj.get("last_modified")),
            server_last_modified=parse_ts(obj.get("server_last_modified")),
            indexed_on=parse_ts(obj.get("indexed_on")),
            properties=obj.get("properties") or {},
            indices=obj.get("indices") or {},
        )
