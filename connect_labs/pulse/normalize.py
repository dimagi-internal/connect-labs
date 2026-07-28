"""Turn Connect export records into PulseEvent rows.

This module is the privacy boundary. Everything upstream of it may contain real
beneficiary and worker identities; nothing downstream of it does. Concretely,
the export's ``user_visits`` rows carry:

    entity_name: "Sa,adatu Yakubu - 8037760312"     <- real name + phone
    form_json:   {...}                              <- whole submitted form

Neither is read here, and ``PulseEvent`` has no column for either. The FLW
``username`` arrives already hashed upstream (``985770f1bf2079f58119``) and is
what we display.

The allow-list below is deliberate: normalisation reads named keys rather than
copying the record, so a new PII field appearing upstream cannot flow through.
"""

from __future__ import annotations

import re
from datetime import datetime
from datetime import timezone as dt_timezone
from typing import Any

# Fields this module is permitted to read off an export visit record. Anything
# not named here never leaves the ingest boundary.
VISIT_SOURCE_FIELDS = frozenset(
    {
        "id",
        "opportunity_id",
        "username",
        "visit_date",
        "date_created",
        "status",
        "flagged",
        "flag_reason",
        "review_status",
        "location",
        "deliver_unit",
    }
)

# Fields that must never be read or stored, asserted by tests. Listed explicitly
# so the intent survives someone skimming the code.
FORBIDDEN_FIELDS = frozenset({"entity_name", "entity_id", "form_json", "name", "phone", "justification", "reason"})


# Rough country boxes. Only used to label a point for grouping/colour — never to
# place it, which always uses the real coordinates.
_COUNTRY_BOXES = {
    "NG": (3.5, 14.5, 2.0, 15.2),
    "KE": (-5.2, 5.5, 33.4, 42.2),
    "UG": (-1.6, 4.6, 29.4, 35.6),
    "IN": (6.0, 36.0, 68.0, 97.6),
    "CD": (-13.6, 5.6, 12.0, 31.6),
    "LR": (4.0, 8.7, -11.6, -7.3),
    "SL": (6.8, 10.1, -13.4, -10.2),
    "TZ": (-11.8, -0.9, 29.3, 40.5),
    "ML": (10.0, 25.0, -12.3, 4.3),
}

COUNTRY_NAMES = {
    "NG": "Nigeria",
    "KE": "Kenya",
    "UG": "Uganda",
    "IN": "India",
    "CD": "DR Congo",
    "LR": "Liberia",
    "SL": "Sierra Leone",
    "TZ": "Tanzania",
    "ML": "Mali",
}

# Opportunity name -> the service a funder would recognise. Opp names are
# operational ("KMC - UG - PIPN - P1 - Apr 26"); these are not.
_SERVICE_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"^KMC\b|kangaroo|कंगारू", re.I), "kmc", "Kangaroo Mother Care"),
    (re.compile(r"mother baby wellness", re.I), "mbw", "Mother & baby wellness"),
    (re.compile(r"\breaders\b", re.I), "readers", "Reading assessment"),
    (re.compile(r"back to school", re.I), "b2s", "Back-to-school enrolment"),
    (re.compile(r"malaria rdt", re.I), "rdt", "Malaria rapid test"),
    (re.compile(r"^ITN\b|bednet", re.I), "itn", "Bednet distribution"),
    (re.compile(r"poverty targeting", re.I), "poverty", "Household poverty survey"),
    (re.compile(r"chc\b", re.I), "chc", "Community health case"),
]

SERVICE_LABELS = {slug: label for _, slug, label in _SERVICE_PATTERNS}
SERVICE_LABELS["other"] = "Service delivery"

# Flag keys as they appear inside the export's flag_reason blob, mapped to
# language a non-engineer can read.
FLAG_LABELS = {
    "form_value_not_found": "form value missing",
    "location": "location mismatch",
    "duration": "form filled too fast",
    "duplicate": "duplicate beneficiary",
    "form_submission_period": "out-of-hours submission",
    "user_suspended": "suspended worker",
}


def parse_location(raw: Any) -> tuple[float, float] | None:
    """Parse Connect's ``"<lat> <lon> <alt> <precision>"`` location string.

    Syntax and coordinate-range validation only. Returns None for missing,
    malformed, impossible, or null-island points.

    Whether a *valid* coordinate is somewhere Connect plausibly operates is a
    separate question — see ``is_on_map``. Keeping the two apart matters: bad
    GPS is a data defect, but an unexpected country is news.
    """
    if not raw or raw in ("None", "null"):
        return None
    parts = str(raw).split()
    if len(parts) < 2:
        return None
    try:
        lat, lon = float(parts[0]), float(parts[1])
    except (TypeError, ValueError):
        return None
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return None
    # Null island — a GPS chip reporting nothing, not a service in the Atlantic.
    if abs(lat) < 0.01 and abs(lon) < 0.01:
        return None
    return lat, lon


