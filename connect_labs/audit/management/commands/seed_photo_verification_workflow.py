"""Create a local, ready-to-view Photo Audit Report instance.

    python manage.py seed_photo_verification_workflow

Seeds the demo audit data (two synthetic opps under a demo program), then
creates a workflow definition + render code + a run from the
``photo_audit_report`` template with the filter config pre-filled. Prints
runner URLs for both single-opportunity and whole-program views. Dev-only
convenience; creates a fresh instance each run.
"""

from django.core.management.base import BaseCommand

from connect_labs.audit.photo_verification_demo import (
    AUDITOR,
    C3HD_OPP_ID,
    EHA_OPP_ID,
    IDS_CSV,
    PROGRAM_ID,
    seed_demo,
)
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates import create_workflow_from_template


class Command(BaseCommand):
    help = "Seed a viewable Photo Audit Report instance against the demo program/opps."

    def handle(self, *args, **options):
        seed_demo()

        # Program-owned instance: it loads under program context, shows the
        # program total + per-opportunity breakdown, and drills into one
        # opportunity via its own dropdown. Labs-only program (>= 10_000):
        # writes route to the local ORM backend; the token is unused but
        # required by the constructor.
        da = WorkflowDataAccess(program_id=PROGRAM_ID, access_token="labs-only-dummy")
        definition, _render_code, _pipeline = create_workflow_from_template(
            da, "photo_audit_report", program_id=PROGRAM_ID, opportunity_ids=[EHA_OPP_ID, C3HD_OPP_ID]
        )

        # Scope now comes from the top-right context picker, so config holds only
        # the filters (auditor + audit IDs; dates left open).
        config = {"auditIds": IDS_CSV, "auditor": AUDITOR}
        run = da.create_run(
            definition.id,
            program_id=PROGRAM_ID,
            period_start="2026-09-01",
            period_end="2026-09-22",
            initial_state={"config": config, "phase": "ready"},
        )

        url = f"/labs/workflow/{definition.id}/run/?run_id={run.id}&program_id={PROGRAM_ID}"
        self.stdout.write(self.style.SUCCESS("Created Photo Audit Report instance (program-owned):"))
        self.stdout.write(f"  definition_id={definition.id}  run_id={run.id}")
        self.stdout.write(f"  URL: {url}")
        self.stdout.write(
            "  Expect: program total 81.25%, EHA 85.00%, C3HD 75.00%; drill via the opportunity dropdown."
        )
