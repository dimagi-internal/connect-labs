"""One-off backfill for the flw_weekly_audit_report template's weekly runs.

Computes and completes one WorkflowRun per opportunity, per week, for the N
most recent completed Monday-Sunday weeks (UTC) before now — reusing
run_default verbatim (imported directly, not reimplemented) so backfilled
runs and future scheduler-fired runs use identical logic.

Mints its Connect access token from the given owner's persisted
UserConnectToken (get_valid_access_token), the same mechanism
run_scheduled_workflow uses — no raw token needs to be copied into the
--token CLI arg or a CI log.

Not idempotent by default: every call creates a fresh WorkflowRun per
opportunity per week, matching this codebase's existing "Fire = execute, no
reuse" convention for scheduled-report templates (see audit_generation.py).
Pass --replace-existing to first delete any run(s) already present for the
same (definition, period_start) before creating the new one -- e.g. after
fixing a bug in flw_audit_compute.py's indicator math, to replace
previously-backfilled runs computed under the old (incorrect) logic rather
than accumulating duplicates alongside them.

Intended to be invoked once via the run-labs-command.yml GitHub Action
against production, e.g.:

    backfill_flw_weekly_audit_report --definition 6621 --program 176 \\
        --owner-email wvink@dimagi.com --weeks 4 --replace-existing
"""

import json
from datetime import datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from connect_labs.users.models import User
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.templates.flw_weekly_audit_report import run_default


class Command(BaseCommand):
    help = "Backfill flw_weekly_audit_report runs for the last N completed Mon-Sun weeks."

    def add_arguments(self, parser):
        parser.add_argument("--definition", type=int, required=True, help="Workflow definition id.")
        parser.add_argument("--program", type=int, required=True, help="Owning program id.")
        parser.add_argument(
            "--owner-email",
            type=str,
            required=True,
            help="Email of the user whose persisted Connect token mints the access token.",
        )
        parser.add_argument(
            "--weeks",
            type=int,
            default=4,
            help="Number of most-recent completed Mon-Sun weeks to backfill (default 4).",
        )
        parser.add_argument(
            "--replace-existing",
            action="store_true",
            help="Delete any run(s) already present for the same (definition, period_start) before "
            "creating the new one, instead of accumulating duplicates alongside them.",
        )

    def _delete_existing_runs_for_period(self, token, definition_id, program_id, period_start_iso):
        program_wda = WorkflowDataAccess(access_token=token, program_id=program_id)
        try:
            existing = program_wda.list_runs(definition_id=definition_id)
        finally:
            program_wda.close()

        stale = [r for r in existing if r.data.get("period_start") == period_start_iso]
        for run in stale:
            opp_wda = WorkflowDataAccess(access_token=token, opportunity_id=run.opportunity_id)
            try:
                opp_wda.delete_run(run.id, delete_linked=True)
                self.stdout.write(f"Deleted stale run {run.id} (opp {run.opportunity_id}, period {period_start_iso})")
            finally:
                opp_wda.close()
        return len(stale)

    def handle(self, *args, **options):
        try:
            owner = User.objects.get(email=options["owner_email"])
        except User.DoesNotExist:
            raise CommandError(f"No user with email {options['owner_email']!r}")

        try:
            token = get_valid_access_token(owner)
        except ConnectTokenError as e:
            raise CommandError(str(e))

        wda = WorkflowDataAccess(access_token=token, program_id=options["program"])
        try:
            definition = wda.get_definition(options["definition"])
        finally:
            wda.close()
        if definition is None:
            raise CommandError(f"Definition {options['definition']} not found under program {options['program']}")

        now = datetime.now(timezone.utc)
        this_monday = datetime(now.year, now.month, now.day, tzinfo=timezone.utc) - timedelta(days=now.weekday())
        if now < this_monday:
            this_monday -= timedelta(days=7)

        weeks = options["weeks"]
        for i in range(weeks):
            window_end = this_monday - timedelta(days=7 * i)
            window_start = window_end - timedelta(days=7)
            period_start_iso = window_start.date().isoformat()

            if options["replace_existing"]:
                deleted = self._delete_existing_runs_for_period(
                    token, options["definition"], options["program"], period_start_iso
                )
                if deleted:
                    self.stdout.write(f"Replaced {deleted} stale run(s) for period {period_start_iso}.")

            result = run_default(
                definition=definition,
                access_token=token,
                request=None,
                window=(window_start, window_end),
            )
            self.stdout.write(
                f"Week {window_start.date()} - {(window_end - timedelta(days=1)).date()}: {json.dumps(result)}"
            )

        self.stdout.write(self.style.SUCCESS(f"Backfilled {weeks} week(s) for definition {options['definition']}."))
