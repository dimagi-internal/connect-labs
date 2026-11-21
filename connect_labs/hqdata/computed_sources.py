"""The computed sources a page over a project can name. Each is registered with ``hqdata.computed.register``."""

from __future__ import annotations

import time
from contextlib import contextmanager

from connect_labs.hqdata import computed
from connect_labs.hqdata.stores import StoreUnavailable

#: How long one fill may spend bringing the campaign's pipelines up to date before it hands over to the next (the
#: work done is kept): inside the fill task's time limit.
PREPARE_SECONDS = 25 * 60


def _campaign_spec(params: dict):
    from connect_labs.microplans.coverage import campaign

    try:
        return campaign.CampaignSpec.from_dict(params)
    except campaign.CampaignSpecError as exc:
        raise computed.ComputedProblem(str(exc)) from exc


@contextmanager
def _campaign_data(user, project, spec, report):
    """The campaign's pipelines brought up to date (``campaign.prepare``), in parts if that takes long."""
    from connect_labs.labs.analysis.backends.sql.incremental import MaintenanceContinues
    from connect_labs.microplans.coverage import campaign

    report(
        "Bringing the campaign's records up to date (only what changed since the last time)",
        stage_name="records",
        current_stage=1,
        total_stages=2,
    )
    try:
        yield campaign.prepare(user, project, spec, report, deadline=time.monotonic() + PREPARE_SECONDS)
    except MaintenanceContinues as exc:
        raise computed.ComputedContinues() from exc
    except StoreUnavailable as exc:
        raise computed.ComputedProblem(str(exc)) from exc


def _campaign_coverage(user, project, params, report):
    """Children reached per health centre and cycle against the centre's plan (``microplans.coverage.campaign``)."""
    from connect_labs.microplans.coverage import campaign

    spec = campaign.CampaignSpec.from_dict(params)
    with _campaign_data(user, project, spec, report) as data:
        report("Counting children per health centre and cycle", stage_name="counting", current_stage=2, total_stages=2)
        rows, metadata = campaign.rows(data, spec)
    return {"rows": rows, "metadata": metadata}


def _campaign_reach(user, project, params, report):
    """Coverage against satellite population and buildings reached, per district and cycle (slow: background).

    The health centres are placed from CommCare's locations when the person's login may read them, else where each
    centre's plan form was filled in (never from the distribution records)."""
    from connect_labs.hqdata import locations
    from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none
    from connect_labs.microplans.coverage import campaign, commcare, request

    spec = campaign.CampaignSpec.from_dict(params)
    report("Finding where each health centre is", stage_name="locations", current_stage=1, total_stages=4)
    token = cchq_token_or_none(user)
    found, source, why = {"facilities": [], "without_coordinates": 0}, "commcare_locations", None
    if token is not None:
        try:
            found = commcare.facility_list(token, project.domain, spec.facility_type)
        except locations.LocationsError as exc:
            why = str(exc)
    try:
        with _campaign_data(user, project, spec, report) as data:
            scanned = campaign.scan(data, spec, with_points=True)
            names, totals = scanned["names"], scanned["counts"]
            centre_district = {csi: names.get(district) or district for (district, csi, _cycle) in totals}
            if not found["facilities"]:
                source = "plan_forms"
                points = campaign.plan_points(data, spec)
                found = {
                    "facilities": [{"id": c, "lon": p[0], "lat": p[1]} for c, p in sorted(points.items())],
                    "without_coordinates": len(set(centre_district) - set(points)),
                }
            facilities = found["facilities"][: request.MAX_FACILITIES_PER_RUN]
            if not facilities:
                raise computed.ComputedProblem(
                    "No health centre has a location in CommCare or from its plan forms, so no catchment can be drawn."
                )
            labels = {f["id"]: centre_district.get(f["id"]) or "(no district)" for f in facilities}
            plan = campaign.targets(data, spec)
            rows, metadata = campaign.reach_rows(data, spec, facilities, labels, plan, report, scanned=scanned)
    except request.BadRequest as exc:
        raise computed.ComputedProblem(str(exc)) from exc
    metadata["facilities_without_coordinates"] = found["without_coordinates"]
    metadata["facilities_over_limit"] = max(0, len(found["facilities"]) - len(facilities))
    metadata["facility_source"] = source
    if why:
        metadata["facility_note"] = (
            f"CommCare's locations could not be read ({why}); each centre is placed where its plan forms were "
            "filled in."
        )
    return {"rows": rows, "metadata": metadata}


def _campaign_params(params: dict) -> dict:
    return _campaign_spec(params).as_dict()


computed.register("campaign_coverage", _campaign_params, _campaign_coverage, slow=True)

computed.register("campaign_reach", _campaign_params, _campaign_reach, slow=True)
