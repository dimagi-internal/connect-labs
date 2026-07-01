"""
Get CommCare OAuth token for CLI usage.

This uses the same OAuth CLI infrastructure as get_cli_token,
but targets CommCare HQ instead of Connect.

Saves token to: ~/.commcare-connect/commcare_token.json
"""
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from connect_labs.labs.integrations.connect.cli import TokenManager, get_oauth_token


class Command(BaseCommand):
    help = "Get CommCare OAuth token for CLI usage"

    def add_arguments(self, parser):
        parser.add_argument(
            "--client-id",
            type=str,
            help="CommCare OAuth client ID (defaults to COMMCARE_OAUTH_CLIENT_ID)",
        )
        parser.add_argument(
            "--port",
            type=int,
            default=8765,
            help="Local port for OAuth callback (default: 8765)",
        )
        parser.add_argument(
            "--scope",
            type=str,
            default="access_apis",
            help="OAuth scope to request (default: access_apis)",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Minimal output",
        )

    def handle(self, *args, **options):
        # Get configuration - use CLI client ID (public client, no secret)
        client_id = options.get("client_id") or getattr(settings, "COMMCARE_OAUTH_CLI_CLIENT_ID", None)
        commcare_url = getattr(settings, "COMMCARE_HQ_URL", "https://www.commcarehq.org")

        if not client_id:
            raise CommandError(
                "CommCare OAuth CLI client ID not found.\n"
                "Set COMMCARE_OAUTH_CLI_CLIENT_ID in settings or use --client-id"
            )

        if not options["quiet"]:
            self.stdout.write(self.style.SUCCESS("\nCommCare OAuth Token Setup"))
            self.stdout.write("=" * 70)
            self.stdout.write(f"CommCare URL: {commcare_url}")
            self.stdout.write(f"Client ID: {client_id}")
            self.stdout.write(f"Scope: {options['scope']}\n")

        # Get OAuth token — CommCare HQ uses /oauth/ paths, not /o/
        token_data = get_oauth_token(
            client_id=client_id,
            production_url=commcare_url,
            client_secret=None,  # Public client - uses PKCE instead
            port=options["port"],
            callback_path="/commcare/callback",
            scope=options["scope"],
            verbose=not options["quiet"],
            authorize_path="/oauth/authorize/",
            token_path="/oauth/token/",
        )

        if not token_data:
            raise CommandError("Failed to obtain OAuth token")

        # Save to separate CommCare token file
        token_file = Path.home() / ".commcare-connect" / "commcare_token.json"
        token_manager = TokenManager(str(token_file))

        if not token_manager.save_token(token_data):
            raise CommandError("Failed to save token")

        self.stdout.write(self.style.SUCCESS(f"\nToken saved to: {token_file}"))

        # Show token info
        info = token_manager.get_token_info()
        if info and "expires_in_seconds" in info:
            minutes = info["expires_in_seconds"] // 60
            self.stdout.write(f"Expires in: {minutes} minutes\n")

        if not options["quiet"]:
            self.stdout.write(self.style.SUCCESS("Setup Complete!"))
            self.stdout.write("You can now use: python manage.py test_coverage_load\n")
