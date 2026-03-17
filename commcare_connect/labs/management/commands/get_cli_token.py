"""
Django management command to obtain OAuth token via CLI flow.

Usage:
    python manage.py get_cli_token
    python manage.py get_cli_token --profile test-user
    python manage.py get_cli_token --list-profiles
    python manage.py get_cli_token --switch-profile test-user
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from commcare_connect.labs.integrations.connect.cli import TokenManager, get_oauth_token


class Command(BaseCommand):
    help = "Obtain an OAuth access token for CLI/script usage via browser authorization"

    def add_arguments(self, parser):
        parser.add_argument(
            "--client-id",
            type=str,
            help="OAuth client ID (defaults to CLI_OAUTH_CLIENT_ID from settings)",
        )
        parser.add_argument(
            "--client-secret",
            type=str,
            help="OAuth client secret (optional, for confidential clients)",
        )
        parser.add_argument(
            "--production-url",
            type=str,
            help="Production URL (defaults to CONNECT_PRODUCTION_URL from settings)",
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
            default="export",
            help='OAuth scopes to request (default: "export")',
        )
        parser.add_argument(
            "--save-to",
            type=str,
            help="Save token to specified file (e.g., .oauth_token)",
        )
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Suppress output (only print token)",
        )
        parser.add_argument(
            "--profile",
            type=str,
            help="Save token under this profile name (auto-detected from username if omitted)",
        )
        parser.add_argument(
            "--list-profiles",
            action="store_true",
            help="List all saved profiles and exit",
        )
        parser.add_argument(
            "--switch-profile",
            type=str,
            metavar="NAME",
            help="Switch active profile and exit",
        )

    def handle(self, *args, **options):
        # Handle --list-profiles
        if options.get("list_profiles"):
            return self._handle_list_profiles()

        # Handle --switch-profile
        if options.get("switch_profile"):
            return self._handle_switch_profile(options["switch_profile"])

        # Get configuration from options or settings
        client_id = options.get("client_id") or getattr(settings, "CLI_OAUTH_CLIENT_ID", None)
        production_url = options.get("production_url") or getattr(settings, "CONNECT_PRODUCTION_URL", None)

        # Load client_secret from command line or settings (required for confidential clients)
        client_secret = options.get("client_secret") or getattr(settings, "CLI_OAUTH_CLIENT_SECRET", None)

        if not client_id:
            raise CommandError(
                "OAuth client ID not provided. " "Set CLI_OAUTH_CLIENT_ID in settings or use --client-id"
            )

        if not production_url:
            raise CommandError(
                "Production URL not provided. " "Set CONNECT_PRODUCTION_URL in settings or use --production-url"
            )

        # Get OAuth token
        token_data = get_oauth_token(
            client_id=client_id,
            production_url=production_url,
            client_secret=client_secret,  # Will be None for public clients
            port=options["port"],
            scope=options["scope"],
            verbose=not options["quiet"],
        )

        if not token_data:
            raise CommandError("Failed to obtain OAuth token")

        # Save to default TokenManager location with profile support
        profile = options.get("profile")
        token_manager = TokenManager(profile=profile)
        if token_manager.save_token(token_data):
            if not options["quiet"]:
                active = token_manager.get_active_profile_name()
                self.stdout.write(
                    self.style.SUCCESS(f"\nToken saved to: {token_manager.token_file} (profile: {active})")
                )
        else:
            self.stderr.write(self.style.ERROR("Failed to save token"))

        # Also save to custom file if requested
        if options["save_to"]:
            token_manager_custom = TokenManager(token_file=options["save_to"])
            if token_manager_custom.save_token(token_data):
                if not options["quiet"]:
                    self.stdout.write(self.style.SUCCESS(f"Also saved to: {options['save_to']}"))
            else:
                self.stderr.write(self.style.ERROR(f"Failed to save token to: {options['save_to']}"))

        # Print token (useful for piping to other commands)
        if options["quiet"]:
            self.stdout.write(token_data["access_token"])
        else:
            self.stdout.write("\n" + "=" * 70)
            self.stdout.write("Usage Examples:")
            self.stdout.write("=" * 70)
            self.stdout.write("\n# Set as environment variable:")
            self.stdout.write(f'export OAUTH_TOKEN="{token_data["access_token"]}"')
            self.stdout.write("\n# Use in Python:")
            self.stdout.write("import os")
            self.stdout.write('token = os.getenv("OAUTH_TOKEN")')
            self.stdout.write("\n# Use with httpx/requests:")
            self.stdout.write('headers = {"Authorization": f"Bearer {token}"}')
            self.stdout.write("")

    def _handle_list_profiles(self):
        """List all saved profiles."""
        token_manager = TokenManager()
        profiles = token_manager.list_profiles()

        if not profiles:
            self.stdout.write("No profiles found.")
            return

        row_fmt = "{:<20} {:<8} {:<20} {:<8} {}"
        self.stdout.write("\n" + row_fmt.format("Profile", "Active", "Username", "Expired", "Saved At"))
        self.stdout.write("-" * 90)

        for p in profiles:
            active_marker = "*" if p["active"] else ""
            expired = "yes" if p["is_expired"] else "no"
            saved_at = p.get("saved_at", "")[:19] if p.get("saved_at") else ""
            self.stdout.write(row_fmt.format(p["name"], active_marker, p["username"], expired, saved_at))

        self.stdout.write("")

    def _handle_switch_profile(self, profile_name: str):
        """Switch the active profile."""
        token_manager = TokenManager()
        if token_manager.set_active_profile(profile_name):
            self.stdout.write(self.style.SUCCESS(f"Switched active profile to: {profile_name}"))
        else:
            raise CommandError(f"Profile '{profile_name}' not found. Use --list-profiles to see available profiles.")
