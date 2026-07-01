"""Create an MCP Personal Access Token.

Usage:
    python manage.py mcp_create_token --user alice --name my-laptop
    python manage.py mcp_create_token --user alice --name ci --ttl-days 30
"""
from django.core.management.base import BaseCommand, CommandError

from connect_labs.mcp.models import MCPAccessToken
from connect_labs.mcp.snippets import build_mcp_json_snippet
from connect_labs.users.models import User


class Command(BaseCommand):
    help = "Create an MCP Personal Access Token. Prints the raw token to stdout (only shown once)."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Username to issue token for")
        parser.add_argument("--name", required=True, help="Label for this token (e.g. 'laptop')")
        parser.add_argument(
            "--ttl-days",
            type=int,
            default=90,
            help="Lifetime in days (default: 90). Pass 0 for no expiry.",
        )

    def handle(self, *args, **opts):
        username = opts["user"]
        name = opts["name"]
        ttl = opts["ttl_days"] or None

        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            raise CommandError(f"No user with username {username!r}")

        _, raw = MCPAccessToken.create_token(user, name=name, ttl_days=ttl)

        self.stdout.write(self.style.SUCCESS("Token created. Store it now — it is not retrievable later.\n"))
        self.stdout.write(f"Token: {raw}\n")
        self.stdout.write("\nAdd this to your .claude/mcp.json:\n")
        self.stdout.write(self.style.WARNING(build_mcp_json_snippet(raw)))
