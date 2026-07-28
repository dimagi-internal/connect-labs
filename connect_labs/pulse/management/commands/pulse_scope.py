"""Report what the Pulse poller can actually see, and compare it to a baseline.

Why this exists: every headline number on a Pulse display — 494 opportunities,
1.65 million services — is bounded by the org membership of whichever Connect
user the poller runs as. Those figures were measured through one account
(``ace@dimagi-ai.com``) during design. If the configured poller sees a different
set, the display silently shows different numbers, and nobody would know which
account produced them.

So: run this before trusting anything, and again whenever the poller changes.

    python manage.py pulse_scope
    python manage.py pulse_scope --baseline    # compare to the design baseline
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError

from connect_labs.pulse.client import PulseAuthError, fetch_json, get_client, get_poller_user

# Measured 2026-07-28 through ace@dimagi-ai.com. Recorded so drift is visible
# rather than inferred; see docs/superpowers/specs/2026-07-28-connect-pulse-design.md
ACE_BASELINE = {
    "orgs": 15,
    "programs": 108,
    "opportunities": 494,
    "active_opportunities": 64,
    "lifetime_visits": 1647855,
}


class Command(BaseCommand):
    help = "Report the Connect scope visible to the Pulse poller user."

    def add_arguments(self, parser):
        parser.add_argument(
            "--baseline",
            action="store_true",
            help="Compare against the ace@dimagi-ai.com baseline measured during design.",
        )
        parser.add_argument("--json", action="store_true", help="Emit machine-readable output.")

    def handle(self, *args, **options):
        try:
            user = get_poller_user()
            with get_client() as client:
                payload = fetch_json(client, "/export/opp_org_program_list/")
        except PulseAuthError as exc:
            raise CommandError(
                f"{exc}\n\n"
                "Pulse polls as a designated Connect user. Set PULSE_POLLER_USERNAME and make "
                "sure that user has signed into labs in a browser so a refresh token exists."
            )

        opps = payload.get("opportunities") or []
        actual = {
            "orgs": len(payload.get("organizations") or []),
            "programs": len(payload.get("programs") or []),
            "opportunities": len(opps),
            "active_opportunities": sum(1 for o in opps if o.get("is_active")),
            "lifetime_visits": sum(o.get("visit_count") or 0 for o in opps),
        }

        if options["json"]:
            self.stdout.write(json.dumps({"user": user.username, "scope": actual}, indent=2))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"Pulse poller: {user.username}"))
        for key, value in actual.items():
            self.stdout.write(f"  {key:<22} {value:>12,}")

        if not options["baseline"]:
            return

        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("vs ace@dimagi-ai.com baseline (2026-07-28)"))
        drifted = False
        for key, expected in ACE_BASELINE.items():
            got = actual[key]
            delta = got - expected
            if delta == 0:
                self.stdout.write(f"  {key:<22} {got:>12,}  same")
                continue
            drifted = True
            pct = (delta / expected * 100) if expected else 0
            style = self.style.WARNING if abs(pct) > 5 else self.style.NOTICE
            self.stdout.write(style(f"  {key:<22} {got:>12,}  {delta:+,} ({pct:+.1f}%)"))

        self.stdout.write("")
        if drifted:
            self.stdout.write(
                self.style.WARNING(
                    "Scope differs from the design baseline. This is not necessarily wrong — a "
                    "different account legitimately sees a different set — but the numbers on "
                    "every display will differ from the spec, so update the spec's measured "
                    "baseline rather than leaving the two disagreeing."
                )
            )
        else:
            self.stdout.write(self.style.SUCCESS("Scope matches the design baseline exactly."))
