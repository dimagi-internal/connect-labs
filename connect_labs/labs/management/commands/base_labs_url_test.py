"""
Base class for testing labs project URLs.
Simple, fail-fast approach for debugging.
Uses real OAuth token from oauth_cli to simulate localhost behavior.
"""
from django.conf import settings
from django.core.management.base import BaseCommand
from django.test import Client

from connect_labs.labs.integrations.connect.cli import TokenManager


class BaseLabsURLTest(BaseCommand):
    """Base command for testing labs project URLs."""

    # Override these in subclasses
    project_name = "labs"  # e.g., "solicitations", "tasks", "audit"
    base_urls = []  # List of base URLs to test

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            help="Email of user (not used when using OAuth token)",
        )

    def handle(self, *args, **options):
        # Load OAuth token from oauth_cli cache
        token_manager = TokenManager()
        token_data = token_manager.load_token()

        if not token_data:
            self.stdout.write(
                self.style.ERROR("No OAuth token found. Run: python -m connect_labs.labs.oauth_cli.client")
            )
            return

        if token_manager.is_expired():
            self.stdout.write(
                self.style.ERROR("OAuth token expired. Run: python -m connect_labs.labs.oauth_cli.client")
            )
            return

        # Fetch user profile and organization data like the OAuth callback does
        access_token = token_data.get("access_token")

        from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data, introspect_token

        try:
            profile_data = introspect_token(
                access_token=access_token,
                client_id=settings.CONNECT_OAUTH_CLIENT_ID,
                client_secret=settings.CONNECT_OAUTH_CLIENT_SECRET,
                production_url=settings.CONNECT_PRODUCTION_URL,
            )
            if not profile_data:
                self.stdout.write(self.style.ERROR("Failed to introspect token"))
                raise SystemExit(1)

            org_data = fetch_user_organization_data(access_token)
            if not org_data:
                self.stdout.write(self.style.ERROR("Failed to fetch organization data"))
                raise SystemExit(1)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to fetch user profile: {e}"))
            raise SystemExit(1)

        # Temporarily add testserver to ALLOWED_HOSTS for testing
        current_allowed_hosts = list(settings.ALLOWED_HOSTS)
        if "testserver" not in current_allowed_hosts:
            settings.ALLOWED_HOSTS = current_allowed_hosts + ["testserver"]

        # Create test client with session containing OAuth token
        client = Client()

        # Set up session with OAuth data (same structure as web OAuth callback creates)
        # Convert expires_at from ISO string to timestamp if needed
        from datetime import datetime

        if "expires_at" in token_data and isinstance(token_data["expires_at"], str):
            expires_at_dt = datetime.fromisoformat(token_data["expires_at"])
            expires_at = expires_at_dt.timestamp()
        else:
            # Fallback: calculate from expires_in
            expires_in = token_data.get("expires_in", 1209600)  # Default 2 weeks
            from django.utils import timezone

            expires_at = (timezone.now() + timezone.timedelta(seconds=expires_in)).timestamp()

        session = client.session
        session["labs_oauth"] = {
            "access_token": access_token,
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_at": expires_at,
            "user_profile": {
                "id": profile_data.get("id"),
                "username": profile_data.get("username"),
                "email": profile_data.get("email"),
                "first_name": profile_data.get("first_name", ""),
                "last_name": profile_data.get("last_name", ""),
            },
            "organization_data": org_data or {},
        }
        session.save()

        self.stdout.write(f"Testing {self.project_name} URLs...")

        try:
            # Test all base URLs - fail fast on first error
            for base_url in self.base_urls:
                self._test_url(client, base_url)

            self.stdout.write(self.style.SUCCESS(f"PASS: All {self.project_name} URLs passed"))
        finally:
            # Restore original ALLOWED_HOSTS
            settings.ALLOWED_HOSTS = current_allowed_hosts

    def _test_url(self, client, url):
        """Test a single URL - fail immediately on error."""
        response = client.get(url, follow=True)
        if response.status_code != 200:
            self.stdout.write(self.style.ERROR(f"FAILED: {url}"))
            self.stdout.write(self.style.ERROR(f"  Status code: {response.status_code}"))
            raise SystemExit(1)

        # Force full template rendering to catch template errors
        try:
            content = response.content  # Access content to force rendering

            # Check if we got actual data (helps verify test is realistic)
            content_str = content.decode("utf-8")

            # Check for table rows (excluding header row)
            if "<tbody>" in content_str:
                tbody_content = content_str.split("<tbody>")[1].split("</tbody>")[0]
                tr_count = tbody_content.count("<tr")

                if tr_count > 0:
                    self.stdout.write(f"  OK {url} ({tr_count} rows)")
                else:
                    self.stdout.write(f"  OK {url} (empty table)")
            else:
                self.stdout.write(f"  OK {url}")
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"FAILED: {url}"))
            self.stdout.write(self.style.ERROR(f"  Error: {str(e)}"))
            import traceback

            self.stdout.write(self.style.ERROR(traceback.format_exc()))
            raise SystemExit(1)
