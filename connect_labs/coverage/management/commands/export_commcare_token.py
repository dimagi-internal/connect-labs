"""
Export CommCare OAuth token from Django session to CLI token file.

This allows the test_coverage_load command to use your web session's CommCare OAuth token.
"""
import json
from pathlib import Path

from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Export CommCare OAuth token from Django session to CLI token file"

    def add_arguments(self, parser):
        parser.add_argument(
            "--session-key",
            type=str,
            help="Django session key (get from browser cookies)",
        )
        parser.add_argument(
            "--username",
            type=str,
            help="Find session by username (alternative to session-key)",
        )

    def handle(self, *args, **options):
        session_key = options.get("session_key")
        username = options.get("username")

        if not session_key and not username:
            self.stdout.write(self.style.ERROR("Must provide either --session-key or --username"))
            self.stdout.write("\nTo get your session key:")
            self.stdout.write("1. Open browser dev tools (F12)")
            self.stdout.write("2. Go to Application > Cookies > localhost:8000")
            self.stdout.write("3. Copy the 'sessionid' value")
            return

        # Find session
        if session_key:
            try:
                session = Session.objects.get(session_key=session_key)
            except Session.DoesNotExist:
                self.stdout.write(self.style.ERROR(f"Session not found: {session_key}"))
                return
        else:
            # Find by username - get most recent session
            sessions = Session.objects.filter(expire_date__gte=timezone.now())
            session = None
            for s in sessions:
                data = s.get_decoded()
                labs_oauth = data.get("labs_oauth", {})
                user_profile = labs_oauth.get("user_profile", {})
                if user_profile.get("username") == username:
                    session = s
                    break

            if not session:
                self.stdout.write(self.style.ERROR(f"No active session found for username: {username}"))
                return

        # Get session data
        session_data = session.get_decoded()

        # Extract CommCare OAuth
        commcare_oauth = session_data.get("commcare_oauth")

        if not commcare_oauth:
            self.stdout.write(self.style.ERROR("No CommCare OAuth token found in session"))
            self.stdout.write("Visit /labs/commcare/initiate/ to authorize")
            return

        # Save to CLI token file
        token_file = Path.home() / ".commcare-connect" / "commcare_token.json"
        token_file.parent.mkdir(exist_ok=True)

        with open(token_file, "w") as f:
            json.dump(commcare_oauth, f, indent=2)

        self.stdout.write(self.style.SUCCESS(f"CommCare token exported to: {token_file}"))

        # Show token info
        from datetime import datetime

        expires_at = datetime.fromtimestamp(commcare_oauth.get("expires_at", 0))
        self.stdout.write(f"\nToken expires: {expires_at}")
        self.stdout.write(f"Time remaining: {expires_at - datetime.now()}")
