"""Report what the Pulse poller can actually see, and compare it to a baseline.

Why this exists: every headline number on a Pulse display — 494 opportunities,
1.65 million services — is bounded by the org membership of whichever Connect
user the poller runs as. Those figures were measured through one account
(``ace@dimagi-ai.com``) during design. If the configured poller sees a different
set, the display silently shows different numbers, and nobody would know which
account produced them.

So: run this before trusting anything, and again whenever the poller changes.

    python manage.py pulse_scope
    python manage.py pulse_scope --baseline           # compare to the design baseline
    python manage.py pulse_scope --profile main       # bootstrap: use a CLI token

``--profile`` exists to break a chicken-and-egg: you cannot set
PULSE_POLLER_USERNAME confidently until you know which account sees what, but
the normal path needs that setting to resolve a user. With a CLI token from
``~/.commcare-connect/token.json`` this reports both the account's identity and
its scope, so you can then configure it knowingly.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.integrations.connect.export_client import ExportAPIClient
from connect_labs.pulse.client import PulseAuthError, fetch_json, get_client, get_poller_user

CLI_TOKEN_PATH = Path.home() / ".commcare-connect" / "token.json"

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
        parser.add_argument(
            "--profile",
            default="",
            help="Bootstrap using a CLI token profile from ~/.commcare-connect/token.json "
            "instead of the configured poller user.",
        )

    def _client_from_profile(self, profile: str):
        """Bootstrap path: authenticate from a locally stored CLI token."""
        if not CLI_TOKEN_PATH.exists():
            raise CommandError(f"No CLI token file at {CLI_TOKEN_PATH}.")
        blob = json.loads(CLI_TOKEN_PATH.read_text())
        profiles = blob.get("profiles") or {}
        entry = profiles.get(profile)
        if not entry:
            raise CommandError(f"Profile {profile!r} not found. Available: {', '.join(sorted(profiles)) or 'none'}")

        expires_at = entry.get("expires_at")
        token = entry.get("access_token")
        if not token:
            raise CommandError(f"Profile {profile!r} has no access_token.")

        identity = self._introspect(token)
        return (
            ExportAPIClient(base_url=settings.CONNECT_PRODUCTION_URL, access_token=token, timeout=120.0),
            identity,
            expires_at,
        )

    def _introspect(self, token: str) -> dict:
        """Ask Connect who this token belongs to — that username is exactly what
        PULSE_POLLER_USERNAME must be set to."""
        from connect_labs.labs.integrations.connect.oauth import introspect_token

        client_id = getattr(settings, "CONNECT_OAUTH_CLIENT_ID", None) or os.environ.get("CONNECT_OAUTH_CLIENT_ID")
        client_secret = getattr(settings, "CONNECT_OAUTH_CLIENT_SECRET", None) or os.environ.get(
            "CONNECT_OAUTH_CLIENT_SECRET"
        )
        if not (client_id and client_secret):
            return {}
        try:
            return introspect_token(token, client_id, client_secret, settings.CONNECT_PRODUCTION_URL) or {}
        except Exception:
            return {}

    def handle(self, *args, **options):
        profile = options["profile"]
        identity, expires_at, user = {}, None, None

        try:
            if profile:
                client, identity, expires_at = self._client_from_profile(profile)
            else:
                user = get_poller_user()
                client = get_client()
            with client:
                payload = fetch_json(client, "/export/opp_org_program_list/")
        except PulseAuthError as exc:
            raise CommandError(
                f"{exc}\n\n"
                "Pulse polls as a designated Connect user. Set PULSE_POLLER_USERNAME and make "
                "sure that user has signed into labs in a browser so a refresh token exists.\n"
                "To bootstrap without that, run with --profile <cli-token-profile>."
            )

        opps = payload.get("opportunities") or []
        actual = {
            "orgs": len(payload.get("organizations") or []),
            "programs": len(payload.get("programs") or []),
            "opportunities": len(opps),
            "active_opportunities": sum(1 for o in opps if o.get("is_active")),
            "lifetime_visits": sum(o.get("visit_count") or 0 for o in opps),
        }

        who = user.username if user else (identity.get("username") or "unknown")

        if options["json"]:
            self.stdout.write(json.dumps({"user": who, "identity": identity, "scope": actual}, indent=2))
            return

        self.stdout.write(self.style.MIGRATE_HEADING(f"Connect account: {who}"))
        if identity:
            email = identity.get("email") or "—"
            self.stdout.write(f"  {'email':<22} {email}")
            self.stdout.write(self.style.NOTICE(f"  → set PULSE_POLLER_USERNAME={who}"))
        if expires_at:
            self.stdout.write(f"  {'token expires':<22} {expires_at}")
        self.stdout.write("")
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
