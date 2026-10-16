"""Seed a labs-only demo organisation with three open calls and a few invented responses.

Usage:
    python manage.py solicitations_seed_demo
    python manage.py solicitations_seed_demo --admin <username>   # make them a member of the demo org
"""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.solicitations import demo


class Command(BaseCommand):
    help = "Create or refresh the invented demo organisation, its open calls and a few responses (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--admin", help="Username to make a member of the demo organisation.")

    def handle(self, *args, **options):
        admin = None
        if options.get("admin"):
            admin = get_user_model().objects.filter(username=options["admin"]).first()
            if admin is None:
                raise CommandError(f"No user {options['admin']!r}")
        result = demo.seed(admin=admin)
        self.stdout.write(self.style.SUCCESS(f"Demo organisation {result['organisation']}"))
        for key, pk in result["calls"].items():
            self.stdout.write(f"  call {key}: /solicitations/{pk}/")
        self.stdout.write(f"  responses: {result['responses']}")
