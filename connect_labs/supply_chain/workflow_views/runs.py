"""The run a pinned workflow shows: its newest open one, or a new one started today.

Kept out of views.py on purpose. Supply views may not write records except through an
operation (tests/test_views.py), and that rule is about SUPPLY records. A run is the
workflow's own record, created through the workflow's data access as the viewer, exactly
as opening the workflow's own page with no run does.
"""

from datetime import date

from connect_labs.workflow.data_access import WorkflowDataAccess


def current_run_id(request, pin) -> int | None:
    """`pin` is a supply_chain.config.Pin."""
    access = WorkflowDataAccess(request=request)
    try:
        runs = [
            r
            for r in access.list_runs(pin.workflow_definition_id)
            if r.status == "in_progress" and (not pin.opportunity_id or r.opportunity_id == pin.opportunity_id)
        ]
        if runs:
            return max(runs, key=lambda r: r.id).id
        today = date.today().isoformat()
        run = access.create_run(
            pin.workflow_definition_id,
            opportunity_id=pin.opportunity_id,
            period_start=today,
            period_end=today,
        )
        return run.id
    finally:
        access.close()
