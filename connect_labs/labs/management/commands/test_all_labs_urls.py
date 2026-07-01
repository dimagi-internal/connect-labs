"""
Management command to test all labs project URLs at once.
Run with: python manage.py test_all_labs_urls
"""
from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Test all labs project URLs (solicitations, tasks, audit)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            type=str,
            default="jjackson-dev@dimagi.com",
            help="Email of user to test with",
        )

    def handle(self, *args, **options):
        user_email = options["user"]

        self.stdout.write(f"\nTesting all labs URLs for {user_email}\n")

        projects = ["solicitations", "tasks", "audit", "explorer"]

        for project in projects:
            command_name = f"test_{project}_urls"
            self.stdout.write(f"\n{project.upper()}:")
            try:
                call_command(command_name, user=user_email)
            except SystemExit as e:
                if e.code != 0:
                    self.stdout.write(self.style.ERROR(f"\nFAILED: {project} tests failed\n"))
                    raise

        self.stdout.write(self.style.SUCCESS("\nPASS: All labs projects passed!\n"))
