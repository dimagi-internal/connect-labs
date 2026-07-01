"""
Test OAuth configuration — stubbed out after allauth removal during labs simplification.
Labs uses its own OAuth flow (/labs/login/).
"""
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Test OAuth configuration (disabled — allauth removed during labs simplification)"

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.WARNING(
                "This command is disabled. allauth was removed during labs simplification.\n"
                "Labs uses its own OAuth flow via /labs/login/."
            )
        )
