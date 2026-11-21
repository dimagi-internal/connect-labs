"""The computed sources a page over a project can name. Each is registered with ``hqdata.computed.register``."""

from __future__ import annotations

import time
from contextlib import contextmanager

from connect_labs.hqdata import computed
from connect_labs.hqdata.stores import StoreUnavailable

#: How long one fill may spend bringing the campaign's pipelines up to date before it hands over to the next (the
#: work done is kept): inside the fill task's time limit.
PREPARE_SECONDS = 25 * 60
#: How long one fill of the satellite check may run before handing over to the next (the footprints read are kept).
REACH_SECONDS = 32 * 60


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
    """Coverage against the satellite population and building footprints, per district and cycle (slow: background).

    From the forms alone: where each dose was recorded and the district its centre is in. Nothing is placed that the
    data does not place itself (``campaign.reach_rows``)."""
    from connect_labs.microplans.coverage import campaign, population, squares

    started = time.monotonic()
    spec = campaign.CampaignSpec.from_dict(params)
    try:
        with _campaign_data(user, project, spec, report) as data:
            scanned = campaign.scan(data, spec, with_points=True)
            names, totals = scanned["names"], scanned["counts"]
            labels = {csi: names.get(district) or district or "(no district)" for (district, csi, _cycle) in totals}
            plan = campaign.targets(data, spec)
            rows, metadata = campaign.reach_rows(
                data, spec, labels, plan, report, scanned=scanned, deadline=started + REACH_SECONDS
            )
    except squares.SquaresContinue as exc:
        raise computed.ComputedContinues() from exc
    except (campaign.CampaignSpecError, ValueError) as exc:
        raise computed.ComputedProblem(str(exc)) from exc
    metadata["grid_name"] = population.GRID_NAME
    return {"rows": rows, "metadata": metadata}


def _campaign_params(params: dict) -> dict:
    return _campaign_spec(params).as_dict()


computed.register("campaign_coverage", _campaign_params, _campaign_coverage, slow=True)

# v2: from the forms alone (no health-centre catchments), against the district's department. v3: districts with no
# placed dose are named, not shown.
computed.register("campaign_reach", _campaign_params, _campaign_reach, slow=True, version=3)
