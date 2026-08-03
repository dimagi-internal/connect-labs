"""One-off backfill for the flw_daily_indicator_report template's daily runs.

Computes and completes one WorkflowRun per opportunity, per day, for N
completed WAT (Africa/Lagos, UTC+1) calendar days ending on --end-date
(default: yesterday WAT) -- reusing run_default verbatim (imported directly,
not reimplemented) so backfilled runs and future scheduler-fired runs use
identical logic. Mirrors backfill_flw_weekly_audit_report.py at daily instead
of weekly granularity.

--end-date lets a later backfill reach further back in time without redoing
days an earlier backfill already covered, e.g. first `--days 14` covers the
most recent 14 days, then a later `--days 40 --end-date <15-days-ago>` fills
in the older gap behind it.

Mints its Connect access token from the given owner's persisted
UserConnectToken (get_valid_access_token), the same mechanism
run_scheduled_workflow uses. Also attempts to mint a CommCare HQ access token
(get_valid_cchq_access_token) for the work_areas (cchq_cases) pipeline's
building-count enrichment -- if the owner hasn't authorized CommCare HQ access
in Labs (CCHQTokenError), the backfill proceeds anyway with
cchq_access_token=None; run_default already degrades gracefully in that case
(indicator #2's ratio is just None for every FLW/day), rather than the whole
backfill failing over one missing scope.

Not idempotent by default: every call creates a fresh WorkflowRun per
opportunity per day, matching flw_weekly_audit_report's own "Fire = execute,
no reuse" convention. Pass --replace-existing to first delete any run(s)
already present for the same (definition, period_start) before creating the
new one.

Intended to be invoked once via the run-labs-command.yml GitHub Action
against production, e.g.:

    backfill_flw_daily_indicator_report --definition 8061 --program 176 \\
        --owner-email wvink@dimagi.com --days 14 --replace-existing

    backfill_flw_daily_indicator_report --definition 8061 --program 176 \\
        --owner-email wvink@dimagi.com --days 40 --end-date 2026-07-16
"""

import json
from datetime import date, datetime, timedelta, timezone

from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError, get_valid_cchq_access_token
from connect_labs.users.models import User
from connect_labs.workflow.data_access import WorkflowDataAccess
from connect_labs.workflow.flw_audit_compute import WAT_OFFSET, wat_date
from connect_labs.workflow.templates.flw_daily_indicator_report import run_default


class Command(BaseCommand):
    help = "Backfill flw_daily_indicator_report runs for the last N completed WAT calendar days."

    def add_arguments(self, parser):
        parser.add_argument("--definition", type=int, required=True, help="Workflow definition id.")
        parser.add_argument("--program", type=int, required=True, help="Owning program id.")
        parser.add_argument(
            "--owner-email",
            type=str,
            required=True,
            help="Email of the user whose persisted Connect (and, if available, CommCare HQ) "
            "token mints the access token(s).",
        )
        parser.add_argument(
            "--days",
            type=int,
            default=14,
            help="Number of completed WAT calendar days to backfill, ending on --end-date (default 14).",
        )
        parser.add_argument(
            "--end-date",
            type=str,
            default=None,
            help="ISO date (YYYY-MM-DD, WAT calendar day) for the most recent day to backfill "
            "(default: yesterday WAT). Use with --days to target an explicit older range, e.g. "
            "--end-date 2026-07-16 --days 40, without re-touching more-recent days already backfilled.",
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

        try:
            cchq_token = get_valid_cchq_access_token(owner)
        except CCHQTokenError as e:
            self.stdout.write(
                self.style.WARNING(
                    f"No usable CommCare HQ token for {options['owner_email']!r} ({e}) -- proceeding without "
                    "one. Indicator #2 (avg forms/building) will be None for every FLW/day in this backfill."
                )
            )
            cchq_token = None

        wda = WorkflowDataAccess(access_token=token, program_id=options["program"])
        try:
            definition = wda.get_definition(options["definition"])
        finally:
            wda.close()
        if definition is None:
            raise CommandError(f"Definition {options['definition']} not found under program {options['program']}")

        if options["end_date"]:
            try:
                end_date_wat = date.fromisoformat(options["end_date"])
            except ValueError:
                raise CommandError(f"--end-date must be YYYY-MM-DD, got {options['end_date']!r}")
        else:
            now = datetime.now(timezone.utc)
            today_wat = (now + WAT_OFFSET).date()
            end_date_wat = today_wat - timedelta(days=1)

        end_date_midnight_utc = (
            datetime(end_date_wat.year, end_date_wat.month, end_date_wat.day, tzinfo=timezone.utc) - WAT_OFFSET
        )

        days = options["days"]
        for i in range(days):
            window_start = end_date_midnight_utc - timedelta(days=i)
            window_end = window_start + timedelta(days=1)
            # Must match what run_default itself tags the created run's period_start
            # with (wat_date(window_start), NOT window_start's raw UTC date) -- using
            # the wrong one here made --replace-existing silently delete the day
            # BEFORE the one actually being (re)created, on every single call.
            period_start_iso = wat_date(window_start)

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
                cchq_access_token=cchq_token,
            )
            self.stdout.write(f"Day {period_start_iso}: {json.dumps(result)}")

        self.stdout.write(self.style.SUCCESS(f"Backfilled {days} day(s) for definition {options['definition']}."))
