"""Add the Photo Audit Report demo program + opps to a user's local session.

    python manage.py inject_demo_context --email you@dimagi.com

DEBUG-only local convenience. After you log into the local server, this
merges the demo program ("Readers Nigeria (demo)") and its two synthetic
opportunities into your session's org data, so they appear in the top-right
context picker and program-mode fan-out can resolve them. Does not touch
your real org data — it only adds the demo entries.
"""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.core.management.base import BaseCommand, CommandError

from connect_labs.audit.photo_verification_demo import demo_org_data


class Command(BaseCommand):
    help = "Add the demo program/opps to a logged-in user's session org data (DEBUG only)."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="User email (or --username).")
        parser.add_argument("--username", help="User username.")

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError("Refusing to run outside DEBUG.")

        User = get_user_model()
        qs = User.objects.all()
        if options.get("email"):
            qs = qs.filter(email__iexact=options["email"])
        elif options.get("username"):
            qs = qs.filter(username=options["username"])
        else:
            raise CommandError("Pass --email or --username.")
        user = qs.first()
        if not user:
            raise CommandError("No matching user. Log into the local server first.")

        demo = demo_org_data()
        patched = 0
        for s in Session.objects.all():
            data = s.get_decoded()
            if str(data.get("_auth_user_id")) != str(user.id):
                continue
            oauth = data.get("labs_oauth")
            if not oauth:
                continue
            org = oauth.setdefault("organization_data", {})
            programs = org.setdefault("programs", [])
            opportunities = org.setdefault("opportunities", [])
            existing_progs = {p.get("id") for p in programs}
            existing_opps = {o.get("id") for o in opportunities}
            for p in demo["programs"]:
                if p["id"] not in existing_progs:
                    programs.append(p)
            for o in demo["opportunities"]:
                if o["id"] not in existing_opps:
                    opportunities.append(o)
            s.session_data = Session.objects.encode(data)
            s.save()
            patched += 1

        if not patched:
            raise CommandError(f"No labs sessions found for {user}. Log into the local server first, then re-run.")
        self.stdout.write(self.style.SUCCESS(f"Added demo program/opps to {patched} session(s) for {user}."))
        self.stdout.write(
            'Refresh the page; pick "Readers Nigeria (demo)" (or a demo opp) in the top-right context selector.'
        )
