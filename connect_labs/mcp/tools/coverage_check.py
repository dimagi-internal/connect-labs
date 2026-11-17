"""``microplans_coverage_check``: how many of a campaign's buildings did the recorded households reach?

Runs ``microplans/coverage/check.py`` over a campaign's visits, read the way ``data_query`` reads them: as the
caller, over a data space, with the Data query's own limits and the sensitive-data rule (an agent labs has not
cleared gets nothing back for production rows, just as ``data_query`` withholds them). The household points are
read in shards that stay under the query row cap, so a large campaign is several reads, not a silently cut one.
The answer is counts per area and cycle plus a data-quality account; no household or point is returned.
"""

from __future__ import annotations

import logging

from connect_labs.labsdata import service
from connect_labs.labsdata.engine import QueryError
from connect_labs.labsdata.scope import ScopeError
from connect_labs.microplans.coverage import check, read

from ..tool_registry import MCPToolError, register
from .labsdata import _caller, _from_canopy, _query_error, _scope_error, _space

logger = logging.getLogger(__name__)


def read_households(caller, space, spec: check.CheckSpec, agent: bool) -> list:
    """Every household row of the space, shard by shard (``microplans/coverage/read.py``)."""
    try:
        return read.read_households(caller, space, spec, agent)
    except read.RowCapExceeded as e:
        raise MCPToolError("CAPACITY", str(e), {"reason": "truncated"}) from e


def _commcare_check(user, settings, project_id, app_id, xmlns) -> dict:
    """The same check over a CommCare project's forms, as a member of it (counts only, like the Connect read)."""
    from connect_labs.hqdata.access import ProjectAccessDenied, may_use_project
    from connect_labs.hqdata.models import HQProject
    from connect_labs.hqdata.pipelines import PipelineProblem
    from connect_labs.hqdata.stores import StoreUnavailable
    from connect_labs.microplans.coverage import commcare as commcare_source

    project = HQProject.objects.filter(pk=project_id).first()
    if project is None or not may_use_project(user, project):
        raise MCPToolError("NOT_FOUND", "No such CommCare project, or you are not a member of it.")
    settings = dict(settings or {})
    if not settings.get("facilities"):
        # No facility list given: the project's own, from its CommCare locations (as the caller's CommCare login).
        from connect_labs.hqdata import locations
        from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none

        token = cchq_token_or_none(user)
        if token is None:
            raise MCPToolError(
                "INVALID_SCHEMA",
                "Connect your CommCare login (/labs/commcare/initiate/) so the facilities can be read from the "
                "project's locations, or pass settings.facilities.",
                {"reason": "commcare_not_connected"},
            )
        try:
            settings["facilities"] = [
                {"id": f["id"], "lon": f["lon"], "lat": f["lat"]}
                for f in commcare_source.facility_list(token, project.domain)["facilities"]
            ]
        except locations.LocationsError as e:
            raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "commcare_locations"}) from e
    try:
        spec = check.CheckSpec.from_dict(settings)
    except check.SpecError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "bad_settings"}) from e
    try:
        rows, _served = commcare_source.read(user, project, spec, app_id=app_id or "", xmlns=xmlns or "")
        result = check.run_check(rows, spec)
    except (PipelineProblem, ProjectAccessDenied, StoreUnavailable) as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "commcare_read"}) from e
    except check.SpecError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "bad_settings"}) from e
    result["total"] = check.total_row(result)
    return result


@register(
    name="microplans_coverage_check",
    description=(
        "Buildings reached: for a campaign's households (a visit form records a household's GPS point on its first "
        "visit), what share of the buildings around each facility did the households visited in each cycle get "
        "close to? The buildings are the microplanner's own (Overture footprints, same cleaning), the catchment "
        "around each facility is fixed by the facility alone (its nearest `catchment_k` buildings, or a fixed "
        "`catchment_radius_m`), and 'reached' means a household's point lies within `max_dist_m` of a building's "
        "centre. Reads the opportunities (or space) as the caller. Returns counts per facility and cycle plus a "
        "data-quality account (no point, 0,0, off-area, poor accuracy, usable); never a household or a point. "
        "Settings default to the eCHIS Niger SMC form's paths; `facilities` is required."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "opportunity_ids": {"type": "array", "items": {"type": "integer"}},
            "program_ids": {"type": "array", "items": {"type": "integer"}},
            "space_id": {"type": "integer"},
            "commcare_project_id": {
                "type": "integer",
                "description": (
                    "Read the households from this CommCare project's stored forms (a campaign CommCare ran: Niger "
                    "SMC) instead of Connect visits. You must belong to the project; the forms are the ones whose "
                    "paths `settings` names. Optional `commcare_app_id` / `commcare_xmlns` narrow to one app or form. "
                    "Without `settings.facilities` the facilities are the project's CommCare locations of the "
                    "facility type."
                ),
            },
            "commcare_app_id": {"type": "string"},
            "commcare_xmlns": {"type": "string"},
            "settings": {
                "type": "object",
                "description": (
                    "household_path, gps_paths[], cycle_path, group_path (form paths, e.g. form.group.question); "
                    "facilities[{id, lon, lat}] (id = the group_path value); catchment_k or catchment_radius_m; "
                    "max_dist_m (default 30); max_accuracy_m (default 50); off_area_m; footprint_filters "
                    "(any microplanner FilterConfig field: area_min_m2, area_max_m2, tiny_neighbor_dist_m, "
                    "tiny_neighbor_min)."
                ),
            },
        },
        "required": ["settings"],
        "additionalProperties": False,
    },
    is_write=False,
)
def microplans_coverage_check(
    user,
    settings,
    opportunity_ids=None,
    program_ids=None,
    space_id=None,
    commcare_project_id=None,
    commcare_app_id="",
    commcare_xmlns="",
):
    if commcare_project_id is not None:
        return _commcare_check(user, settings, commcare_project_id, commcare_app_id, commcare_xmlns)
    agent = _from_canopy()
    caller = _caller(user)
    try:
        spec = check.CheckSpec.from_dict(settings)
    except check.SpecError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "bad_settings"}) from e
    space = _space(caller, space_id, opportunity_ids, program_ids, None)
    try:
        rows = read_households(caller, space, spec, agent)
    except ScopeError as e:
        raise _scope_error(e) from e
    except service.SensitiveDataRefused as e:
        return {"withheld": True, "reason": e.code, "message": e.message}
    except QueryError as e:
        raise _query_error(e) from e
    try:
        result = check.run_check(rows, spec)
    except check.SpecError as e:
        raise MCPToolError("INVALID_SCHEMA", str(e), {"reason": "bad_settings"}) from e
    result["total"] = check.total_row(result)
    return result
