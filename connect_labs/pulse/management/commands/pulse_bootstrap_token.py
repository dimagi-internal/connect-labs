"""Seed a UserConnectToken from a locally stored CLI token.

Normally a ``UserConnectToken`` is written by the browser OAuth callback, which
is how the poller gets a refreshable token on a deployed environment. Locally
there is usually no such row, so ingest cannot run at all — which makes it
impossible to exercise the real code path on a dev machine.

This lifts the access/refresh pair out of ``~/.commcare-connect/token.json``
into the row the poller expects, so ``poll_cheap_tier`` and ``poll_visit_tail``
run locally exactly as they do deployed.

    python manage.py pulse_bootstrap_token --profile main

Dev convenience only. On a deployed environment the browser login is the right
path, and the refresh token stored there belongs to a real interactive session.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.integrations.connect.oauth import introspect_token
from connect_labs.labs.models import UserConnectToken

CLI_TOKEN_PATH = Path.home() / ".commcare-connect" / "token.json"


class Command(BaseCommand):
    help = "Seed a UserConnectToken from a CLI token profile (local development)."

    def add_arguments(self, parser):
        parser.add_argument("--profile", required=True, help="Profile name in ~/.commcare-connect/token.json")
        parser.add_argument("--username", default="", help="Override the Django username to attach the token to.")

    def handle(self, *args, **options):
        if not CLI_TOKEN_PATH.exists():
            raise CommandError(f"No CLI token file at {CLI_TOKEN_PATH}")

        profiles = json.loads(CLI_TOKEN_PATH.read_text()).get("profiles") or {}
        entry = profiles.get(options["profile"])
        if not entry:
            raise CommandError(
                f"Profile {options['profile']!r} not found. Available: {', '.join(sorted(profiles)) or 'none'}"
            )

        access_token = entry.get("access_token")
        refresh_token = entry.get("refresh_token") or ""
        if not access_token:
            raise CommandError("Profile has no access_token.")
        if not refresh_token:
            self.stdout.write(
                self.style.WARNING(
                    "Profile has no refresh_token — ingest will work until this access token "
                    "expires and then stop. That is the exact failure PulseIngestHealth exists "
                    "to surface, so it will be visible rather than silent."
                )
            )

        username = options["username"]
        if not username:
            profile = (
                introspect_token(
                    access_token,
                    getattr(settings, "CONNECT_OAUTH_CLIENT_ID", ""),
                    getattr(settings, "CONNECT_OAUTH_CLIENT_SECRET", ""),
                    settings.CONNECT_PRODUCTION_URL,
                )
                or {}
            )
            username = profile.get("username") or ""
        if not username:
            raise CommandError("Could not determine the Connect username; pass --username explicitly.")

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(username=username)

        expires_at = entry.get("expires_at")
        try:
            parsed = datetime.fromisoformat(str(expires_at))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt_timezone.utc)
        except (TypeError, ValueError):
            parsed = datetime.now(tz=dt_timezone.utc) + timedelta(hours=1)

        UserConnectToken.objects.update_or_create(
            user=user,
            defaults={"access_token": access_token, "refresh_token": refresh_token, "expires_at": parsed},
        )

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded UserConnectToken for {username!r} "
                f"({'created' if created else 'existing'} user), expires {parsed.isoformat()}"
            )
        )
        self.stdout.write(self.style.NOTICE(f"  → set PULSE_POLLER_USERNAME={username}"))
