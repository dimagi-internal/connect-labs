"""Thin management-command wrapper around ``generate_program_audit_batches``.

All logic lives in ``commcare_connect.workflow.audit_generation``; this command
only parses args, resolves the window, and prints the per-opp JSON result.

Token: labs has no service-token path, so the caller supplies one explicitly via
``--token`` (a labs OAuth access token, e.g. copied from an authenticated
session or an MCP PAT-minted token). The command is intended for cron/ops use
where a token is provisioned out of band.

Instances (Phase 1): pass ``--mapping`` as a JSON list of
``{"opportunity_id", "definition_id"}`` (typically the program report's
``config.watched_sources``).

Examples::

    python manage.py generate_program_audit_batches --program 176 \\
        --window last_week --token "$LABS_TOKEN" \\
        --mapping '[{"opportunity_id": 1973, "definition_id": 42}]'

    python manage.py generate_program_audit_batches --program 176 \\
        --start 2026-06-21 --end 2026-06-27 --token "$LABS_TOKEN" \\
        --muac-pct 100 --other-pct 10 --mapping '[...]'
"""

import json
from datetime import date

from django.core.management.base import BaseCommand, CommandError

from commcare_connect.workflow import audit_generation


class Command(BaseCommand):
    help = "Generate per-opp weekly dual-track audit batches for a program (idempotent per opp+window)."

    def add_arguments(self, parser):
        parser.add_argument("--program", type=int, required=True, help="Program id.")
        parser.add_argument("--window", type=str, help="Window preset, e.g. 'last_week'.")
        parser.add_argument("--start", type=str, help="Explicit window start (YYYY-MM-DD).")
        parser.add_argument("--end", type=str, help="Explicit window end (YYYY-MM-DD).")
        parser.add_argument("--muac-pct", type=float, dest="muac_pct", help="MUAC sample percentage override.")
        parser.add_argument("--other-pct", type=float, dest="other_pct", help="Other sample percentage override.")
        parser.add_argument("--token", type=str, required=True, help="Labs OAuth access token.")
        parser.add_argument(
            "--mapping",
            type=str,
            help='JSON list of {"opportunity_id","definition_id"} per-opp instances.',
        )

    def handle(self, *args, **options):
        program_id = options["program"]

        if options.get("window"):
            window_start, window_end = audit_generation.resolve_window(options["window"], date.today())
        elif options.get("start") and options.get("end"):
            window_start, window_end = options["start"], options["end"]
        else:
            raise CommandError("provide --window <preset> OR both --start and --end")

        mapping = json.loads(options["mapping"]) if options.get("mapping") else None

        sample_overrides = {}
        if options.get("muac_pct") is not None:
            sample_overrides["muac_sample_percentage"] = options["muac_pct"]
        if options.get("other_pct") is not None:
            sample_overrides["other_sample_percentage"] = options["other_pct"]

        result = audit_generation.generate_program_audit_batches(
            program_id,
            window_start,
            window_end,
            sample_overrides=sample_overrides or None,
            access_token=options["token"],
            mapping=mapping,
        )

        self.stdout.write(json.dumps(result, default=str, indent=2))
