"""A project's locations (its health facilities, districts...) as CommCare keeps them.

CommCare apps record the facility on a form as a location id (``instance('locations')/locations/location[@id = ...]``),
and CommCare holds each location's name, type, parent and coordinates. This reads them with the person's own CommCare
token, live and never stored, so a report that needs "the facilities" (the coverage viewer) takes them from CommCare
instead of from a list someone has to type in.
"""

from __future__ import annotations

import httpx

from connect_labs.hqdata.identity import hq_base_url

#: One page of locations; and the most pages read, a guard against a loop rather than a limit anyone meets.
PAGE_SIZE = 1000
MAX_PAGES = 100


class LocationsError(Exception):
    """CommCare could not give the locations; the message says why, for the person."""


def _get_all(access_token: str, path: str, limit: int) -> list[dict]:
    url: str | None = f"{hq_base_url()}{path}?limit={limit}"
    out: list[dict] = []
    for _ in range(MAX_PAGES):
        try:
            resp = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
        except httpx.RequestError as exc:
            raise LocationsError(f"Could not reach CommCare for the locations: {exc}") from exc
        if resp.status_code in (401, 403):
            raise LocationsError(
                "CommCare did not let your login read this project's locations (it needs the permission to view "
                "locations)."
            )
        if resp.status_code != 200:
            raise LocationsError(f"CommCare returned {resp.status_code} for the locations.")
        try:
            data = resp.json()
        except ValueError as exc:
            raise LocationsError("CommCare's locations answer was not JSON.") from exc
        out.extend(o for o in data.get("objects") or [] if isinstance(o, dict))
        next_path = (data.get("meta") or {}).get("next")
        if not next_path:
            return out
        url = next_path if next_path.startswith("http") else f"{hq_base_url()}{path}{next_path}"
    raise LocationsError("CommCare's locations list did not end.")


def _id_in(uri) -> int | None:
    """The numeric id at the end of a resource URI (``.../location_type/20292/``)."""
    parts = [p for p in str(uri or "").split("/") if p]
    return int(parts[-1]) if parts and parts[-1].isdigit() else None


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fetch(access_token: str, domain: str) -> tuple[list[dict], list[dict]]:
    """``(location types, locations)`` of a project space.

    Each type is ``{"id", "name", "code"}``; each location ``{"location_id", "name", "type_id", "type", "lon", "lat",
    "parent"}``, where ``parent`` is the parent's name and the coordinates are None when CommCare holds none."""
    base = f"/a/{domain}/api/v0.5"
    types = [
        {"id": t.get("id"), "name": t.get("name") or "", "code": t.get("code") or ""}
        for t in _get_all(access_token, f"{base}/location_type/", 100)
    ]
    type_names = {t["id"]: t["name"] for t in types}
    raw = _get_all(access_token, f"{base}/location/", PAGE_SIZE)
    names = {o.get("id"): o.get("name") or "" for o in raw}
    locations = []
    for o in raw:
        type_id = _id_in(o.get("location_type"))
        locations.append(
            {
                "location_id": o.get("location_id"),
                "name": o.get("name") or "",
                "type_id": type_id,
                "type": type_names.get(type_id, ""),
                "lon": _number(o.get("longitude")),
                "lat": _number(o.get("latitude")),
                "parent": names.get(_id_in(o.get("parent")), ""),
            }
        )
    return types, locations


def facility_type(types: list[dict]) -> int | None:
    """The location type that is probably the health facility (named or coded like a CSI/facility), else None."""
    for word in ("csi", "facility", "clinic", "health"):
        for t in types:
            if word in f"{t['name']} {t['code']}".lower():
                return t["id"]
    return None
