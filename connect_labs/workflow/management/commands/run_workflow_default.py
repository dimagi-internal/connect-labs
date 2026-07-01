"""Run a workflow in its default (no-UI) mode from the CLI.

Generic dispatcher over ``run_default_for_definition``: loads the workflow
definition (opp-scoped) and hands it to the template's ``run_default`` hook. The
command prints the hook's result JSON.

Token: labs has no service-token path, so the caller supplies one explicitly via
``--token`` (a labs OAuth access token, e.g. copied from an authenticated
session or an MCP PAT-minted token). Intended for cron/ops use where a token is
provisioned out of band.

Examples::

    python manage.py run_workflow_default --definition 42 --opportunity 1973 \\
        --token "$LABS_TOKEN"

    # Optional explicit window preset (creator templates that accept it):
    python manage.py run_workflow_default --definition 42 --opportunity 1973 \\
        --window last_week --token "$LABS_TOKEN"
"""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from connect_labs.workflow import audit_generation
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates import run_default_for_definition


class Command(BaseCommand):
    help = "Run a workflow in its default (no-UI) mode via its template's run_default hook."

    def add_arguments(self, parser):
        parser.add_argument("--definition", type=int, required=True, help="Workflow definition id.")
        parser.add_argument(
            "--opportunity",
            type=int,
            required=True,
            help="Opportunity id that owns the definition (scopes the load).",
        )
        parser.add_argument("--token", type=str, required=True, help="Labs OAuth access token.")
        parser.add_argument(
            "--window",
            type=str,
            help="Optional window preset (e.g. 'last_week') forwarded to the template's run_default.",
        )

    def handle(self, *args, **options):
        wda = WorkflowDataAccess(access_token=options["token"], opportunity_id=options["opportunity"])
        try:
            definition = wda.get_definition(options["definition"])
        finally:
            wda.close()
        if definition is None:
            raise CommandError(
                f"Workflow definition {options['definition']} not found (opp {options['opportunity']})."
            )

        kwargs = {}
        if options.get("window"):
            kwargs["window"] = audit_generation.resolve_window(options["window"], date.today())

        result = run_default_for_definition(definition, access_token=options["token"], **kwargs)

        self.stdout.write(json.dumps(result, default=str, indent=2))
