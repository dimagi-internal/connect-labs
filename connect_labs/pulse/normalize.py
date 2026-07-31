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

import hashlib
import re
from datetime import datetime
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation
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
    (re.compile(r"mother baby wellness", re.I), "mbw", "Mother Baby Wellness"),
    (re.compile(r"\breaders\b", re.I), "readers", "Readers Distribution"),
    (re.compile(r"back to school", re.I), "b2s", "Back-to-school enrolment"),
    (re.compile(r"malaria rdt", re.I), "rdt", "Malaria rapid test"),
    (re.compile(r"^ITN\b|bednet", re.I), "itn", "Bednet distribution"),
    (re.compile(r"poverty targeting", re.I), "poverty", "Household poverty survey"),
    (re.compile(r"chc\b", re.I), "chc", "Child Health Campaign"),
]

SERVICE_LABELS = {slug: label for _, slug, label in _SERVICE_PATTERNS}

# Connect's `delivery_type` values. Connect publishes the slug and no display
# text, so every string here is a decision about what a funder reads.
#
# The first version of this table was written from the slugs alone and three of
# them were simply wrong -- `chc` is the Child Health Campaign, not a "community
# health case"; `readers` is Readers Distribution, not a reading assessment;
# `mbw` is Mother Baby Wellness. They were confidently displayed above the
# correct numbers for as long as this ran, which is the failure mode of an
# invented label: it does not look uncertain.
#
# So names here are only the ones confirmed by someone who knows the
# programmes. Everything else falls through to the slug in caps, which is
# visibly a code and cannot be mistaken for a considered label.
SERVICE_LABELS.update(
    {
        "chc": "Child Health Campaign",
        "mbw": "Mother Baby Wellness",
        "readers": "Readers Distribution",
        "ecd": "Early childhood development",
        "kmc": "Kangaroo Mother Care",
        "ivp": "Infant Vaccine Promotion",
        "malaria": "Malaria",
        "hhs": "Household Safety Check",
        "wellme": "Worker Wellbeing",
        "nutrition": "Nutrition",
        "interview": "Interviews",
        # Our own tooling's programmes rather than field delivery. Named as it
        # is because that is what it is called; it should stop appearing here
        # once those programmes are cleaned up on the Connect side.
        "ace": "ACE",
    }
)

# No delivery_type on the programme at all -- 168 opportunities, ~46k units of
# work. Connect does not say what they are, so neither do we: "Service
# delivery" read like a category rather than the absence of one.
SERVICE_LABELS["other"] = "Unclassified"


def service_label(slug: str | None) -> str:
    """Display text for a delivery type, without inventing one."""
    if not slug:
        return SERVICE_LABELS["other"]
    return SERVICE_LABELS.get(slug) or slug.upper()


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


# Programmes whose name says they are not real delivery. Kept deliberately
# narrow: a "[PARTNER]" bracket prefix is a naming convention across genuine
# programmes ("[RUWOYD] CHC Mapping"), so matching on brackets would hide 2/3
# of the real portfolio. Only explicit words count.
_TEST_PROGRAM = re.compile(r"\b(test|demo|sandbox|dummy|trial|smoke|e2e)\b", re.I)


def looks_like_test(program_name: str | None) -> bool:
    """Whether a programme is internal scaffolding rather than delivery.

    Used to keep the programme filter honest: these carry real visit counts
    (one has 9,035) so they cannot be spotted by volume, and a funder picking
    "[TEST 02] Dimagi-GW CHC Program" out of a menu is a bad moment.
    """
    return bool(_TEST_PROGRAM.search(program_name or ""))


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


def work_key_for(record: dict) -> str:
    """Stable dedup key for a completed_work, carrying none of its inputs.

    ``completed_works`` omits ``id`` from the payload (the server uses one for
    the cursor but does not serialise it), so rows have no natural key. The
    identifying tuple is (opportunity, worker, entity, payment unit) — and
    ``entity_id`` is a beneficiary name and phone number.

    Hashing it gives a key that dedupes correctly across overlapping polls
    while storing nothing identifying. The hash is one-way and the entity
    component is never persisted alongside it, so the stored key cannot be
    reversed into the beneficiary it describes.
    """
    parts = (
        str(record.get("opportunity_id") or ""),
        str(record.get("username") or ""),
        str(record.get("entity_id") or ""),
        str(record.get("payment_unit_id") or ""),
    )
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def work_to_fields(record: dict, opportunity=None) -> dict | None:
    """Normalise one completed_works record into PulseWork kwargs.

    Reads named keys only — ``entity_id``/``entity_name`` feed the hash and are
    never carried through.
    """
    created = _parse_ts(record.get("date_created"))
    if created is None:
        return None

    status = (record.get("status") or "").strip() or "unknown"
    return {
        "work_key": work_key_for(record),
        "opportunity_id": int(record.get("opportunity_id") or getattr(opportunity, "opportunity_id", 0) or 0),
        "program_id": getattr(opportunity, "program_id", None) if opportunity is not None else None,
        "org_slug": (getattr(opportunity, "org_slug", "") if opportunity is not None else "") or "",
        "worker_hash": (str(record.get("username") or "").strip())[:64],
        "payment_unit_id": record.get("payment_unit_id"),
        "service_slug": service_slug_for(getattr(opportunity, "name", "") if opportunity is not None else ""),
        "country": (getattr(opportunity, "country", "") if opportunity is not None else "") or "",
        "status": status[:32],
        "created_ts": created,
        "status_ts": _parse_ts(record.get("status_modified_date")),
        "payment_date": _parse_ts(record.get("payment_date")),
        "approved_count": int(record.get("saved_approved_count") or 0),
        "completed_count": int(record.get("saved_completed_count") or 0),
        "usd_to_worker": _decimal_or_none(record.get("saved_payment_accrued_usd")),
        "usd_to_org": _decimal_or_none(record.get("saved_org_payment_accrued_usd")),
    }


def _decimal_or_none(raw):
    if raw in (None, "", "None"):
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, TypeError, ValueError):
        return None


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
