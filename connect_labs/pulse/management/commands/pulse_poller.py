"""Inspect or set the Connect account Pulse polls as.

Every headline figure on a Pulse display is bounded by this user's Connect org
membership, so picking the wrong one silently understates the whole product
rather than failing — which is exactly what happened on the first prod deploy,
back when an unset identity fell back to whichever user held a stored token.
There is no fallback now: the poller is named or ingest refuses to run.

The identity can also be stored in the database rather than only in settings,
because the env var lives in an ECS task definition — changing it needs AWS
access and a redeploy, while this command runs through the existing
run-labs-command workflow.

Check --list before setting a name. Labs usernames come from Connect OAuth, so
they are Connect handles rather than email local-parts or GitHub names, and the
two often differ: Jonathan Jackson is `jonathan` on labs, not `jjackson`.

    python manage.py pulse_poller                     # who are we polling as?
    python manage.py pulse_poller --list              # who *could* we poll as?
    python manage.py pulse_poller --set jonathan      # change it
    python manage.py pulse_poller --clear             # drop back to settings
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from connect_labs.pulse.client import SCALAR_POLLER, PulseAuthError, get_poller_user
from connect_labs.pulse.models import PulseScalar


class Command(BaseCommand):
    help = "Show or set the Connect user Pulse polls as."

    def _list_candidates(self):
        """Users with a stored Connect token — the only ones that can poll."""
        from connect_labs.labs.models import UserConnectToken

        rows = UserConnectToken.objects.select_related("user").order_by("-updated_at")
        if not rows:
            self.stdout.write(self.style.WARNING("No user has a stored Connect token yet."))
            self.stdout.write("A user must log into labs in a browser once before Pulse can poll as them.")
            return
        self.stdout.write(self.style.MIGRATE_HEADING("Users with a Connect token (candidate pollers)"))
        for row in rows:
            expired = " EXPIRED" if row.is_expired else ""
            self.stdout.write(f"  {row.user.username:<32} token updated {row.updated_at:%Y-%m-%d %H:%M}{expired}")

    def add_arguments(self, parser):
        parser.add_argument("--set", dest="set_user", default="", help="Username to poll as.")
        parser.add_argument("--clear", action="store_true", help="Remove the override.")
        parser.add_argument(
            "--list", action="store_true", help="List users holding a Connect token (i.e. usable pollers)."
        )

    def handle(self, *args, **options):
        if options["list"]:
            self._list_candidates()
            return

        if options["clear"]:
            PulseScalar.objects.filter(key=SCALAR_POLLER).delete()
            self.stdout.write(self.style.SUCCESS("Override cleared; PULSE_POLLER_USERNAME now applies."))

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
            # No default exists on purpose — see get_poller_user. Show the
            # candidates rather than making someone go hunting for them.
            self.stdout.write(self.style.ERROR(str(exc)))
            self.stdout.write("")
            self._list_candidates()
            raise CommandError("No Pulse poller configured.")

        override = PulseScalar.objects.filter(key=SCALAR_POLLER).first()
        source = "database override" if override else "PULSE_POLLER_USERNAME"
        self.stdout.write(self.style.MIGRATE_HEADING(f"Polling as: {user.username}  ({source})"))
        self.stdout.write(
            "Scope — and therefore every figure on a Pulse display — follows this "
            "user's Connect org membership. Verify with: manage.py pulse_scope --baseline"
        )
