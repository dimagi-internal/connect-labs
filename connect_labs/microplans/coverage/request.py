"""What the buildings-reached page sends, parsed into the check's settings (no Django: unit-testable on its own)."""

from __future__ import annotations

from connect_labs.microplans.coverage import check

#: The most facilities one run draws catchments for: each is a footprint read.
MAX_FACILITIES = 150
#: The most opportunities one run reads.
MAX_OPPS = 40


class BadRequest(ValueError):
    """The request cannot be run; the message says why."""


def parse_facilities(text: str) -> tuple[list[dict], dict]:
    """``id,lon,lat[,area]`` entries separated by ``;`` or newlines -> (facilities, {id: area label})."""
    facilities, labels = [], {}
    for raw in str(text or "").replace("\n", ";").split(";"):
        raw = raw.strip()
        if not raw:
            continue
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) not in (3, 4, 5):
            raise BadRequest(
                f"facility {raw!r}: write it as id,lon,lat or id,lon,lat,district or id,lon,lat,district,target"
            )
        try:
            lon, lat = float(parts[1]), float(parts[2])
        except ValueError as e:
            raise BadRequest(f"facility {raw!r}: longitude and latitude must be numbers") from e
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise BadRequest(f"facility {raw!r}: longitude/latitude out of range (lon first, then lat)")
        facilities.append({"id": parts[0], "lon": lon, "lat": lat})
        if len(parts) == 5 and parts[4]:
            try:
                float(parts[4].replace(" ", ""))
            except ValueError as e:
                raise BadRequest(f"facility {raw!r}: the target (fifth column) must be a number") from e
        if len(parts) >= 4 and parts[3]:
            labels[parts[0]] = parts[3]
    if not facilities:
        raise BadRequest("name at least one facility: id,lon,lat")
    if len(facilities) > MAX_FACILITIES:
        raise BadRequest(f"at most {MAX_FACILITIES} facilities per run")
    return facilities, labels


def parse_ids(text: str) -> list[int]:
    ids = [int(x) for x in str(text or "").replace(" ", "").split(",") if x.isdigit()]
    if not ids:
        raise BadRequest("name at least one opportunity")
    if len(ids) > MAX_OPPS:
        raise BadRequest(f"at most {MAX_OPPS} opportunities per run")
    return ids


def _number(body: dict, key: str, default, lo, hi):
    raw = body.get(key)
    if raw in (None, ""):
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError) as e:
        raise BadRequest(f"{key} must be a number") from e
    if not lo <= value <= hi:
        raise BadRequest(f"{key} must be between {lo} and {hi}")
    return value


def build_spec(body: dict, need_opps: bool = True) -> tuple[check.CheckSpec, dict, list[int]]:
    """The check's settings from what the page sent. An uploaded file needs no opportunity ids."""
    facilities, labels = parse_facilities(body.get("facilities"))
    settings = {
        "facilities": facilities,
        "max_dist_m": _number(body, "max_dist_m", 30.0, 1.0, 500.0),
        "catchment_k": int(_number(body, "catchment_k", 260, 10, 5000)),
        # The page draws the catchments as a map of cells; a cell under five buildings is never drawn.
        "cell_m": 100.0,
    }
    for key in ("household_path", "cycle_path", "group_path"):
        if body.get(key):
            settings[key] = str(body[key]).strip()
    for key in ("served_path", "served_value", "child_path"):
        if body.get(key):
            settings[key] = str(body[key]).strip()
    if body.get("gps_paths"):
        settings["gps_paths"] = [p.strip() for p in str(body["gps_paths"]).split(",") if p.strip()]
    try:
        return check.CheckSpec.from_dict(settings), labels, (parse_ids(body.get("opps")) if need_opps else [])
    except check.SpecError as e:
        raise BadRequest(str(e)) from e


def parse_iso(text) -> str:
    """The country the population grid is read for: an ISO-3 code such as NER."""
    iso = str(text or "").strip().upper()
    if not iso.isalpha() or len(iso) != 3:
        raise BadRequest("name the country as a three-letter code such as NER (the population lens reads its grid)")
    return iso


def parse_targets(text) -> dict:
    """The optional fifth column of the facility list: an administrative target (children expected) per facility."""
    targets: dict = {}
    for raw in str(text or "").replace("\n", ";").split(";"):
        parts = [p.strip() for p in raw.split(",")]
        if len(parts) >= 5 and parts[4]:
            try:
                value = float(parts[4].replace(" ", ""))
            except ValueError as e:
                raise BadRequest(f"facility {parts[0]!r}: the target must be a number") from e
            if value < 0:
                raise BadRequest(f"facility {parts[0]!r}: the target cannot be negative")
            targets[parts[0]] = value
    return targets
