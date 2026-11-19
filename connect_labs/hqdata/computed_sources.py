"""The computed sources a page over a project can name. Each is registered with ``hqdata.computed.register``."""

from __future__ import annotations

from connect_labs.hqdata import computed
from connect_labs.hqdata.stores import StoreUnavailable, store_for


def _campaign_spec(params: dict):
    from connect_labs.microplans.coverage import campaign

    try:
        return campaign.CampaignSpec.from_dict(params)
    except campaign.CampaignSpecError as exc:
        raise computed.ComputedProblem(str(exc)) from exc


def _campaign_coverage(user, project, params, report):
    """Children reached per health centre and cycle against the centre's plan (``microplans.coverage.campaign``)."""
    from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none
    from connect_labs.microplans.coverage import campaign

    spec = campaign.CampaignSpec.from_dict(params)
    names, name_note = {}, None
    token = cchq_token_or_none(user)
    if token is None:
        name_note = "CommCare is not connected for you, so health centres show as ids rather than names."
    else:
        from connect_labs.hqdata import locations

        try:
            _types, found = locations.fetch(token, project.domain)
            names = {loc["location_id"]: loc["name"] for loc in found}
        except locations.LocationsError as exc:
            name_note = f"Names could not be read from CommCare ({exc})"
    try:
        with store_for(project).session() as store:
            rows, metadata = campaign.rows(store, project.pk, spec, names)
    except StoreUnavailable as exc:
        raise computed.ComputedProblem(str(exc)) from exc
    if name_note:
        metadata["names_note"] = name_note
    return {"rows": rows, "metadata": metadata}


def _campaign_reach(user, project, params, report):
    """Coverage against satellite population and buildings reached, per district and cycle (slow: background)."""
    from connect_labs.hqdata import locations
    from connect_labs.labs.integrations.commcare.cchq_tokens import cchq_token_or_none
    from connect_labs.microplans.coverage import campaign, commcare, request

    spec = campaign.CampaignSpec.from_dict(params)
    token = cchq_token_or_none(user)
    if token is None:
        raise computed.ComputedProblem("Connect your CommCare login (it names the health centres' locations).")
    report("Reading the health centres' locations from CommCare...")
    try:
        found = commcare.facility_list(token, project.domain, spec.facility_type)
    except locations.LocationsError as exc:
        raise computed.ComputedProblem(str(exc)) from exc
    if found["type_id"] is None:
        raise computed.ComputedProblem(
            "Say which CommCare location type is the health centre (facility_type); none looks like one."
        )
    facilities = found["facilities"][: request.MAX_FACILITIES_PER_RUN]
    if not facilities:
        raise computed.ComputedProblem("No health centre has coordinates in CommCare, so no catchment can be drawn.")
    labels = {f["id"]: f["district"] for f in facilities}
    try:
        with store_for(project).session() as store:
            plan = campaign.targets(store, project.pk, spec)
            report("Counting households and children served...")
            rows, metadata = campaign.reach_rows(store, project.pk, spec, facilities, labels, plan)
    except StoreUnavailable as exc:
        raise computed.ComputedProblem(str(exc)) from exc
    except request.BadRequest as exc:
        raise computed.ComputedProblem(str(exc)) from exc
    metadata["facilities_without_coordinates"] = found["without_coordinates"]
    metadata["facilities_over_limit"] = max(0, len(found["facilities"]) - len(facilities))
    return {"rows": rows, "metadata": metadata}


def _campaign_params(params: dict) -> dict:
    return _campaign_spec(params).as_dict()


computed.register("campaign_coverage", _campaign_params, _campaign_coverage, slow=True)

computed.register("campaign_reach", _campaign_params, _campaign_reach, slow=True)