def is_on_map(lat: float | None, lon: float | None) -> bool:
    """Is this coordinate inside a region Connect is known to operate in?

    Real production data contains stray points — I measured one at
    (-57.0, -110.02), the South Pacific, inside an otherwise West-African
    dataset. Plotting those makes a funder screen look broken.

    We drop the *coordinate* but keep the *event*, and ingest counts how often
    this happens. That distinction is the important part: if Connect launches
    somewhere not in ``_COUNTRY_BOXES``, the count climbs and says so, instead
    of a new country silently rendering as an empty map.
    """
    return bool(country_for(lat, lon))


def country_for(lat: float | None, lon: float | None) -> str:
    if lat is None or lon is None:
        return ""
    for code, (lat_lo, lat_hi, lon_lo, lon_hi) in _COUNTRY_BOXES.items():
        if lat_lo <= lat <= lat_hi and lon_lo <= lon <= lon_hi:
            return code
    return ""


def service_slug_for(opportunity_name: str | None) -> str:
    name = opportunity_name or ""
    for pattern, slug, _label in _SERVICE_PATTERNS:
        if pattern.search(name):
            return slug
    return "other"


def flag_type_for(flag_reason: Any) -> str:
    """Extract the primary flag key from the export's flag_reason blob.

    Shape is ``{'flags': [['duration', 'The form was completed...'], ...]}``,
    arriving as a string. Only the key is kept — the human message can quote
    form values.
    """
    if not flag_reason or flag_reason in ("None", "null"):
        return ""
    blob = str(flag_reason)
    for key in FLAG_LABELS:
        if f"'{key}'" in blob or f'"{key}"' in blob:
            return key
    return "other"


def _parse_ts(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_timezone.utc)
    return parsed


def _as_bool(raw: Any) -> bool:
    # The export serialises booleans as the strings "True"/"False".
    return str(raw).strip().lower() == "true"


def visit_to_event_fields(record: dict, opportunity=None) -> dict | None:
    """Normalise one export visit record into PulseEvent kwargs.

    Returns None if the record can't be placed in time, which is the one thing
    every card depends on. Missing GPS is fine (4.7% of real visits lack it) —
    those events still count, they just don't light up the map.

    ``opportunity`` is an optional PulseOpportunity supplying the display name
    and the measured per-service rate.
    """
    visit_id = record.get("id")
    if visit_id is None:
        return None

    sync_ts = _parse_ts(record.get("date_created"))
    field_ts = _parse_ts(record.get("visit_date")) or sync_ts
    if sync_ts is None:
        # Without an arrival time we can't order the tail or judge freshness.
        sync_ts = field_ts
    if field_ts is None or sync_ts is None:
        return None

    point = parse_location(record.get("location"))
    lat, lon = point if point else (None, None)
    # Valid coordinate, implausible place: keep the service, drop the dot.
    # Ingest counts these (see PULSE_SCALAR_OFF_MAP) so an unexpected country
    # surfaces as a rising number rather than as silence.
    if lat is not None and not is_on_map(lat, lon):
        lat, lon = None, None

    status = (record.get("status") or "").strip() or "unknown"
    usd = getattr(opportunity, "usd_per_service", None) if opportunity is not None else None

    return {
        "connect_visit_id": int(visit_id),
        "opportunity_id": int(record.get("opportunity_id") or getattr(opportunity, "opportunity_id", 0) or 0),
        "program_id": getattr(opportunity, "program_id", None) if opportunity is not None else None,
        "org_slug": (getattr(opportunity, "org_slug", "") if opportunity is not None else "") or "",
        "field_ts": field_ts,
        "sync_ts": sync_ts,
        "lat": lat,
        "lon": lon,
        "country": country_for(lat, lon)
        or ((getattr(opportunity, "country", "") if opportunity is not None else "") or ""),
        "status": status[:32],
        "flagged": _as_bool(record.get("flagged")),
        "flag_type": flag_type_for(record.get("flag_reason"))[:48],
        "review_status": (str(record.get("review_status") or "").strip() or "")[:24],
        "service_slug": service_slug_for(getattr(opportunity, "name", "") if opportunity is not None else ""),
        # Already an opaque hash upstream; truncated only to bound the column.
        "worker_hash": (str(record.get("username") or "").strip())[:64],
        # Only approved work actually pays out. Attributing the rate to
        # over_limit / rejected / pending events would overstate money paid.
        "usd_to_worker": usd if (usd is not None and status == "approved") else None,
    }
