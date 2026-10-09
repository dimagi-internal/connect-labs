"""MUAC/Age Plausibility compute job.

A run created by hand ("Create Run" on the workflow list) starts empty: the
figures are classified on the server, never in the browser. Its page offers a
"Compute this report" button, which starts this job via
``actions.startJob(run_id, {job_type: "muac_plausibility_compute", run_id, ...})``.
The job classifies every approved reading (the same ``compute_state`` the
schedule uses) and writes the result into THAT run's state, so the page shows it
on reload. The run stays in progress; the scheduled runs are the completed ones.
"""

import logging

from connect_labs.workflow.tasks import register_job_handler

logger = logging.getLogger(__name__)


@register_job_handler("muac_plausibility_compute")
def muac_plausibility_compute(job_config: dict, access_token: str, progress_callback=None) -> dict:
    """Fill a live muac_plausibility run with freshly classified counts.

    job_config keys:
      - run_id (required): the in-progress run to fill.
      - program_id / opportunity_id (injected by the framework): the run's scope.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.templates.muac_plausibility import STATE_KEY, compute_state

    run_id = job_config.get("run_id")
    if not run_id:
        raise ValueError("muac_plausibility_compute requires run_id in job_config")
    program_id = job_config.get("program_id")
    opportunity_id = job_config.get("opportunity_id")

    def wda():
        if program_id:
            return WorkflowDataAccess(access_token=access_token, program_id=program_id)
        return WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id)

    reader = wda()
    try:
        run = reader.get_run(run_id)
        if run is None:
            raise ValueError(f"run {run_id} not found")
        if run.is_completed:
            raise ValueError(f"run {run_id} is completed; its figures are frozen")
        definition = reader.get_definition(run.definition_id)
        if definition is None:
            raise ValueError(f"definition {run.definition_id} not found")
    finally:
        reader.close()

    if progress_callback:
        progress_callback("Reading and classifying approved visits…", 0, 1)
    computed = compute_state(definition, access_token)
    state = computed["state"]

    writer = wda()
    try:
        writer.update_run_state(run_id, {STATE_KEY: state})
    finally:
        writer.close()
    if progress_callback:
        progress_callback("Saved", 1, 1)

    logger.info(
        "[MuacPlausibility] run %s: %d visits read, %d cells, %d errors",
        run_id,
        computed["visits_read"],
        len(state["cells"]["rows"]),
        len(state["errors"]),
    )
    # Counts only: the cells themselves are in run state, not in the job result.
    return {
        "successful": 1,
        "failed": 0,
        "visits_read": computed["visits_read"],
        "cells": len(state["cells"]["rows"]),
        "errors": state["errors"],
    }
