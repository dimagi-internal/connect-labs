"""Inspect or set the Connect account Pulse polls as.

Every headline figure on a Pulse display is bounded by this user's Connect org
membership, so picking the wrong one silently understates the whole product
rather than failing. On first deploy that is easy to do: with no explicit
setting, ingest falls back to whichever user has a stored token, which may see
a fraction of the estate.

The identity is stored in the database rather than only in settings because the
env var lives in an ECS task definition — changing it needs AWS access and a
redeploy, while this command runs through the existing run-labs-command
workflow.

    python manage.py pulse_poller                     # who are we polling as?
    python manage.py pulse_poller --set jonathan      # change it
    python manage.py pulse_poller --clear             # fall back to env/auto
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.pulse.client import SCALAR_POLLER, PulseAuthError, get_poller_user
from connect_labs.pulse.models import PulseScalar


class Command(BaseCommand):
    help = "Show or set the Connect user Pulse polls as."

    def add_arguments(self, parser):
        parser.add_argument("--set", dest="set_user", default="", help="Username to poll as.")
        parser.add_argument("--clear", action="store_true", help="Remove the override.")

    def handle(self, *args, **options):
        if options["clear"]:
            PulseScalar.objects.filter(key=SCALAR_POLLER).delete()
            self.stdout.write(self.style.SUCCESS("Override cleared; falling back to settings/auto."))

        if options["set_user"]:
            username = options["set_user"]
            user_model = get_user_model()
            if not user_model.objects.filter(username=username).exists():
                known = list(user_model.objects.values_list("username", flat=True)[:20])
                raise CommandError(f"No such user {username!r}. Known usernames include: {', '.join(known)}")
            PulseScalar.objects.update_or_create(key=SCALAR_POLLER, defaults={"value": {"username": username}})
            self.stdout.write(self.style.SUCCESS(f"Pulse will poll as {username!r}."))

        try:
            user = get_poller_user()
        except PulseAuthError as exc:
            raise CommandError(str(exc))

        override = PulseScalar.objects.filter(key=SCALAR_POLLER).first()
        source = "database override" if override else "settings/auto-fallback"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Polling as: {user.username}  ({source})"))
        self.stdout.write(
            "Scope — and therefore every figure on a Pulse display — follows this "
            "user's Connect org membership. Verify with: manage.py pulse_scope --baseline"
        )
